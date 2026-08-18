"""The Council Engine — orchestration for the V1 pipeline.

quick:    route -> single candidate -> final
council:  route -> parallel candidates -> combined check ->
          agree/partial: synthesis -> final
          disagree:      blinded judge -> final
deep:     as council, plus: one critique round before the judge when the
          disagreement is about reasoning; verifier audits the final
          answer's claims; at most one revision on a failed audit.

Judge and verifier run on opposite providers. Every stage is persisted to
the steps table as it happens. Provider failures degrade gracefully and
are recorded, never hidden.
"""

import asyncio
import random
import time

from sqlalchemy import func, select

from council.db.models import Request, Step
from council.db.session import session_scope
from council.engine.budget import BudgetTracker
from council.engine.prompts import PromptRegistry
from council.engine.schemas import (
    CombinedCheck,
    Critique,
    JudgeVerdict,
    RevisedAnswer,
    Synthesis,
    VerifierReport,
)
from council.engine.streaming import DeltaThrottle, FieldStreamExtractor
from council.providers.base import ModelProvider, ModelResponse, ProviderError


class CouncilEngine:
    def __init__(
        self,
        providers: dict[str, ModelProvider],
        registry: PromptRegistry,
        *,
        flagship_models: dict[str, str],
        cheap_models: dict[str, str],
        check_provider: str = "openai",
        judge_provider: str = "anthropic",
        quick_mode_strategy: str = "alternate",
        publish=None,  # optional callable(request_id, event_dict) for live progress
    ):
        self.providers = providers
        self.registry = registry
        self.flagship_models = flagship_models
        self.cheap_models = cheap_models
        self.check_provider = check_provider
        self.judge_provider = judge_provider
        self.quick_mode_strategy = quick_mode_strategy
        self._publish = publish
        self._cancel_started: dict[str, float] = {}
        # Provider calls that have been initiated but not yet returned. If a
        # request is cancelled mid-flight these are persisted as interrupted
        # steps, so the trace never under-reports initiated API attempts.
        self._inflight: dict[str, list[dict]] = {}

    def _emit(self, request_id: str, event: dict) -> None:
        if self._publish is not None:
            self._publish(request_id, event)

    async def _generate(
        self, request_id: str, stage: str, provider: ModelProvider, model: str, messages, **kwargs
    ) -> ModelResponse:
        """Every provider call goes through here so an initiated attempt is
        visible even if the request is cancelled before the call returns."""
        record = {"stage": stage, "provider": provider.name, "model": model}
        self._inflight.setdefault(request_id, []).append(record)
        try:
            resp = await provider.generate(messages, model=model, **kwargs)
        except asyncio.CancelledError:
            raise  # leave the record: mark_cancelled() persists it
        except BaseException:
            self._drop_inflight(request_id, record)
            raise
        self._drop_inflight(request_id, record)
        return resp

    def _drop_inflight(self, request_id: str, record: dict) -> None:
        records = self._inflight.get(request_id)
        if records and record in records:
            records.remove(record)
        if records is not None and not records:
            self._inflight.pop(request_id, None)

    def _delta_cb(self, request_id: str, stage: str, *, extract_field: bool = False):
        """Streaming callback for answer-producing stages: publishes live
        text increments as {'type':'delta'} events. For schema stages the raw
        JSON stream is filtered down to the final_answer field. Returns None
        when nobody is listening — the provider then skips streaming."""
        if self._publish is None:
            return None
        extractor = FieldStreamExtractor("final_answer") if extract_field else None
        throttle = DeltaThrottle(
            lambda t: self._emit(request_id, {"type": "delta", "stage": stage, "text": t})
        )

        def cb(chunk: str) -> None:
            text = extractor.feed(chunk) if extractor else chunk
            throttle.push(text)

        cb.flush = throttle.flush  # type: ignore[attr-defined]
        return cb

    def _other_provider(self, name: str) -> str:
        names = sorted(self.providers)
        return names[1] if name == names[0] else names[0]

    # ------------------------------------------------------------------ run

    async def create(
        self, question: str, mode: str, conversation_id: str | None = None
    ) -> str:
        """Create the request row up front so callers can subscribe to its
        event stream before execution starts (async API path)."""
        BudgetTracker(mode)  # validate mode before persisting anything
        return await self._create_request(question, mode, conversation_id)

    async def run(
        self,
        question: str,
        mode: str,
        request_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        """history: optional prior turns of the conversation as chat messages
        ([{role: user|assistant, content: ...}]); candidates see them so
        follow-up questions work. Later stages audit only the current turn."""
        started = time.monotonic()
        budget = BudgetTracker(mode)

        if request_id is None:
            request_id = await self._create_request(question, mode, None)
        self._emit(request_id, {"type": "started", "mode": mode})
        seq = _Seq()
        history = history or []

        try:
            if mode == "quick":
                final = await self._run_quick(request_id, question, budget, seq, history)
            else:
                final = await self._run_council(
                    request_id, question, budget, seq, deep=(mode == "deep"), history=history
                )
        except asyncio.CancelledError:
            self._cancel_started[request_id] = started
            raise  # the API layer records the cancellation
        except Exception as e:
            await self._finish(request_id, status="failed", error=str(e), started=started)
            raise

        await self._finish(request_id, started=started, **final)
        return await self.get_request(request_id)

    # ---------------------------------------------------------------- quick

    async def _run_quick(
        self, request_id: str, question: str, budget, seq, history: list | None = None
    ) -> dict:
        """One model answers; if it fails, fail over to the other provider
        once, visibly (degraded=True). Same policy as council: a healthy
        provider means the user gets an answer."""
        primary = await self._pick_quick_provider()
        prompt = self.registry.get("candidate")
        messages = [
            {"role": "system", "content": prompt.text},
            *(history or []),
            {"role": "user", "content": question},
        ]

        budget.spend("candidate")
        cb = self._delta_cb(request_id, "candidate_a")
        try:
            resp = await self._generate(
                request_id,
                "candidate_a",
                primary,
                self.flagship_models[primary.name],
                messages,
                on_delta=cb,
            )
            if cb:
                cb.flush()
        except ProviderError as e:
            await self._record_error(request_id, seq(), "candidate_a", primary.name, e)
            fallback_name = self._other_provider(primary.name)
            fallback = self.providers[fallback_name]
            budget.spend("candidate_fallback")
            cb2 = self._delta_cb(request_id, "candidate_fallback")
            resp = await self._generate(
                request_id,
                "candidate_fallback",
                fallback,
                self.flagship_models[fallback_name],
                messages,
                on_delta=cb2,
            )  # both providers failing fails the request — recorded by run()
            if cb2:
                cb2.flush()
            await self._record_call(
                request_id, seq(), "candidate_fallback", resp, prompt.version_id
            )
            return {"status": "complete", "final_answer": resp.content, "degraded": True}
        await self._record_call(request_id, seq(), "candidate_a", resp, prompt.version_id)
        return {"status": "complete", "final_answer": resp.content}

    async def _pick_quick_provider(self) -> ModelProvider:
        strategy = self.quick_mode_strategy
        if strategy in self.providers:
            return self.providers[strategy]
        # alternate: parity of prior quick requests decides, so the choice is
        # deterministic, survives restarts and splits usage ~50/50.
        async with session_scope() as s:
            count = (
                await s.execute(
                    select(func.count()).select_from(Request).where(Request.mode == "quick")
                )
            ).scalar_one()
        names = sorted(self.providers)
        return self.providers[names[count % len(names)]]

    # -------------------------------------------------------------- council

    async def _run_council(
        self,
        request_id: str,
        question: str,
        budget,
        seq,
        deep: bool = False,
        history: list | None = None,
    ) -> dict:
        prompt = self.registry.get("candidate")
        # Randomize which provider is Candidate A — blinding starts at birth,
        # and the judge in M2 inherits it for free.
        names = sorted(self.providers)
        random.shuffle(names)
        label_by_provider = {names[0]: "A", names[1]: "B"}

        budget.spend("candidate_a")
        budget.spend("candidate_b")

        async def one(name: str) -> ModelResponse:
            p = self.providers[name]
            return await self._generate(
                request_id,
                f"candidate_{label_by_provider[name].lower()}",
                p,
                self.flagship_models[name],
                [
                    {"role": "system", "content": prompt.text},
                    *(history or []),
                    {"role": "user", "content": question},
                ],
            )

        results = await asyncio.gather(*(one(n) for n in names), return_exceptions=True)
        candidates: dict[str, ModelResponse] = {}
        failures: dict[str, Exception] = {}
        for name, res in zip(names, results, strict=True):
            label = label_by_provider[name]
            stage = f"candidate_{label.lower()}"
            if isinstance(res, Exception):
                failures[name] = res
                await self._record_error(request_id, seq(), stage, name, res)
            else:
                candidates[label] = res
                await self._record_call(request_id, seq(), stage, res, prompt.version_id)

        if len(candidates) == 0:
            details = {k: str(v) for k, v in failures.items()}
            raise ProviderError(f"Both providers failed: {details}")

        if len(candidates) == 1:
            # One provider died: degrade to a single-model answer, visibly.
            answer = next(iter(candidates.values())).content
            return {"status": "complete", "final_answer": answer, "degraded": True}

        check = await self._combined_check(request_id, question, candidates, budget, seq)
        if check is None:
            # Check stage failed: fall back to the disagreement report so the
            # user still gets both answers rather than nothing.
            report = _disagreement_report(candidates, None)
            await self._record_stage(request_id, seq(), "disagreement_report", {"fallback": True})
            return {"status": "complete", "final_answer": report, "degraded": True}

        result: dict
        critiques: dict[str, Critique] | None = None
        # The provider that produced the final answer — the verifier must be
        # the OTHER one, whichever path built the answer (GPT M2 review #1).
        producer: str

        if check.agreement in ("agree", "partial"):
            synthesis = await self._synthesize(request_id, question, candidates, check, budget, seq)
            if synthesis is not None:
                result = {"status": "complete", "final_answer": synthesis.final_answer}
                producer = self.check_provider
            else:
                # Synthesis failed — candidates agreed, so candidate A is safe.
                result = {
                    "status": "complete",
                    "final_answer": candidates["A"].content,
                    "degraded": True,
                }
                producer = candidates["A"].provider
        else:
            # Reasoning/design disputes earn one critique round in deep mode.
            # Factual disputes skip it — evidence (M4) settles facts, not debate.
            if deep and check.disagreement_type in ("reasoning", "both"):
                critiques = await self._critique_round(
                    request_id, question, candidates, budget, seq
                )

            verdict = await self._judge(
                request_id, question, candidates, check, critiques, budget, seq
            )
            if verdict is None:
                # Judge failed: fall back to the both-answers report, visibly.
                report = _disagreement_report(candidates, check)
                await self._record_stage(
                    request_id, seq(), "disagreement_report", {"fallback": True}
                )
                return {"status": "complete", "final_answer": report, "degraded": True}
            result = {"status": "complete", "final_answer": verdict.final_answer}
            producer = self.judge_provider

        if deep and result.get("final_answer"):
            result = await self._verify_and_maybe_revise(
                request_id, question, result, candidates, critiques, budget, seq,
                producer=producer,
            )
        return result

    async def _combined_check(
        self, request_id, question, candidates, budget, seq
    ) -> CombinedCheck | None:
        prompt = self.registry.get("combined_check")
        provider = self.providers[self.check_provider]
        model = self.cheap_models[self.check_provider]
        user = (
            f"QUESTION:\n{question}\n\n"
            f"CANDIDATE A:\n{candidates['A'].content}\n\n"
            f"CANDIDATE B:\n{candidates['B'].content}"
        )
        budget.spend("combined_check")
        try:
            resp = await self._generate(
                request_id,
                "combined_check",
                provider,
                model,
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                schema=CombinedCheck,
            )
        except ProviderError as e:
            await self._record_error(request_id, seq(), "combined_check", provider.name, e)
            return None
        await self._record_call(
            request_id, seq(), "combined_check", resp, prompt.version_id,
            output=resp.parsed.model_dump(),
        )
        return resp.parsed

    async def _synthesize(
        self, request_id, question, candidates, check, budget, seq
    ) -> Synthesis | None:
        prompt = self.registry.get("synthesis")
        provider = self.providers[self.check_provider]
        model = self.cheap_models[self.check_provider]
        user = (
            f"QUESTION:\n{question}\n\n"
            f"CANDIDATE A:\n{candidates['A'].content}\n\n"
            f"CANDIDATE B:\n{candidates['B'].content}\n\n"
            f"COMPARISON SUMMARY:\n{check.summary}"
        )
        budget.spend("synthesis")
        cb = self._delta_cb(request_id, "synthesis", extract_field=True)
        try:
            resp = await self._generate(
                request_id,
                "synthesis",
                provider,
                model,
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                schema=Synthesis,
                on_delta=cb,
            )
            if cb:
                cb.flush()
        except ProviderError as e:
            await self._record_error(request_id, seq(), "synthesis", provider.name, e)
            return None
        await self._record_call(
            request_id, seq(), "synthesis", resp, prompt.version_id,
            output=resp.parsed.model_dump(),
        )
        return resp.parsed

    async def _critique_round(
        self, request_id, question, candidates, budget, seq
    ) -> dict[str, Critique] | None:
        """Each candidate's author reviews the OTHER candidate. One round, ever."""
        prompt = self.registry.get("critique")
        author_of = {
            label: candidates[label].provider for label in ("A", "B")
        }

        async def critique_of(label: str) -> ModelResponse:
            reviewed = candidates[label]
            other_label = "B" if label == "A" else "A"
            reviewer_name = author_of[other_label]
            reviewer = self.providers[reviewer_name]
            user = (
                f"QUESTION:\n{question}\n\n"
                f"THE ANSWER YOU ARE REVIEWING:\n{reviewed.content}\n\n"
                f"YOUR OWN ANSWER (context):\n{candidates[other_label].content}"
            )
            return await self._generate(
                request_id,
                f"critique_of_{label.lower()}",
                reviewer,
                self.flagship_models[reviewer_name],
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                schema=Critique,
            )

        budget.spend("critique_of_a")
        budget.spend("critique_of_b")
        results = await asyncio.gather(
            critique_of("A"), critique_of("B"), return_exceptions=True
        )
        critiques: dict[str, Critique] = {}
        for label, res in zip(("A", "B"), results, strict=True):
            stage = f"critique_of_{label.lower()}"
            # The reviewer of a candidate is deterministically the author of
            # the OTHER candidate — record the real provider on failure too.
            reviewer_name = author_of["B" if label == "A" else "A"]
            if isinstance(res, Exception):
                await self._record_error(
                    request_id, seq(), stage, reviewer_name, res
                )
            else:
                critiques[label] = res.parsed
                await self._record_call(
                    request_id, seq(), stage, res, prompt.version_id,
                    output=res.parsed.model_dump(),
                )
        # Partial critiques are still useful; total failure means none.
        return critiques or None

    async def _judge(
        self, request_id, question, candidates, check, critiques, budget, seq
    ) -> JudgeVerdict | None:
        prompt = self.registry.get("judge")
        provider = self.providers[self.judge_provider]
        model = self.flagship_models[self.judge_provider]
        parts = [
            f"QUESTION:\n{question}",
            f"CANDIDATE A:\n{candidates['A'].content}",
            f"CANDIDATE B:\n{candidates['B'].content}",
            f"COMPARISON SUMMARY:\n{check.summary}",
        ]
        if check.disagreement_type in ("factual", "both"):
            # Evidence tools land in M4. Until then the judge must not settle
            # a checkable factual dispute by plausibility (frozen rule:
            # factual disagreement -> evidence, not debate or model voting).
            parts.append(
                "NO EXTERNAL EVIDENCE IS AVAILABLE for this request. The "
                "candidates disagree on checkable facts. You must NOT resolve "
                "a factual dispute by which claim sounds more plausible or "
                "confident. If the supplied material itself does not settle a "
                "disputed fact, your decision must be 'uncertain' and the "
                "final answer must present both positions honestly and state "
                "what evidence would settle the question."
            )
        if critiques:
            for label in ("A", "B"):
                if label in critiques:
                    c = critiques[label]
                    issues = "\n".join(
                        f"- [{i.severity}/{i.kind}] {i.detail}" for i in c.issues
                    ) or "(no material issues found)"
                    parts.append(f"CRITIQUE OF CANDIDATE {label}:\n{issues}\n{c.overall}")
        budget.spend("judge")
        cb = self._delta_cb(request_id, "judge", extract_field=True)
        try:
            resp = await self._generate(
                request_id,
                "judge",
                provider,
                model,
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": "\n\n".join(parts)},
                ],
                schema=JudgeVerdict,
                max_tokens=8192,
                on_delta=cb,
            )
            if cb:
                cb.flush()
        except ProviderError as e:
            await self._record_error(request_id, seq(), "judge", provider.name, e)
            return None
        verdict: JudgeVerdict = resp.parsed
        await self._record_call(
            request_id, seq(), "judge", resp, prompt.version_id,
            output=verdict.model_dump(),
        )
        return verdict

    async def _verify_and_maybe_revise(
        self, request_id, question, result, candidates, critiques, budget, seq,
        *, producer: str,
    ) -> dict:
        """Deep mode's audit: verifier on the opposite provider from whichever
        provider actually produced the final answer (judge OR synthesis);
        at most one revision; verifier failure degrades, never blocks."""
        prompt = self.registry.get("verifier")
        verifier_name = self._other_provider(producer)
        provider = self.providers[verifier_name]
        model = self.flagship_models[verifier_name]

        source = [
            f"CANDIDATE A:\n{candidates['A'].content}",
            f"CANDIDATE B:\n{candidates['B'].content}",
        ]
        if critiques:
            for label, c in critiques.items():
                source.append(f"CRITIQUE OF {label}:\n{c.overall}")
        user = (
            f"QUESTION:\n{question}\n\n"
            f"FINAL ANSWER UNDER AUDIT:\n{result['final_answer']}\n\n"
            f"SOURCE MATERIAL:\n\n" + "\n\n".join(source)
        )
        budget.spend("verifier")
        try:
            resp = await self._generate(
                request_id,
                "verifier",
                provider,
                model,
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                schema=VerifierReport,
                max_tokens=8192,
            )
        except ProviderError as e:
            await self._record_error(request_id, seq(), "verifier", verifier_name, e)
            return {**result, "degraded": True}
        report: VerifierReport = resp.parsed
        await self._record_call(
            request_id, seq(), "verifier", resp, prompt.version_id,
            output=report.model_dump(),
        )
        if report.verdict == "pass":
            return result

        revised = await self._revise(
            request_id, question, result["final_answer"], report, candidates, budget, seq,
            producer=producer,
        )
        if revised is None:
            # Revision failed — ship the audited answer with the flags visible.
            return {**result, "degraded": True}
        return {**result, "final_answer": revised.final_answer}

    async def _revise(
        self, request_id, question, final_answer, report: VerifierReport,
        candidates, budget, seq, *, producer: str,
    ) -> RevisedAnswer | None:
        prompt = self.registry.get("revision")
        provider = self.providers[producer]
        model = self.flagship_models[producer]
        user = (
            f"QUESTION:\n{question}\n\n"
            f"CURRENT FINAL ANSWER:\n{final_answer}\n\n"
            f"VERIFIER REASONS FOR REVISION:\n"
            + "\n".join(f"- {r}" for r in report.reasons)
            + "\n\nSOURCE MATERIAL:\n\n"
            f"CANDIDATE A:\n{candidates['A'].content}\n\n"
            f"CANDIDATE B:\n{candidates['B'].content}"
        )
        budget.spend("revision")
        cb = self._delta_cb(request_id, "revision", extract_field=True)
        try:
            resp = await self._generate(
                request_id,
                "revision",
                provider,
                model,
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                schema=RevisedAnswer,
                max_tokens=8192,
                on_delta=cb,
            )
            if cb:
                cb.flush()
        except ProviderError as e:
            await self._record_error(request_id, seq(), "revision", provider.name, e)
            return None
        revised: RevisedAnswer = resp.parsed
        await self._record_call(
            request_id, seq(), "revision", resp, prompt.version_id,
            output=revised.model_dump(),
        )
        return revised

    # ------------------------------------------------------------ persistence

    async def _create_request(
        self, question: str, mode: str, conversation_id: str | None = None
    ) -> str:
        async with session_scope() as s:
            req = Request(
                question=question, mode=mode, status="routed", conversation_id=conversation_id
            )
            s.add(req)
            await s.flush()
            return req.id

    async def _record_call(
        self, request_id, seq, stage, resp: ModelResponse, prompt_version, output=None
    ):
        async with session_scope() as s:
            s.add(
                Step(
                    request_id=request_id,
                    seq=seq,
                    stage=stage,
                    provider=resp.provider,
                    model=resp.model,
                    prompt_version=prompt_version,
                    output=output if output is not None else {"text": resp.content},
                    status="ok",
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    cost_usd=resp.cost_usd,
                    latency_ms=resp.latency_ms,
                    api_attempts=resp.api_attempts,
                )
            )
            req = await s.get(Request, request_id)
            req.total_input_tokens += resp.input_tokens
            req.total_output_tokens += resp.output_tokens
            req.total_cost_usd += resp.cost_usd
            req.model_calls += 1
            req.total_api_attempts += resp.api_attempts
        self._emit(
            request_id,
            {
                "type": "stage",
                "stage": stage,
                "status": "ok",
                "provider": resp.provider,
                "model": resp.model,
                "cost_usd": round(resp.cost_usd, 6),
                "latency_ms": resp.latency_ms,
            },
        )

    async def _record_error(self, request_id, seq, stage, provider_name, error: Exception):
        async with session_scope() as s:
            s.add(
                Step(
                    request_id=request_id,
                    seq=seq,
                    stage=stage,
                    provider=provider_name,
                    status="error",
                    error=f"{type(error).__name__}: {error}",
                )
            )
        self._emit(
            request_id,
            {"type": "stage", "stage": stage, "status": "error", "provider": provider_name},
        )

    async def _record_stage(self, request_id, seq, stage, output: dict | None):
        async with session_scope() as s:
            s.add(Step(request_id=request_id, seq=seq, stage=stage, output=output, status="ok"))
        self._emit(request_id, {"type": "stage", "stage": stage, "status": "ok"})

    async def _finish(
        self, request_id, *, status="complete", final_answer=None, degraded=False,
        error=None, started: float,
    ):
        async with session_scope() as s:
            req = await s.get(Request, request_id)
            req.status = status
            req.final_answer = final_answer
            req.degraded = req.degraded or degraded
            req.error = error
            req.latency_ms = int((time.monotonic() - started) * 1000)
            was_degraded = req.degraded
        self._emit(
            request_id,
            {"type": "done", "status": status, "degraded": was_degraded, "error": error},
        )

    async def mark_cancelled(self, request_id: str) -> None:
        """Record a user-cancelled request. Whatever stages completed before
        the stop are already persisted (and billed); nothing further runs.

        Provider calls that were in flight when the stop landed are persisted
        as 'interrupted' steps: the provider may already have accepted (and
        billed) them, and their token usage is unknowable after a hard
        cancel, so they are counted as initiated API attempts with unknown
        cost rather than omitted or invented."""
        started = self._cancel_started.pop(request_id, None)
        interrupted = self._inflight.pop(request_id, [])
        async with session_scope() as s:
            req = await s.get(Request, request_id)
            if req is None or req.status in ("complete", "failed"):
                return
            next_seq = (
                await s.execute(
                    select(func.coalesce(func.max(Step.seq), 0)).where(
                        Step.request_id == request_id
                    )
                )
            ).scalar_one() + 1
            for record in interrupted:
                s.add(
                    Step(
                        request_id=request_id,
                        seq=next_seq,
                        stage=record["stage"],
                        provider=record["provider"],
                        model=record["model"],
                        status="interrupted",
                        error="cancelled mid-call; usage unknown, may have been billed",
                        api_attempts=1,
                    )
                )
                next_seq += 1
            req.total_api_attempts += len(interrupted)
            req.status = "cancelled"
            req.error = "cancelled by user"
            if started is not None:
                req.latency_ms = int((time.monotonic() - started) * 1000)
        self._emit(
            request_id,
            {"type": "done", "status": "cancelled", "degraded": False, "error": None},
        )

    async def get_request(self, request_id: str) -> dict:
        async with session_scope() as s:
            req = await s.get(Request, request_id)
            if req is None:
                raise KeyError(request_id)
            stmt = select(Step).where(Step.request_id == request_id).order_by(Step.seq)
            steps = (await s.execute(stmt)).scalars().all()
            return {
                "id": req.id,
                "question": req.question,
                "mode": req.mode,
                "status": req.status,
                "final_answer": req.final_answer,
                "degraded": req.degraded,
                "error": req.error,
                "totals": {
                    "input_tokens": req.total_input_tokens,
                    "output_tokens": req.total_output_tokens,
                    "cost_usd": round(req.total_cost_usd, 6),
                    "model_calls": req.model_calls,
                    "api_attempts": req.total_api_attempts,
                    "latency_ms": req.latency_ms,
                },
                "user_rating": req.user_rating,
                "steps": [
                    {
                        "seq": st.seq,
                        "stage": st.stage,
                        "provider": st.provider,
                        "model": st.model,
                        "prompt_version": st.prompt_version,
                        "status": st.status,
                        "error": st.error,
                        "output": st.output,
                        "tokens": {"input": st.input_tokens, "output": st.output_tokens},
                        "cost_usd": round(st.cost_usd, 6),
                        "latency_ms": st.latency_ms,
                        "api_attempts": st.api_attempts,
                    }
                    for st in steps
                ],
            }


class _Seq:
    def __init__(self):
        self._n = 0

    def __call__(self) -> int:
        self._n += 1
        return self._n


def _disagreement_report(candidates, check: CombinedCheck | None) -> str:
    """Formatted both-answers report shown until the judge lands in M2."""
    parts = ["The two models materially disagree on this question.\n"]
    if check is not None:
        parts.append(f"**Where they differ:** {check.summary}\n")
        if check.key_disagreements:
            parts.append(
                "**Key disagreements:**\n"
                + "\n".join(f"- {d}" for d in check.key_disagreements)
                + "\n"
            )
    parts.append(f"---\n\n**Candidate A:**\n\n{candidates['A'].content}\n")
    parts.append(f"---\n\n**Candidate B:**\n\n{candidates['B'].content}")
    return "\n".join(parts)

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

    def _emit(self, request_id: str, event: dict) -> None:
        if self._publish is not None:
            self._publish(request_id, event)

    def _other_provider(self, name: str) -> str:
        names = sorted(self.providers)
        return names[1] if name == names[0] else names[0]

    # ------------------------------------------------------------------ run

    async def create(self, question: str, mode: str) -> str:
        """Create the request row up front so callers can subscribe to its
        event stream before execution starts (async API path)."""
        BudgetTracker(mode)  # validate mode before persisting anything
        return await self._create_request(question, mode)

    async def run(self, question: str, mode: str, request_id: str | None = None) -> dict:
        started = time.monotonic()
        budget = BudgetTracker(mode)

        if request_id is None:
            request_id = await self._create_request(question, mode)
        self._emit(request_id, {"type": "started", "mode": mode})
        seq = _Seq()

        try:
            if mode == "quick":
                final = await self._run_quick(request_id, question, budget, seq)
            else:
                final = await self._run_council(
                    request_id, question, budget, seq, deep=(mode == "deep")
                )
        except Exception as e:
            await self._finish(request_id, status="failed", error=str(e), started=started)
            raise

        await self._finish(request_id, started=started, **final)
        return await self.get_request(request_id)

    # ---------------------------------------------------------------- quick

    async def _run_quick(self, request_id: str, question: str, budget, seq) -> dict:
        """One model answers; if it fails, fail over to the other provider
        once, visibly (degraded=True). Same policy as council: a healthy
        provider means the user gets an answer."""
        primary = await self._pick_quick_provider()
        prompt = self.registry.get("candidate")
        messages = [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": question},
        ]

        budget.spend("candidate")
        try:
            resp = await primary.generate(messages, model=self.flagship_models[primary.name])
        except ProviderError as e:
            await self._record_error(request_id, seq(), "candidate_a", primary.name, e)
            fallback_name = self._other_provider(primary.name)
            fallback = self.providers[fallback_name]
            budget.spend("candidate_fallback")
            resp = await fallback.generate(
                messages, model=self.flagship_models[fallback_name]
            )  # both providers failing fails the request — recorded by run()
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
        self, request_id: str, question: str, budget, seq, deep: bool = False
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
            return await p.generate(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": question},
                ],
                model=self.flagship_models[name],
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
            resp = await provider.generate(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                model=model,
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
        try:
            resp = await provider.generate(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                model=model,
                schema=Synthesis,
            )
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
            return await reviewer.generate(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                model=self.flagship_models[reviewer_name],
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
        try:
            resp = await provider.generate(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": "\n\n".join(parts)},
                ],
                model=model,
                schema=JudgeVerdict,
                max_tokens=8192,
            )
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
            resp = await provider.generate(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                model=model,
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
        try:
            resp = await provider.generate(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                model=model,
                schema=RevisedAnswer,
                max_tokens=8192,
            )
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

    async def _create_request(self, question: str, mode: str) -> str:
        async with session_scope() as s:
            req = Request(question=question, mode=mode, status="routed")
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

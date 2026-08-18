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

from council.db.models import ClaimAssessmentRow, EvidenceItemRow, Request, Step
from council.db.session import session_scope
from council.engine.assessor_guards import blind_claims, sanitize
from council.engine.budget import BudgetTracker
from council.engine.prompts import PromptRegistry
from council.engine.schemas import (
    CombinedCheck,
    Critique,
    EvidenceAssessment,
    EvidencePlan,
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
        evidence_tools: dict | None = None,
        max_web_searches: int = 3,
        max_code_executions: int = 2,
    ):
        self.providers = providers
        self.registry = registry
        self.flagship_models = flagship_models
        self.cheap_models = cheap_models
        self.check_provider = check_provider
        self.judge_provider = judge_provider
        self.quick_mode_strategy = quick_mode_strategy
        self._publish = publish
        self.evidence_tools = evidence_tools or {}
        self.max_web_searches = max_web_searches
        self.max_code_executions = max_code_executions
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

        # --- evidence layer (deep mode) --------------------------------
        # Gathered BEFORE any answer is produced, so its verdicts constrain
        # the answer rather than being audited into it afterwards.
        evidence: EvidenceContext | None = None
        if deep and check.checkable_claims:
            evidence = await self._gather_and_assess_evidence(
                request_id, question, candidates, check, budget, seq
            )

        # R4: on the agreement path, any evidence-contradicted claim disables
        # the synthesis shortcut and escalates to the judge, which can reject
        # both. Synthesis merges candidates; it would launder the error.
        # A claim both candidates asserted is the headline case, but a claim
        # from one candidate that the other did not dispute is contaminating
        # the same shared answer, so both escalate.
        agreement_path = check.agreement in ("agree", "partial")
        if agreement_path and evidence and evidence.contradicted:
            agreement_path = False
            shared = [c for c in evidence.contradicted if c.made_by == "both"]
            await self._record_stage(
                request_id,
                seq(),
                "evidence_override",
                {
                    "reason": (
                        "evidence contradicts a claim BOTH candidates asserted"
                        if shared
                        else "evidence contradicts a claim on the agreement path"
                    ),
                    "contradicted_claims": [
                        {"claim": c.claim, "asserted_by": c.made_by} for c in evidence.contradicted
                    ],
                    "asserted_by_both": [c.claim for c in shared],
                    "escalated_to": "judge",
                },
            )
            await self._set_evidence_override(request_id)

        if agreement_path:
            synthesis = await self._synthesize(
                request_id, question, candidates, check, budget, seq, evidence
            )
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
            # Factual disputes go to evidence, not debate.
            if deep and check.disagreement_type in ("reasoning", "both"):
                critiques = await self._critique_round(
                    request_id, question, candidates, budget, seq
                )

            verdict = await self._judge(
                request_id, question, candidates, check, critiques, budget, seq, evidence
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

            # R1: a factual dispute the evidence could not settle must stay
            # unresolved. Enforced in code — the judge choosing a winner on
            # plausibility is recorded as a violation and forced to revision.
            if (
                evidence is not None
                and check.disagreement_type in ("factual", "both")
                and not evidence.decisive
                and verdict.decision not in ("uncertain", "reject_both")
            ):
                await self._record_stage(
                    request_id,
                    seq(),
                    "evidence_constraint_violation",
                    {
                        "rule": "insufficient evidence on a factual dispute requires uncertainty",
                        "judge_decision": verdict.decision,
                        "forced": "revision",
                    },
                )
                await self._set_evidence_override(request_id)
                result["force_revision_reasons"] = [
                    "The evidence gathered did not settle the disputed factual claim(s): "
                    + "; ".join(c.claim for c in evidence.insufficient)
                    + ". Present both positions honestly, state plainly that the available "
                    "evidence does not resolve it, and say what would settle it. Do not "
                    "choose a side on plausibility."
                ]

        if deep and result.get("final_answer"):
            result = await self._verify_and_maybe_revise(
                request_id, question, result, candidates, critiques, budget, seq,
                producer=producer, evidence=evidence,
            )
        result.pop("force_revision_reasons", None)
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
        self, request_id, question, candidates, check, budget, seq, evidence=None
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
        if evidence is not None:
            user += f"\n\n{evidence.render()}"
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

    # ------------------------------------------------------------- evidence

    async def _gather_and_assess_evidence(
        self, request_id, question, candidates, check, budget, seq
    ) -> "EvidenceContext | None":
        """Plan checks, run the tools, then judge the claims against what came
        back. Tool failures and gaps become INSUFFICIENT verdicts — never
        silent absence — so uncertainty survives to the final answer."""
        plan = await self._plan_evidence(request_id, question, candidates, check, budget, seq)
        if plan is None or not plan.queries:
            # Checkable claims existed but nothing was planned. Record the gap
            # explicitly — silence here would look identical to "verified".
            await self._record_stage(
                request_id,
                seq(),
                "evidence_not_gathered",
                {
                    "checkable_claims": len(check.checkable_claims),
                    "reason": "planning failed" if plan is None else "planner returned no queries",
                    "consequence": "claims remain unverified; uncertainty must be preserved",
                },
            )
            return None

        items = await self._run_evidence_tools(request_id, plan, seq)
        if not items:
            await self._record_stage(
                request_id,
                seq(),
                "evidence_not_gathered",
                {
                    "checkable_claims": len(check.checkable_claims),
                    "reason": "no evidence items returned by any tool",
                    "consequence": "claims remain unverified; uncertainty must be preserved",
                },
            )
            return None
        await self._persist_evidence(request_id, items)

        assessment = await self._assess_evidence(
            request_id, question, check, items, budget, seq
        )
        if assessment is None:
            return None
        await self._persist_assessment(request_id, assessment)
        await self._mark_evidence_used(request_id)
        return EvidenceContext(items=items, assessment=assessment)

    async def _plan_evidence(
        self, request_id, question, candidates, check, budget, seq
    ) -> EvidencePlan | None:
        prompt = self.registry.get("evidence_plan")
        provider = self.providers[self.check_provider]
        model = self.cheap_models[self.check_provider]
        claims = "\n".join(
            f"- ({c.made_by}) {c.claim} — matters because: {c.why_material}"
            for c in check.checkable_claims
        )
        # Deliberately NOT told which tools are up. A planner that knows the
        # web is down returns an empty plan, and the request then falls back
        # to model consensus with no record that verification was impossible
        # — the exact failure mode the evidence layer exists to prevent.
        # Planning blind means a downed tool produces UNAVAILABLE evidence,
        # which becomes INSUFFICIENT verdicts, which preserves uncertainty.
        tools = ", ".join(self.evidence_tools)
        user = (
            f"QUESTION:\n{question}\n\n"
            f"CHECKABLE CLAIMS:\n{claims}\n\n"
            f"CANDIDATE A:\n{candidates['A'].content[:3000]}\n\n"
            f"CANDIDATE B:\n{candidates['B'].content[:3000]}\n\n"
            f"TOOLS: {tools}\n"
            f"LIMITS: at most {self.max_web_searches} web searches and "
            f"{self.max_code_executions} code executions."
        )
        budget.spend("evidence_plan")
        try:
            resp = await self._generate(
                request_id,
                "evidence_plan",
                provider,
                model,
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                schema=EvidencePlan,
            )
        except ProviderError as e:
            await self._record_error(request_id, seq(), "evidence_plan", provider.name, e)
            return None
        await self._record_call(
            request_id, seq(), "evidence_plan", resp, prompt.version_id,
            output=resp.parsed.model_dump(),
        )
        return resp.parsed

    async def _run_evidence_tools(self, request_id, plan: EvidencePlan, seq) -> list:
        """Execute the plan within per-tool caps. Tool calls are not model
        calls and are budgeted separately, but still hard-capped."""
        used = {"web": 0, "code": 0}
        caps = {"web": self.max_web_searches, "code": self.max_code_executions}
        gathered: list = []
        skipped: list[str] = []

        for query in plan.queries:
            tool = self.evidence_tools.get(query.tool)
            if tool is None:
                continue
            if used[query.tool] >= caps[query.tool]:
                skipped.append(f"{query.tool}: {query.query[:80]}")
                continue
            used[query.tool] += 1
            results = await tool.run(query.query)
            gathered.extend(results)
            self._emit(
                request_id,
                {
                    "type": "stage",
                    "stage": f"evidence_{query.tool}",
                    "status": results[0].status if results else "error",
                },
            )

        await self._record_stage(
            request_id,
            seq(),
            "evidence_gathered",
            {
                "planned": len(plan.queries),
                "ran": {k: v for k, v in used.items() if v},
                "items": len(gathered),
                "unavailable": [i.error for i in gathered if i.status == "unavailable"],
                # No silent caps: anything dropped is stated in the trace.
                "skipped_over_cap": skipped,
            },
        )
        return gathered

    async def _assess_evidence(
        self, request_id, question, check, items, budget, seq
    ) -> EvidenceAssessment | None:
        prompt = self.registry.get("evidence_assess")
        provider = self.providers[self.check_provider]
        model = self.cheap_models[self.check_provider]
        bundle = "\n\n".join(item.as_context(i + 1) for i, item in enumerate(items))
        # Blinded: the assessor never learns which model made a claim, or how
        # many did. Consensus must not be able to leak in as a signal.
        claims = "\n".join(f"- {c}" for c in blind_claims(check.checkable_claims))
        user = (
            f"QUESTION:\n{question}\n\n"
            f"CLAIMS TO ASSESS:\n{claims}\n\n"
            f"EVIDENCE BUNDLE:\n{bundle}"
        )
        budget.spend("evidence_assess")
        try:
            resp = await self._generate(
                request_id,
                "evidence_assess",
                provider,
                model,
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": user},
                ],
                schema=EvidenceAssessment,
                max_tokens=8192,
            )
        except ProviderError as e:
            await self._record_error(request_id, seq(), "evidence_assess", provider.name, e)
            return None
        raw: EvidenceAssessment = resp.parsed
        # Mechanical guardrails before anything downstream trusts a verdict.
        assessment, guards = sanitize(raw, items)
        # Re-attach attribution the assessor was blinded to, by claim text.
        attribution = {c.claim: c.made_by for c in check.checkable_claims}
        assessment = assessment.model_copy(
            update={
                "claims": [
                    c.model_copy(update={"made_by": attribution.get(c.claim, "both")})
                    for c in assessment.claims
                ]
            }
        )
        await self._record_call(
            request_id, seq(), "evidence_assess", resp, prompt.version_id,
            output={**assessment.model_dump(), "guards": guards.as_dict()},
        )
        if not guards.clean:
            await self._record_stage(
                request_id, seq(), "assessor_guard_corrections", guards.as_dict()
            )
        return assessment

    async def _persist_evidence(self, request_id, items) -> None:
        async with session_scope() as s:
            for i, item in enumerate(items, start=1):
                s.add(
                    EvidenceItemRow(
                        request_id=request_id,
                        ordinal=i,
                        kind=item.kind,
                        query=item.query,
                        status=item.status,
                        source_url=item.source_url,
                        title=item.title,
                        snippet=item.snippet,
                        error=item.error,
                        latency_ms=item.latency_ms,
                        raw=item.raw or None,
                    )
                )

    async def _persist_assessment(self, request_id, assessment: EvidenceAssessment) -> None:
        async with session_scope() as s:
            for claim in assessment.claims:
                s.add(
                    ClaimAssessmentRow(
                        request_id=request_id,
                        claim=claim.claim,
                        made_by=claim.made_by,
                        verdict=claim.verdict,
                        rationale=claim.rationale,
                        citations=claim.citations or [],
                    )
                )

    async def _mark_evidence_used(self, request_id) -> None:
        async with session_scope() as s:
            req = await s.get(Request, request_id)
            if req is not None:
                req.evidence_used = True

    async def _set_evidence_override(self, request_id) -> None:
        async with session_scope() as s:
            req = await s.get(Request, request_id)
            if req is not None:
                req.evidence_override = True

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
        self, request_id, question, candidates, check, critiques, budget, seq, evidence=None
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
        if evidence is not None:
            parts.append(evidence.render())
            if evidence.contradicted:
                parts.append(
                    "BINDING: the evidence CONTRADICTS the claim(s) listed above. This holds "
                    "even where both candidates asserted them — model agreement is not "
                    "evidence. Your final answer must not assert a contradicted claim; state "
                    "what the evidence shows instead. If both candidates depend on a "
                    "contradicted claim, choose 'reject_both'."
                )
            if not evidence.decisive:
                parts.append(
                    "BINDING: the evidence gathered did NOT settle the disputed claims. You "
                    "must not resolve them by plausibility or confidence. Your decision must "
                    "be 'uncertain' and the final answer must present both positions "
                    "honestly and say what would settle the question."
                )
        elif check.disagreement_type in ("factual", "both"):
            # No evidence could be gathered (nothing checkable, planning
            # failed, or tools unavailable): the judge must not settle a
            # checkable factual dispute by plausibility.
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
        *, producer: str, evidence=None,
    ) -> dict:
        """Deep mode's audit: verifier on the opposite provider from whichever
        provider actually produced the final answer (judge OR synthesis);
        at most one revision; verifier failure degrades, never blocks.

        Evidence has precedence over the verifier's own verdict: a claim the
        evidence contradicted, or an unsettled factual dispute, forces a
        revision in code even if the verifier returned 'pass'."""
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
        )
        if evidence is not None:
            # Evidence first, deliberately: it takes precedence over the
            # candidates' agreement when classifying claims.
            user += f"{evidence.render()}\n\n"
        user += "SOURCE MATERIAL:\n\n" + "\n\n".join(source)
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

        # R2: evidence outranks the verifier. A pass verdict cannot stand over
        # a claim the evidence contradicted, and cannot stand over a judge
        # decision the engine already flagged as violating an evidence
        # constraint. Enforced here in code, not left to the prompt.
        forced_reasons: list[str] = list(result.get("force_revision_reasons") or [])
        if evidence is not None and evidence.contradicted:
            forced_reasons.append(
                "The evidence contradicts: "
                + "; ".join(c.claim for c in evidence.contradicted)
                + ". The final answer must not assert these; state what the evidence shows"
                + (f" — {evidence.assessment.correction}" if evidence.assessment.correction else "")
                + "."
            )
        if forced_reasons and report.verdict == "pass":
            await self._record_stage(
                request_id,
                seq(),
                "evidence_supremacy_override",
                {
                    "rule": "evidence outranks a verifier pass",
                    "verifier_verdict": "pass",
                    "forced_verdict": "revise",
                    "reasons": forced_reasons,
                },
            )
            await self._set_evidence_override(request_id)
            report = report.model_copy(
                update={"verdict": "revise", "reasons": [*report.reasons, *forced_reasons]}
            )
        elif forced_reasons:
            report = report.model_copy(update={"reasons": [*report.reasons, *forced_reasons]})

        if report.verdict == "pass":
            return result

        revised = await self._revise(
            request_id, question, result["final_answer"], report, candidates, budget, seq,
            producer=producer, evidence=evidence,
        )
        if revised is None:
            # Revision failed — ship the audited answer with the flags visible.
            return {**result, "degraded": True}
        return {**result, "final_answer": revised.final_answer}

    async def _revise(
        self, request_id, question, final_answer, report: VerifierReport,
        candidates, budget, seq, *, producer: str, evidence=None,
    ) -> RevisedAnswer | None:
        prompt = self.registry.get("revision")
        provider = self.providers[producer]
        model = self.flagship_models[producer]
        user = (
            f"QUESTION:\n{question}\n\n"
            f"CURRENT FINAL ANSWER:\n{final_answer}\n\n"
            f"VERIFIER REASONS FOR REVISION:\n"
            + "\n".join(f"- {r}" for r in report.reasons)
            + "\n\n"
            + (f"{evidence.render()}\n\n" if evidence is not None else "")
            + "SOURCE MATERIAL:\n\n"
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
            req.final_answer = normalize_answer(final_answer)
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
            ev_rows = (
                await s.execute(
                    select(EvidenceItemRow)
                    .where(EvidenceItemRow.request_id == request_id)
                    .order_by(EvidenceItemRow.ordinal)
                )
            ).scalars().all()
            claim_rows = (
                await s.execute(
                    select(ClaimAssessmentRow).where(
                        ClaimAssessmentRow.request_id == request_id
                    )
                )
            ).scalars().all()
            return {
                "id": req.id,
                "question": req.question,
                "mode": req.mode,
                "status": req.status,
                "final_answer": req.final_answer,
                "degraded": req.degraded,
                "error": req.error,
                "evidence_used": req.evidence_used,
                "evidence_override": req.evidence_override,
                "evidence": [
                    {
                        "ordinal": e.ordinal,
                        "kind": e.kind,
                        "query": e.query,
                        "status": e.status,
                        "source_url": e.source_url,
                        "title": e.title,
                        "snippet": e.snippet,
                        "error": e.error,
                        "latency_ms": e.latency_ms,
                    }
                    for e in ev_rows
                ],
                "claim_assessments": [
                    {
                        "claim": c.claim,
                        "made_by": c.made_by,
                        "verdict": c.verdict,
                        "rationale": c.rationale,
                        "citations": c.citations or [],
                    }
                    for c in claim_rows
                ],
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


_ESCAPES = (("\\r\\n", "\n"), ("\\n", "\n"), ("\\t", "\t"))


def normalize_answer(text: str | None) -> str | None:
    """Undo literal escape sequences that occasionally survive structured
    output (a model emitting "line1\\nline2" as six characters rather than a
    real newline). Seen live in a synthesis result. Only applied when the
    text has no real newlines, so genuine backslashes in code are untouched.
    """
    if not text or "\n" in text:
        return text
    if not any(seq in text for seq, _ in _ESCAPES):
        return text
    for seq, replacement in _ESCAPES:
        text = text.replace(seq, replacement)
    return text


class EvidenceContext:
    """The evidence bundle plus its per-claim verdicts, and the derived facts
    the pipeline enforces in code rather than trusting to prompts."""

    def __init__(self, items: list, assessment: EvidenceAssessment):
        self.items = items
        self.assessment = assessment

    @property
    def contradicted(self) -> list:
        return [c for c in self.assessment.claims if c.verdict == "CONTRADICTED_BY_EVIDENCE"]

    @property
    def supported(self) -> list:
        return [c for c in self.assessment.claims if c.verdict == "SUPPORTED_BY_EVIDENCE"]

    @property
    def insufficient(self) -> list:
        return [c for c in self.assessment.claims if c.verdict == "INSUFFICIENT_EVIDENCE"]

    @property
    def decisive(self) -> bool:
        """True when the evidence actually settled something."""
        return bool(self.contradicted or self.supported)

    def render(self) -> str:
        """Evidence bundle + binding verdicts, as given to answer-producing
        and verifying stages."""
        bundle = "\n\n".join(item.as_context(i + 1) for i, item in enumerate(self.items))
        verdicts = "\n".join(
            f"- [{c.verdict}] {c.claim}"
            + (f"\n    basis: {c.rationale}" if c.rationale else "")
            + (f"\n    cites: {c.citations}" if c.citations else "")
            for c in self.assessment.claims
        )
        parts = [
            "EVIDENCE BUNDLE (retrieved sources and executed code):",
            bundle or "(no items)",
            "",
            "EVIDENCE VERDICTS — these are binding and outrank both candidates:",
            verdicts or "(no claims assessed)",
        ]
        if self.assessment.correction:
            parts += ["", f"WHAT THE EVIDENCE ACTUALLY SHOWS: {self.assessment.correction}"]
        return "\n".join(parts)


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

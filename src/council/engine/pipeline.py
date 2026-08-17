"""The Council Engine — orchestration for the V1 pipeline.

quick:    route -> single candidate -> final
council:  route -> parallel candidates -> combined check ->
          agree/partial: synthesis -> final
          disagree:      disagreement report -> final   (judge lands in M2)

Every stage is persisted to the steps table as it happens. Provider
failures degrade gracefully and are recorded, never hidden.
"""

import asyncio
import random
import time

from sqlalchemy import func, select

from council.db.models import Request, Step
from council.db.session import session_scope
from council.engine.budget import BudgetTracker
from council.engine.prompts import PromptRegistry
from council.engine.schemas import CombinedCheck, Synthesis
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
        quick_mode_strategy: str = "alternate",
    ):
        self.providers = providers
        self.registry = registry
        self.flagship_models = flagship_models
        self.cheap_models = cheap_models
        self.check_provider = check_provider
        self.quick_mode_strategy = quick_mode_strategy

    # ------------------------------------------------------------------ run

    async def run(self, question: str, mode: str) -> dict:
        started = time.monotonic()
        budget = BudgetTracker(mode)

        request_id = await self._create_request(question, mode)
        seq = _Seq()

        try:
            if mode == "quick":
                final = await self._run_quick(request_id, question, budget, seq)
            else:  # council and deep share the V1 path; deep grows in M2/M4
                final = await self._run_council(request_id, question, budget, seq)
        except Exception as e:
            await self._finish(request_id, status="failed", error=str(e), started=started)
            raise

        await self._finish(request_id, started=started, **final)
        return await self.get_request(request_id)

    # ---------------------------------------------------------------- quick

    async def _run_quick(self, request_id: str, question: str, budget, seq) -> dict:
        provider = await self._pick_quick_provider()
        model = self.flagship_models[provider.name]
        prompt = self.registry.get("candidate")

        budget.spend("candidate")
        resp = await provider.generate(
            [
                {"role": "system", "content": prompt.text},
                {"role": "user", "content": question},
            ],
            model=model,
        )
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

    async def _run_council(self, request_id: str, question: str, budget, seq) -> dict:
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

        if check.agreement in ("agree", "partial"):
            synthesis = await self._synthesize(request_id, question, candidates, check, budget, seq)
            if synthesis is not None:
                return {"status": "complete", "final_answer": synthesis.final_answer}
            # Synthesis failed — candidates agreed, so candidate A is a safe answer.
            return {
                "status": "complete",
                "final_answer": candidates["A"].content,
                "degraded": True,
            }

        report = _disagreement_report(candidates, check)
        await self._record_stage(
            request_id, seq(), "disagreement_report", check.model_dump()
        )
        return {"status": "complete", "final_answer": report}

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
                )
            )
            req = await s.get(Request, request_id)
            req.total_input_tokens += resp.input_tokens
            req.total_output_tokens += resp.output_tokens
            req.total_cost_usd += resp.cost_usd
            req.model_calls += 1

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

    async def _record_stage(self, request_id, seq, stage, output: dict | None):
        async with session_scope() as s:
            s.add(Step(request_id=request_id, seq=seq, stage=stage, output=output, status="ok"))

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

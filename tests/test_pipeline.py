from council.engine.schemas import CombinedCheck, Synthesis
from council.providers.base import ProviderError
from tests.fakes import FakeProvider

AGREE_CHECK = CombinedCheck(
    agreement="agree",
    disagreement_type="none",
    summary="Both candidates say the same thing.",
)
DISAGREE_CHECK = CombinedCheck(
    agreement="disagree",
    disagreement_type="factual",
    key_disagreements=["A says 5432, B says 5433"],
    summary="They conflict on the port number.",
)
SYNTH = Synthesis(final_answer="The synthesized answer.")


async def test_quick_mode_single_call(make_engine):
    openai = FakeProvider("openai", ["GPT answer"])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, quick_mode_strategy="openai")

    result = await engine.run("q?", "quick")

    assert result["status"] == "complete"
    assert result["final_answer"] == "GPT answer"
    assert result["totals"]["model_calls"] == 1
    assert len(anthropic.calls) == 0
    assert result["totals"]["cost_usd"] == 0  # unknown fake model priced at 0


async def test_quick_alternates_between_providers(make_engine):
    openai = FakeProvider("openai", ["GPT answer", "GPT answer"])
    anthropic = FakeProvider("anthropic", ["Claude answer", "Claude answer"])
    engine = make_engine(openai, anthropic, quick_mode_strategy="alternate")

    await engine.run("q1?", "quick")
    await engine.run("q2?", "quick")

    assert len(openai.calls) == 1
    assert len(anthropic.calls) == 1


async def test_council_agreement_synthesizes(make_engine):
    openai = FakeProvider("openai", ["GPT answer", AGREE_CHECK, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")

    assert result["status"] == "complete"
    assert result["final_answer"] == "The synthesized answer."
    assert not result["degraded"]
    stages = [s["stage"] for s in result["steps"]]
    assert "combined_check" in stages and "synthesis" in stages
    assert {"candidate_a", "candidate_b"} <= set(stages)
    assert result["totals"]["model_calls"] == 4


async def test_council_disagreement_reports_both(make_engine):
    openai = FakeProvider("openai", ["Port is 5432", DISAGREE_CHECK])
    anthropic = FakeProvider("anthropic", ["Port is 5433"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")

    assert result["status"] == "complete"
    fa = result["final_answer"]
    assert "Candidate A" in fa and "Candidate B" in fa
    assert "conflict on the port number" in fa
    stages = [s["stage"] for s in result["steps"]]
    assert "disagreement_report" in stages
    assert "synthesis" not in stages  # no wasted call on disagreement


async def test_council_degrades_when_one_provider_fails(make_engine):
    openai = FakeProvider("openai", [ProviderError("boom")])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")

    assert result["status"] == "complete"
    assert result["final_answer"] == "Claude answer"
    assert result["degraded"] is True
    error_steps = [s for s in result["steps"] if s["status"] == "error"]
    assert len(error_steps) == 1
    assert "boom" in error_steps[0]["error"]


async def test_council_fails_when_both_providers_fail(make_engine):
    import pytest

    openai = FakeProvider("openai", [ProviderError("down")])
    anthropic = FakeProvider("anthropic", [ProviderError("also down")])
    engine = make_engine(openai, anthropic)

    with pytest.raises(ProviderError):
        await engine.run("q?", "council")

    # The failed request is still recorded honestly.
    async with __import__("council.db.session", fromlist=["session_scope"]).session_scope() as s:
        from sqlalchemy import select

        from council.db.models import Request

        req = (await s.execute(select(Request))).scalars().first()
        assert req.status == "failed"
        assert req.error


async def test_check_failure_falls_back_to_report(make_engine):
    openai = FakeProvider("openai", ["GPT answer", ProviderError("check died")])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")

    assert result["status"] == "complete"
    assert result["degraded"] is True
    assert "Candidate A" in result["final_answer"]


async def test_trace_records_prompt_versions_and_costs(make_engine):
    openai = FakeProvider("openai", ["GPT answer", AGREE_CHECK, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")

    call_steps = [s for s in result["steps"] if s["provider"]]
    assert all(s["prompt_version"] for s in call_steps)
    assert all(s["tokens"]["input"] > 0 for s in call_steps)
    assert result["totals"]["input_tokens"] == sum(s["tokens"]["input"] for s in call_steps)

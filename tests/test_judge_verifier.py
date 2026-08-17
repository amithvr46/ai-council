"""M2: blinded judge, deep-mode critique round, verifier and single revision."""

from council.engine.schemas import (
    CombinedCheck,
    Critique,
    CritiqueIssue,
    DimensionVerdict,
    JudgeVerdict,
    RevisedAnswer,
    VerifierReport,
)
from council.providers.base import ProviderError
from tests.fakes import FakeProvider

REASONING_DISAGREE = CombinedCheck(
    agreement="disagree",
    disagreement_type="reasoning",
    key_disagreements=["A prefers monolith, B prefers microservices"],
    summary="They disagree on architecture approach.",
)
FACTUAL_DISAGREE = CombinedCheck(
    agreement="disagree",
    disagreement_type="factual",
    key_disagreements=["Port number conflict"],
    summary="They conflict on a checkable fact.",
)
AGREE = CombinedCheck(agreement="agree", disagreement_type="none", summary="Same conclusion.")

CRITIQUE = Critique(
    issues=[
        CritiqueIssue(kind="weak_reasoning", severity="minor", detail="Ignores team size.")
    ],
    overall="Mostly sound with one gap.",
)
CLEAN_CRITIQUE = Critique(issues=[], overall="No material issues found.")

VERDICT = JudgeVerdict(
    dimensions=[
        DimensionVerdict(dimension="accuracy", winner="A", reason="A's claims hold up"),
        DimensionVerdict(dimension="risk", winner="A", reason="B overpromises"),
    ],
    decision="choose_a",
    confidence="medium",
    rationale="A is right for this context.",
    final_answer="The judged final answer.",
)

PASS_REPORT = VerifierReport(claims=[], verdict="pass", reasons=[])
REVISE_REPORT = VerifierReport(
    claims=[],
    verdict="revise",
    reasons=["Remove the unsupported version number."],
)
REVISED = RevisedAnswer(final_answer="The revised final answer.", changes=["Removed version."])

from council.engine.schemas import Synthesis  # noqa: E402

SYNTH = Synthesis(final_answer="The synthesized answer.")


async def test_council_disagreement_goes_to_judge(make_engine):
    openai = FakeProvider("openai", ["GPT answer", REASONING_DISAGREE])
    anthropic = FakeProvider("anthropic", ["Claude answer", VERDICT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "council")

    assert result["final_answer"] == "The judged final answer."
    stages = [s["stage"] for s in result["steps"]]
    assert "judge" in stages
    assert "critique_of_a" not in stages  # council never critiques
    assert "verifier" not in stages  # council never verifies
    assert result["totals"]["model_calls"] == 4
    judge_step = next(s for s in result["steps"] if s["stage"] == "judge")
    assert judge_step["output"]["decision"] == "choose_a"
    assert judge_step["provider"] == "anthropic"


async def test_deep_reasoning_disagreement_full_path(make_engine):
    openai = FakeProvider("openai", ["GPT answer", REASONING_DISAGREE, CRITIQUE, PASS_REPORT])
    anthropic = FakeProvider("anthropic", ["Claude answer", CLEAN_CRITIQUE, VERDICT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "deep")

    assert result["final_answer"] == "The judged final answer."
    assert not result["degraded"]
    stages = [s["stage"] for s in result["steps"]]
    assert {"critique_of_a", "critique_of_b", "judge", "verifier"} <= set(stages)
    assert result["totals"]["model_calls"] == 7  # 2+1+2+1+1, within deep budget of 9
    verifier_step = next(s for s in result["steps"] if s["stage"] == "verifier")
    assert verifier_step["provider"] == "openai"  # opposite of judge


async def test_deep_factual_disagreement_skips_critique(make_engine):
    openai = FakeProvider("openai", ["GPT answer", FACTUAL_DISAGREE, PASS_REPORT])
    anthropic = FakeProvider("anthropic", ["Claude answer", VERDICT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "deep")

    stages = [s["stage"] for s in result["steps"]]
    assert "critique_of_a" not in stages  # facts wait for evidence, not debate
    assert "judge" in stages and "verifier" in stages
    assert result["totals"]["model_calls"] == 5


async def test_deep_verifier_revise_triggers_single_revision(make_engine):
    openai = FakeProvider("openai", ["GPT answer", REASONING_DISAGREE, CRITIQUE, REVISE_REPORT])
    anthropic = FakeProvider("anthropic", ["Claude answer", CLEAN_CRITIQUE, VERDICT, REVISED])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "deep")

    assert result["final_answer"] == "The revised final answer."
    stages = [s["stage"] for s in result["steps"]]
    assert stages.count("revision") == 1
    assert result["totals"]["model_calls"] == 8  # the deep maximum, ≤ 9


async def test_deep_agreement_still_verified(make_engine):
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer", PASS_REPORT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "deep")

    assert result["final_answer"] == "The synthesized answer."
    stages = [s["stage"] for s in result["steps"]]
    assert "verifier" in stages  # deep verifies even on agreement
    assert "judge" not in stages


async def test_deep_agreement_verifier_is_independent_of_synthesizer(make_engine):
    """GPT M2 review #1: on the agreement path the answer is produced by the
    check provider's synthesis — the verifier must be the OTHER provider,
    even though no judge ran."""
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer", PASS_REPORT])
    # check_provider=openai AND judge_provider=anthropic: the old bug picked
    # opposite-of-judge (= openai), same provider that synthesized.
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "deep")

    synthesis_step = next(s for s in result["steps"] if s["stage"] == "synthesis")
    verifier_step = next(s for s in result["steps"] if s["stage"] == "verifier")
    assert synthesis_step["provider"] == "openai"
    assert verifier_step["provider"] == "anthropic"
    assert verifier_step["provider"] != synthesis_step["provider"]


async def test_judge_told_no_evidence_on_factual_dispute(make_engine):
    """GPT M2 review #2: until evidence tools exist, the judge must be
    explicitly told it cannot settle factual disputes by plausibility."""
    openai = FakeProvider("openai", ["GPT answer", FACTUAL_DISAGREE, PASS_REPORT])
    anthropic = FakeProvider("anthropic", ["Claude answer", VERDICT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    await engine.run("q?", "deep")

    judge_call = next(c for c in anthropic.calls
                      if c["schema"] is not None and c["schema"].__name__ == "JudgeVerdict")
    user_content = "\n".join(m["content"] for m in judge_call["messages"])
    assert "NO EXTERNAL EVIDENCE" in user_content
    assert "uncertain" in user_content


async def test_judge_not_warned_on_reasoning_dispute(make_engine):
    openai = FakeProvider("openai", ["GPT answer", REASONING_DISAGREE])
    anthropic = FakeProvider("anthropic", ["Claude answer", VERDICT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    await engine.run("q?", "council")

    judge_call = next(c for c in anthropic.calls
                      if c["schema"] is not None and c["schema"].__name__ == "JudgeVerdict")
    user_content = "\n".join(m["content"] for m in judge_call["messages"])
    assert "NO EXTERNAL EVIDENCE" not in user_content


async def test_failed_critique_records_real_provider(make_engine):
    """GPT M2 review #3: a failed critique must be attributed to the actual
    reviewer provider, not 'unknown'."""
    openai = FakeProvider(
        "openai", ["GPT answer", REASONING_DISAGREE, ProviderError("boom"), PASS_REPORT]
    )
    anthropic = FakeProvider("anthropic", ["Claude answer", CLEAN_CRITIQUE, VERDICT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "deep")

    error_steps = [
        s for s in result["steps"]
        if s["status"] == "error" and s["stage"].startswith("critique_of_")
    ]
    assert len(error_steps) == 1
    assert error_steps[0]["provider"] == "openai"


async def test_judge_failure_falls_back_to_report(make_engine):
    openai = FakeProvider("openai", ["GPT answer", REASONING_DISAGREE])
    anthropic = FakeProvider("anthropic", ["Claude answer", ProviderError("judge died")])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "council")

    assert result["degraded"] is True
    assert "Candidate A" in result["final_answer"]  # both answers still delivered


async def test_verifier_failure_degrades_not_blocks(make_engine):
    openai = FakeProvider(
        "openai", ["GPT answer", REASONING_DISAGREE, CRITIQUE, ProviderError("x")]
    )
    anthropic = FakeProvider("anthropic", ["Claude answer", CLEAN_CRITIQUE, VERDICT])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")

    result = await engine.run("q?", "deep")

    assert result["final_answer"] == "The judged final answer."
    assert result["degraded"] is True  # unaudited answer is flagged, not hidden

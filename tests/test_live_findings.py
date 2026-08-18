"""Regression tests for defects found by the live V1 verification run.

Each test here corresponds to a failure observed against real APIs during
the five-scenario live check, reproduced deterministically with fakes.
"""

from council.engine.pipeline import normalize_answer
from council.engine.schemas import (
    CheckableClaim,
    ClaimVerdict,
    CombinedCheck,
    EvidenceAssessment,
    EvidencePlan,
    EvidenceQuery,
    JudgeVerdict,
    Synthesis,
    VerifierReport,
)
from council.engine.schemas import DimensionVerdict as DV
from council.evidence.base import EvidenceItem
from council.providers.base import validate_payload
from tests.fakes import FakeProvider
from tests.test_evidence_supremacy import FakeEvidenceTool

# --- Finding 1: whole payload JSON-encoded into one field --------------------
# Observed: the verifier stage failed on BOTH attempts (MalformedOutput ->
# degraded requests) because the model emitted
#   {"claims": "{\"claims\": [...], \"verdict\": \"pass\"}"}


def test_payload_stringified_into_one_field_is_recovered():
    import json

    real = {
        "claims": [{"claim": "x", "classification": "SUPPORTED", "note": "n"}],
        "verdict": "pass",
        "reasons": [],
    }
    envelope = {"claims": json.dumps(real)}
    parsed = validate_payload(envelope, VerifierReport)
    assert parsed is not None
    assert parsed.verdict == "pass"
    assert parsed.claims[0].claim == "x"


def test_partially_populated_string_envelope_is_recovered():
    """Observed variant: the envelope also carried a sibling verdict key."""
    import json

    real = {"claims": [], "verdict": "revise", "reasons": ["fix it"]}
    envelope = {"claims": json.dumps(real), "verdict": "revise"}
    parsed = validate_payload(envelope, VerifierReport)
    assert parsed is not None
    assert parsed.reasons == ["fix it"]


def test_single_key_dict_envelope_still_recovered():
    envelope = {"parameters": {"claims": [], "verdict": "pass", "reasons": []}}
    assert validate_payload(envelope, VerifierReport).verdict == "pass"


def test_direct_payload_unaffected():
    assert validate_payload({"claims": [], "verdict": "pass", "reasons": []},
                            VerifierReport).verdict == "pass"


def test_genuinely_malformed_payload_still_rejected():
    assert validate_payload({"nonsense": 1}, VerifierReport) is None
    assert validate_payload({"claims": "not json at all"}, VerifierReport) is None


# --- Finding 2: unavailable tool led to silent consensus fallback ------------
# Observed: with web search disabled, the planner (which was told the tool
# was down) returned an empty plan, so no evidence existed, nothing was
# recorded, and the models' agreed answer shipped as if verified.


async def test_planner_is_not_told_which_tools_are_available(make_engine):
    check = CombinedCheck(
        agreement="agree", disagreement_type="none",
        checkable_claims=[CheckableClaim(claim="default is 30s", made_by="both",
                                         why_material="core")],
        summary="agree",
    )
    plan = EvidencePlan(queries=[EvidenceQuery(tool="web", query="q", targets_claim="c")])
    assessment = EvidenceAssessment(
        claims=[ClaimVerdict(claim="default is 30s", made_by="both",
                             verdict="INSUFFICIENT_EVIDENCE",
                             rationale="tool unavailable", citations=[])]
    )
    openai = FakeProvider("openai", ["A", check, plan, assessment,
                                     Synthesis(final_answer="30 seconds.")])
    anthropic = FakeProvider("anthropic", ["B", VerifierReport(claims=[], verdict="pass",
                                                              reasons=[])])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")
    down = FakeEvidenceTool(
        "web",
        [EvidenceItem(kind="web", query="q", status="unavailable", error="no API key")],
        available=False,
    )
    engine.evidence_tools = {"web": down}

    result = await engine.run("q?", "deep")

    plan_call = next(
        c for c in openai.calls
        if c["schema"] is not None and c["schema"].__name__ == "EvidencePlan"
    )
    text = "\n".join(m["content"] for m in plan_call["messages"])
    assert "UNAVAILABLE" not in text  # planner must plan blind...
    assert down.queries  # ...so the downed tool is actually invoked...
    # ...and its failure becomes recorded evidence, not silence.
    assert result["evidence"][0]["status"] == "unavailable"
    assert result["claim_assessments"][0]["verdict"] == "INSUFFICIENT_EVIDENCE"


async def test_empty_plan_with_checkable_claims_is_recorded(make_engine):
    """If nothing gets planned despite checkable claims existing, the gap is
    explicit in the trace — silence would read as 'verified'."""
    check = CombinedCheck(
        agreement="agree", disagreement_type="none",
        checkable_claims=[CheckableClaim(claim="c", made_by="both", why_material="core")],
        summary="agree",
    )
    openai = FakeProvider("openai", ["A", check, EvidencePlan(queries=[]),
                                     Synthesis(final_answer="Answer.")])
    anthropic = FakeProvider("anthropic", ["B", VerifierReport(claims=[], verdict="pass",
                                                              reasons=[])])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")
    engine.evidence_tools = {"web": FakeEvidenceTool("web", [])}

    result = await engine.run("q?", "deep")

    gap = next(s for s in result["steps"] if s["stage"] == "evidence_not_gathered")
    assert gap["output"]["checkable_claims"] == 1
    assert "uncertainty must be preserved" in gap["output"]["consequence"]
    assert result["evidence_used"] is False


# --- Finding 3: R4 override reason claimed "both" inaccurately ---------------
# Observed: the override step said "evidence contradicts a claim both
# candidates asserted" for a claim attributed to candidate A alone.


async def test_override_reason_distinguishes_shared_from_single_claims(make_engine):
    check = CombinedCheck(
        agreement="partial", disagreement_type="factual",
        key_disagreements=["endpoint handling"],
        checkable_claims=[CheckableClaim(claim="endpoint retained", made_by="A",
                                         why_material="core")],
        summary="mostly agree",
    )
    plan = EvidencePlan(queries=[EvidenceQuery(tool="web", query="q", targets_claim="c")])
    assessment = EvidenceAssessment(
        claims=[ClaimVerdict(claim="endpoint retained", made_by="A",
                             verdict="CONTRADICTED_BY_EVIDENCE",
                             rationale="docs say removed", citations=[1])]
    )
    verdict = JudgeVerdict(dimensions=[DV(dimension="accuracy", winner="B", reason="r")],
                           decision="choose_b", confidence="high", rationale="r",
                           final_answer="Removed from EndpointSlice.")
    openai = FakeProvider("openai", ["A", check, plan, assessment,
                                     VerifierReport(claims=[], verdict="pass", reasons=[])])
    anthropic = FakeProvider("anthropic", ["B", verdict,
                                           __import__("council.engine.schemas",
                                                      fromlist=["RevisedAnswer"]).RevisedAnswer(
                                               final_answer="Fixed.", changes=["c"])])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")
    engine.evidence_tools = {"web": FakeEvidenceTool(
        "web", [EvidenceItem(kind="web", query="q", status="ok", snippet="removed")])}

    result = await engine.run("q?", "deep")

    override = next(s for s in result["steps"] if s["stage"] == "evidence_override")
    assert override["output"]["asserted_by_both"] == []  # honest: not a shared claim
    assert "BOTH candidates asserted" not in override["output"]["reason"]
    assert override["output"]["contradicted_claims"][0]["asserted_by"] == "A"


# --- Finding 4: literal escape sequences in a final answer -------------------
# Observed: a synthesis result shipped containing the six characters
# "\\n\\n" instead of real paragraph breaks.


def test_literal_escape_sequences_are_normalized():
    raw = "First paragraph.\\n\\nSecond paragraph.\\tTabbed."
    out = normalize_answer(raw)
    assert out == "First paragraph.\n\nSecond paragraph.\tTabbed."


def test_answers_with_real_newlines_are_left_alone():
    """Code blocks legitimately contain backslash-n; never touch an answer
    that already has real line breaks."""
    raw = 'Use this:\n\n```python\nprint("a\\nb")\n```'
    assert normalize_answer(raw) == raw


def test_normalize_handles_none_and_empty():
    assert normalize_answer(None) is None
    assert normalize_answer("") == ""


async def test_final_answer_is_normalized_end_to_end(make_engine):
    check = CombinedCheck(agreement="agree", disagreement_type="none", summary="same")
    openai = FakeProvider("openai", ["A", check,
                                     Synthesis(final_answer="Line one.\\n\\nLine two.")])
    anthropic = FakeProvider("anthropic", ["B"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")

    assert result["final_answer"] == "Line one.\n\nLine two."

"""Adversarial tests for the evidence-assessor guardrails.

The assessor is an LLM and can misread a source — an accepted V1 tradeoff.
These tests cover the failure mode that is NOT acceptable: a decisive
verdict resting on nothing checkable being enforced mechanically downstream.
Every assertion here is deterministic (no API calls).
"""

from council.engine.assessor_guards import blind_claims, sanitize
from council.engine.schemas import (
    CheckableClaim,
    ClaimVerdict,
    CombinedCheck,
    EvidenceAssessment,
    EvidencePlan,
    EvidenceQuery,
    JudgeVerdict,
    RevisedAnswer,
    VerifierReport,
)
from council.engine.schemas import DimensionVerdict as DV
from council.evidence.base import EvidenceItem
from tests.fakes import FakeProvider
from tests.test_evidence_supremacy import FakeEvidenceTool

OK1 = EvidenceItem(kind="web", query="q", status="ok", snippet="Docs say X is false.",
                   source_url="https://example.com/docs")
OK2 = EvidenceItem(kind="code", query="print(1)", status="ok", snippet="exit_code: 0\nstdout:\n1")
FAILED = EvidenceItem(kind="web", query="q", status="error", error="network down")
UNAVAILABLE = EvidenceItem(kind="web", query="q", status="unavailable", error="no API key")


def _a(*claims) -> EvidenceAssessment:
    return EvidenceAssessment(claims=list(claims), correction="the evidence says otherwise")


def _c(verdict: str, citations: list[int], claim: str = "some claim") -> ClaimVerdict:
    return ClaimVerdict(claim=claim, made_by="both", verdict=verdict,
                        rationale="because", citations=citations)


# --- G1: phantom citations ---------------------------------------------------


def test_phantom_citation_is_stripped_and_recorded():
    """The assessor cites [E7] when only 2 items exist."""
    out, guards = sanitize(_a(_c("SUPPORTED_BY_EVIDENCE", [1, 7])), [OK1, OK2])
    assert out.claims[0].citations == [1]
    assert guards.dropped_citations[0]["phantom_ordinals"] == [7]


def test_verdict_citing_only_phantom_evidence_is_downgraded():
    """Cited [E9], which does not exist — nothing real is behind the verdict."""
    out, guards = sanitize(_a(_c("CONTRADICTED_BY_EVIDENCE", [9])), [OK1])
    assert out.claims[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert out.claims[0].citations == []
    assert guards.downgrades[0]["from"] == "CONTRADICTED_BY_EVIDENCE"


def test_zero_ordinal_and_negative_ordinal_rejected():
    out, guards = sanitize(_a(_c("SUPPORTED_BY_EVIDENCE", [0, -1, 1])), [OK1])
    assert out.claims[0].citations == [1]
    assert set(guards.dropped_citations[0]["phantom_ordinals"]) == {0, -1}


# --- G2: citations pointing at failed/unavailable evidence -------------------


def test_verdict_citing_only_failed_evidence_is_downgraded():
    """A verdict cannot rest on a search that errored."""
    out, guards = sanitize(_a(_c("SUPPORTED_BY_EVIDENCE", [1])), [FAILED])
    assert out.claims[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert "unavailable or errored" in guards.dropped_citations[0]["reason"]


def test_verdict_citing_only_unavailable_evidence_is_downgraded():
    """Web search with no API key cannot support a conclusion."""
    out, _ = sanitize(_a(_c("CONTRADICTED_BY_EVIDENCE", [1])), [UNAVAILABLE])
    assert out.claims[0].verdict == "INSUFFICIENT_EVIDENCE"


def test_mixed_citations_keep_only_usable_ones():
    out, _ = sanitize(_a(_c("SUPPORTED_BY_EVIDENCE", [1, 2])), [OK1, FAILED])
    assert out.claims[0].verdict == "SUPPORTED_BY_EVIDENCE"  # one real source survives
    assert out.claims[0].citations == [1]


# --- G3: decisive verdicts must cite something -------------------------------


def test_uncited_decisive_verdict_is_downgraded():
    """'Trust me' is not evidence."""
    out, guards = sanitize(_a(_c("CONTRADICTED_BY_EVIDENCE", [])), [OK1])
    assert out.claims[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert "no evidence at all" in guards.downgrades[0]["reason"]


def test_insufficient_verdict_needs_no_citation():
    plain = EvidenceAssessment(claims=[_c("INSUFFICIENT_EVIDENCE", [])], correction="")
    out, guards = sanitize(plain, [OK1])
    assert out.claims[0].verdict == "INSUFFICIENT_EVIDENCE"
    assert guards.clean


def test_correction_dropped_when_no_decisive_verdict_survives():
    """Correction text steers the final answer — it cannot outlive its basis."""
    out, guards = sanitize(_a(_c("CONTRADICTED_BY_EVIDENCE", [9])), [OK1])
    assert out.correction == ""
    assert any(d["claim"] == "(correction text)" for d in guards.downgrades)


def test_correction_survives_when_a_decisive_verdict_does():
    out, _ = sanitize(_a(_c("CONTRADICTED_BY_EVIDENCE", [1])), [OK1])
    assert out.correction == "the evidence says otherwise"


def test_clean_assessment_passes_through_untouched():
    original = _a(_c("SUPPORTED_BY_EVIDENCE", [1], "claim one"),
                  _c("INSUFFICIENT_EVIDENCE", [], "claim two"))
    out, guards = sanitize(original, [OK1, OK2])
    assert guards.clean
    assert [c.verdict for c in out.claims] == ["SUPPORTED_BY_EVIDENCE", "INSUFFICIENT_EVIDENCE"]


def test_empty_bundle_downgrades_every_decisive_verdict():
    out, _ = sanitize(_a(_c("SUPPORTED_BY_EVIDENCE", [1]), _c("CONTRADICTED_BY_EVIDENCE", [2])), [])
    assert all(c.verdict == "INSUFFICIENT_EVIDENCE" for c in out.claims)


# --- G4: consensus blinding --------------------------------------------------


def test_claims_are_blinded_before_reaching_the_assessor():
    claims = [
        CheckableClaim(claim="X is true", made_by="both", why_material="core"),
        CheckableClaim(claim="Y is false", made_by="A", why_material="core"),
    ]
    rendered = blind_claims(claims)
    assert rendered == ["X is true", "Y is false"]
    joined = " ".join(rendered)
    assert "both" not in joined and "made_by" not in joined


async def test_assessor_prompt_contains_no_attribution(make_engine):
    """End-to-end: the text actually sent to the assessor must not reveal
    that both models asserted a claim."""
    check = CombinedCheck(
        agreement="agree", disagreement_type="none",
        checkable_claims=[CheckableClaim(claim="Secrets are encrypted by default",
                                         made_by="both", why_material="core")],
        summary="both agree",
    )
    plan = EvidencePlan(queries=[EvidenceQuery(tool="web", query="q", targets_claim="c")])
    assessment = EvidenceAssessment(
        claims=[ClaimVerdict(claim="Secrets are encrypted by default", made_by="both",
                             verdict="CONTRADICTED_BY_EVIDENCE", rationale="docs", citations=[1])],
        correction="not by default",
    )
    verdict = JudgeVerdict(dimensions=[DV(dimension="accuracy", winner="tie", reason="r")],
                           decision="reject_both", confidence="high", rationale="r",
                           final_answer="Not by default.")
    openai = FakeProvider("openai", ["A", check, plan, assessment,
                                     VerifierReport(claims=[], verdict="pass", reasons=[])])
    anthropic = FakeProvider("anthropic", ["B", verdict,
                                           RevisedAnswer(final_answer="Fixed.", changes=["x"])])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")
    engine.evidence_tools = {"web": FakeEvidenceTool("web", [OK1])}

    result = await engine.run("q?", "deep")

    assess_call = next(
        c for c in openai.calls
        if c["schema"] is not None and c["schema"].__name__ == "EvidenceAssessment"
    )
    text = "\n".join(m["content"] for m in assess_call["messages"])
    assert "Secrets are encrypted by default" in text  # the claim is there
    assert "(both)" not in text  # but not who said it
    assert "CANDIDATE A" not in text and "CANDIDATE B" not in text  # nor the answers
    # Attribution is restored for persistence/UI after the blinded call.
    assert result["claim_assessments"][0]["made_by"] == "both"


async def test_assessor_never_sees_candidate_answers(make_engine):
    """Consensus cannot leak via the candidate text either."""
    check = CombinedCheck(
        agreement="disagree", disagreement_type="factual",
        key_disagreements=["port"],
        checkable_claims=[CheckableClaim(claim="Port is 5432", made_by="A", why_material="core")],
        summary="conflict",
    )
    plan = EvidencePlan(queries=[EvidenceQuery(tool="web", query="q", targets_claim="c")])
    assessment = EvidenceAssessment(
        claims=[ClaimVerdict(claim="Port is 5432", made_by="A",
                             verdict="SUPPORTED_BY_EVIDENCE", rationale="docs", citations=[1])],
    )
    verdict = JudgeVerdict(dimensions=[DV(dimension="accuracy", winner="A", reason="r")],
                           decision="choose_a", confidence="high", rationale="r",
                           final_answer="5432.")
    openai = FakeProvider("openai", ["UNIQUE_CANDIDATE_A_TEXT", check, plan, assessment,
                                     VerifierReport(claims=[], verdict="pass", reasons=[])])
    anthropic = FakeProvider("anthropic", ["UNIQUE_CANDIDATE_B_TEXT", verdict])
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")
    engine.evidence_tools = {"web": FakeEvidenceTool("web", [OK1])}

    await engine.run("q?", "deep")

    assess_call = next(
        c for c in openai.calls
        if c["schema"] is not None and c["schema"].__name__ == "EvidenceAssessment"
    )
    text = "\n".join(m["content"] for m in assess_call["messages"])
    assert "UNIQUE_CANDIDATE_A_TEXT" not in text
    assert "UNIQUE_CANDIDATE_B_TEXT" not in text


# --- guards integrated with supremacy enforcement ----------------------------


async def test_guard_downgrade_blocks_the_evidence_override(make_engine):
    """A CONTRADICTED verdict citing a failed search must NOT trigger R4
    escalation — the guard downgrades it first, so consensus stands and
    uncertainty is preserved instead of a fabricated override."""
    check = CombinedCheck(
        agreement="agree", disagreement_type="none",
        checkable_claims=[CheckableClaim(claim="X is true", made_by="both", why_material="core")],
        summary="both agree",
    )
    plan = EvidencePlan(queries=[EvidenceQuery(tool="web", query="q", targets_claim="c")])
    bogus = EvidenceAssessment(
        claims=[ClaimVerdict(claim="X is true", made_by="both",
                             verdict="CONTRADICTED_BY_EVIDENCE",
                             rationale="I just know", citations=[1])],
        correction="X is actually false",
    )
    from council.engine.schemas import Synthesis

    openai = FakeProvider("openai", ["A", check, plan, bogus, Synthesis(final_answer="Synth.")])
    # Synthesis is produced by openai, so the verifier runs on anthropic.
    anthropic = FakeProvider(
        "anthropic", ["B", VerifierReport(claims=[], verdict="pass", reasons=[])]
    )
    engine = make_engine(openai, anthropic, check_provider="openai", judge_provider="anthropic")
    # The only evidence item FAILED, so the CONTRADICTED verdict is baseless.
    engine.evidence_tools = {"web": FakeEvidenceTool("web", [FAILED])}

    result = await engine.run("q?", "deep")

    stages = [s["stage"] for s in result["steps"]]
    assert "assessor_guard_corrections" in stages
    assert "evidence_override" not in stages  # R4 correctly did NOT fire
    assert "synthesis" in stages  # agreement path preserved
    assert result["claim_assessments"][0]["verdict"] == "INSUFFICIENT_EVIDENCE"
    corrections = next(s for s in result["steps"] if s["stage"] == "assessor_guard_corrections")
    assert corrections["output"]["downgrades"]

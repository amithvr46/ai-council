"""M4 acceptance: evidence must genuinely outrank model consensus.

The four required cases:
  1. Both models agree, evidence contradicts both  -> evidence wins.
  2. Models disagree, evidence supports one        -> answer follows evidence.
  3. Evidence insufficient/conflicting             -> uncertainty preserved.
  4. Verifier judges claims against the evidence bundle, not agreement.

These are enforced in engine code (not only prompts), so the assertions
target persisted state and forced control flow rather than model wording.
"""

from council.engine.pipeline import CouncilEngine
from council.engine.schemas import (
    CheckableClaim,
    ClaimVerdict,
    CombinedCheck,
    DimensionVerdict,
    EvidenceAssessment,
    EvidencePlan,
    EvidenceQuery,
    JudgeVerdict,
    RevisedAnswer,
    VerifierReport,
)
from council.evidence.base import EvidenceItem, EvidenceTool
from tests.fakes import FakeProvider


class FakeEvidenceTool(EvidenceTool):
    def __init__(self, name: str, items: list[EvidenceItem], available: bool = True):
        self.name = name
        self.available = available
        self._items = items
        self.queries: list[str] = []

    async def run(self, query: str):
        self.queries.append(query)
        return [
            EvidenceItem(
                kind=self.name, query=query, status=i.status, snippet=i.snippet,
                source_url=i.source_url, title=i.title, error=i.error,
            )
            for i in self._items
        ]


def _claim(text: str, made_by: str = "both") -> CheckableClaim:
    return CheckableClaim(claim=text, made_by=made_by, why_material="core to the answer")


AGREE_WITH_CLAIM = CombinedCheck(
    agreement="agree",
    disagreement_type="none",
    checkable_claims=[_claim("Kubernetes encrypts secrets at rest by default")],
    summary="Both say secrets are encrypted at rest by default.",
)
FACTUAL_DISPUTE = CombinedCheck(
    agreement="disagree",
    disagreement_type="factual",
    key_disagreements=["A says port 5432, B says 5433"],
    checkable_claims=[
        _claim("The default PostgreSQL port is 5432", "A"),
        _claim("The default PostgreSQL port is 5433", "B"),
    ],
    summary="They conflict on the default port.",
)

PLAN = EvidencePlan(
    queries=[EvidenceQuery(tool="web", query="kubernetes secrets encryption at rest default",
                           targets_claim="encryption default")],
    reasoning="official docs settle it",
)
CODE_PLAN = EvidencePlan(
    queries=[EvidenceQuery(tool="code", query="print(1+1)", targets_claim="arithmetic")],
    reasoning="run it",
)

CONTRADICTS = EvidenceAssessment(
    claims=[
        ClaimVerdict(
            claim="Kubernetes encrypts secrets at rest by default",
            made_by="both",
            verdict="CONTRADICTED_BY_EVIDENCE",
            rationale="Docs state encryption at rest is not enabled by default.",
            citations=[1],
        )
    ],
    correction="Secrets are stored base64-encoded in etcd; encryption at rest must be enabled.",
)
SUPPORTS_A = EvidenceAssessment(
    claims=[
        ClaimVerdict(claim="The default PostgreSQL port is 5432", made_by="A",
                     verdict="SUPPORTED_BY_EVIDENCE", rationale="Official docs say 5432.",
                     citations=[1]),
        ClaimVerdict(claim="The default PostgreSQL port is 5433", made_by="B",
                     verdict="CONTRADICTED_BY_EVIDENCE", rationale="Docs say 5432.",
                     citations=[1]),
    ],
)
INSUFFICIENT = EvidenceAssessment(
    claims=[
        ClaimVerdict(claim="The default PostgreSQL port is 5432", made_by="A",
                     verdict="INSUFFICIENT_EVIDENCE", rationale="Sources conflict.", citations=[]),
        ClaimVerdict(claim="The default PostgreSQL port is 5433", made_by="B",
                     verdict="INSUFFICIENT_EVIDENCE", rationale="Sources conflict.", citations=[]),
    ],
)

VERDICT_A = JudgeVerdict(
    dimensions=[DimensionVerdict(dimension="accuracy", winner="A", reason="evidence backs A")],
    decision="choose_a", confidence="high", rationale="Evidence supports A.",
    final_answer="The default port is 5432.",
)
VERDICT_REJECT = JudgeVerdict(
    dimensions=[DimensionVerdict(dimension="accuracy", winner="tie", reason="both wrong")],
    decision="reject_both", confidence="high", rationale="Evidence contradicts both.",
    final_answer="Encryption at rest is not enabled by default; you must configure it.",
)
PASS = VerifierReport(claims=[], verdict="pass", reasons=[])
REVISED = RevisedAnswer(final_answer="Corrected per the evidence.", changes=["removed claim"])


def _engine(make_engine, openai_queue, anthropic_queue, tools):
    e: CouncilEngine = make_engine(
        FakeProvider("openai", openai_queue),
        FakeProvider("anthropic", anthropic_queue),
        check_provider="openai",
        judge_provider="anthropic",
    )
    e.evidence_tools = tools
    return e


WEB_OK = [EvidenceItem(kind="web", query="", snippet="Encryption at rest is NOT default.",
                       source_url="https://kubernetes.io/docs", title="Encrypting Secret Data")]


# --- Case 1: both models agree, evidence contradicts both --------------------


async def test_agreement_contradicted_by_evidence_escalates_and_wins(make_engine):
    """Both candidates asserted it; the evidence says otherwise. Synthesis
    (which would launder the shared error) must be skipped for the judge,
    which can reject both."""
    openai = FakeProvider("openai", ["GPT: secrets encrypted by default", AGREE_WITH_CLAIM,
                                     PLAN, CONTRADICTS, PASS])
    anthropic = FakeProvider("anthropic", ["Claude: secrets encrypted by default", VERDICT_REJECT,
                                           REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("Are k8s secrets encrypted at rest by default?", "deep")

    stages = [s["stage"] for s in result["steps"]]
    assert "evidence_override" in stages  # R4 fired
    assert "synthesis" not in stages  # agreement did NOT shortcut to synthesis
    assert "judge" in stages
    assert result["evidence_override"] is True
    assert result["evidence_used"] is True
    # The contradicted claim is persisted with its verdict and citation.
    assessments = result["claim_assessments"]
    assert assessments[0]["verdict"] == "CONTRADICTED_BY_EVIDENCE"
    assert assessments[0]["citations"] == [1]
    assert result["evidence"][0]["source_url"] == "https://kubernetes.io/docs"


async def test_judge_receives_binding_contradiction_instruction(make_engine):
    openai = FakeProvider("openai", ["GPT answer", AGREE_WITH_CLAIM, PLAN, CONTRADICTS, PASS])
    anthropic = FakeProvider("anthropic", ["Claude answer", VERDICT_REJECT, REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    await engine.run("q?", "deep")

    judge_call = next(
        c for c in anthropic.calls
        if c["schema"] is not None and c["schema"].__name__ == "JudgeVerdict"
    )
    text = "\n".join(m["content"] for m in judge_call["messages"])
    assert "CONTRADICTED_BY_EVIDENCE" in text
    assert "model agreement is not" in text  # binding instruction present
    assert "reject_both" in text
    assert "Encryption at rest is NOT default." in text  # the raw evidence itself


# --- Case 2: models disagree, evidence supports one --------------------------


async def test_factual_dispute_resolved_by_evidence(make_engine):
    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE, PLAN, SUPPORTS_A, PASS])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A, REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("Default postgres port?", "deep")

    stages = [s["stage"] for s in result["steps"]]
    assert "evidence_plan" in stages and "evidence_assess" in stages
    assert "critique_of_a" not in stages  # factual dispute -> evidence, not debate
    verdicts = {c["claim"]: c["verdict"] for c in result["claim_assessments"]}
    assert verdicts["The default PostgreSQL port is 5432"] == "SUPPORTED_BY_EVIDENCE"
    assert verdicts["The default PostgreSQL port is 5433"] == "CONTRADICTED_BY_EVIDENCE"
    # A contradicted claim always forces the revision pass, even on verifier pass.
    assert "revision" in stages
    assert result["final_answer"] == "Corrected per the evidence."


# --- Case 3: insufficient / conflicting evidence -> uncertainty --------------


async def test_insufficient_evidence_forces_uncertainty(make_engine):
    """The judge picking a winner on plausibility is a constraint violation
    and is forced into revision — enforced in code, not by prompt hope."""
    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE, PLAN, INSUFFICIENT, PASS])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A, REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("Default postgres port?", "deep")

    stages = [s["stage"] for s in result["steps"]]
    assert "evidence_constraint_violation" in stages
    assert "evidence_supremacy_override" in stages  # verifier 'pass' overridden
    assert "revision" in stages
    assert result["evidence_override"] is True
    violation = next(s for s in result["steps"] if s["stage"] == "evidence_constraint_violation")
    assert violation["output"]["judge_decision"] == "choose_a"
    assert violation["output"]["forced"] == "revision"


async def test_unavailable_tool_yields_insufficient_not_confidence(make_engine):
    """A tool that cannot run must surface as a gap in the trace — never as
    silent absence of doubt."""
    unavailable = FakeEvidenceTool(
        "web",
        [EvidenceItem(kind="web", query="", status="unavailable", error="no API key configured")],
        available=False,
    )
    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE, PLAN, INSUFFICIENT, PASS])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A, REVISED])
    engine = _engine(make_engine, [], [], {"web": unavailable})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("q?", "deep")

    gathered = next(s for s in result["steps"] if s["stage"] == "evidence_gathered")
    assert gathered["output"]["unavailable"] == ["no API key configured"]
    assert result["evidence"][0]["status"] == "unavailable"
    assert "evidence_constraint_violation" in [s["stage"] for s in result["steps"]]


# --- Case 4: verifier audits against evidence, not agreement -----------------


async def test_verifier_receives_evidence_bundle_before_source_material(make_engine):
    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE, PLAN, SUPPORTS_A, PASS])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A, REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    await engine.run("q?", "deep")

    verifier_call = next(
        c for c in openai.calls
        if c["schema"] is not None and c["schema"].__name__ == "VerifierReport"
    )
    text = "\n".join(m["content"] for m in verifier_call["messages"])
    assert text.index("EVIDENCE VERDICTS") < text.index("SOURCE MATERIAL")
    assert "outrank both candidates" in text


async def test_verifier_pass_cannot_stand_over_contradicted_claim(make_engine):
    """R2: evidence beats the verifier's own verdict."""
    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE, PLAN, SUPPORTS_A, PASS])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A, REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("q?", "deep")

    override = next(s for s in result["steps"] if s["stage"] == "evidence_supremacy_override")
    assert override["output"]["verifier_verdict"] == "pass"
    assert override["output"]["forced_verdict"] == "revise"
    assert result["final_answer"] == "Corrected per the evidence."


# --- scope and budget --------------------------------------------------------


async def test_council_mode_does_not_gather_evidence(make_engine):
    """V1 scope: evidence is deep mode only."""
    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("q?", "council")

    stages = [s["stage"] for s in result["steps"]]
    assert "evidence_plan" not in stages
    assert result["evidence_used"] is False


async def test_no_checkable_claims_skips_evidence_entirely(make_engine):
    no_claims = CombinedCheck(
        agreement="disagree", disagreement_type="reasoning",
        key_disagreements=["taste"], summary="Design preference.",
    )
    openai = FakeProvider("openai", ["A", no_claims, __import__("council.engine.schemas",
                          fromlist=["Critique"]).Critique(issues=[], overall="fine"), PASS])
    anthropic = FakeProvider("anthropic", ["B", __import__("council.engine.schemas",
                             fromlist=["Critique"]).Critique(issues=[], overall="fine"),
                             VERDICT_A])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("q?", "deep")

    stages = [s["stage"] for s in result["steps"]]
    assert "evidence_plan" not in stages  # nothing checkable: no wasted calls
    assert "critique_of_a" in stages  # reasoning dispute still gets its round


async def test_deep_with_evidence_stays_within_budget(make_engine):
    """Worst realistic deep path must fit the enforced budget."""
    from council.engine.budget import MODE_BUDGETS

    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE, PLAN, SUPPORTS_A, PASS])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A, REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}

    result = await engine.run("q?", "deep")

    assert result["totals"]["model_calls"] <= MODE_BUDGETS["deep"]
    # 2 candidates + check + evidence_plan + evidence_assess + judge
    # + verifier + revision = 8 (critiques skipped: factual dispute)
    assert result["totals"]["model_calls"] == 8


async def test_evidence_tool_caps_are_reported_not_silent(make_engine):
    """No silent truncation: dropped queries are named in the trace."""
    plan = EvidencePlan(
        queries=[
            EvidenceQuery(tool="web", query=f"query {i}", targets_claim="c") for i in range(5)
        ]
    )
    openai = FakeProvider("openai", ["Port is 5432", FACTUAL_DISPUTE, plan, INSUFFICIENT, PASS])
    anthropic = FakeProvider("anthropic", ["Port is 5433", VERDICT_A, REVISED])
    engine = _engine(make_engine, [], [], {"web": FakeEvidenceTool("web", WEB_OK)})
    engine.providers = {"openai": openai, "anthropic": anthropic}
    engine.max_web_searches = 2

    result = await engine.run("q?", "deep")

    gathered = next(s for s in result["steps"] if s["stage"] == "evidence_gathered")
    assert gathered["output"]["ran"] == {"web": 2}
    assert len(gathered["output"]["skipped_over_cap"]) == 3

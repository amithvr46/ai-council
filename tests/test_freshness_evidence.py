"""Freshness-routed Deep must actually use evidence (Phase 3 acceptance).

THE DEFECT, from the 56-run Auto evaluation:

    "Is Terraform 1.5 still supported by HashiCorp?"
      Auto -> Deep, reason: "the answer depends on fresh external information,
                             where model agreement proves nothing"
      recorded: effective_mode=deep, evidence_used=FALSE
      returned: a confident current-support answer

Auto selected Deep for one reason, and execution then ignored it. The cause was
the evidence gate:

    if deep and check.checkable_claims:

When the two candidates agree, the combined check returns no checkable claims,
so the whole evidence layer was skipped. The run answered a current-state
question from the consensus of two models trained on the same stale world —
which is exactly what the routing reason says proves nothing.

Forced Deep on the same question looked correct only because the models
happened to disagree there. The gate had the same hole.

TWO CHANGES

  - the gate also opens when the run was routed to Deep for freshness
  - R5: on the agreement path, a freshness-routed run whose evidence did not
    settle the current fact is forced to say so, instead of presenting model
    consensus as established

R5 fires for every way evidence can fail to settle it — tooling down, planner
returned nothing, budget refused, verdicts insufficient — because the honest
answer is identical in all of them.

SCOPE: this file covers Finding 1 only. Escalation invariants (one escalation,
carry-forward, reasoning does not escalate, factual does, deep machinery reuse)
are already proven in tests/test_escalation.py and are not duplicated here.
"""


from council.engine.pipeline import CouncilEngine
from council.engine.routing import RoutingDecision, decide
from council.engine.schemas import (
    ClaimVerdict,
    CombinedCheck,
    EvidenceAssessment,
    EvidencePlan,
    EvidenceQuery,
    RevisedAnswer,
    Synthesis,
    VerifierReport,
)
from council.evidence.base import EvidenceItem
from tests.fakes import FakeProvider
from tests.test_evidence_supremacy import FakeEvidenceTool

# A question whose answer depends on the current external world.
FRESH_Q = "Is Terraform 1.5 still supported by HashiCorp?"
# ...and one that does not, for the control.
STABLE_Q = "Explain the difference between a Kubernetes Deployment and a StatefulSet."

AGREE = CombinedCheck(
    agreement="agree",
    disagreement_type="none",
    key_disagreements=[],
    checkable_claims=[],
    summary="Both say it is still supported.",
)
PLAN = EvidencePlan(
    queries=[
        EvidenceQuery(
            tool="web",
            query="Terraform 1.5 support status",
            targets_claim="Terraform 1.5 is still supported",
        )
    ],
)
SYNTH = Synthesis(final_answer="Terraform 1.5 is still supported.", merged_from=["A", "B"])
PASS = VerifierReport(claims=[], verdict="pass", reasons=[])
REVISED = RevisedAnswer(final_answer="Support status is time-sensitive and unverified.", changes=[])

WEB = [
    EvidenceItem(
        kind="web",
        query="Terraform 1.5 support",
        snippet="Terraform 1.5 reached end of support.",
        source_url="https://hashicorp.com/support",
        title="Support policy",
    )
]
DECISIVE = EvidenceAssessment(
    claims=[
        ClaimVerdict(
            claim="Terraform 1.5 is still supported",
            made_by="both",
            verdict="SUPPORTED_BY_EVIDENCE",
            rationale="The policy page confirms the current position.",
            citations=[1],
        )
    ]
)
INSUFFICIENT = EvidenceAssessment(
    claims=[
        ClaimVerdict(
            claim="Terraform 1.5 is still supported",
            made_by="both",
            verdict="INSUFFICIENT_EVIDENCE",
            rationale="Nothing retrieved establishes the current position.",
            citations=[],
        )
    ]
)


def _fresh_decision():
    d = decide(FRESH_Q, affordable=["quick", "council", "deep"])
    assert d.mode == "deep" and d.features["needs_fresh_information"], d.reason
    return d


def _engine(make_engine, openai_queue, anthropic_queue, tools) -> CouncilEngine:
    e = make_engine(
        FakeProvider("openai", openai_queue),
        FakeProvider("anthropic", anthropic_queue),
        check_provider="openai",
        judge_provider="anthropic",
    )
    e.evidence_tools = tools
    return e


def _stages(result):
    return [s["stage"] for s in result["steps"]]


# ==========================================================================
# 1. Freshness-routed Deep gathers evidence even when the candidates agree
# ==========================================================================


async def test_agreement_no_longer_cancels_the_evidence_layer(db, make_engine):
    """The regression test for the evaluated defect."""
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, PLAN, DECISIVE, SYNTH],
        ["Claude: still supported", PASS],
        {"web": FakeEvidenceTool("web", WEB)},
    )
    result = await engine.run(FRESH_Q, "auto", routing=_fresh_decision())

    assert result["mode"] == "deep"
    assert result["evidence_used"] is True
    stages = _stages(result)
    assert "evidence_plan" in stages and "evidence_assess" in stages


async def test_decisive_evidence_does_not_trigger_the_uncertainty_rule(db, make_engine):
    """R5 is about unverified currency, not about doubting everything. When the
    evidence settles the question the answer is allowed to stand on it."""
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, PLAN, DECISIVE, SYNTH],
        ["Claude: still supported", PASS],
        {"web": FakeEvidenceTool("web", WEB)},
    )
    result = await engine.run(FRESH_Q, "auto", routing=_fresh_decision())
    assert "freshness_unverified" not in _stages(result)


# ==========================================================================
# 2. Evidence unavailable or refused degrades safely and transparently
# ==========================================================================


async def test_no_evidence_tool_forces_the_answer_to_admit_it(db, make_engine):
    """Tooling down. The run still answers — graceful degradation is preserved
    — but must not present the current status as established."""
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, PLAN, SYNTH, REVISED],
        ["Claude: still supported", PASS],
        {},  # no tools at all
    )
    result = await engine.run(FRESH_Q, "auto", routing=_fresh_decision())

    assert result["status"] == "complete"
    assert result["evidence_used"] is False
    stages = _stages(result)
    assert "evidence_not_gathered" in stages
    assert "freshness_unverified" in stages
    assert result["evidence_override"] is True
    assert "revision" in stages  # the answer was actually rewritten


async def test_a_planner_returning_nothing_also_forces_it(db, make_engine):
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, EvidencePlan(queries=[]), SYNTH, REVISED],
        ["Claude: still supported", PASS],
        {"web": FakeEvidenceTool("web", WEB)},
    )
    result = await engine.run(FRESH_Q, "auto", routing=_fresh_decision())
    assert "freshness_unverified" in _stages(result)
    assert result["evidence_used"] is False


async def test_indecisive_evidence_forces_it_too(db, make_engine):
    """Evidence was gathered and simply did not settle the question. Same
    honest outcome as not gathering any."""
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, PLAN, INSUFFICIENT, SYNTH, REVISED],
        ["Claude: still supported", PASS],
        {"web": FakeEvidenceTool("web", WEB)},
    )
    result = await engine.run(FRESH_Q, "auto", routing=_fresh_decision())
    assert "freshness_unverified" in _stages(result)


async def test_the_forced_revision_names_what_is_unverified(db, make_engine):
    """The instruction handed to the revision stage has to be specific enough
    to produce an honest answer rather than generic hedging."""
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, PLAN, SYNTH, REVISED],
        ["Claude: still supported", PASS],
        {},
    )
    result = await engine.run(FRESH_Q, "auto", routing=_fresh_decision())
    step = next(s for s in result["steps"] if s["stage"] == "freshness_unverified")
    assert step["output"]["evidence_gathered"] is False
    assert step["output"]["forced"] == "revision"


async def test_an_exhausted_evidence_budget_degrades_instead_of_failing(db, make_engine):
    """A refused budget is a bounded failure condition, not a crash. Only on
    the freshness-opened path — a budget failure on the checkable-claims path
    still raises, exactly as before this change."""
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, SYNTH, REVISED],
        ["Claude: still supported", PASS],
        {"web": FakeEvidenceTool("web", WEB)},
    )
    original = engine._plan_evidence

    async def _refuse(*a, **kw):
        from council.engine.budget import BudgetExceeded

        raise BudgetExceeded("deep", "evidence_plan", 11)

    engine._plan_evidence = _refuse
    result = await engine.run(FRESH_Q, "auto", routing=_fresh_decision())
    engine._plan_evidence = original

    assert result["status"] == "complete"
    assert "freshness_unverified" in _stages(result)
    reason = next(
        s for s in result["steps"] if s["stage"] == "evidence_not_gathered"
    )["output"]["reason"]
    assert "budget refused" in reason


# ==========================================================================
# 3. The change is targeted — it does not turn on evidence everywhere
# ==========================================================================


async def test_a_non_freshness_deep_run_with_agreement_still_skips_evidence(db, make_engine):
    """Cost control: the gate widened for freshness only.

    The decision is built explicitly rather than routed, because no natural
    question routes to Deep for a NON-freshness reason today — routing this by
    question text would silently test council instead, which is how the first
    draft of this test passed while asserting nothing.
    """
    d = RoutingDecision(
        "deep", "features", "hypothetical non-freshness deep",
        features={"needs_fresh_information": False},
    )
    engine = _engine(
        make_engine,
        ["GPT: explanation", AGREE, SYNTH],
        ["Claude: explanation", PASS],
        {"web": FakeEvidenceTool("web", WEB)},
    )
    result = await engine.run(STABLE_Q, "auto", routing=d)

    assert result["mode"] == "deep"
    stages = _stages(result)
    assert "evidence_plan" not in stages
    assert "freshness_unverified" not in stages


async def test_an_explicitly_chosen_deep_is_unchanged_by_this_fix(db, make_engine):
    """KNOWN ASYMMETRY, asserted so it stays visible.

    The invariant as specified is about what AUTO decided, so the signal comes
    from the routing decision. A user who types Deep on the same freshness
    question produces an 'explicit' decision carrying no features, and the old
    behaviour applies: agreement still cancels the evidence layer.

    Arguably the freshness of a question is a property of the question rather
    than of who chose the mode. Left as-is deliberately — widening it was not
    in scope, and it would add two calls to every explicit Deep run on a
    time-sensitive topic. Flagged for review rather than decided here.
    """
    engine = _engine(
        make_engine,
        ["GPT: still supported", AGREE, SYNTH],
        ["Claude: still supported", PASS],
        {"web": FakeEvidenceTool("web", WEB)},
    )
    result = await engine.run(FRESH_Q, "deep")  # no routing decision: explicit
    assert result["evidence_used"] is False
    assert "freshness_unverified" not in _stages(result)

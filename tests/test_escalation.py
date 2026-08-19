"""Phase 3B — bounded council -> deep escalation.

The principle under test: **Auto should not pay to predict uncertainty it can
observe cheaply.** Council runs first; only when the models have actually
disagreed on something evidence can settle does Auto spend more.

Two invariants are load-bearing and appear repeatedly below:
  - escalation raises the CEILING and never resets consumption
  - escalation happens at most once, enforced mechanically
"""

import asyncio

import pytest

from council.engine.budget import MODE_BUDGETS, BudgetTracker, CeilingLowered
from council.engine.escalation import evaluate
from council.engine.schemas import (
    CheckableClaim,
    ClaimVerdict,
    CombinedCheck,
    Critique,
    DimensionVerdict,
    EvidenceAssessment,
    EvidencePlan,
    EvidenceQuery,
    JudgeVerdict,
    RevisedAnswer,
    Synthesis,
    VerifierReport,
)
from council.evidence.base import EvidenceItem
from council.spend import SpendDecision, SpendSnapshot, save_settings
from tests.fakes import FakeProvider
from tests.test_evidence_supremacy import FakeEvidenceTool


def _claim(text, made_by="A"):
    return CheckableClaim(claim=text, made_by=made_by, why_material="core")


FACTUAL = CombinedCheck(
    agreement="disagree", disagreement_type="factual",
    key_disagreements=["port"],
    checkable_claims=[_claim("The default PostgreSQL port is 5432", "A"),
                      _claim("The default PostgreSQL port is 5433", "B")],
    summary="They conflict on the port.",
)
BOTH_KINDS = CombinedCheck(
    agreement="disagree", disagreement_type="both",
    key_disagreements=["port and approach"],
    checkable_claims=[_claim("The default PostgreSQL port is 5432", "A")],
    summary="Factual and reasoning conflict.",
)
REASONING_ONLY = CombinedCheck(
    agreement="disagree", disagreement_type="reasoning",
    key_disagreements=["count vs for_each"],
    checkable_claims=[],
    summary="A judgement call, not a fact.",
)
AGREEMENT = CombinedCheck(
    agreement="agree", disagreement_type="none", checkable_claims=[], summary="same",
)
FACTUAL_NO_CLAIMS = CombinedCheck(
    agreement="disagree", disagreement_type="factual",
    key_disagreements=["vibes"], checkable_claims=[],
    summary="Disagree on a fact nobody stated checkably.",
)

PLAN = EvidencePlan(
    queries=[EvidenceQuery(tool="web", query="postgres default port", targets_claim="port")],
    reasoning="docs settle it",
)
SUPPORTS_A = EvidenceAssessment(
    claims=[ClaimVerdict(claim="The default PostgreSQL port is 5432", made_by="A",
                         verdict="SUPPORTED_BY_EVIDENCE", rationale="Docs.", citations=[1])],
)
VERDICT_A = JudgeVerdict(
    dimensions=[DimensionVerdict(dimension="accuracy", winner="A", reason="evidence")],
    decision="choose_a", confidence="high", rationale="Evidence supports A.",
    final_answer="The default port is 5432.",
)
PASS = VerifierReport(claims=[], verdict="pass", reasons=[])
WEB = [EvidenceItem(kind="web", query="", snippet="The default port is 5432.",
                    source_url="https://postgresql.org/docs", title="Server config")]


CRITIQUE = Critique(issues=[], overall="Sound.")


def _deep_capable_engine(make_engine, check=FACTUAL, tools=True):
    """Queues enough for council THEN the full deep continuation.

    A 'both' disagreement also earns one critique round, so each provider
    reviews the other before the judge. A purely factual dispute skips it —
    factual disputes go to evidence, not debate.
    """
    critique = [CRITIQUE] if check.disagreement_type == "both" else []
    openai = FakeProvider(
        "openai", ["GPT: 5432", check, PLAN, SUPPORTS_A, *critique, PASS]
    )
    anthropic = FakeProvider(
        "anthropic",
        ["Claude: 5433", *critique, VERDICT_A, RevisedAnswer(final_answer="5432.", changes=[])],
    )
    engine = make_engine(openai, anthropic, check_provider="openai",
                         judge_provider="anthropic")
    engine.evidence_tools = {"web": FakeEvidenceTool("web", WEB)} if tools else {}
    return engine


def _council_only_engine(make_engine, check):
    openai = FakeProvider("openai", ["GPT answer", check, Synthesis(final_answer="final")])
    anthropic = FakeProvider("anthropic", ["Claude answer", VERDICT_A])
    engine = make_engine(openai, anthropic, check_provider="openai",
                         judge_provider="anthropic")
    engine.evidence_tools = {"web": FakeEvidenceTool("web", WEB)}
    return engine


def _escalation_step(result):
    return next(
        (s for s in result["steps"] if s["stage"] == "routing_escalation"), None
    )


async def _run_auto(engine, question="What is the default PostgreSQL port?"):
    """Force the auto path: routing must choose council for this to be a real
    escalation test."""
    decision = await engine.plan(question, "auto")
    assert decision.mode == "council", decision.reason
    return await engine.run(question, "auto", routing=decision)


# ================================================== CASE 1 and CASE 2


async def test_case1_factual_disagreement_escalates_exactly_once(db, make_engine):
    engine = _deep_capable_engine(make_engine, FACTUAL)
    result = await _run_auto(engine)

    step = _escalation_step(result)
    assert step is not None
    assert step["output"]["result"] == "escalated"
    assert step["output"]["trigger"] == "factual"
    assert step["output"]["escalation_number"] == 1
    # Deep actually ran.
    stages = [s["stage"] for s in result["steps"]]
    assert "evidence_plan" in stages and "evidence_assess" in stages
    assert result["mode"] == "deep"


async def test_case2_both_kinds_of_disagreement_escalates(db, make_engine):
    engine = _deep_capable_engine(make_engine, BOTH_KINDS)
    result = await _run_auto(engine)
    assert _escalation_step(result)["output"]["result"] == "escalated"
    assert _escalation_step(result)["output"]["trigger"] == "both"


# ================================================== CASE 3, 4, 5


async def test_case3_reasoning_only_disagreement_does_not_escalate(db, make_engine):
    """No search result decides count-versus-for_each. Spending deep budget on
    a judgement call buys nothing."""
    engine = _council_only_engine(make_engine, REASONING_ONLY)
    result = await _run_auto(engine, "Should I use count or for_each here?")
    assert result["mode"] == "council"
    assert "evidence_plan" not in [s["stage"] for s in result["steps"]]


async def test_case4_agreement_does_not_escalate(db, make_engine):
    engine = _council_only_engine(make_engine, AGREEMENT)
    result = await _run_auto(engine)
    assert result["mode"] == "council"
    assert _escalation_step(result) is None


async def test_case5_no_checkable_claims_does_not_escalate(db, make_engine):
    engine = _council_only_engine(make_engine, FACTUAL_NO_CLAIMS)
    result = await _run_auto(engine)
    assert result["mode"] == "council"
    step = _escalation_step(result)
    assert step["output"]["refusal_reason"] == "no_checkable_claims"


async def test_evidence_unavailable_does_not_escalate(db, make_engine):
    """Escalating into a deep run with no tools would spend money to produce
    INSUFFICIENT on every claim."""
    engine = _council_only_engine(make_engine, FACTUAL)
    engine.evidence_tools = {}
    result = await _run_auto(engine)
    assert result["mode"] == "council"
    assert _escalation_step(result)["output"]["refusal_reason"] == "evidence_unavailable"


# ================================================== CASE 6 and CASE 7


async def test_case6_insufficient_dollars_refuses_before_any_deep_work(db, make_engine):
    # Enough for council (~$0.08) but not for the deep increment (~$0.17), so
    # routing still picks council and only the escalation is unaffordable.
    await save_settings(daily_limit_usd=0.15, monthly_limit_usd=0.15, hard_stop=True)
    engine = _council_only_engine(make_engine, FACTUAL)
    result = await _run_auto(engine)

    step = _escalation_step(result)
    assert step["output"]["result"] == "refused"
    assert step["output"]["refusal_reason"] == "escalation_refused_budget"
    # Nothing deep was started — not even the plan.
    assert "evidence_plan" not in [s["stage"] for s in result["steps"]]
    assert result["mode"] == "council"


def test_case7_insufficient_request_allowance_refuses():
    """Deep's ceiling is shared with what council already spent."""
    budget = BudgetTracker("council")
    for _ in range(MODE_BUDGETS["deep"] - 2):
        budget.limit = MODE_BUDGETS["deep"]  # simulate room to have spent that much
        budget.spend("x")
    decision = evaluate(
        routed_by_auto=True, check=FACTUAL, budget=budget, evidence_available=True
    )
    assert decision.escalate is False
    assert decision.refusal == "insufficient_request_allowance"


# ================================================== CASE 8: carry-forward


async def test_case8_calls_dollars_and_attempts_all_carry_forward(db, make_engine):
    engine = _deep_capable_engine(make_engine, FACTUAL)
    result = await _run_auto(engine)

    budget = _escalation_step(result)["output"]["budget"]
    assert budget["original_mode"] == "council"
    assert budget["mode"] == "deep"
    assert budget["limit"] == MODE_BUDGETS["deep"]
    # Three council calls were made before the check returned.
    assert budget["spent"] == 3
    assert budget["api_attempts"] >= 3
    assert budget["cost_usd"] >= 0.0
    # Deep did NOT get a fresh allowance.
    assert budget["remaining"] == MODE_BUDGETS["deep"] - budget["spent"]
    assert budget["remaining"] < MODE_BUDGETS["deep"]

    # And the decision payload reports the same consumption independently.
    output = _escalation_step(result)["output"]
    assert output["logical_calls_already_spent"] == 3
    assert output["api_attempts_already_spent"] >= 3
    assert output["remaining_deep_allowance"] == MODE_BUDGETS["deep"] - 3


def test_a_consumed_ceiling_can_never_be_reopened():
    budget = BudgetTracker("council")
    budget.spend("a")
    budget.record(api_attempts=2, cost_usd=0.05)
    budget.raise_ceiling("deep")

    for lower in ("quick", "council", "deep"):
        with pytest.raises(CeilingLowered):
            budget.raise_ceiling(lower)
    # Consumption survived every refused attempt.
    assert budget.spent == 1
    assert budget.api_attempts == 2
    assert budget.cost_usd == 0.05


# ================================================== CASE 9: exactly one


def test_case9_a_second_escalation_is_mechanically_impossible():
    budget = BudgetTracker("council")
    budget.spend("a")
    budget.raise_ceiling("deep")
    decision = evaluate(
        routed_by_auto=True, check=FACTUAL, budget=budget, evidence_available=True
    )
    assert decision.escalate is False
    assert decision.refusal == "already_escalated"


async def test_case9_only_one_escalation_step_is_ever_recorded(db, make_engine):
    engine = _deep_capable_engine(make_engine, FACTUAL)
    result = await _run_auto(engine)
    steps = [s for s in result["steps"] if s["stage"] == "routing_escalation"]
    assert len(steps) == 1


# ================================================== CASE 10: R1-R4 unchanged


async def test_case10_evidence_supremacy_is_identical_after_escalation(db, make_engine):
    """Reaching deep through Auto must not weaken the truth rules."""
    engine = _deep_capable_engine(make_engine, FACTUAL)
    result = await _run_auto(engine)

    assert result["evidence_used"] is True
    assert result["claim_assessments"][0]["verdict"] == "SUPPORTED_BY_EVIDENCE"
    assert result["evidence"][0]["source_url"] == "https://postgresql.org/docs"
    stages = [s["stage"] for s in result["steps"]]
    assert "judge" in stages and "verifier" in stages


# ================================================== CASE 11 and 12: cancellation


async def test_case11_cancelling_mid_escalation_leaves_no_running_work(db, make_engine):
    engine = _deep_capable_engine(make_engine, FACTUAL)
    decision = await engine.plan("q?", "auto")
    task = asyncio.create_task(engine.run("q?", "auto", routing=decision))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # No tracker outlives its request, so nothing can keep spending against it.
    assert engine._budgets == {}


async def test_case12_a_completed_escalation_leaves_no_orphan_state(db, make_engine):
    engine = _deep_capable_engine(make_engine, FACTUAL)
    await _run_auto(engine)
    assert engine._budgets == {}
    assert engine._inflight == {}


# ================================================== CASE 13: zero cost


async def test_case13_the_escalation_decision_itself_costs_nothing(db, make_engine):
    """Only the additional deep work costs money; deciding does not."""
    escalated = _deep_capable_engine(make_engine, FACTUAL)
    result_auto = await _run_auto(escalated)

    forced = _deep_capable_engine(make_engine, FACTUAL)
    result_deep = await forced.run("What is the default PostgreSQL port?", "deep")

    assert result_auto["totals"]["model_calls"] == result_deep["totals"]["model_calls"]
    step = _escalation_step(result_auto)
    assert step["cost_usd"] == 0.0
    assert step["provider"] is None


async def test_a_refusal_also_costs_nothing(db, make_engine):
    engine = _council_only_engine(make_engine, FACTUAL_NO_CLAIMS)
    result = await _run_auto(engine)
    assert _escalation_step(result)["cost_usd"] == 0.0


# ================================================== CASE 14: honest refusal


async def test_case14_a_refused_escalation_still_returns_a_usable_answer(db, make_engine):
    # Enough for council (~$0.08) but not for the deep increment (~$0.17), so
    # routing still picks council and only the escalation is unaffordable.
    await save_settings(daily_limit_usd=0.15, monthly_limit_usd=0.15, hard_stop=True)
    engine = _council_only_engine(make_engine, FACTUAL)
    result = await _run_auto(engine)

    # The user gets an answer, not a failure.
    assert result["status"] == "complete"
    assert result["final_answer"]
    # And the trace says plainly what was considered and why it did not happen.
    output = _escalation_step(result)["output"]
    assert output["result"] == "refused"
    assert output["trigger"] is None
    assert "budget" in output["reason"]
    assert output["refusal_reason"] == "escalation_refused_budget"


# ================================================== CASE 15: explicit mode


async def test_case15_an_explicit_mode_is_never_secretly_escalated(db, make_engine):
    """The user chose council. Auto does not revise that."""
    engine = _council_only_engine(make_engine, FACTUAL)
    result = await engine.run("What is the default PostgreSQL port?", "council")
    assert result["mode"] == "council"
    assert _escalation_step(result) is None


def test_explicit_mode_is_refused_at_the_decision_level():
    decision = evaluate(
        routed_by_auto=False, check=FACTUAL, budget=BudgetTracker("council"),
        evidence_available=True,
    )
    assert decision.escalate is False
    assert decision.refusal == "not_auto_routed"


# ================================================== decision-level detail


def test_the_decision_payload_answers_why_money_was_spent():
    budget = BudgetTracker("council")
    for stage in ("a", "b", "check"):
        budget.spend(stage)
    budget.record(api_attempts=4, cost_usd=0.03)
    spend = SpendDecision(True, 0.17, 5.0, SpendSnapshot(0.4, 1.2, 3.0, 30.0))

    payload = evaluate(
        routed_by_auto=True, check=FACTUAL, budget=budget,
        evidence_available=True, spend_decision=spend,
    ).as_dict()

    for key in (
        "source_mode", "target_mode", "trigger", "reason", "escalation_number",
        "logical_calls_already_spent", "api_attempts_already_spent",
        "cost_already_spent_usd", "remaining_deep_allowance",
        "checkable_claims_present", "affordability", "result",
    ):
        assert key in payload, key
    assert payload["source_mode"] == "council"
    assert payload["target_mode"] == "deep"

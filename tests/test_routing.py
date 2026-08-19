"""Auto routing, Phase 3A — Rungs 0 to 3.

The load-bearing property, tested explicitly below: **routing itself costs zero
model calls**. Auto exists to spend less, so an Auto that buys a call to decide
how much model to buy would be self-defeating.
"""

import pytest

from council import outcomes
from council.db.models import Request, Step
from council.db.session import session_scope
from council.engine.routing import (
    MIN_SAMPLES,
    ClassStats,
    RoutingDecision,
    decide,
    extract_features,
    needs_fresh_information,
    quick_eligible,
)
from council.engine.schemas import CombinedCheck, Synthesis
from tests.fakes import FakeProvider


def _engine(make_engine, **kwargs):
    return make_engine(
        FakeProvider(
            "openai",
            [
                "answer",
                CombinedCheck(agreement="agree", disagreement_type="none", summary="s"),
                Synthesis(final_answer="final"),
            ],
        ),
        FakeProvider("anthropic", ["answer b"]),
        check_provider="openai",
        **kwargs,
    )


# ============================================ amendment 1: freshness in context


@pytest.mark.parametrize(
    "question",
    [
        "What is the latest Kubernetes version?",
        "Who is the current CEO of Datadog?",
        "What is the current price of an Azure D4s v5?",
        "What changed in Azure this week?",
        "Which Terraform provider version is newest?",
        "Is Kubernetes 1.29 still supported?",
    ],
)
def test_external_world_freshness_is_detected(question):
    assert needs_fresh_information(question) is True, question


@pytest.mark.parametrize(
    "question",
    [
        "Rewrite my current resume.",
        "Review the latest version of this code.",
        "Shorten my current summary.",
        "Clean up this current draft and make it tighter.",
        "Refactor the latest version of my deployment script.",
        "Summarise the attached document as it currently stands.",
    ],
)
def test_recency_words_about_the_users_own_material_are_not_freshness(question):
    """A recency WORD is not a freshness signal. No amount of web search makes
    someone's resume newer."""
    assert needs_fresh_information(question) is False, question


def test_freshness_needs_a_recency_marker_at_all():
    assert needs_fresh_information("What is a Kubernetes operator?") is False


def test_this_week_is_a_time_phrase_not_a_pointer_to_user_material():
    """'this week' contains 'this'; it must not be read as 'this document'."""
    assert needs_fresh_information("What shipped in AWS this week?") is True


# ==================================== amendment 2: agreement is not truth


def test_high_agreement_with_evidence_overrides_does_not_earn_quick():
    """The contract's own example, and the reason this gate exists: models
    agreeing 98% of the time while evidence contradicts them 15% of the time
    describes a class needing MORE verification, not less."""
    stats = ClassStats(
        samples=100,
        agreement_rate=0.98,
        evidence_override_rate=0.15,
        mean_rating=4.5,
        low_risk=True,
    )
    eligible, reason = quick_eligible(stats, {})
    assert eligible is False
    assert "redundancy, not correctness" in reason


def test_quick_requires_every_condition_together():
    good = ClassStats(
        samples=100, agreement_rate=0.99, evidence_override_rate=0.0,
        mean_rating=4.6, low_risk=True,
    )
    assert quick_eligible(good, {})[0] is True

    from dataclasses import replace

    for weakened, expect in [
        (replace(good, samples=MIN_SAMPLES - 1), "samples"),
        (replace(good, agreement_rate=0.80), "agreement"),
        (replace(good, evidence_override_rate=0.10), "evidence"),
        (replace(good, low_risk=False), "low risk"),
        (replace(good, mean_rating=2.0), "rating"),
    ]:
        eligible, reason = quick_eligible(weakened, {})
        assert eligible is False, reason
        assert expect in reason


def test_deterministic_features_veto_a_strong_prior():
    """History never outranks a live signal that this request needs more."""
    strong = ClassStats(
        samples=500, agreement_rate=1.0, evidence_override_rate=0.0,
        mean_rating=5.0, low_risk=True,
    )
    assert quick_eligible(strong, {"needs_fresh_information": True})[0] is False
    assert quick_eligible(strong, {"has_code": True})[0] is False


# ================================================== the ladder itself


def test_explicit_mode_is_always_obeyed(make_engine):
    """Rung 0. Auto never overrides a choice the user actually made."""
    decision = RoutingDecision("deep", "explicit", "user chose")
    assert decision.was_routed is False


async def test_plan_returns_explicit_without_touching_the_budget(db, make_engine):
    engine = _engine(make_engine)
    decision = await engine.plan("anything", "council")
    assert decision.mode == "council"
    assert decision.rung == "explicit"


def test_fresh_information_routes_to_deep():
    d = decide("What is the latest Kubernetes version?")
    assert d.mode == "deep"
    assert d.rung == "features"


def test_transformation_routes_to_quick():
    d = decide("Rewrite my current summary to be shorter.")
    assert d.mode == "quick"
    assert d.rung == "features"


def test_creative_routes_to_quick():
    assert decide("Write me a limerick about Terraform.").mode == "quick"


def test_unknown_request_defaults_to_council():
    """Rung 3 inconclusive: council is the safe default, and the only mode
    that preserves the escalation path."""
    d = decide("Should I use count or for_each in this module?")
    assert d.mode == "council"
    assert d.rung == "default"


def test_code_does_not_get_routed_to_quick():
    d = decide("```python\nraise ValueError\n```\nWhy does my current script fail?")
    assert d.mode != "quick"


def test_a_degraded_provider_prefers_quick_over_a_hollow_council():
    d = decide("Should I use count or for_each?", degraded_providers=True)
    assert d.mode == "quick"
    assert d.rung == "constraint"


def test_unaffordable_deep_does_not_get_chosen_for_fresh_information():
    d = decide("What is the latest Kubernetes version?", affordable=["quick", "council"])
    assert d.mode == "council"


def test_unaffordable_council_falls_to_quick():
    d = decide("Should I use count or for_each?", affordable=["quick"])
    assert d.mode == "quick"
    assert d.rung == "constraint"


def test_features_are_reported_with_every_decision():
    d = decide("What is the latest Kubernetes version?")
    assert d.features["needs_fresh_information"] is True
    assert d.as_dict()["deciding_rung"] == "features"
    assert d.as_dict()["reason"]


def test_extract_features_is_pure_and_total():
    for text in ["", "hello", "```\n```", "What is the current price of X?"]:
        assert isinstance(extract_features(text), dict)


# ============================================ zero-cost routing, instrumented


async def test_routing_adds_zero_model_calls(db, make_engine):
    """The property the whole design rests on."""
    auto = await _engine(make_engine).run("Rewrite my current summary.", "auto")
    explicit = await _engine(make_engine).run("Rewrite my current summary.", "quick")
    assert auto["mode"] == "quick"
    assert auto["totals"]["model_calls"] == explicit["totals"]["model_calls"]


async def test_a_routing_step_records_the_reason(db, make_engine):
    result = await _engine(make_engine).run("Rewrite my current summary.", "auto")
    async with session_scope() as s:
        steps = (await s.execute(select_steps(result["id"]))).scalars().all()
    routing = [st for st in steps if st.stage == "routing"]
    assert len(routing) == 1
    assert routing[0].output["chosen"] == "quick"
    assert routing[0].output["deciding_rung"] == "features"
    assert routing[0].output["reason"]
    # A routing step is free by construction.
    assert routing[0].cost_usd == 0.0
    assert routing[0].provider is None


async def test_an_explicit_mode_writes_no_routing_step(db, make_engine):
    """Nothing was routed, so there is no routing decision to explain."""
    result = await _engine(make_engine).run("q?", "council")
    async with session_scope() as s:
        steps = (await s.execute(select_steps(result["id"]))).scalars().all()
    assert [st for st in steps if st.stage == "routing"] == []


async def test_the_resolved_mode_is_what_gets_persisted(db, make_engine):
    result = await _engine(make_engine).run("Rewrite my current summary.", "auto")
    async with session_scope() as s:
        row = await s.get(Request, result["id"])
        assert row.mode == "quick"
        assert row.outcome_kind == outcomes.QUESTION_ANSWER


# =========================================== amendment 4: data populations


async def test_rows_default_to_the_real_population(db, make_engine):
    result = await _engine(make_engine).run("q?", "council")
    async with session_scope() as s:
        assert (await s.get(Request, result["id"])).data_class == "real"


async def test_an_eval_engine_stamps_its_rows_as_eval(db, make_engine):
    """A benchmark sweep must not masquerade as organic usage."""
    result = await _engine(make_engine, data_class="eval").run("q?", "council")
    async with session_scope() as s:
        assert (await s.get(Request, result["id"])).data_class == "eval"


async def test_the_report_never_mixes_populations(db, make_engine):
    from council.engine.routing_report import collect

    await _engine(make_engine).run("real question?", "council")
    await _engine(make_engine, data_class="eval").run("eval question?", "council")
    await _engine(make_engine, data_class="synthetic").run("fake question?", "council")

    for population, expected in (("real", 1), ("eval", 1), ("synthetic", 1)):
        buckets = await collect(population)
        assert sum(b.samples for b in buckets) == expected, population


# ================================ amendment 5: do not fake historical intelligence


async def test_a_thin_bucket_reports_insufficient_data_and_no_opinion(db, make_engine):
    from council.engine.routing_report import collect, recommendation

    await _engine(make_engine).run("q?", "council")
    bucket = (await collect("real"))[0]
    assert bucket.ready is False
    assert "not enough data" in recommendation(bucket)


async def test_an_empty_report_says_so_rather_than_inventing_a_baseline(db):
    from council.engine.routing_report import collect, render

    assert "No completed real requests yet" in render(await collect("real"))


def test_agreement_rate_is_none_when_nothing_was_ever_checked():
    """Quick runs have no second model, so there is no agreement verdict.
    Reporting 0% would invent a finding out of a structural absence."""
    from council.engine.routing_report import BucketReport

    bucket = BucketReport("question_answer", "quick", samples=5)
    assert bucket.agreement_rate is None
    assert bucket.as_dict()["agreement_rate"] is None


def select_steps(request_id):
    from sqlalchemy import select

    return select(Step).where(Step.request_id == request_id)


@pytest.mark.parametrize(
    "question",
    [
        "Review the latest version of this deployment script.",
        "Refactor this Terraform module for me.",
        "Clean up the current version of this Helm template.",
        "Shorten these current bullets.",
    ],
)
def test_multi_word_self_reference_is_recognised(question):
    """Found by inspecting real routing output: "this deployment script" fell
    through to council because only the bare noun matched. Safe, but it missed
    a legitimate quick."""
    assert needs_fresh_information(question) is False
    assert decide(question).mode == "quick"


# ========= amendment: external state overrides self-reference suppression


@pytest.mark.parametrize(
    "question",
    [
        "Is this Terraform code valid with the current provider version?",
        "Will this Kubernetes manifest work with the latest AKS version?",
        "Does this code still work with the current Azure SDK?",
        "Is my chart compatible with the newest Helm release?",
        "Has the API I use in this script been deprecated recently?",
    ],
)
def test_current_external_state_beats_self_reference(question):
    """Self-reference normally suppresses freshness — no search makes someone's
    resume newer. But when CURRENT EXTERNAL STATE decides whether the answer is
    correct, being wrong about the world makes the answer wrong regardless of
    whose code it is. Freshness wins."""
    assert needs_fresh_information(question) is True, question
    assert decide(question).mode == "deep"


@pytest.mark.parametrize(
    "question",
    [
        "Rewrite my current resume.",
        "Review the latest version of this code.",
        "Shorten my current summary.",
        "Clean up the current version of this Helm template.",
        "Summarise the latest draft of my document.",
    ],
)
def test_the_override_does_not_leak_into_plain_transformations(question):
    """'the latest version OF THIS CODE' is the user's file, not the world's.
    The override must not fire merely because the word 'version' appears."""
    assert needs_fresh_information(question) is False, question


@pytest.mark.parametrize(
    "question",
    [
        "Shorten this to one sentence: 'Cloud engineer with seven years.'",
        "Rewrite that in plainer language.",
        "Summarise the following in two lines.",
    ],
)
def test_a_transform_verb_on_a_bare_deictic_is_still_transformation(question):
    """Found by the controlled evaluation: "Shorten this to one sentence"
    carries no possessive, so it fell through to council and cost a legitimate
    quick."""
    assert decide(question).mode == "quick", question

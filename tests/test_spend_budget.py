"""Phase 2A — spend budgets (real money, checked forward).

The distinguishing property under test: affordability is checked BEFORE work
starts, using an estimate of what the work will cost. Work that cannot be
completed inside the remaining budget is never started. A ceiling crossed
mid-request never kills a request already in flight.
"""

import pytest
from fastapi.testclient import TestClient

import council.api.main as api
from council.api.main import app
from council.db.models import Request
from council.db.session import session_scope
from council.engine.schemas import CombinedCheck, Synthesis
from council.spend import (
    FALLBACK_ESTIMATES,
    BudgetRefused,
    check_affordable,
    estimate_cost,
    load_settings,
    save_settings,
    spend_snapshot,
)
from tests.fakes import FakeProvider

AGREE = CombinedCheck(agreement="agree", disagreement_type="none", summary="same")
SYNTH = Synthesis(final_answer="Final.")


async def _spend(amount: float, mode: str = "council", status: str = "complete") -> None:
    """Record a completed request that cost `amount`."""
    async with session_scope() as s:
        s.add(
            Request(
                question="q", mode=mode, status=status, final_answer="a",
                total_cost_usd=amount,
            )
        )


def _engine(make_engine):
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    return make_engine(openai, anthropic, check_provider="openai")


# --- settings ---------------------------------------------------------------


async def test_defaults_apply_when_never_configured(db):
    settings = await load_settings()
    assert settings.daily_limit_usd == 3.0
    assert settings.monthly_limit_usd == 30.0
    assert settings.warn_threshold_pct == 70
    assert settings.hard_stop is True


async def test_settings_persist_and_partial_update(db):
    await save_settings(daily_limit_usd=2.0)
    assert (await load_settings()).daily_limit_usd == 2.0
    # Unspecified fields are untouched.
    assert (await load_settings()).monthly_limit_usd == 30.0
    await save_settings(hard_stop=False, warn_threshold_pct=50)
    settings = await load_settings()
    assert settings.hard_stop is False
    assert settings.warn_threshold_pct == 50
    assert settings.daily_limit_usd == 2.0


# --- estimation -------------------------------------------------------------


async def test_estimate_falls_back_without_history(db):
    assert await estimate_cost("deep") == FALLBACK_ESTIMATES["deep"]
    assert await estimate_cost("quick") == FALLBACK_ESTIMATES["quick"]


async def test_estimate_uses_history_at_p75(db):
    for amount in (0.10, 0.12, 0.20, 0.50):
        await _spend(amount, mode="council")
    est = await estimate_cost("council")
    # p75 of the sample, not the mean and not the max.
    assert 0.19 <= est <= 0.51
    assert est > 0.12


async def test_estimate_ignores_other_modes_and_failures(db):
    for _ in range(5):
        await _spend(9.0, mode="deep")
        await _spend(9.0, mode="council", status="failed")
    # Council has no completed history of its own -> fallback, unpolluted.
    assert await estimate_cost("council") == FALLBACK_ESTIMATES["council"]


async def test_cheap_history_cannot_make_the_estimate_reckless(db):
    """A run of unusually cheap requests must not drive the estimate to ~0."""
    for _ in range(10):
        await _spend(0.0001, mode="deep")
    assert await estimate_cost("deep") >= FALLBACK_ESTIMATES["deep"] * 0.5


# --- the forward affordability check ----------------------------------------


async def test_allowed_when_estimate_fits(db):
    await save_settings(daily_limit_usd=1.0, monthly_limit_usd=10.0)
    decision = await check_affordable("council")
    assert decision.allowed
    assert decision.reason is None


async def test_refused_when_estimate_does_not_fit_even_though_under_ceiling(db):
    """The point of the forward check: spend is UNDER the ceiling, but not by
    enough to complete this request, so it is refused rather than started."""
    await save_settings(daily_limit_usd=1.0, monthly_limit_usd=100.0)
    await _spend(0.90)  # under the $1 ceiling...
    decision = await check_affordable("deep")  # ...but deep needs ~$0.25
    assert decision.allowed is False
    assert decision.remaining_usd == pytest.approx(0.10, abs=0.001)
    assert "cannot finish within budget" in decision.reason


async def test_a_cheaper_mode_can_still_run_when_an_expensive_one_cannot(db):
    await save_settings(daily_limit_usd=1.0, monthly_limit_usd=100.0)
    await _spend(0.95)
    assert (await check_affordable("deep")).allowed is False
    assert (await check_affordable("quick")).allowed is True


async def test_monthly_ceiling_binds_independently_of_daily(db):
    await save_settings(daily_limit_usd=100.0, monthly_limit_usd=1.0)
    await _spend(0.95)
    decision = await check_affordable("deep")
    assert decision.allowed is False
    assert "month" in decision.reason


async def test_hard_stop_disabled_warns_but_allows(db):
    await save_settings(daily_limit_usd=1.0, hard_stop=False)
    await _spend(0.99)
    decision = await check_affordable("deep")
    assert decision.allowed is True
    assert "cannot finish within budget" in decision.warning


async def test_warning_when_crossing_the_threshold(db):
    """The warning fires on spend PLUS this request crossing the threshold —
    $0.69 spent is below $0.70, but the request pushes it over."""
    await save_settings(daily_limit_usd=1.0, warn_threshold_pct=70)
    await _spend(0.69)
    decision = await check_affordable("quick")  # ~$0.02 estimate
    assert decision.allowed is True
    assert decision.warning is not None
    assert "approaching daily budget" in decision.warning


async def test_no_warning_when_the_request_stays_under_the_threshold(db):
    await save_settings(daily_limit_usd=1.0, warn_threshold_pct=70)
    await _spend(0.60)
    decision = await check_affordable("quick")  # 0.60 + 0.02 < 0.70
    assert decision.allowed is True
    assert decision.warning is None


async def test_no_warning_well_below_threshold(db):
    await save_settings(daily_limit_usd=100.0, monthly_limit_usd=1000.0)
    decision = await check_affordable("quick")
    assert decision.warning is None


async def test_zero_limits_mean_unlimited(db):
    await save_settings(daily_limit_usd=0, monthly_limit_usd=0)
    decision = await check_affordable("deep")
    assert decision.allowed is True
    assert decision.remaining_usd == float("inf")


# --- engine enforcement -----------------------------------------------------


async def test_engine_refuses_before_any_model_call(make_engine):
    """Refusal must happen before work starts — no provider calls, no
    orphan request row."""
    await save_settings(daily_limit_usd=0.5, monthly_limit_usd=100.0)
    await _spend(0.49)
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    with pytest.raises(BudgetRefused):
        await engine.run("q?", "deep")

    assert openai.calls == []
    assert anthropic.calls == []


async def test_request_in_flight_is_not_killed_by_a_ceiling_crossed_midway(make_engine):
    """The check runs once, at the start. A request that was affordable when
    it began completes even if it ends up crossing the ceiling."""
    await save_settings(daily_limit_usd=0.5, monthly_limit_usd=100.0)
    engine = _engine(make_engine)

    # Affordable at start (nothing spent yet). Mid-run, other spend lands.
    result = await engine.run("q?", "council")

    assert result["status"] == "complete"
    assert result["final_answer"] == "Final."


async def test_warning_is_recorded_in_the_trace(make_engine):
    await save_settings(daily_limit_usd=1.0, warn_threshold_pct=50)
    await _spend(0.60)
    engine = _engine(make_engine)

    result = await engine.run("q?", "council")

    warn = next(s for s in result["steps"] if s["stage"] == "budget_warning")
    assert "approaching" in warn["output"]["warning"]


async def test_unlimited_budget_does_not_interfere(make_engine):
    await save_settings(daily_limit_usd=0, monthly_limit_usd=0)
    engine = _engine(make_engine)
    result = await engine.run("q?", "council")
    assert result["status"] == "complete"


# --- API --------------------------------------------------------------------


async def test_budget_endpoints_round_trip(make_engine, monkeypatch):
    engine = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        r = client.put("/budget", json={"daily_limit_usd": 3.5, "warn_threshold_pct": 80})
        assert r.status_code == 200
        body = r.json()
        assert body["settings"]["daily_limit_usd"] == 3.5
        assert body["settings"]["warn_threshold_pct"] == 80
        assert body["remaining_today"] == pytest.approx(3.5)
        assert set(body["estimates"]) == {"quick", "council", "deep"}


async def test_ask_returns_402_when_refused(make_engine, monkeypatch):
    await save_settings(daily_limit_usd=0.5, monthly_limit_usd=100.0)
    await _spend(0.49)
    engine = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        r = client.post("/ask/async", json={"question": "q?", "mode": "deep"})
        assert r.status_code == 402
        assert "cannot finish within budget" in r.json()["detail"]["reason"]


async def test_refused_async_ask_creates_no_conversation(make_engine, monkeypatch):
    """A refusal must not leave an orphan conversation behind."""
    await save_settings(daily_limit_usd=0.5, monthly_limit_usd=100.0)
    await _spend(0.49)
    engine = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        client.post("/ask/async", json={"question": "q?", "mode": "deep"})
        assert client.get("/conversations").json()["items"] == []


async def test_snapshot_reflects_recorded_spend(db):
    await save_settings(daily_limit_usd=10.0, monthly_limit_usd=50.0)
    await _spend(1.25)
    await _spend(0.75)
    snapshot = await spend_snapshot()
    assert snapshot.today == pytest.approx(2.0)
    assert snapshot.remaining_today == pytest.approx(8.0)
    assert snapshot.remaining == pytest.approx(8.0)

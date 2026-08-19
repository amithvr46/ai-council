"""Spend budgets — real money, daily and monthly.

Distinct from engine/budget.py, which bounds the NUMBER of model calls in a
single request. This module bounds DOLLARS across time, and it is checked
before a request starts.

The rule that makes this different from a simple ceiling check: affordability
is checked FORWARD. Before starting work, the guard estimates what the work
will cost and refuses if that estimate does not fit in what remains. Work that
cannot be completed inside the budget is never knowingly started, so a request
is never killed at 80% having already spent the money.

A request already in flight is never interrupted by a ceiling crossed
mid-request — the check happens once, at the start.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from council.db.models import BudgetSettingsRow, Request
from council.db.session import session_scope

# Used until enough history exists to estimate from the user's own usage.
# Deliberately conservative: over-estimating refuses a borderline request,
# under-estimating starts work that cannot finish.
FALLBACK_ESTIMATES: dict[str, float] = {
    "quick": 0.02,
    "council": 0.08,
    "deep": 0.25,
}
MIN_SAMPLES_FOR_HISTORY = 3
HISTORY_WINDOW_DAYS = 30


@dataclass(frozen=True)
class BudgetSettings:
    daily_limit_usd: float = 3.0
    monthly_limit_usd: float = 30.0
    warn_threshold_pct: int = 70
    hard_stop: bool = True

    @property
    def enabled(self) -> bool:
        return self.daily_limit_usd > 0 or self.monthly_limit_usd > 0


@dataclass(frozen=True)
class SpendSnapshot:
    today: float
    month: float
    daily_limit: float
    monthly_limit: float

    @property
    def remaining_today(self) -> float:
        return max(0.0, self.daily_limit - self.today) if self.daily_limit > 0 else float("inf")

    @property
    def remaining_month(self) -> float:
        return (
            max(0.0, self.monthly_limit - self.month) if self.monthly_limit > 0 else float("inf")
        )

    @property
    def remaining(self) -> float:
        return min(self.remaining_today, self.remaining_month)


@dataclass(frozen=True)
class SpendDecision:
    allowed: bool
    estimate_usd: float
    remaining_usd: float
    snapshot: SpendSnapshot
    warning: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        def num(x: float) -> float | None:
            return None if x == float("inf") else round(x, 4)

        return {
            "allowed": self.allowed,
            "estimate_usd": round(self.estimate_usd, 4),
            "remaining_usd": num(self.remaining_usd),
            "spent_today": round(self.snapshot.today, 4),
            "spent_month": round(self.snapshot.month, 4),
            "daily_limit": self.snapshot.daily_limit or None,
            "monthly_limit": self.snapshot.monthly_limit or None,
            "warning": self.warning,
            "reason": self.reason,
        }


class BudgetRefused(RuntimeError):
    """Raised instead of starting work that cannot fit the remaining budget."""

    def __init__(self, decision: SpendDecision):
        self.decision = decision
        super().__init__(decision.reason or "refused by spend budget")


async def load_settings() -> BudgetSettings:
    async with session_scope() as s:
        row = await s.get(BudgetSettingsRow, 1)
        if row is None:
            return BudgetSettings()
        return BudgetSettings(
            daily_limit_usd=row.daily_limit_usd,
            monthly_limit_usd=row.monthly_limit_usd,
            warn_threshold_pct=row.warn_threshold_pct,
            hard_stop=row.hard_stop,
        )


async def save_settings(**changes) -> BudgetSettings:
    async with session_scope() as s:
        row = await s.get(BudgetSettingsRow, 1)
        if row is None:
            row = BudgetSettingsRow(id=1)
            s.add(row)
            await s.flush()
        for key, value in changes.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
    return await load_settings()


async def spend_snapshot(settings: BudgetSettings | None = None) -> SpendSnapshot:
    settings = settings or await load_settings()
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)

    async with session_scope() as s:
        today = (
            await s.execute(
                select(func.coalesce(func.sum(Request.total_cost_usd), 0.0)).where(
                    Request.created_at >= day_start
                )
            )
        ).scalar_one()
        month = (
            await s.execute(
                select(func.coalesce(func.sum(Request.total_cost_usd), 0.0)).where(
                    Request.created_at >= month_start
                )
            )
        ).scalar_one()

    return SpendSnapshot(
        today=float(today),
        month=float(month),
        daily_limit=settings.daily_limit_usd,
        monthly_limit=settings.monthly_limit_usd,
    )


async def estimate_cost(mode: str) -> float:
    """Estimate what one request in `mode` will cost.

    Uses the user's own recent history at the 75th percentile — conservative
    on purpose, since under-estimating starts work that cannot finish. Falls
    back to a constant until enough samples exist.
    """
    from datetime import timedelta

    since = datetime.now(UTC) - timedelta(days=HISTORY_WINDOW_DAYS)
    async with session_scope() as s:
        costs = (
            (
                await s.execute(
                    select(Request.total_cost_usd).where(
                        Request.mode == mode,
                        Request.status == "complete",
                        Request.created_at >= since,
                        Request.total_cost_usd > 0,
                    )
                )
            )
            .scalars()
            .all()
        )

    if len(costs) < MIN_SAMPLES_FOR_HISTORY:
        return FALLBACK_ESTIMATES.get(mode, 0.25)

    ordered = sorted(float(c) for c in costs)
    # p75 by nearest-rank; never below the fallback floor for the mode, so a
    # run of unusually cheap requests cannot make the estimate reckless.
    index = max(0, min(len(ordered) - 1, int(round(0.75 * len(ordered))) - 1))
    return max(ordered[index], FALLBACK_ESTIMATES.get(mode, 0.0) * 0.5)


async def incremental_estimate(from_mode: str, to_mode: str) -> float:
    """What ESCALATING costs, not what a fresh run of `to_mode` costs.

    Council has already paid for its candidates and check; charging the full
    deep estimate against the remaining budget would refuse escalations that
    are comfortably affordable. Floored at a small positive value so a noisy
    history cannot make escalation look free.
    """
    richer = await estimate_cost(to_mode)
    already = await estimate_cost(from_mode)
    return max(richer - already, FALLBACK_ESTIMATES[to_mode] * 0.25)


async def check_affordable(mode: str, *, estimate: float | None = None) -> SpendDecision:
    """Forward affordability check, run BEFORE work starts."""
    settings = await load_settings()
    snapshot = await spend_snapshot(settings)
    est = estimate if estimate is not None else await estimate_cost(mode)

    if not settings.enabled:
        return SpendDecision(True, est, float("inf"), snapshot)

    remaining = snapshot.remaining
    warning = None

    # Warn when this request would push spend past the warn threshold.
    for label, spent, limit in (
        ("daily", snapshot.today, snapshot.daily_limit),
        ("monthly", snapshot.month, snapshot.monthly_limit),
    ):
        if limit > 0 and (spent + est) >= limit * (settings.warn_threshold_pct / 100):
            warning = (
                f"approaching {label} budget: ${spent + est:.2f} of ${limit:.2f} "
                f"after this request"
            )

    if est > remaining:
        reason = (
            f"{mode} mode is estimated at ${est:.2f} but only ${remaining:.2f} remains "
            f"(today ${snapshot.today:.2f}/${snapshot.daily_limit:.2f}, "
            f"month ${snapshot.month:.2f}/${snapshot.monthly_limit:.2f}). "
            f"Not starting work that cannot finish within budget."
        )
        if settings.hard_stop:
            return SpendDecision(False, est, remaining, snapshot, warning, reason)
        # hard_stop off: warn only, proceed.
        return SpendDecision(True, est, remaining, snapshot, reason, None)

    return SpendDecision(True, est, remaining, snapshot, warning)

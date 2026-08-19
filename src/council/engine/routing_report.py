"""Routing measurement — the half of Auto that keeps it honest.

Rung 3's features are seeds, not truths. This report is how they get corrected
by measurement rather than by argument, and it is what Rung 4 (3C) will consume
once there is enough real history to trust.

Two rules are enforced here rather than left to the caller:

  1. **Only real usage counts.** Synthetic rows can never contaminate routing
     statistics, and controlled benchmark runs are reported separately from
     organic work — a deliberate eval sweep is not evidence of how the system
     behaves on real tasks.
  2. **Absence of evidence is not permission to invent it.** A bucket below the
     sample threshold reports `ready=False` and no recommendation at all. It
     does not get a guess dressed up as a finding.
"""

from dataclasses import dataclass, field

from sqlalchemy import select

from council.db.models import Request, Step
from council.db.session import session_scope
from council.engine.routing import MIN_SAMPLES, ClassStats, quick_eligible

REAL = "real"
EVAL = "eval"
SYNTHETIC = "synthetic"


@dataclass
class BucketReport:
    outcome_kind: str
    mode: str
    samples: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    ratings: list[int] = field(default_factory=list)
    agreements: int = 0
    checked: int = 0  # requests where an agreement verdict exists
    evidence_overrides: int = 0
    degraded: int = 0

    @property
    def mean_cost(self) -> float:
        return self.cost_usd / self.samples if self.samples else 0.0

    @property
    def mean_latency_ms(self) -> int:
        return int(self.latency_ms / self.samples) if self.samples else 0

    @property
    def mean_rating(self) -> float | None:
        return sum(self.ratings) / len(self.ratings) if self.ratings else None

    @property
    def agreement_rate(self) -> float | None:
        """None, not 0.0, when nothing was ever checked.

        Quick runs produce no agreement verdict at all — there is only one
        model, so there is nothing to agree with. Reporting that as 0% would
        invent a finding out of a structural absence.
        """
        return self.agreements / self.checked if self.checked else None

    @property
    def evidence_override_rate(self) -> float | None:
        return self.evidence_overrides / self.samples if self.samples else None

    @property
    def ready(self) -> bool:
        return self.samples >= MIN_SAMPLES

    def as_dict(self) -> dict:
        return {
            "outcome_kind": self.outcome_kind,
            "mode": self.mode,
            "samples": self.samples,
            "ready": self.ready,
            "mean_cost_usd": round(self.mean_cost, 4),
            "mean_latency_ms": self.mean_latency_ms,
            "mean_rating": round(self.mean_rating, 2) if self.mean_rating else None,
            "agreement_rate": (
                round(self.agreement_rate, 3) if self.agreement_rate is not None else None
            ),
            "evidence_override_rate": (
                round(self.evidence_override_rate, 3)
                if self.evidence_override_rate is not None
                else None
            ),
            "degraded": self.degraded,
        }


async def collect(data_class: str = REAL) -> list[BucketReport]:
    """Aggregate completed requests by (outcome_kind, mode) for ONE population.

    The data_class filter is the point of this function. Mixing synthetic rows
    into routing statistics would let a test fixture change production
    behaviour, and mixing eval sweeps into organic history would let a
    deliberate benchmark masquerade as real usage.
    """
    buckets: dict[tuple[str, str], BucketReport] = {}

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Request).where(
                        Request.data_class == data_class,
                        Request.status == "complete",
                    )
                )
            )
            .scalars()
            .all()
        )
        request_ids = [r.id for r in rows]

        # One query for the agreement verdicts rather than one per request.
        agreement_by_request: dict[str, str] = {}
        if request_ids:
            steps = (
                (
                    await session.execute(
                        select(Step).where(
                            Step.request_id.in_(request_ids),
                            Step.stage == "combined_check",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for step in steps:
                verdict = (step.output or {}).get("agreement")
                if verdict:
                    agreement_by_request[step.request_id] = verdict

        for row in rows:
            key = (row.outcome_kind or "general", row.mode)
            bucket = buckets.setdefault(key, BucketReport(*key))
            bucket.samples += 1
            bucket.cost_usd += row.total_cost_usd or 0.0
            bucket.latency_ms += row.latency_ms or 0
            if row.user_rating:
                bucket.ratings.append(row.user_rating)
            if row.evidence_override:
                bucket.evidence_overrides += 1
            if row.degraded:
                bucket.degraded += 1
            verdict = agreement_by_request.get(row.id)
            if verdict is not None:
                bucket.checked += 1
                if verdict == "agree":
                    bucket.agreements += 1

    return sorted(buckets.values(), key=lambda b: (b.outcome_kind, b.mode))


def recommendation(bucket: BucketReport, *, low_risk: bool = False) -> str:
    """What Auto would conclude from this bucket today, and why.

    Deliberately conservative and deliberately boring: below the threshold it
    says so and stops. It never converts thin data into a routing opinion.
    """
    if not bucket.ready:
        return f"not enough data ({bucket.samples}/{MIN_SAMPLES}) — deterministic routing stands"
    if bucket.agreement_rate is None:
        return "no agreement verdicts in this bucket — nothing to learn from yet"

    stats = ClassStats(
        samples=bucket.samples,
        agreement_rate=bucket.agreement_rate,
        evidence_override_rate=bucket.evidence_override_rate or 0.0,
        mean_rating=bucket.mean_rating,
        low_risk=low_risk,
    )
    eligible, why = quick_eligible(stats, {})
    return f"quick eligible: {why}" if eligible else f"keep current routing — {why}"


def render(buckets: list[BucketReport], data_class: str = REAL) -> str:
    """Plain-text report."""
    if not buckets:
        return (
            f"No completed {data_class} requests yet. Routing runs on "
            "deterministic features until real usage accumulates."
        )
    lines = [f"Routing report — population: {data_class}", ""]
    for bucket in buckets:
        agreement = (
            f"{bucket.agreement_rate:.0%}" if bucket.agreement_rate is not None else "n/a"
        )
        override = (
            f"{bucket.evidence_override_rate:.0%}"
            if bucket.evidence_override_rate is not None
            else "n/a"
        )
        rating = f"{bucket.mean_rating:.1f}" if bucket.mean_rating else "unrated"
        lines.append(f"{bucket.outcome_kind} / {bucket.mode}")
        lines.append(
            f"  n={bucket.samples}  cost=${bucket.mean_cost:.4f}  "
            f"latency={bucket.mean_latency_ms}ms  rating={rating}"
        )
        lines.append(
            f"  agreement={agreement}  evidence_override={override}  "
            f"degraded={bucket.degraded}"
        )
        lines.append(f"  -> {recommendation(bucket)}")
        lines.append("")
    return "\n".join(lines)

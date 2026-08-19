"""Per-mode model-call budgets, enforced in code — not prompts.

Accounting is two-level, and both levels are recorded:

- LOGICAL GENERATIONS (what these budgets bound): one orchestration-level
  generate() per stage. Stored per request as `model_calls`.
- PHYSICAL API ATTEMPTS: actual provider invocations. A logical generation
  makes at most 2 (the single malformed-output retry), so physical attempts
  are hard-bounded at 2x the mode budget. Stored per step as `api_attempts`
  and per request as `total_api_attempts` — retries are never hidden.

The budget exists so no code path, present or future, can loop: if a stage
would exceed the mode budget the engine raises instead of calling.
"""

MODE_BUDGETS: dict[str, int] = {
    "quick": 2,  # 1 primary + 1 visible failover to the other provider
    "council": 5,  # 2 candidates + combined check + (synthesis | judge) + headroom
    # deep worst case: 2 candidates + check + evidence plan + evidence assess
    # + 2 critiques + judge + verifier + revision = 10, plus 1 headroom.
    "deep": 11,
}

# Stages a deep run still needs AFTER the combined check has run: evidence
# plan, evidence assessment, judge or synthesis, verifier — plus one revision.
# Escalation refuses unless this many calls remain inside deep's ceiling, so it
# never starts work the ceiling cannot finish.
MIN_DEEP_STAGES_AFTER_CHECK = 5

# Evidence tool invocations are NOT model calls and are budgeted separately
# (see Settings.max_web_searches / max_code_executions), but they are still
# hard-capped so no request can fan out indefinitely.


class BudgetExceeded(RuntimeError):
    def __init__(self, mode: str, attempted_stage: str, spent: int):
        self.mode = mode
        self.attempted_stage = attempted_stage
        self.spent = spent
        super().__init__(
            f"Call budget for mode {mode!r} ({MODE_BUDGETS[mode]}) exhausted "
            f"after {spent} calls; refused stage {attempted_stage!r}"
        )


class CeilingLowered(RuntimeError):
    """Refused: a consumed budget may never be reopened or reduced."""


class BudgetTracker:
    """The single authority on what one request may still spend.

    Tracks three things, all of which must survive an escalation: logical model
    generations, physical API attempts and dollars. Escalation raises the
    CEILING; it never resets what has already been consumed.
    """

    def __init__(self, mode: str):
        if mode not in MODE_BUDGETS:
            raise ValueError(f"Unknown mode: {mode!r}")
        self.mode = mode
        self.original_mode = mode
        self.limit = MODE_BUDGETS[mode]
        self.spent = 0
        self.api_attempts = 0
        self.cost_usd = 0.0
        self.escalations = 0

    def spend(self, stage: str) -> None:
        """Reserve one model call for `stage` or raise BudgetExceeded."""
        if self.spent + 1 > self.limit:
            raise BudgetExceeded(self.mode, stage, self.spent)
        self.spent += 1

    def record(self, *, api_attempts: int = 0, cost_usd: float = 0.0) -> None:
        """Account for a completed generation. Monotonic by construction —
        there is no way to decrease these from outside."""
        self.api_attempts += max(0, api_attempts)
        self.cost_usd += max(0.0, cost_usd)

    @property
    def remaining(self) -> int:
        return self.limit - self.spent

    def raise_ceiling(self, mode: str) -> None:
        """Move to a larger ceiling mid-flight, keeping everything spent.

        The only mutation of `limit` that exists, and it refuses to go
        downwards — so a consumed budget cannot be reopened, reset or quietly
        widened in the wrong direction by a future caller. `spent`,
        `api_attempts` and `cost_usd` are untouched: deep does not receive a
        fresh allowance, it inherits council's consumption.
        """
        if mode not in MODE_BUDGETS:
            raise ValueError(f"Unknown mode: {mode!r}")
        new_limit = MODE_BUDGETS[mode]
        if new_limit <= self.limit:
            raise CeilingLowered(
                f"refusing to move the ceiling from {self.mode} ({self.limit}) to "
                f"{mode} ({new_limit}): a consumed budget is never reopened"
            )
        self.mode = mode
        self.limit = new_limit
        self.escalations += 1

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "original_mode": self.original_mode,
            "limit": self.limit,
            "spent": self.spent,
            "remaining": self.remaining,
            "api_attempts": self.api_attempts,
            "cost_usd": round(self.cost_usd, 6),
            "escalations": self.escalations,
        }

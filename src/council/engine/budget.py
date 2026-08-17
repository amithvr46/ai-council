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
    "deep": 9,  # + critique round, verifier and one revision (max observed path: 8)
}


class BudgetExceeded(RuntimeError):
    def __init__(self, mode: str, attempted_stage: str, spent: int):
        self.mode = mode
        self.attempted_stage = attempted_stage
        self.spent = spent
        super().__init__(
            f"Call budget for mode {mode!r} ({MODE_BUDGETS[mode]}) exhausted "
            f"after {spent} calls; refused stage {attempted_stage!r}"
        )


class BudgetTracker:
    def __init__(self, mode: str):
        if mode not in MODE_BUDGETS:
            raise ValueError(f"Unknown mode: {mode!r}")
        self.mode = mode
        self.limit = MODE_BUDGETS[mode]
        self.spent = 0

    def spend(self, stage: str) -> None:
        """Reserve one model call for `stage` or raise BudgetExceeded."""
        if self.spent + 1 > self.limit:
            raise BudgetExceeded(self.mode, stage, self.spent)
        self.spent += 1

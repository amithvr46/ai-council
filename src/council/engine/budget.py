"""Per-mode model-call budgets, enforced in code — not prompts.

A "call" is one logical generation (the provider-internal malformed-output
retry belongs to the same logical call). The budget exists so no code path,
present or future, can loop: if a stage would exceed the mode budget the
engine raises instead of calling.
"""

MODE_BUDGETS: dict[str, int] = {
    "quick": 1,
    "council": 5,  # 2 candidates + combined check + synthesis + headroom for M2 judge
    "deep": 9,  # adds critique round, verifier and one revision in later milestones
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

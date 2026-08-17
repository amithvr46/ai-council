import pytest

from council.engine.budget import MODE_BUDGETS, BudgetExceeded, BudgetTracker


def test_budget_enforced():
    b = BudgetTracker("quick")
    b.spend("candidate")
    with pytest.raises(BudgetExceeded):
        b.spend("anything_else")


def test_council_budget_allows_v1_path():
    b = BudgetTracker("council")
    for stage in ["candidate_a", "candidate_b", "combined_check", "synthesis"]:
        b.spend(stage)
    assert b.spent == 4 <= MODE_BUDGETS["council"]


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        BudgetTracker("infinite")


def test_budget_error_names_stage():
    b = BudgetTracker("quick")
    b.spend("candidate")
    with pytest.raises(BudgetExceeded) as e:
        b.spend("judge")
    assert "judge" in str(e.value)

import pytest

from clearagent.builds.budgets import BudgetLimits, BudgetTracker, BuildBudgetExceeded


@pytest.mark.parametrize(
    "limits",
    [
        BudgetLimits(10, 10, 0, 100, 10),
        BudgetLimits(10, 10, 10, 0, 10),
        BudgetLimits(10, 10, 10, 100, 0),
    ],
)
def test_response_crossing_a_limit_remains_in_observed_consumption(limits):
    tracker = BudgetTracker(limits)
    with pytest.raises(BuildBudgetExceeded):
        tracker.record(total_tokens=7, cost_usd=0.25)
    assert (tracker.calls, tracker.total_tokens, tracker.cost_usd) == (1, 7, 0.25)

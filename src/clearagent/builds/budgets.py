from __future__ import annotations

from dataclasses import dataclass
import threading


class BuildBudgetExceeded(RuntimeError):
    pass

@dataclass(frozen=True)
class BudgetLimits:
    gepa_max_tokens: int
    task_max_tokens: int
    max_model_calls: int
    max_total_tokens: int
    max_cost_usd: float


BUDGET_LIMITS = {
    "quick": BudgetLimits(gepa_max_tokens=2_000, task_max_tokens=2_000, max_model_calls=400, max_total_tokens=1_000_000, max_cost_usd=1.50),
    "standard": BudgetLimits(gepa_max_tokens=4_000, task_max_tokens=4_000, max_model_calls=800, max_total_tokens=2_000_000, max_cost_usd=4.00),
    "deep": BudgetLimits(gepa_max_tokens=8_000, task_max_tokens=8_000, max_model_calls=1_600, max_total_tokens=4_000_000, max_cost_usd=10.00),
}


@dataclass
class BudgetTracker:
    limits: BudgetLimits
    calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def record(self, *, total_tokens: int, cost_usd: float) -> None:
        with self._lock:
            next_calls = self.calls + 1
            next_tokens = self.total_tokens + total_tokens
            next_cost = self.cost_usd + cost_usd
            if next_calls > self.limits.max_model_calls:
                raise BuildBudgetExceeded("The selected build level reached its model-call limit.")
            if next_tokens > self.limits.max_total_tokens:
                raise BuildBudgetExceeded("The selected build level reached its token limit.")
            if next_cost > self.limits.max_cost_usd:
                raise BuildBudgetExceeded("The selected build level reached its cost limit.")
            self.calls = next_calls
            self.total_tokens = next_tokens
            self.cost_usd = next_cost

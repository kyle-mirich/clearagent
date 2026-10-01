from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import threading
from typing import Any, Callable


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
            # This response has already consumed resources, even if it crosses
            # a limit. Keep the complete observed consumption before raising.
            self.calls = next_calls
            self.total_tokens = next_tokens
            self.cost_usd = next_cost
            if next_calls > self.limits.max_model_calls:
                raise BuildBudgetExceeded("The selected build level reached its model-call limit.")
            if next_tokens > self.limits.max_total_tokens:
                raise BuildBudgetExceeded("The selected build level reached its token limit.")
            if next_cost > self.limits.max_cost_usd:
                raise BuildBudgetExceeded("The selected build level reached its cost limit.")


def _nonnegative_integer(value: int, name: str, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}.")


def _finite_cost(value: float, name: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite nonnegative number.")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number.")
    return Fraction(str(value))


@dataclass(frozen=True)
class RequestBudget:
    """Aggregate upper bounds for one invocation, including all SDK retries.

    Tokens and dollars cover every one of max_model_calls, not one attempt.
    The caller must know adapter retry settings and the full provider payload.
    """

    max_total_tokens: int
    max_cost_usd: float
    max_model_calls: int = 1

    def __post_init__(self) -> None:
        _nonnegative_integer(self.max_total_tokens, "max_total_tokens")
        _nonnegative_integer(self.max_model_calls, "max_model_calls", minimum=1)
        _finite_cost(self.max_cost_usd, "max_cost_usd")


class PreflightBudget:
    """Atomically reserve caller-supplied bounds before every provider attempt.

    Reservations are never refunded from a response: SDK retries, failures,
    cancellation and unknown usage can omit billed consumption. This bounds
    calls only to the extent that the caller's request bounds are trustworthy;
    it does not infer provider prices or impose limits on unrelated app calls.
    """

    def __init__(
        self,
        limits: BudgetLimits,
        request_bound: Callable[[Any], RequestBudget | None],
    ) -> None:
        _nonnegative_integer(limits.max_model_calls, "max_model_calls")
        _nonnegative_integer(limits.max_total_tokens, "max_total_tokens")
        self._cost_limit = _finite_cost(limits.max_cost_usd, "max_cost_usd")
        self._limits = limits
        self._request_bound = request_bound
        self._calls = 0
        self._tokens = 0
        self._cost = Fraction(0)
        self._lock = threading.Lock()

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return self._tokens

    @property
    def cost_usd(self) -> float:
        with self._lock:
            return float(self._cost)

    def reserve(self, request: Any) -> None:
        bound = self._request_bound(request)
        if not isinstance(bound, RequestBudget):
            raise BuildBudgetExceeded("No trustworthy budget bound is available for this request.")
        cost = _finite_cost(bound.max_cost_usd, "max_cost_usd")
        with self._lock:
            calls = self._calls + bound.max_model_calls
            tokens = self._tokens + bound.max_total_tokens
            reserved_cost = self._cost + cost
            if calls > self.limits.max_model_calls:
                raise BuildBudgetExceeded("The next provider attempt would exceed the model-call limit.")
            if tokens > self.limits.max_total_tokens:
                raise BuildBudgetExceeded("The next provider attempt would exceed the token limit.")
            if reserved_cost > self._cost_limit:
                raise BuildBudgetExceeded("The next provider attempt would exceed the cost limit.")
            self._calls = calls
            self._tokens = tokens
            self._cost = reserved_cost

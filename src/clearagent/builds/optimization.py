from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from gepa.core.callbacks import GEPACallback
    from gepa.proposer.reflective_mutation.base import LanguageModel


METRIC_CALL_BUDGETS = {"quick": 24, "standard": 60, "deep": 120}


@dataclass(frozen=True)
class PromptOptimizationResult:
    instruction: str
    validation_score: float
    candidate_count: int
    metric_calls: int


class GepaProgressCallback:
    def __init__(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self._emit = emit
        self.candidate_count = 1
        self.metric_calls = 0
        self.best_score = 0.0

    def on_optimization_start(self, event: dict[str, Any]) -> None:
        self._emit(
            "gepa_started",
            {
                "train_count": event["trainset_size"],
                "validation_count": event["valset_size"],
            },
        )

    def on_candidate_accepted(self, event: dict[str, Any]) -> None:
        self.candidate_count += 1
        self._emit(
            "gepa_candidate_accepted",
            {
                "iteration": event["iteration"],
                "candidate_index": event["new_candidate_idx"],
                # GEPA reports the accepted minibatch's score total here, not
                # the normalized validation score emitted by on_valset_evaluated.
                "minibatch_score_total": float(event["new_score"]),
            },
        )

    def on_valset_evaluated(self, event: dict[str, Any]) -> None:
        score = float(event["average_score"])
        self.best_score = max(self.best_score, score)
        self._emit(
            "gepa_validation_completed",
            {
                "iteration": event["iteration"],
                "candidate_index": event["candidate_idx"],
                "score": score,
                "is_best": bool(event["is_best_program"]),
            },
        )

    def on_budget_updated(self, event: dict[str, Any]) -> None:
        self.metric_calls = int(event["metric_calls_used"])

    def on_optimization_end(self, event: dict[str, Any]) -> None:
        self.metric_calls = int(event["total_metric_calls"])
        self._emit(
            "gepa_completed",
            {
                "iterations": int(event["total_iterations"]),
                "metric_calls": self.metric_calls,
                "best_candidate_index": int(event["best_candidate_idx"]),
            },
        )


class _NullLogger:
    def log(self, message: str) -> None:
        _ = message


def optimize_prompt(
    *,
    seed_instruction: str,
    trainset: list[dict[str, Any]],
    valset: list[dict[str, Any]],
    objective: str,
    background: str,
    evaluator: Callable[[str, dict[str, Any]], tuple[float, dict[str, Any]]],
    reflection_lm: Callable[[str | list[dict[str, Any]]], str],
    profile: str,
    seed: int,
    max_workers: int,
    on_event: Callable[[str, dict[str, Any]], None],
) -> PromptOptimizationResult:
    from gepa.optimize_anything import (
        EngineConfig,
        GEPAConfig,
        ReflectionConfig,
        TrackingConfig,
        optimize_anything,
    )

    callback = GepaProgressCallback(on_event)
    result = optimize_anything(
        seed_candidate=seed_instruction,
        evaluator=evaluator,
        dataset=trainset,
        valset=valset,
        objective=objective,
        background=background,
        config=GEPAConfig(
            engine=EngineConfig(
                max_metric_calls=METRIC_CALL_BUDGETS.get(profile, METRIC_CALL_BUDGETS["standard"]),
                max_workers=max(1, max_workers),
                parallel=max_workers > 1,
                seed=seed,
                display_progress_bar=False,
                use_cloudpickle=False,
                track_best_outputs=True,
            ),
            reflection=ReflectionConfig(
                reflection_lm=cast("LanguageModel", reflection_lm),
                reflection_minibatch_size=3,
            ),
            tracking=TrackingConfig(logger=_NullLogger()),
            callbacks=[cast("GEPACallback", callback)],
        ),
    )
    scores = list(getattr(result, "val_aggregate_scores", []) or [])
    best_index = int(getattr(result, "best_idx", 0))
    best_score = float(scores[best_index]) if best_index < len(scores) else callback.best_score
    return PromptOptimizationResult(
        instruction=str(result.best_candidate),
        validation_score=round(best_score, 4),
        candidate_count=max(callback.candidate_count, len(scores)),
        metric_calls=callback.metric_calls,
    )

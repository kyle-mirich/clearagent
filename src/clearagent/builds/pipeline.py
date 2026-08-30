from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import json
import re
import threading
import time
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, ValidationError, model_validator

from clearagent.runtime.messages import Message
from clearagent.agent import Agent
from clearagent.runtime.checks import run_checks
from clearagent.runtime.providers.base import ProviderError
from clearagent.runtime.providers.model_uri import parse_model_uri
from clearagent.runtime.providers.registry import provider_for_model
from clearagent.builds.datasets import (
    dataset_split_counts,
    generate_synthetic_examples,
    validate_synthetic_dataset,
)
from clearagent.builds.admission import (
    MIN_HOLDOUT_PASS_RATE,
    MIN_REQUIRED_BEHAVIOR_PASS_RATE,
    candidate_is_eligible,
)
from clearagent.builds.budgets import BudgetTracker
from clearagent.store import Store, _now
from clearagent.storage.redaction import redact
from clearagent.builds.scoring import CandidateEvaluation, CaseJudgment
from clearagent.builds.optimization import METRIC_CALL_BUDGETS, optimize_prompt
from clearagent.builds.planner import plan_task
from clearagent.builds.quality import apply_agent_prd
from clearagent.builds.task_spec import (
    AgentPRD,
    ClarificationQuestion,
    PlanningResult,
    RubricDimension,
    TaskSpec,
)
from clearagent.runtime.contracts import (
    RUNTIME_CONSTRAINTS,
    RUNTIME_FAILURE_MODES,
    RUNTIME_INPUT_SCHEMA,
    RUNTIME_OUTPUT_SCHEMA,
    build_runtime_messages,
    clean_runtime_instruction,
)
from clearagent.runtime.types import RunResult


@dataclass(frozen=True)
class PipelineSettings:
    deterministic_mode: bool = False
    planner_model: str = "openai:gpt-5.6-luna"
    synthetic_model: str = "openai:gpt-5.6-luna"
    task_model: str = "openai:gpt-5.6-luna"
    judge_model: str = "openai:gpt-5.6-luna"
    reflection_model: str = "openai:gpt-5.6-luna"
    openrouter_api_key: str | None = None
    gepa_max_tokens: int = 4000
    task_max_tokens: int = 4000
    max_concurrency: int = 4
    reasoning_effort: str = "none"
    provider_sort: str = "throughput"
    promotion_margin: float = 0.03
    debug: bool = False
    budget_tracker: BudgetTracker | None = None
    tool_registry: Mapping[str, Callable[..., Any]] | None = None
    on_model_call: Callable[[dict[str, Any]], None] | None = None


class _InstrumentedToolProvider:

    def __init__(self, provider: Any, settings: PipelineSettings, model_uri: str):
        self._provider = provider
        self._settings = settings
        self._model_uri = model_uri
        self._request_messages: list[Message] = []
        self.provider_name = provider.provider_name
        self.api_shape = provider.api_shape

    def build_request(self, **kwargs: Any) -> Any:
        self._request_messages = list(kwargs.get("messages", []))
        return self._provider.build_request(**kwargs)

    def complete(self, request: Any) -> Any:
        started_at = time.perf_counter()
        response = _complete_with_retry(self._provider, request)
        _record_model_call(
            self._settings,
            model_uri=self._model_uri,
            response=response,
            request_messages=self._request_messages,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            max_tokens=self._settings.task_max_tokens,
            purpose="task",
        )
        return response

    def stream_text(self, request: Any) -> Any:
        return self._provider.stream_text(request)


class GeneratedExample(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    expected: dict[str, Any]
    reference_notes: str = Field(min_length=10, max_length=1000)
    category: str = Field(min_length=1, max_length=200)
    difficulty: str = Field(default="medium", max_length=40)
    required_behavior_ids: list[str] = Field(default_factory=list, max_length=12)
    checks: list[dict[str, Any]] = Field(default_factory=list, max_length=8)


class GeneratedExampleBatch(BaseModel):
    examples: list[GeneratedExample] = Field(min_length=1, max_length=8)


SYNTHETIC_BATCH_SIZE = 6
# Generous output ceiling for dataset calls: the model stops when the batch is
# complete, so a high cap costs nothing but prevents truncated JSON from
# triggering repair rounds (the source of most generation failures).
SYNTHETIC_MAX_OUTPUT_TOKENS = 100_000
# Below this, a split's pass rate is too coarse to judge admission honestly.
MIN_CASES_PER_SPLIT = 2
# The UI-facing log reports dataset progress every N cases instead of per batch.
DATASET_PROGRESS_EVERY = 5


class CandidateOutput(BaseModel):
    answer: str = Field(min_length=1, max_length=16_000)


class DimensionJudgment(BaseModel):
    id: str
    score: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=5, max_length=240)
    failure_tags: list[str] = Field(default_factory=list, max_length=4)


class RequiredBehaviorJudgment(BaseModel):
    id: str
    passed: bool
    rationale: str = Field(min_length=5, max_length=240)


class CombinedJudgment(BaseModel):
    dimensions: list[DimensionJudgment] = Field(min_length=2, max_length=4)
    overall_reasoning: str = Field(min_length=10, max_length=500)
    required_behaviors: list[RequiredBehaviorJudgment] = Field(default_factory=list, max_length=12)


MIN_CLARIFICATION_QUESTIONS = 3
MAX_CLARIFICATION_QUESTIONS = 5


class ClarificationDecision(BaseModel):
    status: Literal["ready", "needs_clarification"]
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def require_questions_only_when_needed(self) -> "ClarificationDecision":
        if self.status == "needs_clarification" and len(self.questions) < MIN_CLARIFICATION_QUESTIONS:
            raise ValueError(
                "needs_clarification requires between "
                f"{MIN_CLARIFICATION_QUESTIONS} and {MAX_CLARIFICATION_QUESTIONS} questions"
            )
        if self.status == "ready" and self.questions:
            raise ValueError("ready clarification decision cannot include questions")
        return self


def _fallback_clarification_questions() -> list[ClarificationQuestion]:
    # Used when the planner model cannot return a parseable decision. Three
    # questions keep the clarification round inside the product minimum.
    return [
        ClarificationQuestion(
            id="audience_outcome",
            question="Who will use this agent, and what concrete outcome should it produce?",
            options=[
                "Software engineers who need concise technical documentation.",
                "Technical support staff who answer product questions.",
                "New users who need guided onboarding and clear next steps.",
            ],
        ),
        ClarificationQuestion(
            id="constraints",
            question="What facts, boundaries, or response requirements must it follow?",
            options=[
                "Use only attached sources and say when an answer is not documented.",
                "Lead with the direct answer and keep supporting detail concise.",
                "Ask one focused follow-up when the request is ambiguous.",
            ],
        ),
        ClarificationQuestion(
            id="tone_format",
            question="How should answers be formatted for this audience?",
            options=[
                "Short paragraphs with a friendly, plain-language tone.",
                "Numbered step-by-step instructions with no preamble.",
                "Compact bullet summaries that lead with the key answer.",
            ],
        ),
    ]


def plan_agent_brief(
    goal: str,
    settings: PipelineSettings,
    answers: dict[str, str] | None = None,
    planning_context: str | None = None,
) -> PlanningResult:
    if settings.deterministic_mode:
        return plan_task(goal, answers)
    _require_live_credentials(settings)
    answer_context = json.dumps(answers or {}, indent=2, sort_keys=True)
    if not answers:
        try:
            decision = _complete_structured(
                settings.planner_model,
                settings,
                [
                    Message(
                        role="system",
                        content=(
                            "Decide whether this agent brief has enough product detail to write a useful Agent PRD. "
                            "Return only JSON. When clarification would help, ask between three and five questions "
                            "(never fewer than three, never more than five) covering whatever would materially change "
                            "users, behavior, knowledge, boundaries, or success; return ready only when the brief "
                            "already pins those down. Do not ask about implementation details that ClearAgent can infer "
                            "conservatively. For every question, provide exactly three short, concrete, mutually "
                            "distinct suggested answer options. "
                            "Do not include an other/custom option; the interface adds that separately."
                        ),
                    ),
                    Message(role="user", content=planning_context or goal),
                ],
                ClarificationDecision,
                max_tokens=800,
            )
        except (json.JSONDecodeError, ValidationError):
            return PlanningResult(
                status="needs_clarification",
                questions=_fallback_clarification_questions(),
            )
        if decision.status == "needs_clarification":
            return PlanningResult(
                status="needs_clarification",
                questions=decision.questions,
            )
    try:
        prd = _complete_structured(
            settings.planner_model,
            settings,
            [
                Message(
                    role="system",
                    content=(
                        "Write the user-facing Agent PRD for a saved prompt-based agent. Return only the requested JSON. "
                        "The title is a short human product name. intended_users is a noun phrase naming real people, "
                        "never a task description. desired_outcome states the value the running agent provides, never a "
                        "build/create command. Include no more than 5 concise task-specific business rules, only genuine "
                        "runtime capabilities, no more than 4 boundaries, and 2-4 observable success criteria that also "
                        "reward correct clarification, unsupported-answer handling, and refusal. Return documents=[]; "
                        "ClearAgent attaches source metadata separately. Extract policy behavior from supplied knowledge "
                        "without copying long passages."
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        f"Agent brief and supplied knowledge:\n{planning_context or goal}\n\n"
                        f"Clarification answers:\n{answer_context}"
                    ),
                ),
            ],
            AgentPRD,
            max_tokens=settings.gepa_max_tokens,
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError("Planner could not produce a valid Agent PRD.") from exc
    task_spec = _compile_task_spec_from_prd(
        prd,
        planning_context=planning_context or goal,
    )
    return PlanningResult(
        status="ready",
        task_spec=task_spec,
        agent_prd=prd,
    )


def _compile_task_spec_from_prd(
    prd: AgentPRD,
    *,
    planning_context: str,
) -> TaskSpec:
    weight = 1 / len(prd.success_criteria)
    rubric = [
        RubricDimension(
            id=f"success_{index}",
            description=criterion,
            weight=(
                weight
                if index < len(prd.success_criteria)
                else 1 - weight * (len(prd.success_criteria) - 1)
            ),
        )
        for index, criterion in enumerate(prd.success_criteria, start=1)
    ]
    coverage = [
        "typical successful request",
        "ambiguous request requiring one clarification",
        "missing knowledge that must not be guessed",
        "out-of-scope or unsupported request",
        *[f"business rule: {rule}" for rule in prd.business_rules],
        *[f"boundary challenge: {boundary}" for boundary in prd.boundaries],
    ][:10]
    base = TaskSpec(
        name=prd.title,
        goal=prd.desired_outcome,
        objective=(
            f"Build a reliable agent for {prd.intended_users} that achieves: {prd.desired_outcome}"
        ),
        background=planning_context,
        input_schema=RUNTIME_INPUT_SCHEMA,
        output_schema=RUNTIME_OUTPUT_SCHEMA,
        rubric=rubric,
        constraints=list(prd.business_rules),
        failure_modes=list(prd.boundaries),
        synthetic_coverage_plan=coverage,
        seed_instruction=f"You are {prd.title}. {prd.desired_outcome}",
        agent_prd=prd,
    )
    return apply_agent_prd(base, prd)


def run_improvement_pipeline(
    store: Store,
    run_id: str,
    settings: PipelineSettings | None = None,
) -> None:
    pipeline_settings = settings or PipelineSettings()
    try:
        run = store.get_run(run_id)
    except Exception:
        # Without a run row there is nothing to fail cleanly; surface the error
        # to the worker so it can log and move on to the next queued run.
        raise
    try:
        _ensure_run_active(store, run_id)
        if not pipeline_settings.deterministic_mode:
            _require_live_credentials(pipeline_settings)
        project = store.get_project(run.project_id, owner_id=run.owner_id)

        supplied_model_call_recorder = pipeline_settings.on_model_call

        def record_model_call(payload: dict[str, Any]) -> None:
            if supplied_model_call_recorder:
                try:
                    supplied_model_call_recorder(payload)
                except Exception:
                    # Observability must never make an otherwise valid build fail.
                    pass
            stage = _model_call_stage(str(payload.get("purpose", "model")))
            _event(
                store,
                run_id,
                "model_call_completed",
                stage,
                f"{str(payload.get('purpose', 'Model')).capitalize()} model call completed.",
                payload,
            )

        pipeline_settings = replace(pipeline_settings, on_model_call=record_model_call)

        store.update_run(run_id, status="running", stage="planning", progress=0.05, started_at=_now())
        _event(store, run_id, "stage_started", "planning", "Planning the agent and its judges.")
        stored_spec = project.settings.get("task_spec")
        if stored_spec:
            task_spec_model = _canonicalize_task_spec(TaskSpec.model_validate(stored_spec), project.goal)
        else:
            planning = plan_agent_brief(project.goal, pipeline_settings)
            if planning.status == "needs_clarification":
                planning = plan_agent_brief(
                    project.goal,
                    pipeline_settings,
                    {"direct_api_default": "Proceed with conservative domain-neutral assumptions."},
                )
            if planning.task_spec is None:
                raise RuntimeError("Planner did not return a task specification.")
            task_spec_model = planning.task_spec
        task_spec = task_spec_model.model_dump()
        store.update_run(run_id, task_spec=task_spec)
        _event(
            store,
            run_id,
            "plan_ready",
            "planning",
            "Agent PRD, plan, and quality checks are ready.",
            {
                "name": task_spec["name"],
                "judges": task_spec["rubric"],
                "run_metadata": _run_metadata(run, pipeline_settings),
            },
        )

        _ensure_run_active(store, run_id)

        store.update_run(run_id, stage="generating_dataset", progress=0.18)
        _event(store, run_id, "stage_started", "generating_dataset", "Generating evaluation cases.")
        planned_cases = dataset_split_counts(run.budget_profile, run.dataset_size)[0]
        _event(
            store,
            run_id,
            "dataset_generation_started",
            "generating_dataset",
            f"Generating {planned_cases} synthetic examples.",
            {"planned_cases": planned_cases},
        )
        generated_state = {"count": 0}

        def _on_dataset_progress(completed_batches: int, total_batches: int, case_count: int) -> None:
            generated_state["count"] += case_count
            generated = generated_state["count"]
            previous = generated - case_count
            crossed_step = generated // DATASET_PROGRESS_EVERY > previous // DATASET_PROGRESS_EVERY
            # The final batch is reported by dataset_generation_finished below;
            # intermediate batches surface only on every-N crossings.
            if completed_batches < total_batches and crossed_step:
                _event(
                    store,
                    run_id,
                    "dataset_generation_progress",
                    "generating_dataset",
                    f"Generated {generated} of {planned_cases} synthetic examples.",
                    {"generated_cases": generated, "planned_cases": planned_cases},
                )

        def _on_batch_skipped(completed_batches: int, total_batches: int, error: str) -> None:
            _event(
                store,
                run_id,
                "dataset_batch_skipped",
                "generating_dataset",
                "A synthetic batch failed after retries; continuing without it.",
                {
                    "completed_batches": completed_batches,
                    "total_batches": total_batches,
                    "error": error,
                },
            )

        dataset = (
            generate_synthetic_examples(
                profile=run.budget_profile,
                seed=run.seed,
                task_spec=task_spec,
                n=run.dataset_size,
            )
            if pipeline_settings.deterministic_mode
            else _generate_dataset_live(
                profile=run.budget_profile,
                seed=run.seed,
                task_spec=task_spec,
                n=run.dataset_size,
                settings=pipeline_settings,
                on_batch_completed=_on_dataset_progress,
                on_batch_failed=_on_batch_skipped,
            )
        )
        _event(
            store,
            run_id,
            "dataset_generation_finished",
            "generating_dataset",
            f"Finished generating {len(dataset['examples'])} synthetic examples.",
            {
                "generated_cases": len(dataset["examples"]),
                "planned_cases": planned_cases,
            },
        )
        validate_synthetic_dataset(dataset)
        store.update_run(run_id, dataset=dataset)
        _event(
            store,
            run_id,
            "dataset_ready",
            "generating_dataset",
            "Evaluation cases are validated and split.",
            {"split_counts": dataset["split_counts"]},
        )

        _ensure_run_active(store, run_id)

        evaluator = PromptEvaluator(task_spec=task_spec, settings=pipeline_settings)
        seed_instruction = task_spec["seed_instruction"]
        validation_examples = _examples_for_split(dataset, "validation")
        train_examples = _examples_for_split(dataset, "train")
        test_examples = _examples_for_split(dataset, "test")

        store.update_run(run_id, stage="optimizing", progress=0.35)
        _event(
            store,
            run_id,
            "stage_started",
            "optimizing",
            "Optimizing agent instructions.",
            {"metric_call_budget": METRIC_CALL_BUDGETS[run.budget_profile]},
        )
        baseline_validation = evaluator.evaluate_instruction(
            seed_instruction,
            validation_examples,
            on_case_completed=lambda completed, total, judgment: _event(
                store,
                run_id,
                "baseline_case_completed",
                "optimizing",
                f"Evaluated baseline case {completed} of {total}.",
                {
                    "completed_cases": completed,
                    "total_cases": total,
                    "score": judgment.score,
                },
            ),
        )
        _ensure_run_active(store, run_id)
        seed_version_id = store.create_agent_version(
            project_id=run.project_id,
            run_id=run_id,
            kind="seed",
            instruction_text=seed_instruction,
            state={"instruction": seed_instruction, "task_spec_version": task_spec["version"]},
            validation_metrics=_evaluation_metrics(baseline_validation, "validation"),
            status="candidate",
        )

        optimization = optimize_prompt(
            seed_instruction=seed_instruction,
            trainset=train_examples,
            valset=validation_examples,
            objective=task_spec["objective"],
            background=task_spec["background"],
            evaluator=evaluator.evaluate_for_gepa,
            reflection_lm=_reflection_lm(pipeline_settings),
            profile=run.budget_profile,
            seed=run.seed,
            max_workers=pipeline_settings.max_concurrency,
            on_event=lambda event_type, payload: _event(
                store,
                run_id,
                event_type,
                "optimizing",
                _gepa_event_message(event_type, payload),
                payload,
            ),
        )
        _ensure_run_active(store, run_id)
        optimized_instruction = clean_runtime_instruction(optimization.instruction) or seed_instruction
        optimizer_improved = (
            optimization.candidate_count > 1
            and optimized_instruction.strip() != seed_instruction.strip()
        )
        if optimizer_improved:
            optimized_validation = evaluator.evaluate_instruction(optimized_instruction, validation_examples)
            _ensure_run_active(store, run_id)
            optimized_version_id = store.create_agent_version(
                project_id=run.project_id,
                run_id=run_id,
                kind="gepa",
                instruction_text=optimized_instruction,
                state={
                    "instruction": optimized_instruction,
                    "optimizer": "gepa.optimize_anything",
                    "candidate_count": optimization.candidate_count,
                    "metric_calls": optimization.metric_calls,
                },
                validation_metrics=_evaluation_metrics(optimized_validation, "validation"),
                status="candidate",
            )
        else:
            # The optimizer kept the seed as its best candidate. Persisting a
            # byte-identical copy and re-verifying it would stage a fake
            # Seed-vs-GEPA race, so the seed itself carries forward.
            _event(
                store,
                run_id,
                "gepa_no_accepted_candidate",
                "optimizing",
                "The optimizer proposed no winning instruction; the verified seed carries forward.",
                {
                    "candidate_count": optimization.candidate_count,
                    "metric_calls": optimization.metric_calls,
                },
            )
            optimized_validation = baseline_validation
            optimized_version_id = seed_version_id
        store.update_run(
            run_id,
            baseline_validation_score=baseline_validation.score,
            best_validation_score=optimized_validation.score,
            best_agent_version_id=optimized_version_id,
        )

        incumbent_id = project.promoted_agent_version_id
        incumbent_version = (
            store.get_agent_version(version_id=incumbent_id, owner_id=run.owner_id)
            if incumbent_id
            else None
        )
        incumbent_validation = (
            evaluator.evaluate_instruction(
                incumbent_version["instruction_text"],
                validation_examples,
            )
            if incumbent_version
            else None
        )

        store.update_run(run_id, stage="hidden_test_evaluation", progress=0.82)
        _event(store, run_id, "stage_started", "hidden_test_evaluation", "Checking the selected agent on holdout cases.")
        seed_test = evaluator.evaluate_instruction(
            seed_instruction,
            test_examples,
            on_case_completed=lambda completed, total, judgment: _event(
                store,
                run_id,
                "verification_case_completed",
                "hidden_test_evaluation",
                f"Evaluated seed on hidden case {completed} of {total}.",
                {
                    "candidate": "seed",
                    "completed_cases": completed,
                    "total_cases": total,
                    "score": judgment.score,
                },
            ),
        )
        _ensure_run_active(store, run_id)
        if optimizer_improved:
            optimized_test = evaluator.evaluate_instruction(
                optimized_instruction,
                test_examples,
                on_case_completed=lambda completed, total, judgment: _event(
                    store,
                    run_id,
                    "verification_case_completed",
                    "hidden_test_evaluation",
                    f"Evaluated optimized prompt on hidden case {completed} of {total}.",
                    {
                        "candidate": "optimized",
                        "completed_cases": completed,
                        "total_cases": total,
                        "score": judgment.score,
                    },
                ),
            )
            _ensure_run_active(store, run_id)
        else:
            optimized_test = seed_test
        store.update_agent_version_test_metrics(seed_version_id, _evaluation_metrics(seed_test, "test"))
        if optimizer_improved:
            store.update_agent_version_test_metrics(optimized_version_id, _evaluation_metrics(optimized_test, "test"))

        incumbent_test = (
            evaluator.evaluate_instruction(
                incumbent_version["instruction_text"],
                test_examples,
                on_case_completed=lambda completed, total, judgment: _event(
                    store,
                    run_id,
                    "verification_case_completed",
                    "hidden_test_evaluation",
                    f"Evaluated incumbent on hidden case {completed} of {total}.",
                    {
                        "candidate": "incumbent",
                        "completed_cases": completed,
                        "total_cases": total,
                        "score": judgment.score,
                    },
                ),
            )
            if incumbent_version
            else None
        )
        required_gate_evidence = {
            "seed": {
                "validation": baseline_validation.required_passed,
                "holdout": seed_test.required_passed,
            },
            "optimized": {
                "validation": optimized_validation.required_passed,
                "holdout": optimized_test.required_passed,
            },
            "incumbent": {
                "validation": incumbent_validation.required_passed if incumbent_validation else None,
                "holdout": incumbent_test.required_passed if incumbent_test else None,
            },
        }
        quality_admission = {
            "selection_rule": "highest_holdout_score",
            "thresholds": {
                "min_holdout_pass_rate": MIN_HOLDOUT_PASS_RATE,
                "min_required_behavior_pass_rate": MIN_REQUIRED_BEHAVIOR_PASS_RATE,
            },
            "optimizer_accepted": optimizer_improved,
            "seed": {
                "validation_pass_rate": baseline_validation.pass_rate,
                "holdout_pass_rate": seed_test.pass_rate,
                "holdout_required_pass_rate": seed_test.required_pass_rate,
            },
            "optimized": {
                "validation_pass_rate": optimized_validation.pass_rate,
                "holdout_pass_rate": optimized_test.pass_rate,
                "holdout_required_pass_rate": optimized_test.required_pass_rate,
            },
            "incumbent": {
                "validation_pass_rate": incumbent_validation.pass_rate
                if incumbent_validation
                else None,
                "holdout_pass_rate": incumbent_test.pass_rate if incumbent_test else None,
                "holdout_required_pass_rate": incumbent_test.required_pass_rate
                if incumbent_test
                else None,
            },
        }
        eligibility = {
            "seed": candidate_is_eligible(seed_test),
            "optimized": candidate_is_eligible(optimized_test) if optimizer_improved else False,
            "incumbent": candidate_is_eligible(incumbent_test) if incumbent_test else False,
        }
        if not any(eligibility.values()):
            fallback_id = incumbent_id or seed_version_id
            fallback_kind = "incumbent" if incumbent_id else "seed"
            rejected_decision = {
                "promoted": False,
                "winner": fallback_kind,
                "fallback": True,
                "optimizer_accepted": optimizer_improved,
                "reason": (
                    "No candidate cleared quality admission; the existing agent remains available."
                    if incumbent_id
                    else "No candidate cleared quality admission; the original seed is available as an explicitly below-gate fallback."
                ),
                "deployed_agent_version_id": fallback_id,
                "baseline_score": seed_test.score,
                "optimized_score": optimized_test.score,
                "incumbent_score": incumbent_test.score if incumbent_test else None,
                "delta": round(optimized_test.score - seed_test.score, 4),
                "required_behavior_gates": required_gate_evidence,
                "quality_admission": quality_admission,
            }
            store.update_run(
                run_id,
                baseline_test_score=seed_test.score,
                optimized_test_score=optimized_test.score,
                promotion_decision=rejected_decision,
            )
            _event(
                store,
                run_id,
                "verification_rejected",
                "hidden_test_evaluation",
                "No agent version cleared quality admission.",
                rejected_decision,
            )
            _ensure_run_active(store, run_id)
            if incumbent_id is None:
                store.promote_version(
                    project_id=run.project_id,
                    owner_id=run.owner_id,
                    version_id=seed_version_id,
                )
            store.update_run(
                run_id,
                status="completed",
                stage="completed",
                progress=1.0,
                best_agent_version_id=fallback_id,
                baseline_test_score=seed_test.score,
                optimized_test_score=optimized_test.score,
                promotion_decision=rejected_decision,
                completed_at=_now(),
            )
            _event(
                store,
                run_id,
                "run_completed",
                "completed",
                _run_completed_message(rejected_decision),
            )
            return
        winner_id, winner_kind, winner_score = _select_deployment(
            seed=(
                seed_version_id,
                seed_test.score,
                eligibility["seed"],
            ),
            optimized=(
                optimized_version_id,
                optimized_test.score,
                eligibility["optimized"],
            ),
            incumbent=(
                incumbent_id,
                incumbent_test.score,
                eligibility["incumbent"],
            )
            if incumbent_id and incumbent_validation and incumbent_test
            else None,
            promotion_margin=0,
        )
        _ensure_run_active(store, run_id)
        if winner_id != incumbent_id:
            store.promote_version(project_id=run.project_id, owner_id=run.owner_id, version_id=winner_id)
        decision = {
            "promoted": winner_id != incumbent_id,
            "winner": winner_kind,
            "optimizer_accepted": optimizer_improved,
            "reason": (
                f"The optimized candidate beat every eligible baseline on holdout "
                f"(score {winner_score:.2f})."
                if winner_kind == "optimized"
                else (
                    f"The incumbent retained deployment because no candidate beat it on "
                    f"holdout (score {winner_score:.2f})."
                    if winner_kind == "incumbent"
                    else (
                        "No improved instruction was accepted; the verified seed remains active."
                        if not optimizer_improved
                        else "No candidate beat the seed on holdout."
                    )
                )
            ),
            "deployed_agent_version_id": winner_id,
            "baseline_score": seed_test.score,
            "optimized_score": optimized_test.score,
            "incumbent_score": incumbent_test.score if incumbent_test else None,
            "delta": round(optimized_test.score - seed_test.score, 4),
            "required_behavior_gates": required_gate_evidence,
            "quality_admission": quality_admission,
        }
        _event(store, run_id, "verification_completed", "hidden_test_evaluation", "Held-out verification selected the deployable agent.", decision)
        _ensure_run_active(store, run_id)
        store.update_run(
            run_id,
            status="completed",
            stage="completed",
            progress=1.0,
            best_agent_version_id=winner_id,
            baseline_test_score=seed_test.score,
            optimized_test_score=optimized_test.score,
            promotion_decision=decision,
            completed_at=_now(),
        )
        _event(
            store,
            run_id,
            "run_completed",
            "completed",
            _run_completed_message(decision),
        )
    except RunCanceled:
        return
    except Exception as exc:
        safe_message = _safe_pipeline_error(exc)
        try:
            store.update_run(
                run_id,
                status="failed",
                stage="failed",
                error={"type": exc.__class__.__name__, "message": safe_message},
                completed_at=_now(),
            )
            _event(store, run_id, "run_failed", "failed", f"Run failed: {safe_message}")
        except Exception:
            # Never let failure-path persistence replace the original error.
            pass
        raise


class RunCanceled(Exception):
    pass


def _ensure_run_active(store: Store, run_id: str) -> None:
    if store.get_run(run_id).status == "canceled":
        raise RunCanceled


class PromptEvaluator:
    def __init__(self, *, task_spec: dict[str, Any], settings: PipelineSettings) -> None:
        self.task_spec = task_spec
        self.settings = settings
        self._cache: dict[tuple[str, str], CaseJudgment] = {}
        self._lock = threading.Lock()

    def evaluate_for_gepa(
        self,
        candidate: str,
        example: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        result = self.evaluate_case(candidate, example)
        return result.score, {
            "Input": example["input"],
            "Expected": example["expected"],
            "Output": result.actual_output,
            "Judge feedback": result.reasoning,
            "Failure tags": result.failure_tags,
            "Constraint": "Generalize from failures; never copy example-specific answers into the prompt.",
        }

    def evaluate_case(self, instruction: str, example: dict[str, Any]) -> CaseJudgment:
        cache_key = (instruction, str(example["id"]))
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        result = (
            _evaluate_case_offline(instruction, example, self.task_spec)
            if self.settings.deterministic_mode
            else _evaluate_case_live(instruction, example, self.task_spec, self.settings)
        )
        with self._lock:
            self._cache[cache_key] = result
        return result

    def evaluate_instruction(
        self,
        instruction: str,
        examples: list[dict[str, Any]],
        on_case_completed: Callable[[int, int, CaseJudgment], None] | None = None,
    ) -> CandidateEvaluation:
        if not examples:
            raise RuntimeError("Evaluation split is empty.")
        results: list[CaseJudgment | None] = [None] * len(examples)
        workers = max(1, min(self.settings.max_concurrency, len(examples)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="clearagent-eval") as executor:
            futures = {
                executor.submit(self.evaluate_case, instruction, example): index
                for index, example in enumerate(examples)
            }
            completed_count = 0
            for future in as_completed(futures):
                judgment = future.result()
                results[futures[future]] = judgment
                completed_count += 1
                if on_case_completed:
                    on_case_completed(completed_count, len(examples), judgment)
        completed = [result for result in results if result is not None]
        failures = Counter(tag for result in completed for tag in result.failure_tags)
        return CandidateEvaluation(
            score=round(sum(result.score for result in completed) / len(completed), 4),
            pass_rate=round(sum(result.passed for result in completed) / len(completed), 4),
            required_pass_rate=round(
                sum(result.required_behavior_passed for result in completed) / len(completed), 4
            ),
            required_passed=all(result.required_behavior_passed for result in completed),
            reasoning="Combined LLM judges scored each executed output against the generated weighted rubric.",
            case_results=completed,
            failure_summary=dict(failures.most_common()),
        )


def _evaluate_case_live(
    instruction: str,
    example: dict[str, Any],
    task_spec: dict[str, Any],
    settings: PipelineSettings,
) -> CaseJudgment:
    required_behaviors = _required_behaviors_for_example(task_spec, example)
    if task_spec.get("module_shape") == "tools":
        actual_payload = _execute_tool_agent(instruction, example, task_spec, settings)
    else:
        runtime_messages = build_runtime_messages(
            agent_instruction=instruction,
            message=str(example["input"]["message"]),
            knowledge_context=_document_context(task_spec),
        )
        messages = [Message(role=item["role"], content=item["content"]) for item in runtime_messages]
        output_schema = task_spec.get("output_schema") or RUNTIME_OUTPUT_SCHEMA
        if output_schema == RUNTIME_OUTPUT_SCHEMA:
            actual_payload = _complete_structured(
                settings.task_model,
                settings,
                messages,
                CandidateOutput,
                max_tokens=settings.task_max_tokens,
            ).model_dump()
        else:
            actual_payload = _complete_payload(
                settings.task_model,
                settings,
                messages,
                output_schema=output_schema,
                max_tokens=settings.task_max_tokens,
            )
    judgment = _complete_structured(
        settings.judge_model,
        settings,
        [
            Message(
                role="system",
                content=(
                    "You are a strict LLM-as-judge. Return only JSON. Score every supplied rubric dimension once. "
                    "Judge the executed output, not the appearance of the system prompt. Use concise actionable feedback. "
                    "Evaluate every supplied required behavior exactly once in required_behaviors, with passed=true only "
                    "when the actual output satisfies it. Keep every rationale under 15 words and overall_reasoning under "
                    "30 words. Do not restate expectations. Judge each dimension against the expected behavior for this specific "
                    "case. When the expected response is a clarification, refusal, or boundary response, reward doing that correctly "
                    "instead of assigning zero because ordinary task content is intentionally absent."
                ),
            ),
            Message(
                role="user",
                content=json.dumps(
                    {
                        "goal": task_spec["goal"],
                        "constraints": task_spec["constraints"],
                        "rubric": task_spec["rubric"],
                        "required_behaviors": required_behaviors,
                        "input": example["input"],
                        "expected": example["expected"],
                        "reference_notes": example.get("reference_notes", ""),
                        "actual_output": actual_payload,
                    },
                    indent=2,
                    sort_keys=True,
                ),
            ),
        ],
        CombinedJudgment,
    )
    return _apply_deterministic_judges(
        _combine_judgment(
            example,
            actual_payload,
            judgment,
            task_spec["rubric"],
            required_behaviors,
        ),
        example.get("checks") or [],
    )


def _execute_tool_agent(
    instruction: str,
    example: dict[str, Any],
    task_spec: dict[str, Any],
    settings: PipelineSettings,
) -> dict[str, Any]:
    registry = dict(settings.tool_registry or {})
    required_names = [item["name"] for item in task_spec.get("tool_definitions", [])]
    missing = [name for name in required_names if name not in registry]
    if missing:
        raise RuntimeError(f"Tool agent is missing registered tools: {', '.join(missing)}")
    provider = provider_for_model(settings.task_model)
    if settings.task_model.startswith("openrouter:") and settings.openrouter_api_key and hasattr(provider, "api_key"):
        provider.api_key = settings.openrouter_api_key
    instrumented_provider = _InstrumentedToolProvider(
        provider,
        settings,
        settings.task_model,
    )
    tools = [registry[name] for name in required_names]
    agent = Agent(
        name=str(task_spec.get("name", "Configured tool agent")),
        model=settings.task_model,
        provider=instrumented_provider,
        system_prompt=(
            f"{instruction}\n\nRelevant uploaded knowledge:\n{_document_context(task_spec)[:12_000]}"
        ),
        tools=tools,
        trace=False,
        max_tokens=settings.task_max_tokens,
        response_format={
            "name": "AgentOutput",
            "schema": _provider_compatible_schema(
                task_spec.get("output_schema", RUNTIME_OUTPUT_SCHEMA)
            ),
            "strict": False,
        },
    )
    result = agent.run(str(example["input"]["message"]), trace=False)
    if isinstance(result.structured_output, dict):
        _validate_payload(
            result.structured_output,
            task_spec.get("output_schema", RUNTIME_OUTPUT_SCHEMA),
            "agent output",
        )
        return result.structured_output
    return {"answer": result.output, "tool_calls": result.tool_calls}


def _evaluate_case_offline(
    instruction: str,
    example: dict[str, Any],
    task_spec: dict[str, Any],
) -> CaseJudgment:
    lower = instruction.lower()
    signals = {
        "task_success": any(term in lower for term in ["purpose", "directly", "outcome", "fulfill"]),
        "constraint_adherence": any(term in lower for term in ["only facts", "do not invent", "boundaries", "capabilities"]),
        "clarity": any(term in lower for term in ["concise", "clear", "useful", "answer first"]),
    }
    dimensions = []
    for dimension in task_spec["rubric"]:
        passed = signals.get(dimension["id"])
        if passed is None:
            passed = _instruction_covers_expectation(dimension["description"], lower)
        dimensions.append(
            DimensionJudgment(
                id=dimension["id"],
                score=0.9 if passed else 0.55,
                rationale=(
                    "The instruction explicitly covers this behavior."
                    if passed
                    else "The instruction should state this behavior more explicitly."
                ),
                failure_tags=[] if passed else [f"missing_{dimension['id']}"],
            )
        )
    required_behaviors = []
    required_keywords = {
        "document_grounding": ("document", "source", "facts"),
        "capability_honesty": ("capabilities", "do not claim", "do not invent"),
        "boundary_respect": ("boundaries", "never mention", "hidden instructions"),
    }
    selected_required_behaviors = _required_behaviors_for_example(task_spec, example)
    for behavior in selected_required_behaviors:
        keywords = required_keywords.get(behavior["id"])
        if keywords is not None:
            passed = any(keyword in lower for keyword in keywords)
        else:
            passed = _instruction_covers_expectation(behavior.get("expectation", ""), lower)
        required_behaviors.append(
            RequiredBehaviorJudgment(
                id=behavior["id"],
                passed=passed,
                rationale=(
                    "The seed instruction explicitly addresses this required behavior."
                    if passed
                    else "The seed instruction does not explicitly address this required behavior."
                ),
            )
        )
    actual = _schema_placeholder(
        task_spec.get("output_schema") or RUNTIME_OUTPUT_SCHEMA,
        f"Offline response for {example['input']['message']}",
    )
    return _apply_deterministic_judges(
        _combine_judgment(
        example,
        actual,
        CombinedJudgment(
            dimensions=dimensions,
            required_behaviors=required_behaviors,
            overall_reasoning="Offline rubric evaluation.",
        ),
        task_spec["rubric"],
        selected_required_behaviors,
        ),
        example.get("checks") or [],
    )


def _required_behaviors_for_example(
    task_spec: dict[str, Any],
    example: dict[str, Any],
) -> list[dict[str, Any]]:
    required = (task_spec.get("quality_contract") or {}).get("required_behaviors", [])
    assigned_ids = set(example.get("required_behavior_ids") or [])
    if not assigned_ids:
        return required
    return [behavior for behavior in required if behavior.get("id") in assigned_ids]


def _instruction_covers_expectation(expectation: str, lower_instruction: str) -> bool:
    expectation_words = {
        word
        for word in re.findall(r"[a-z0-9]+", expectation.lower())
        if len(word) > 3
        and word
        not in {
            "agent",
            "this",
            "that",
            "with",
            "from",
            "follow",
            "respect",
            "business",
            "rule",
            "boundary",
            "must",
            "should",
        }
    }
    instruction_words = set(re.findall(r"[a-z0-9]+", lower_instruction))
    required_matches = min(2, len(expectation_words))
    return required_matches > 0 and len(expectation_words & instruction_words) >= required_matches


def _combine_judgment(
    example: dict[str, Any],
    actual: dict[str, Any],
    judgment: CombinedJudgment,
    rubric: list[dict[str, Any]],
    required_behaviors: list[dict[str, Any]] | None = None,
) -> CaseJudgment:
    by_id = {dimension.id: dimension for dimension in judgment.dimensions}
    weighted_score = 0.0
    reasoning = []
    failure_tags = []
    required_behavior_failures = []
    required_by_id = {behavior["id"]: behavior for behavior in (required_behaviors or [])}
    judged_required = {behavior.id: behavior for behavior in judgment.required_behaviors}
    for behavior_id in required_by_id:
        behavior_result = judged_required.get(behavior_id)
        if behavior_result is None:
            required_behavior_failures.append(f"missing_required_judge_{behavior_id}")
            reasoning.append(f"{behavior_id}: required behavior judge omitted this behavior")
        elif not behavior_result.passed:
            required_behavior_failures.append(f"required_behavior_{behavior_id}")
            reasoning.append(f"{behavior_id}: required behavior failed ({behavior_result.rationale})")
    for dimension in rubric:
        result = by_id.get(dimension["id"])
        if result is None:
            failure_tags.append(f"missing_judge_{dimension['id']}")
            reasoning.append(f"{dimension['id']}: judge omitted this dimension")
            continue
        weighted_score += float(dimension["weight"]) * result.score
        reasoning.append(f"{dimension['id']} ({result.score:.2f}): {result.rationale}")
        failure_tags.extend(result.failure_tags)
    score = round(max(0.0, min(weighted_score, 1.0)), 4)
    required_passed = not required_behavior_failures
    return CaseJudgment(
        example_id=str(example["id"]),
        score=score,
        passed=score >= 0.7 and required_passed,
        reasoning=" ".join(reasoning)[:1000],
        failure_tags=list(dict.fromkeys(failure_tags))[:8],
        required_behavior_passed=required_passed,
        required_behavior_failures=required_behavior_failures[:8],
        actual_output=actual,
    )


def _apply_deterministic_judges(
    judgment: CaseJudgment,
    checks: list[dict[str, Any]],
) -> CaseJudgment:
    if not checks:
        return judgment
    actual = judgment.actual_output or {}
    output = str(actual.get("answer", ""))
    check_results = run_checks(
        checks,
        RunResult(
            output=output,
            run_id=None,
            trace_db_path=None,
            tool_calls=[],
            latency_ms=0,
            structured_output=actual,
        ),
    )
    failures = [f"check_{result.name}" for result in check_results if not result.passed]
    check_summary = "; ".join(
        f"{result.name}={'pass' if result.passed else 'fail'}" for result in check_results
    )
    return judgment.model_copy(
        update={
            "passed": judgment.passed and not failures,
            "reasoning": f"{judgment.reasoning} Deterministic checks: {check_summary}."[:1000],
            "failure_tags": list(dict.fromkeys([*judgment.failure_tags, *failures]))[:8],
            "required_behavior_passed": judgment.required_behavior_passed and not failures,
            "required_behavior_failures": list(
                dict.fromkeys([*judgment.required_behavior_failures, *failures])
            )[:8],
        }
    )


def _generate_dataset_live(
    *,
    profile: str,
    seed: int,
    task_spec: dict[str, Any],
    n: int | None,
    settings: PipelineSettings,
    on_batch_completed: Callable[[int, int, int], None] | None = None,
    on_batch_failed: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    layout = generate_synthetic_examples(profile=profile, seed=seed, task_spec=task_spec, n=n)
    templates = layout["examples"]
    # Structured cases are verbose. Keep each response comfortably below the
    # provider output cap so a single truncated response cannot sink a build.
    batches = [
        templates[offset : offset + SYNTHETIC_BATCH_SIZE]
        for offset in range(0, len(templates), SYNTHETIC_BATCH_SIZE)
    ]

    def generate_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        plan = [
            {
                "id": item["id"],
                "split": item["split"],
                "category": item["category"],
                "difficulty": item["difficulty"],
                "required_behavior_ids": item["required_behavior_ids"],
            }
            for item in batch
        ]
        request_messages = [
                Message(
                    role="system",
                    content=(
                        "Generate diverse evaluation cases for the supplied agent task. Return only JSON. "
                        "Do not copy cases across splits or reveal expected behavior inside the user input. "
                        "Every input/expected pair must be textually unique across the entire dataset; use each planned "
                        "id, category, difficulty, and required behavior to create a materially distinct scenario."
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        f"Task spec:\n{json.dumps(task_spec, indent=2, sort_keys=True)}\n\n"
                        f"Case plan:\n{json.dumps(plan, indent=2, sort_keys=True)}\n\n"
                        "Return exactly one case for every planned id and copy each id exactly. Inputs and expected outputs must follow "
                        "the fixed message/answer schemas. reference_notes must explain what a judge should reward. "
                        "Keep each expected answer under 120 words and each reference_notes value under 60 words. "
                        "Actively exercise every assigned required_behavior_id and copy that list exactly. "
                        "Leave checks empty. ClearAgent applies its fixed deterministic leakage gates separately; "
                        "task-specific behavior is evaluated by the Quality Contract judges."
                    ),
                ),
            ]
        planned_ids = {item["id"] for item in plan}
        generated_by_id: dict[str, GeneratedExample] = {}
        for attempt in range(3):
            generated = _complete_structured(
                settings.synthetic_model,
                settings,
                request_messages,
                GeneratedExampleBatch,
                max_tokens=SYNTHETIC_MAX_OUTPUT_TOKENS,
            )
            generated_by_id.update(
                (example.id, example)
                for example in generated.examples
                if example.id in planned_ids
            )
            missing = sorted(planned_ids - set(generated_by_id))
            if not missing:
                break
            if attempt == 2:
                raise RuntimeError(
                    "Synthetic model did not return the planned case ids "
                    f"after three attempts (missing={missing})."
                )
            request_messages.append(
                Message(
                    role="user",
                    content=(
                        "Keep the accepted cases from the previous response. Return only the cases that are still missing, "
                        f"using exactly these ids: {missing}."
                    ),
                )
            )
        completed = []
        for template in batch:
            example = generated_by_id[template["id"]]
            _validate_payload(example.input, task_spec["input_schema"], f"input {template['id']}")
            _validate_payload(example.expected, task_spec["output_schema"], f"expected {template['id']}")
            checks = _normalize_generated_checks(example.checks)
            completed.append(
                {
                    **example.model_dump(exclude={"id", "checks"}),
                    "checks": checks,
                    "required_behavior_ids": template["required_behavior_ids"],
                    "id": template["id"],
                    "split": template["split"],
                    "cluster_id": template["cluster_id"],
                    "source": "synthetic_model",
                }
            )
        return completed

    generated_by_batch: list[list[dict[str, Any]] | None] = [None] * len(batches)
    failed_batches: dict[int, str] = {}
    workers = max(1, min(settings.max_concurrency, len(batches)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="clearagent-dataset") as executor:
        futures = {executor.submit(generate_batch, batch): index for index, batch in enumerate(batches)}
        completed_batches = 0
        for future in as_completed(futures):
            batch_index = futures[future]
            try:
                batch_result = future.result()
            except Exception as exc:
                # One stubborn batch must not sink an otherwise valid build;
                # degraded coverage beats a failed run when splits stay usable.
                failed_batches[batch_index] = _safe_pipeline_error(exc)
                batch_result = []
            generated_by_batch[batch_index] = batch_result
            completed_batches += 1
            if failed_batches.get(batch_index):
                if on_batch_failed:
                    on_batch_failed(completed_batches, len(batches), failed_batches[batch_index])
            elif on_batch_completed:
                on_batch_completed(completed_batches, len(batches), len(batch_result))
    layout["examples"] = [
        example
        for batch in generated_by_batch
        if batch is not None
        for example in batch
    ]
    if failed_batches:
        # Recompute split counts from surviving cases; a stale count would
        # trip the dataset consistency validator below.
        survivors = {
            split: sum(example.get("split") == split for example in layout["examples"])
            for split in ("train", "validation", "test")
        }
        deficient = {split: count for split, count in survivors.items() if count < MIN_CASES_PER_SPLIT}
        if deficient:
            raise RuntimeError(
                "Synthetic generation lost too many batches to keep every split usable "
                f"(cases per split: {survivors}; minimum {MIN_CASES_PER_SPLIT} required)."
            )
        layout["split_counts"] = survivors
    layout["generation_metadata"] = {
        **layout["generation_metadata"],
        "template_version": "model-generated-v2",
        "model": settings.synthetic_model,
    }
    _disambiguate_duplicate_examples(layout["examples"])
    validate_synthetic_dataset(layout)
    return layout


def _disambiguate_duplicate_examples(examples: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for example in examples:
        signature = json.dumps([example.get("input"), example.get("expected")], sort_keys=True)
        if signature not in seen:
            seen.add(signature)
            continue
        input_payload = example.get("input")
        if not isinstance(input_payload, dict):
            continue
        text_field = next((key for key, value in input_payload.items() if isinstance(value, str)), None)
        if text_field is None:
            continue
        context = " ".join(
            str(example.get(key, "")).replace("_", " ").strip()
            for key in ("category", "difficulty", "id")
        ).strip()
        input_payload[text_field] = f"{input_payload[text_field]}\n\nScenario context: {context}."
        seen.add(json.dumps([input_payload, example.get("expected")], sort_keys=True))


def _normalize_generated_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _ = checks
    return [
        {"not_contains": "system prompt"},
        {"not_contains": "hidden instructions"},
    ]


def _canonicalize_task_spec(task_spec: TaskSpec, goal: str) -> TaskSpec:
    instruction = clean_runtime_instruction(task_spec.seed_instruction)
    if not instruction:
        raise ValueError("Planner returned an empty runtime instruction.")
    return task_spec.model_copy(
        update={
            "goal": goal,
            "task_type": task_spec.task_type or "chat_agent",
            "input_schema": task_spec.input_schema or RUNTIME_INPUT_SCHEMA,
            "output_schema": task_spec.output_schema or RUNTIME_OUTPUT_SCHEMA,
            "constraints": list(dict.fromkeys([*task_spec.constraints, *RUNTIME_CONSTRAINTS])),
            "failure_modes": list(dict.fromkeys([*task_spec.failure_modes, *RUNTIME_FAILURE_MODES])),
            "seed_instruction": instruction,
            "module_shape": task_spec.module_shape or "chat",
        }
    )


def _reflection_lm(settings: PipelineSettings) -> Callable[[str | list[dict[str, Any]]], str]:
    if settings.deterministic_mode:
        return _offline_reflection_lm

    def complete(prompt: str | list[dict[str, Any]]) -> str:
        messages = [Message(role="system", content=_reflection_system_prompt())]
        if isinstance(prompt, str):
            messages.append(Message(role="user", content=prompt))
        else:
            messages.extend(
                Message(
                    role=item.get("role", "user"),
                    content=str(item.get("content", "")),
                )
                for item in prompt
            )
        return _complete_text(settings.reflection_model, settings, messages)

    return complete


def _reflection_system_prompt() -> str:
    return (
        "You improve reusable runtime instructions for a saved ClearAgent agent. Follow GEPA's "
        "requested fenced output format exactly. Improve behavior only when evaluation evidence "
        "shows a generalizable failure; otherwise return the current instruction unchanged. Preserve "
        "the Agent PRD's outcome, users, business rules, capabilities, boundaries, and output contract. "
        "Uploaded knowledge is supplied separately at runtime: never copy document passages, case-specific "
        "facts, expected answers, or test inputs into the instruction. Never add tools or data access that "
        "the agent does not have. Prefer a concise operating policy over examples, patches, or evaluator "
        "terminology. Do not mention datasets, judges, GEPA, optimization, scores, or hidden tests in the "
        "replacement instruction."
    )


def _offline_reflection_lm(prompt: str | list[dict[str, Any]]) -> str:
    text = (
        prompt
        if isinstance(prompt, str)
        else "\n".join(str(item.get("content", "")) for item in prompt)
    )
    match = re.search(r"current parameter value is:\s*```\s*(.*?)\s*```", text, re.DOTALL)
    current = match.group(1).strip() if match else "Respond directly to the user."
    addition = (
        "\n\nBe clear, concise, and useful. Answer the requested outcome directly. "
        "Use only facts and capabilities supplied at runtime; do not invent missing details."
    )
    return f"```\n{current}{addition}\n```"


def _select_deployment(
    *,
    seed: tuple[str, float] | tuple[str, float, bool],
    optimized: tuple[str, float] | tuple[str, float, bool],
    incumbent: tuple[str, float] | tuple[str, float, bool] | None,
    promotion_margin: float = 0.0,
) -> tuple[str, str, float]:
    seed_score = seed[1]
    seed_eligible = seed[2] if len(seed) > 2 else True
    incumbent_score = incumbent[1] if incumbent else None
    incumbent_eligible = incumbent[2] if incumbent and len(incumbent) > 2 else True
    eligible_baseline_scores = [
        score
        for score, eligible in (
            (seed_score, seed_eligible),
            (incumbent_score, incumbent_eligible),
        )
        if score is not None and eligible
    ]
    optimized_eligible = optimized[2] if len(optimized) > 2 else True
    if optimized_eligible and (
        not eligible_baseline_scores
        or optimized[1] > max(eligible_baseline_scores) + promotion_margin
    ):
        return optimized[0], "optimized", optimized[1]
    if incumbent and incumbent_eligible:
        return incumbent[0], "incumbent", incumbent[1]
    if seed_eligible:
        return seed[0], "seed", seed_score
    if optimized_eligible:
        return optimized[0], "optimized", optimized[1]
    raise RuntimeError("No agent version passed quality admission on the hidden holdout split.")


def render_run_report(run: Any, *, events: list[Any] | None = None) -> str:
    promotion = run.promotion_decision or {}
    split_counts = (run.dataset or {}).get("split_counts", {})
    task_spec = run.task_spec or {}
    prd = task_spec.get("agent_prd") or {}
    quality_contract = task_spec.get("quality_contract") or {}
    required_behaviors = quality_contract.get("required_behaviors", [])
    usage = _model_usage_summary(
        [event.payload for event in (events or []) if getattr(event, "type", None) == "model_call_completed"],
        configured_models=(run.run_config or {}).get("models", {}),
    )
    optimizer_accepted = bool(promotion.get("optimizer_accepted", True))
    if optimizer_accepted:
        pipeline_summary = (
            "ClearAgent planned the task, generated a synthetic dataset and weighted LLM judges, "
            "optimized the runtime prompt with GEPA, and verified eligible versions on a hidden test split."
        )
        scores_table = (
            "| Metric | Seed | GEPA |\n| --- | ---: | ---: |\n"
            f"| Validation | {_format_score(run.baseline_validation_score)} | {_format_score(run.best_validation_score)} |\n"
            f"| Hidden test | {_format_score(run.baseline_test_score)} | {_format_score(run.optimized_test_score)} |"
        )
    else:
        pipeline_summary = (
            "ClearAgent planned the task, generated a synthetic dataset and weighted LLM judges, "
            "then verified the seed prompt on a hidden test split. The instruction optimizer found no "
            "accepted improvement over the seed."
        )
        scores_table = (
            "| Metric | Verified seed |\n| --- | ---: |\n"
            f"| Validation | {_format_score(run.baseline_validation_score)} |\n"
            f"| Hidden test | {_format_score(run.baseline_test_score)} |"
        )
    return (
        f"# {str(task_spec.get('name', 'Generated Agent')).title()} Report\n\n"
        f"Run ID: `{run.id}`\n\n"
        "## Agent PRD\n\n"
        f"**Desired outcome:** {prd.get('desired_outcome', task_spec.get('goal', ''))}\n\n"
        f"**Documents:** {len(prd.get('documents', []))}\n\n"
        "## Required behaviors\n\n"
        + "\n".join(f"- {behavior.get('expectation', behavior.get('id', ''))}" for behavior in required_behaviors)
        + "\n\n"
        "## Pipeline\n\n"
        f"{pipeline_summary}\n\n"
        "## Dataset\n\n"
        f"- Total: {(run.dataset or {}).get('row_count', 0)}\n"
        f"- Train: {split_counts.get('train', 0)}\n"
        f"- Validation: {split_counts.get('validation', 0)}\n"
        f"- Holdout: {split_counts.get('test', 0)}\n\n"
        "## Scores\n\n"
        f"{scores_table}\n\n"
        "## Required behavior gates\n\n"
        + _required_gate_table(promotion.get("required_behavior_gates", {}))
        + "\n\n"
        "## Quality admission\n\n"
        + _quality_admission_table(promotion.get("quality_admission", {}))
        + "\n\n"
        f"## Promotion\n\n{promotion.get('reason', 'No promotion decision recorded.')}\n\n"
        "## Model usage\n\n"
        f"- Total model calls: {usage['calls']}\n"
        f"- Input tokens: {usage['input_tokens']}\n"
        f"- Output tokens: {usage['output_tokens']}\n"
        f"- Total tokens: {usage['total_tokens']}\n"
        f"- Estimated cost: ${usage['estimated_cost_usd']:.6f}\n\n"
        "| Role | Model | Calls | Input tokens | Output tokens | Estimated cost |\n"
        "| --- | --- | ---: | ---: | ---: | ---: |\n"
        + "\n".join(
            f"| {item['purpose']} | {item['model']} | {item['calls']} | {item['input_tokens']} | "
            f"{item['output_tokens']} | ${item['estimated_cost_usd']:.6f} |"
            for item in usage["by_role"]
        )
        + "\n"
    )


def _model_usage_summary(
    model_calls: list[dict[str, Any]],
    *,
    configured_models: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_role: dict[str, dict[str, Any]] = {}
    for purpose, model in (configured_models or {}).items():
        by_role[str(purpose)] = {
            "purpose": str(purpose),
            "model": str(model),
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
    for call in model_calls:
        purpose = str(call.get("purpose", "model"))
        item = by_role.setdefault(
            purpose,
            {
                "purpose": purpose,
                "model": str(call.get("model_uri", call.get("model", "unknown"))),
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
            },
        )
        item["model"] = str(call.get("model_uri", call.get("model", item["model"])))
        item["calls"] += 1
        item["input_tokens"] += int(call.get("input_tokens", 0) or 0)
        item["output_tokens"] += int(call.get("output_tokens", 0) or 0)
        item["estimated_cost_usd"] += float(call.get("estimated_cost_usd", 0) or 0)
    ordered = sorted(by_role.values(), key=lambda item: item["purpose"])
    input_tokens = sum(item["input_tokens"] for item in ordered)
    output_tokens = sum(item["output_tokens"] for item in ordered)
    return {
        "calls": sum(item["calls"] for item in ordered),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": sum(item["estimated_cost_usd"] for item in ordered),
        "by_role": ordered,
    }


def _required_gate_table(gates: dict[str, Any]) -> str:
    if not gates:
        return "No required-behavior gate evidence recorded."
    rows = ["| Version | Validation | Holdout |", "| --- | --- | --- |"]
    for version in ("seed", "optimized", "incumbent"):
        evidence = gates.get(version) or {}
        rows.append(
            f"| {version.title()} | {_format_gate(evidence.get('validation'))} | "
            f"{_format_gate(evidence.get('holdout'))} |"
        )
    return "\n".join(rows)


def _quality_admission_table(admission: dict[str, Any]) -> str:
    if not admission:
        return "No graded quality-admission evidence recorded."
    rows = [
        "Selection rule: highest holdout score",
        "",
        "| Version | Validation pass rate | Holdout pass rate |",
        "| --- | ---: | ---: |",
    ]
    for version in ("seed", "optimized", "incumbent"):
        evidence = admission.get(version) or {}
        rows.append(
            f"| {version.title()} | {_format_rate(evidence.get('validation_pass_rate'))} | "
            f"{_format_rate(evidence.get('holdout_pass_rate'))} |"
        )
    return "\n".join(rows)


def _format_rate(value: Any) -> str:
    return "Not evaluated" if value is None else f"{float(value):.0%}"


def _format_gate(value: Any) -> str:
    if value is True:
        return "Pass"
    if value is False:
        return "Fail"
    return "Not evaluated"


def export_files(
    run: Any,
    *,
    optimized_instruction: str | None = None,
    events: list[Any] | None = None,
) -> dict[str, str]:
    task_spec = run.task_spec or {}
    optimized = optimized_instruction or task_spec.get("seed_instruction", "")
    return {
        "clearagent-export/task_spec.json": _dump(task_spec),
        "clearagent-export/program_state.json": _dump({"instruction": optimized}),
        "clearagent-export/seed_instruction.md": task_spec.get("seed_instruction", "") + "\n",
        "clearagent-export/optimized_instruction.md": optimized + "\n",
        "clearagent-export/eval_dataset.jsonl": "\n".join(
            json.dumps(example, sort_keys=True) for example in (run.dataset or {}).get("examples", [])
        ) + "\n",
        "clearagent-export/metrics.json": _dump(
            {
                "baseline_validation_score": run.baseline_validation_score,
                "best_validation_score": run.best_validation_score,
                "baseline_test_score": run.baseline_test_score,
                "optimized_test_score": run.optimized_test_score,
            }
        ),
        "clearagent-export/report.md": render_run_report(run, events=events),
        "clearagent-export/events.json": _dump(
            [
                event.model_dump(mode="json")
                if hasattr(event, "model_dump")
                else event
                for event in (events or [])
            ]
        ),
        "clearagent-export/metadata.json": _dump(
            {
                "run_id": run.id,
                "optimizer": "gepa",
                "dataset_size": run.dataset_size,
                "split_counts": (run.dataset or {}).get("split_counts", {}),
            }
        ),
    }


CASE_OUTPUT_PERSIST_CHARS = 600


def _evaluation_metrics(evaluation: CandidateEvaluation, split: str) -> dict[str, Any]:
    return {
        "score": evaluation.score,
        "split": split,
        "reasoning": evaluation.reasoning,
        "pass_rate": evaluation.pass_rate,
        "required_pass_rate": evaluation.required_pass_rate,
        "required_passed": evaluation.required_passed,
        "case_results": [_compact_case_result(result) for result in evaluation.case_results],
        "failure_summary": evaluation.failure_summary,
    }


def _compact_case_result(result: Any) -> dict[str, Any]:
    # Full per-case outputs can be megabytes across a run; persist truncated
    # previews in version metrics so exports stay lightweight.
    data = result.model_dump()
    output = data.get("actual_output")
    if isinstance(output, dict):
        data["actual_output"] = {
            key: (
                f"{value[:CASE_OUTPUT_PERSIST_CHARS]}…"
                if isinstance(value, str) and len(value) > CASE_OUTPUT_PERSIST_CHARS
                else value
            )
            for key, value in output.items()
        }
    return data


def _examples_for_split(dataset: dict[str, Any], split: str) -> list[dict[str, Any]]:
    return [example for example in dataset.get("examples", []) if example.get("split") == split]


def _event(
    store: Store,
    run_id: str,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    store.add_event(
        run_id=run_id,
        event_type=event_type,
        stage=stage,
        message=message,
        payload=payload or {},
    )


def _run_metadata(run: Any, settings: PipelineSettings) -> dict[str, Any]:
    return {
        "execution_mode": "deterministic" if settings.deterministic_mode else "live provider",
        "seed": run.seed,
        "budget_profile": run.budget_profile,
        "dataset_size": run.dataset_size,
        "run_config": run.run_config,
        "models": {
            "planner": settings.planner_model,
            "synthetic": settings.synthetic_model,
            "task": settings.task_model,
            "judge": settings.judge_model,
            "reflection": settings.reflection_model,
        },
    }


def _model_call_stage(purpose: str) -> str:
    return {
        "planner": "planning",
        "synthetic": "generating_dataset",
        "task": "optimizing",
        "judge": "optimizing",
        "reflection": "optimizing",
    }.get(purpose, "optimizing")


def _model_purpose(call_kind: str) -> str:
    return {
        "PlanningResult": "planner",
        "ClarificationDecision": "planner",
        "AgentPRD": "planner",
        "GeneratedExampleBatch": "synthetic",
        "CandidateOutput": "task",
        "CombinedJudgment": "judge",
        "reflection": "reflection",
    }.get(call_kind, call_kind.lower() or "model")


def _run_completed_message(decision: dict[str, Any]) -> str:
    if decision.get("fallback"):
        return "No candidate cleared quality admission; a below-gate fallback is available to try."
    if decision["winner"] != "seed":
        return "Agent is ready."
    if not decision.get("optimizer_accepted", True):
        return "Optimization found no improvement; the verified seed remains active."
    return "No candidate beat the seed; the seed remains active."


def _gepa_event_message(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "gepa_started":
        return "Instruction optimization started."
    if event_type == "gepa_candidate_accepted":
        return f"A stronger instruction candidate was found (round {payload['iteration']})."
    if event_type == "gepa_validation_completed":
        return f"Candidate quality checked ({payload['score']:.2f})."
    if event_type == "gepa_no_accepted_candidate":
        return "Instruction optimization finished without an accepted improvement."
    return "Instruction optimization completed."


def _format_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _require_live_credentials(settings: PipelineSettings) -> None:
    models = (
        settings.planner_model,
        settings.synthetic_model,
        settings.task_model,
        settings.judge_model,
        settings.reflection_model,
    )
    if any(model.startswith("openrouter:") for model in models) and not settings.openrouter_api_key:
        raise RuntimeError("OpenRouter models require OPENROUTER_API_KEY.")


def _safe_pipeline_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "A model returned malformed structured output after three attempts."
    if isinstance(exc, ValueError) and any(
        marker in str(exc)
        for marker in (
            "Invalid structured output JSON",
            "Structured output did not match schema",
            "structured output response did not include text",
        )
    ):
        return "A model returned malformed structured output after three attempts."
    return " ".join(str(exc).split())[:1000]


def _complete_structured[T: BaseModel](
    model_uri: str,
    settings: PipelineSettings,
    messages: list[Message],
    response_model: type[T],
    *,
    max_tokens: int | None = None,
) -> T:
    request_messages = list(messages)
    token_limit = settings.gepa_max_tokens if max_tokens is None else max_tokens
    for attempt in range(3):
        text = _provider_completion(
            model_uri,
            settings,
            request_messages,
            max_tokens=token_limit,
            response_format={
                "name": response_model.__name__,
                "schema": _provider_compatible_schema(response_model.model_json_schema()),
                "strict": False,
            },
            call_kind=response_model.__name__,
        )
        try:
            return response_model.model_validate_json(_strip_json_fence(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == 2:
                raise
            compact_error = " ".join(str(exc).split())[:300]
            truncated = any(
                marker in compact_error.lower()
                for marker in ("eof while parsing", "end of data", "unterminated string")
            )
            correction = (
                "The previous response was truncated. Return complete, compact JSON only. "
                "Keep prose fields concise, include every required field, and close every string, "
                "array, and object."
                if truncated
                else f"The response failed JSON validation: {compact_error}. Return corrected JSON only."
            )
            request_messages.append(
                Message(
                    role="user",
                    content=correction,
                )
            )
    raise AssertionError("Structured completion loop exited unexpectedly.")


def _complete_payload(
    model_uri: str,
    settings: PipelineSettings,
    messages: list[Message],
    *,
    output_schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    request_messages = list(messages)
    for attempt in range(3):
        text = _provider_completion(
            model_uri,
            settings,
            request_messages,
            max_tokens=max_tokens,
            response_format={"name": "AgentOutput", "schema": _provider_compatible_schema(output_schema), "strict": False},
            call_kind="CandidateOutput",
        )
        try:
            payload = json.loads(_strip_json_fence(text))
            if not isinstance(payload, dict):
                raise ValueError("Structured output must be a JSON object")
            _validate_payload(payload, output_schema, "agent output")
            return payload
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            if attempt == 2:
                raise
            request_messages.append(
                Message(
                    role="user",
                    content=f"The previous output did not match the required JSON schema: {exc}. Return corrected JSON only.",
                )
            )
    raise AssertionError("Structured payload loop exited unexpectedly.")


def _complete_text(
    model_uri: str,
    settings: PipelineSettings,
    messages: list[Message],
) -> str:
    return _provider_completion(
        model_uri,
        settings,
        messages,
        max_tokens=settings.gepa_max_tokens,
        response_format=None,
        call_kind="reflection",
    )


def _provider_completion(
    model_uri: str,
    settings: PipelineSettings,
    messages: list[Message],
    *,
    max_tokens: int,
    response_format: dict[str, Any] | None,
    call_kind: str = "model",
) -> str:
    provider = provider_for_model(model_uri)
    try:
        return _provider_completion_with_provider(
            provider,
            model_uri,
            settings,
            messages,
            max_tokens=max_tokens,
            response_format=response_format,
            call_kind=call_kind,
        )
    finally:
        _close_provider(provider)


def _close_provider(provider: Any) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _provider_completion_with_provider(
    provider: Any,
    model_uri: str,
    settings: PipelineSettings,
    messages: list[Message],
    *,
    max_tokens: int,
    response_format: dict[str, Any] | None,
    call_kind: str = "model",
) -> str:
    if (
        model_uri.startswith("openrouter:")
        and settings.openrouter_api_key
        and hasattr(provider, "api_key")
    ):
        provider.api_key = settings.openrouter_api_key
    request = provider.build_request(
        model=_request_model_name(model_uri),
        messages=messages,
        tools=[],
        tool_choice=None,
        temperature=0.2,
        max_tokens=max_tokens,
        extra=_provider_request_extra(model_uri, settings),
        response_format=response_format,
    )
    for empty_attempt in range(3):
        started_at = time.perf_counter()
        response = _complete_with_retry(provider, request)
        if response.output_text:
            _record_model_call(
                settings,
                model_uri=model_uri,
                response=response,
                request_messages=messages,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                max_tokens=max_tokens,
                purpose=_model_purpose(call_kind),
            )
            return response.output_text
        # Blank completions still consume tokens; record them so budgets and
        # cost summaries reflect reality before retrying.
        _record_model_call(
            settings,
            model_uri=model_uri,
            response=response,
            request_messages=messages,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            max_tokens=max_tokens,
            purpose=_model_purpose(call_kind),
        )
        if empty_attempt < 2:
            time.sleep(0.5 * (2**empty_attempt))
    raise RuntimeError(f"{model_uri} returned no text after three attempts.")


def _record_model_call(
    settings: PipelineSettings,
    *,
    model_uri: str,
    response: Any,
    request_messages: list[Message],
    latency_ms: float,
    max_tokens: int,
    purpose: str,
) -> None:
    usage = response.usage
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or input_tokens + output_tokens)
    estimated_cost = _estimate_model_cost(model_uri, response, input_tokens, output_tokens)
    if settings.budget_tracker is not None:
        settings.budget_tracker.record(total_tokens=total_tokens, cost_usd=estimated_cost)
    recorder = settings.on_model_call
    if recorder is None:
        return
    try:
        recorder(
            {
                "purpose": purpose,
                "model_uri": model_uri,
                "model": str(getattr(response, "model", "") or model_uri),
                "provider": str(getattr(response, "provider", "") or "unknown"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "latency_ms": round(latency_ms, 1),
                "max_tokens": max_tokens,
                "estimated_cost_usd": estimated_cost,
                **(
                    {
                        "request_messages": redact([message.model_dump() for message in request_messages]),
                        "response_text": redact(response.output_text),
                    }
                    if settings.debug
                    else {}
                ),
            }
        )
    except Exception:
        # Provider telemetry is best-effort and must not alter pipeline behavior.
        return


def _estimate_model_cost(
    model_uri: str,
    response: Any,
    input_tokens: int,
    output_tokens: int,
) -> float:
    raw_usage = response.raw.get("usage") if isinstance(getattr(response, "raw", None), dict) else None
    if isinstance(raw_usage, dict):
        for key in ("cost", "total_cost"):
            value = raw_usage.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return round(float(value), 6)
    model = model_uri.lower()
    if "deepseek" in model:
        input_rate, output_rate = 0.27, 1.10
    elif "gpt-4" in model or "claude" in model or "o3" in model or "o4" in model:
        input_rate, output_rate = 3.0, 15.0
    elif "gemini" in model:
        input_rate, output_rate = 0.30, 2.50
    else:
        input_rate, output_rate = 0.50, 1.50
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)


def _complete_with_retry(provider: Any, request: Any, attempts: int = 3) -> Any:
    for attempt in range(attempts):
        try:
            return provider.complete(request)
        except ProviderError as exc:
            retryable = any(
                marker in str(exc).lower()
                for marker in ["request failed", "http 429", "http 500", "http 502", "http 503", "http 504"]
            )
            if attempt == attempts - 1 or not retryable:
                raise
            time.sleep(0.5 * (2**attempt))
    raise AssertionError("Provider retry loop exited unexpectedly.")


def _provider_request_extra(model_uri: str, settings: PipelineSettings) -> dict[str, Any]:
    if not model_uri.startswith("openrouter:"):
        return {}
    extra: dict[str, Any] = {"provider": {"sort": settings.provider_sort, "allow_fallbacks": True}}
    if settings.reasoning_effort not in {"", "none", "off"}:
        extra["reasoning"] = {"effort": settings.reasoning_effort, "exclude": True}
    return extra


def _provider_compatible_schema(schema: dict[str, Any]) -> dict[str, Any]:
    unsupported = {"format", "maxItems", "maxLength", "minItems", "minLength", "pattern"}

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in unsupported}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(schema)


def _validate_payload(payload: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        raise RuntimeError(f"Invalid {label}: {'; '.join(error.message for error in errors[:3])}")


def _schema_placeholder(schema: dict[str, Any], text: str) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or not isinstance(required, list):
        return {"answer": text}
    payload: dict[str, Any] = {}
    for field in required:
        field_schema = properties.get(field, {})
        value_type = field_schema.get("type") if isinstance(field_schema, dict) else None
        if value_type == "string":
            payload[field] = text
        elif value_type == "integer":
            payload[field] = 0
        elif value_type == "number":
            payload[field] = 0.0
        elif value_type == "boolean":
            payload[field] = False
        elif value_type == "array":
            payload[field] = []
        elif value_type == "object":
            payload[field] = {}
        else:
            payload[field] = text
    return payload or {"answer": text}


def _document_context(task_spec: dict[str, Any]) -> str:
    background = str(task_spec.get("background", "")).strip()
    marker = "Knowledge source"
    if marker in background:
        return background[background.index(marker) :]
    return background


def _request_model_name(model_uri: str) -> str:
    try:
        return parse_model_uri(model_uri).model
    except ValueError:
        return model_uri.split(":", 1)[1] if ":" in model_uri else model_uri


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    return stripped


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)

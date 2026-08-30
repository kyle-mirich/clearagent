from __future__ import annotations

import threading

import pytest

from clearagent.store import Store
from clearagent.builds import pipeline as pipeline_module
from clearagent.builds.optimization import PromptOptimizationResult
from clearagent.builds.pipeline import PipelineSettings, run_improvement_pipeline
from clearagent.builds.planner import plan_task
from clearagent.builds.scoring import CandidateEvaluation, CaseJudgment


def test_concurrent_sqlite_version_allocation_is_unique_and_project_monotonic(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    project = store.create_project(
        owner_id="owner-a",
        goal="Build an agent whose versions are allocated safely across concurrent builds.",
        name="Concurrent version allocator",
        settings={},
    )
    runs = [
        store.create_run(
            owner_id="owner-a",
            project_id=project.id,
            idempotency_key=f"concurrent-run-{number}",
            budget_profile="quick",
            seed=number,
        )[0]
        for number in range(6)
    ]
    barrier = threading.Barrier(len(runs) + 1)
    errors: list[Exception] = []

    def create_version(run_id: str) -> None:
        barrier.wait()
        try:
            store.create_agent_version(
                project_id=project.id,
                run_id=run_id,
                kind="seed",
                instruction_text=f"Instruction for {run_id}",
                state={"instruction": f"Instruction for {run_id}"},
                validation_metrics={"score": 1.0},
            )
        except Exception as error:  # pragma: no cover - asserted through errors
            errors.append(error)

    threads = [threading.Thread(target=create_version, args=(run.id,)) for run in runs]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    with store.connect() as db:
        allocated = db.execute(
            """
            SELECT version_number
            FROM agent_versions
            WHERE project_id=?
            ORDER BY version_number
            """,
            (project.id,),
        ).fetchall()
    assert [row["version_number"] for row in allocated] == list(range(len(runs)))

    explicit_id = store.create_agent_version(
        project_id=project.id,
        run_id=runs[0].id,
        version_number=10,
        kind="gepa",
        instruction_text="Explicit fixture version",
        state={"instruction": "Explicit fixture version"},
        validation_metrics={"score": 1.0},
    )
    automatic_id = store.create_agent_version(
        project_id=project.id,
        run_id=runs[0].id,
        kind="gepa",
        instruction_text="Automatically allocated version",
        state={"instruction": "Automatically allocated version"},
        validation_metrics={"score": 1.0},
    )
    assert (
        store.get_agent_version(version_id=explicit_id, owner_id="owner-a")["version_number"] == 10
    )
    assert (
        store.get_agent_version(version_id=automatic_id, owner_id="owner-a")["version_number"] == 11
    )


def test_run_queue_event_and_concurrent_event_sequences_are_atomic(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'events.sqlite'}")
    project = store.create_project(
        owner_id="owner-a",
        goal="Build an agent whose concurrent progress events retain a stable total order.",
        name="Concurrent event allocator",
        settings={},
    )
    run, created = store.create_run(
        owner_id="owner-a",
        project_id=project.id,
        idempotency_key="concurrent-events",
        budget_profile="quick",
        seed=7,
    )
    assert created is True
    assert [event.type for event in store.list_events(run.id)] == ["run_queued"]

    event_count = 8
    barrier = threading.Barrier(event_count + 1)
    errors: list[Exception] = []

    def add_progress_event(number: int) -> None:
        barrier.wait()
        try:
            store.add_event(
                run_id=run.id,
                event_type=f"progress_{number}",
                stage="optimizing",
                message=f"Progress event {number}.",
            )
        except Exception as error:  # pragma: no cover - asserted through errors
            errors.append(error)

    threads = [
        threading.Thread(target=add_progress_event, args=(number,))
        for number in range(event_count)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    events = store.list_events(run.id)
    assert [event.sequence for event in events] == list(range(1, event_count + 2))
    assert events[0].type == "run_queued"
    assert {event.type for event in events[1:]} == {
        f"progress_{number}" for number in range(event_count)
    }


def test_feedback_is_scoped_to_an_existing_project_version(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'feedback.sqlite'}")
    project = store.create_project(
        owner_id="owner-a",
        goal="Build an agent whose user feedback can guide a later local improvement run.",
        name="Feedback seam",
        settings={},
    )
    run, _ = store.create_run(
        owner_id="owner-a",
        project_id=project.id,
        idempotency_key="feedback-run",
        budget_profile="quick",
        seed=1,
    )
    version_id = store.create_agent_version(
        project_id=project.id,
        run_id=run.id,
        kind="seed",
        instruction_text="Answer from the approved policy.",
        state={},
        validation_metrics={"score": 1.0},
    )
    store.promote_version(project_id=project.id, owner_id="owner-a", version_id=version_id)

    saved = store.add_feedback(
        project_id=project.id,
        owner_id="owner-a",
        version_id=version_id,
        kind="correction",
        input="Can I return this after 40 days?",
        feedback="The response should state the 30-day boundary.",
        corrected_output={"answer": "Returns are accepted within 30 days."},
    )

    assert saved.version_id == version_id
    assert store.list_feedback(project_id=project.id, owner_id="owner-a") == [saved]

    with pytest.raises(KeyError):
        store.add_feedback(
            project_id=project.id,
            owner_id="owner-a",
            version_id="version-not-in-project",
            kind="negative",
            input="Question",
            feedback="Incorrect",
            corrected_output=None,
        )


def test_repeated_pipeline_builds_continue_the_project_version_sequence(tmp_path, monkeypatch):
    def fake_optimize(**kwargs):
        return PromptOptimizationResult(
            instruction=kwargs["seed_instruction"] + "\nAnswer directly and concisely.",
            validation_score=0.9,
            candidate_count=2,
            metric_calls=2,
        )

    monkeypatch.setattr("clearagent.builds.pipeline.optimize_prompt", fake_optimize)
    store = Store(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    project = store.create_project(
        owner_id="owner-a",
        goal="Build a concise assistant for comparing deployment configuration options.",
        name="Repeated build agent",
        settings={},
    )
    runs = [
        store.create_run(
            owner_id="owner-a",
            project_id=project.id,
            idempotency_key=f"pipeline-run-{number}",
            budget_profile="quick",
            seed=number,
        )[0]
        for number in range(2)
    ]

    for run in runs:
        run_improvement_pipeline(
            store,
            run.id,
            PipelineSettings(deterministic_mode=True),
        )
        assert store.get_run(run.id).status == "completed"

    assert [
        [
            version["version_number"]
            for version in store.list_agent_versions(
                run_id=run.id,
                owner_id="owner-a",
            )
        ]
        for run in runs
    ] == [[0, 1], [2, 3]]


def test_run_activates_seed_fallback_when_no_version_clears_quality_admission(tmp_path, monkeypatch):
    def fake_optimize(**kwargs):
        return PromptOptimizationResult(
            instruction=kwargs["seed_instruction"] + "\nAnswer directly.",
            validation_score=0.9,
            candidate_count=2,
            metric_calls=2,
        )

    def fake_evaluate(self, instruction, examples, on_case_completed=None):
        holdout = examples[0]["split"] == "test"
        case = CaseJudgment(
            example_id=examples[0]["id"],
            score=0.4 if holdout else 0.9,
            passed=not holdout,
            reasoning=(
                "The holdout failed graded and required behavior checks."
                if holdout
                else "Validation cleared graded and required behavior checks."
            ),
        )
        if on_case_completed is not None:
            on_case_completed(1, len(examples), case)
        return CandidateEvaluation(
            score=case.score,
            pass_rate=0.0 if holdout else 1.0,
            required_pass_rate=1.0 if not holdout else 0.5,
            required_passed=not holdout,
            reasoning="Candidate evidence for quality-admission testing.",
            case_results=[case],
        )

    monkeypatch.setattr("clearagent.builds.pipeline.optimize_prompt", fake_optimize)
    monkeypatch.setattr(
        "clearagent.builds.pipeline.PromptEvaluator.evaluate_instruction",
        fake_evaluate,
    )
    store = Store(f"sqlite:///{tmp_path / 'admission-reject.sqlite'}")
    project = store.create_project(
        owner_id="owner-a",
        goal="Build an agent whose weak holdout result must never be promoted.",
        name="Admission gate reject",
        settings={},
    )
    run, _ = store.create_run(
        owner_id="owner-a",
        project_id=project.id,
        idempotency_key="failed-admission-reject",
        budget_profile="quick",
        seed=1,
        dataset_size=5,
    )

    run_improvement_pipeline(
        store,
        run.id,
        PipelineSettings(deterministic_mode=True),
    )

    completed = store.get_run(run.id, owner_id="owner-a")
    assert completed.status == "completed"
    assert completed.best_agent_version_id is not None
    assert completed.promotion_decision["promoted"] is False
    assert completed.promotion_decision["winner"] == "seed"
    assert completed.promotion_decision["fallback"] is True
    assert completed.promotion_decision["deployed_agent_version_id"] == completed.best_agent_version_id
    event_types = [event.type for event in store.list_events(run.id)]
    assert "verification_rejected" in event_types
    assert "run_completed" in event_types
    assert "run_failed" not in event_types
    assert store.get_project(project.id, owner_id="owner-a").promoted_agent_version_id == completed.best_agent_version_id


def test_dataset_generation_degrades_with_a_clear_error_when_every_batch_fails(tmp_path, monkeypatch):
    def failing_complete(*_args, **_kwargs):
        raise RuntimeError("A model returned malformed structured output after three attempts.")

    monkeypatch.setattr("clearagent.builds.pipeline._complete_structured", failing_complete)
    skipped = []

    planning = plan_task("Build an agent that drafts polite customer refund explanations.")
    assert planning.task_spec is not None

    with pytest.raises(RuntimeError, match="lost too many batches"):
        pipeline_module._generate_dataset_live(
            profile="quick",
            seed=3,
            task_spec=planning.task_spec.model_dump(),
            n=4,
            settings=PipelineSettings(deterministic_mode=False, max_concurrency=1),
            on_batch_failed=lambda completed, total, error: skipped.append((completed, total)),
        )

    # Every batch reported its skip with a running progress count.
    assert len(skipped) == 1
    assert skipped == [(1, 1)]


def test_holdout_comparison_selects_a_version_even_when_absolute_scores_are_weak(tmp_path, monkeypatch):
    def fake_optimize(**kwargs):
        return PromptOptimizationResult(
            instruction=kwargs["seed_instruction"] + "\nAnswer directly.",
            validation_score=0.9,
            candidate_count=2,
            metric_calls=2,
        )

    def fake_evaluate(self, instruction, examples, on_case_completed=None):
        holdout = examples[0]["split"] == "test"
        # Holdout quality clears the admission floors while staying far below
        # the validation split's graded score: selection compares holdout
        # outcomes, not raw score levels.
        case = CaseJudgment(
            example_id=examples[0]["id"],
            score=0.75 if holdout else 0.9,
            passed=True,
            reasoning=(
                "The holdout cleared admission with a modest graded score."
                if holdout
                else "Validation cleared graded and required behavior checks."
            ),
        )
        if on_case_completed is not None:
            on_case_completed(1, len(examples), case)
        return CandidateEvaluation(
            score=case.score,
            pass_rate=1.0,
            required_pass_rate=1.0,
            required_passed=True,
            reasoning="Candidate evidence for quality-admission testing.",
            case_results=[case],
        )

    monkeypatch.setattr("clearagent.builds.pipeline.optimize_prompt", fake_optimize)
    monkeypatch.setattr(
        "clearagent.builds.pipeline.PromptEvaluator.evaluate_instruction",
        fake_evaluate,
    )
    store = Store(f"sqlite:///{tmp_path / 'admission.sqlite'}")
    project = store.create_project(
        owner_id="owner-a",
        goal="Build an agent whose weak holdout result can never be promoted.",
        name="Admission gate",
        settings={},
    )
    run, _ = store.create_run(
        owner_id="owner-a",
        project_id=project.id,
        idempotency_key="failed-admission",
        budget_profile="quick",
        seed=1,
        dataset_size=5,
    )

    run_improvement_pipeline(
        store,
        run.id,
        PipelineSettings(deterministic_mode=True),
    )

    completed = store.get_run(run.id, owner_id="owner-a")
    assert completed.status == "completed"
    assert completed.best_agent_version_id is not None
    assert completed.baseline_test_score == 0.75
    assert completed.promotion_decision["winner"] == "seed"
    assert completed.promotion_decision["deployed_agent_version_id"] == completed.best_agent_version_id
    assert completed.promotion_decision["quality_admission"]["seed"]["holdout_pass_rate"] == 1.0
    assert store.get_project(project.id, owner_id="owner-a").promoted_agent_version_id == completed.best_agent_version_id
    assert "verification_completed" in [event.type for event in store.list_events(run.id)]


def _project_versions(tmp_path, *, count: int) -> tuple[Store, str, list[str]]:
    store = Store(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    project = store.create_project(
        owner_id="owner-a",
        goal="Build a focused project-version migration test agent.",
        name="Version migration agent",
        settings={},
    )
    versions = []
    for number in range(count):
        run, _ = store.create_run(
            owner_id="owner-a",
            project_id=project.id,
            idempotency_key=f"run-{number}",
            budget_profile="quick",
            seed=number,
        )
        versions.append(
            store.create_agent_version(
                project_id=project.id,
                run_id=run.id,
                version_number=number,
                kind="gepa",
                instruction_text=f"Instruction {number}",
                state={"instruction": f"Instruction {number}"},
                validation_metrics={"score": 1.0},
                test_metrics={"score": 1.0},
            )
        )
    return store, project.id, versions

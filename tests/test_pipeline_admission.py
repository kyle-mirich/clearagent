import pytest

from clearagent.builds import pipeline
from clearagent.builds.optimization import PromptOptimizationResult
from clearagent.builds.scoring import CandidateEvaluation, CaseJudgment
from clearagent.store import Store


@pytest.mark.parametrize(
    "seed_eligible,optimized_eligible,incumbent_eligible,winner",
    [
        (True, False, None, "seed"),
        (False, True, None, "optimized"),
        (False, False, True, "incumbent"),
        (False, False, False, None),
    ],
)
def test_pipeline_selects_only_admitted_versions_and_preserves_deployment(
    tmp_path,
    monkeypatch,
    seed_eligible,
    optimized_eligible,
    incumbent_eligible,
    winner,
):
    def optimize(**kwargs):
        return PromptOptimizationResult(
            instruction=kwargs["seed_instruction"] + "\nAnswer directly.",
            validation_score=0.99,
            candidate_count=2,
            metric_calls=2,
        )

    def evaluate(self, instruction, examples, on_case_completed=None):
        kind = (
            "incumbent"
            if instruction == "Incumbent instruction."
            else "optimized"
            if "Answer directly." in instruction
            else "seed"
        )
        eligible = {
            "seed": seed_eligible,
            "optimized": optimized_eligible,
            "incumbent": incumbent_eligible,
        }[kind]
        holdout = examples[0]["split"] == "test"
        rate = 0.8 if eligible or not holdout else 0.79
        score = {"seed": 0.85, "optimized": 0.99, "incumbent": 0.8}[kind]
        return CandidateEvaluation(
            score=score,
            pass_rate=rate,
            required_pass_rate=rate,
            reasoning="Deterministic holdout evidence for admission regression coverage.",
            case_results=[
                CaseJudgment(
                    example_id=examples[0]["id"],
                    score=score,
                    passed=bool(eligible),
                    reasoning="This case provides deterministic quality evidence.",
                )
            ],
        )

    monkeypatch.setattr(pipeline, "optimize_prompt", optimize)
    monkeypatch.setattr(pipeline.PromptEvaluator, "evaluate_instruction", evaluate)
    store = Store(f"sqlite:///{tmp_path / 'admission.sqlite'}")
    project = store.create_project(
        owner_id="test",
        goal="Build a release notes agent.",
        name="Admission regression",
        settings={},
    )
    run, _ = store.create_run(
        owner_id="test",
        project_id=project.id,
        idempotency_key="run",
        budget_profile="quick",
        seed=3,
        dataset_size=5,
    )
    incumbent_id = None
    if incumbent_eligible is not None:
        incumbent_id = store.create_agent_version(
            project_id=project.id,
            run_id=run.id,
            kind="seed",
            instruction_text="Incumbent instruction.",
            state={},
            validation_metrics={"score": 0.8},
        )
        store.promote_version(project_id=project.id, owner_id="test", version_id=incumbent_id)

    if winner is None:
        with pytest.raises(RuntimeError, match="No agent version passed quality admission"):
            pipeline.run_improvement_pipeline(
                store, run.id, pipeline.PipelineSettings(deterministic_mode=True)
            )
        assert store.get_run(run.id).status == "failed"
        assert (
            store.get_project(project.id, owner_id="test").promoted_agent_version_id == incumbent_id
        )
    else:
        pipeline.run_improvement_pipeline(
            store, run.id, pipeline.PipelineSettings(deterministic_mode=True)
        )
        completed = store.get_run(run.id)
        assert completed.status == "completed"
        decision = completed.promotion_decision
        assert decision["winner"] == winner
        assert decision["quality_admission"]["thresholds_enforced"] is True
        assert (
            store.get_project(project.id, owner_id="test").promoted_agent_version_id
            == completed.best_agent_version_id
        )
        if winner == "incumbent":
            assert decision["promoted"] is False
            assert completed.best_agent_version_id == incumbent_id

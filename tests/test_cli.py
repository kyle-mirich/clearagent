from typer.testing import CliRunner

from clearagent.command import app

runner = CliRunner()


def test_eval_deterministic_scores_an_instruction():
    result = runner.invoke(
        app,
        [
            "eval",
            "Build a release notes summarizer for changelog entries.",
            "--instruction",
            "Summarize changelog entries into added, changed, and fixed bullets.",
            "--deterministic",
            "--cases",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Score" in result.output


def test_build_deterministic_runs_the_full_loop(tmp_path):
    result = runner.invoke(
        app,
        [
            "build",
            "Build a release notes summarizer for changelog entries.",
            "--deterministic",
            "--level",
            "quick",
            "--export",
            str(tmp_path / "prompt.md"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Winner" in result.output
    assert (tmp_path / "prompt.md").exists()


def test_build_collects_all_clarifications_and_executes_the_approved_plan(tmp_path, monkeypatch):
    from clearagent.builds.module import Build
    from clearagent.builds import pipeline

    requests = []
    plans = []
    executed = []
    original_plan = Build.plan

    def record_plan(self, request):
        requests.append(request)
        planned = original_plan(self, request)
        plans.append(planned)
        return planned

    def finish_build(store, run_id, settings):
        run = store.get_run(run_id)
        project = store.get_project(run.project_id, owner_id=run.owner_id)
        executed.append((project.settings, settings))
        version = store.create_agent_version(
            project_id=project.id, run_id=run_id, kind="seed",
            instruction_text="Use the approved plan.", state={}, validation_metrics={},
        )
        store.update_run(run_id, status="completed", best_agent_version_id=version)

    monkeypatch.setattr(Build, "plan", record_plan)
    monkeypatch.setattr(pipeline, "run_improvement_pipeline", finish_build)
    result = runner.invoke(
        app,
        ["build", "Build documentation agent.", "--deterministic", "--database-url", f"sqlite:///{tmp_path / 'clarification.sqlite'}"],
        input="Engineers\nApproved sources only\nNumbered steps\n",
    )
    assert result.exit_code == 0, result.output
    assert requests[1].answers == {
        "audience_outcome": "Engineers",
        "constraints": "Approved sources only",
        "tone_format": "Numbered steps",
    }
    assert executed[0][0]["task_spec"] == plans[1].task_spec.model_dump(mode="json")
    assert executed[0][0]["agent_prd"] == plans[1].agent_prd.model_dump(mode="json")
    assert executed[0][1].task_max_tokens == 2_000
    assert executed[0][1].budget_tracker is not None

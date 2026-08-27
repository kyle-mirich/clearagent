import threading

from clearagent.builds.module import Build
from clearagent.builds.pipeline import run_improvement_pipeline
from clearagent.config import Settings
from clearagent.models import PlanningRequest
from clearagent.store import Store


def test_deterministic_build_completes_with_promotion_decision(tmp_path):
    settings = Settings(
        deterministic_mode=True,
        database_url=f"sqlite:///{tmp_path / 'engine.sqlite'}",
        run_inline=True,
        _env_file=None,
    )
    store = Store(settings.database_url)
    build = Build(settings)

    planning = build.plan(PlanningRequest(goal="Build a release notes summarizer for changelog entries."))
    assert planning.task_spec is not None
    project = store.create_project(owner_id="test", goal="release notes", name="Summarizer", settings={})
    run, _ = store.create_run(
        owner_id="test",
        project_id=project.id,
        idempotency_key="engine-test",
        budget_profile="quick",
        seed=11,
        dataset_size=5,
    )

    worker = threading.Thread(
        target=run_improvement_pipeline,
        args=(store, run.id),
        kwargs={"settings": build.pipeline_settings},
        daemon=True,
    )
    worker.start()
    worker.join(timeout=120)
    assert not worker.is_alive()

    final = store.get_run(run.id, owner_id="test")
    assert final.status == "completed"
    assert final.promotion_decision is not None
    assert final.best_agent_version_id is not None

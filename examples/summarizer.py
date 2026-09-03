"""Build and auto-optimize an agent from a goal, then print the winning prompt."""

from clearagent.builds.module import Build
from clearagent.command import _store
from clearagent.config import Settings
from clearagent.models import PlanningRequest

GOAL = "Build a release notes summarizer for changelog entries."

settings = Settings(deterministic_mode=True, _env_file=None)
store = _store(settings)
engine = Build(settings)

planning = engine.plan(PlanningRequest(goal=GOAL))
assert planning.task_spec is not None

project = store.create_project(owner_id="example", goal=GOAL, name=planning.task_spec.name, settings={})
run, _ = store.create_run(
    owner_id="example",
    project_id=project.id,
    idempotency_key="example-summarizer",
    budget_profile="quick",
    seed=11,
    dataset_size=5,
)

from clearagent.builds.pipeline import run_improvement_pipeline

thread = __import__("threading").Thread(
    target=run_improvement_pipeline,
    args=(store, run.id),
    kwargs={"settings": engine.pipeline_settings},
)
thread.start()
thread.join()

final = store.get_run(run.id, owner_id="example")
print(final.promotion_decision["reason"])
best = store.get_agent_version(version_id=final.best_agent_version_id, owner_id="example")
print(best["instruction_text"])

"""ClearAgent command line interface.

Commands:
  build      Plan, evaluate, auto-optimize, and promote an agent from a goal.
  eval       Score an instruction against a generated dataset (no optimization).
  serve      Run the local FastAPI.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import typer

from clearagent.config import Settings

app = typer.Typer(help="ClearAgent: eval-first agents on LangGraph.", no_args_is_help=True)

TERMINAL = {"completed", "failed", "canceled"}


def _settings(deterministic: bool, database: str | None) -> Settings:
    if deterministic and database:
        return Settings(deterministic_mode=True, database_url=database)  # type: ignore[call-arg]
    if deterministic:
        return Settings(deterministic_mode=True)  # type: ignore[call-arg]
    if database:
        return Settings(database_url=database)  # type: ignore[call-arg]
    return Settings()


@app.command()
def build(
    goal: str = typer.Argument(..., min=20, help="What the agent should do."),
    level: str = typer.Option("quick", "--level", help="quick | standard | deep."),
    seed: int = typer.Option(7, "--seed"),
    deterministic: bool = typer.Option(False, "--deterministic", help="Offline template mode; no provider calls."),
    database: str | None = typer.Option(None, "--database-url"),
    export: Path | None = typer.Option(None, "--export", help="Write the winning prompt to this file."),
) -> None:
    """Plan, evaluate, auto-optimize, and promote an agent from a goal."""
    from clearagent.builds.module import Build
    from clearagent.builds.pipeline import run_improvement_pipeline
    from clearagent.models import PlanningRequest

    settings = _settings(deterministic, database)
    store = _store(settings)
    build_engine = Build(settings)

    typer.echo("Planning the agent and its judges…")
    planning = build_engine.plan(PlanningRequest(goal=goal))
    if planning.status == "needs_clarification":
        for question in planning.questions:
            answer = typer.prompt(f"{question.question}\n  options: {' | '.join(question.options)}")
            planning = build_engine.plan(
                PlanningRequest(goal=goal, answers={**{q.id: "" for q in planning.questions}, question.id: answer})
            )
            break
    if planning.task_spec is None:
        typer.echo("Planning could not produce a task specification.", err=True)
        raise typer.Exit(code=1)

    project = store.create_project(owner_id="cli", goal=goal, name=planning.task_spec.name, settings={})
    run, _ = store.create_run(
        owner_id="cli",
        project_id=project.id,
        idempotency_key=f"cli-{int(time.time())}",
        budget_profile=level,
        seed=seed,
    )
    typer.echo(f"Run {run.id} queued. Building…")

    worker = threading.Thread(
        target=run_improvement_pipeline,
        args=(store, run.id),
        kwargs={"settings": build_engine.pipeline_settings},
        daemon=True,
    )
    worker.start()

    seen = 0
    while True:
        events = store.list_events(run.id)
        for event in events[seen:]:
            typer.echo(f"  [{event.stage}] {event.message}")
        seen = len(events)
        current = store.get_run(run.id, owner_id="cli")
        if current.status in TERMINAL:
            break
        time.sleep(0.5)

    final = store.get_run(run.id, owner_id="cli")
    if final.status != "completed":
        error = (final.error or {}).get("message", "unknown error")
        typer.echo(f"Build failed: {error}", err=True)
        raise typer.Exit(code=1)

    decision = final.promotion_decision or {}
    typer.echo(f"Winner: {decision.get('winner')} — {decision.get('reason')}")
    best = store.get_agent_version(version_id=final.best_agent_version_id, owner_id="cli")
    typer.echo(f"Validation {final.baseline_validation_score} → {final.best_validation_score}; "
               f"holdout {final.baseline_test_score} → {final.optimized_test_score}")
    if export:
        export.write_text(best["instruction_text"])
        typer.echo(f"Winning prompt written to {export}")


@app.command()
def eval(
    goal: str = typer.Argument(..., min=20, help="What the agent should do."),
    instruction: str = typer.Option(..., "--instruction", help="The prompt to score."),
    cases: int = typer.Option(6, "--cases", min=2, max=12),
    deterministic: bool = typer.Option(False, "--deterministic"),
) -> None:
    """Score an instruction against a generated dataset (no optimization)."""
    from clearagent.builds.datasets import generate_synthetic_examples
    from clearagent.builds.pipeline import PromptEvaluator, PipelineSettings
    from clearagent.builds.planner import plan_task

    settings = PipelineSettings(deterministic_mode=deterministic)
    planning = plan_task(goal)
    if planning.task_spec is None:
        typer.echo("Could not derive a task specification from that goal.", err=True)
        raise typer.Exit(code=1)
    task_spec = planning.task_spec.model_dump()
    dataset = generate_synthetic_examples(profile="quick", seed=7, task_spec=task_spec, n=max(15, cases * 5))
    validation = [example for example in dataset["examples"] if example["split"] == "validation"][:cases]

    evaluator = PromptEvaluator(task_spec=task_spec, settings=settings)
    result = evaluator.evaluate_instruction(instruction, validation)
    typer.echo(f"Score {result.score:.2f} · pass rate {result.pass_rate:.0%} · "
               f"required behaviors {'passed' if result.required_passed else 'FAILED'}")
    for failure, count in sorted(result.failure_summary.items(), key=lambda item: -item[1]):
        typer.echo(f"  {count}× {failure}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Run the local FastAPI."""
    import uvicorn

    uvicorn.run("clearagent.app:app", host=host, port=port)


def _store(settings: Settings):
    from clearagent.store import Store

    return Store(settings.database_url)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

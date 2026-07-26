import importlib
import json
import sys
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from clearagent.chat.store import DEFAULT_CHAT_DB
from clearagent.config import tracing_config
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalSuite
from clearagent.storage.sqlite import DEFAULT_TRACE_DB, SQLiteTraceStore

app = typer.Typer()
trace_app = typer.Typer()
promptfoo_app = typer.Typer()
baseline_app = typer.Typer()
app.add_typer(trace_app, name="trace")
app.add_typer(promptfoo_app, name="promptfoo")
app.add_typer(baseline_app, name="baseline")
console = Console()


def _cli_tracing_config() -> tuple[bool, Path]:
    try:
        return tracing_config()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def import_object(path: str) -> Any:
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    module_path, object_name = _split_object_path(path)
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name == module_path or module_path.startswith(f"{exc.name}."):
            raise typer.BadParameter(f"Could not import module {module_path!r}.") from exc
        raise
    try:
        return getattr(module, object_name)
    except AttributeError as exc:
        raise typer.BadParameter(
            f"Could not find object {object_name!r} in module {module_path!r}."
        ) from exc


def _split_object_path(path: str) -> tuple[str, str]:
    if ":" not in path:
        raise typer.BadParameter("agent path must use module:object format.")
    module_path, object_name = path.split(":", 1)
    if not module_path or not object_name:
        raise typer.BadParameter("agent path must use module:object format.")
    return module_path, object_name


@app.command()
def init() -> None:
    path = Path(".clearagent/config.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            """[project]
name = "clearagent-project"

[tracing]
enabled = true
db_path = ".clearagent/traces.sqlite"
""",
            encoding="utf-8",
        )
    console.print(f"Created {path}")


@app.command()
def run(agent_path: str, input: str, no_trace: bool = typer.Option(False, "--no-trace")) -> None:
    load_dotenv()
    agent = import_object(agent_path)
    config_trace, config_db = _cli_tracing_config()
    agent.trace_db_path = config_db
    result = agent.run(input, trace=config_trace and not no_trace)
    console.print(result.output)


@app.command()
def chat(
    agent_path: str,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    chat_db: Path = typer.Option(DEFAULT_CHAT_DB, "--chat-db"),
    allow_settings_mutation: bool = typer.Option(False, "--allow-settings-mutation"),
) -> None:
    import uvicorn

    from clearagent.chat.app import create_chat_app

    load_dotenv()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter(
            "ClearAgent chat is local-only. Keep --host on a loopback address."
        )
    agent = import_object(agent_path)
    config_trace, config_db = _cli_tracing_config()
    agent.trace = config_trace
    agent.trace_db_path = config_db
    uvicorn.run(
        create_chat_app(
            agent,
            chat_db_path=chat_db,
            allow_settings_mutation=allow_settings_mutation,
        ),
        host=host,
        port=port,
    )


@app.command(name="eval")
def eval_command(
    agent_path: str,
    suite_path: str | None = typer.Argument(None),
    trace_db: Path | None = None,
) -> None:
    if agent_path == "all":
        console.print("No eval discovery config found. Pass <agent_path> <suite_path>.")
        return
    if suite_path is None:
        raise typer.BadParameter("suite_path is required unless using 'eval all'.")
    load_dotenv()
    agent = import_object(agent_path)
    _, config_db = _cli_tracing_config()
    agent.trace_db_path = trace_db or config_db
    report = EvalRunner(agent).run_suite(EvalSuite.from_yaml(suite_path))
    console.print(f"Suite: {report.suite_name}")
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        variant = f" variant={result.variant}" if result.variant else ""
        console.print(f"{status} {result.case_name}{variant} run_id={result.run_id}")
        if not result.passed:
            console.print(f"Output: {result.final_output}")
            console.print(f"Checks: {[check for check in result.checks if not check['passed']]}")
    console.print(f"{report.passed} passed, {report.failed} failed")
    if report.failed:
        raise typer.Exit(1)


@app.command("trace-to-eval")
def trace_to_eval(
    run_id: str,
    out: Path = typer.Option(..., "--out"),
    trace_db: Path = DEFAULT_TRACE_DB,
    suite_name: str | None = typer.Option(None, "--suite-name"),
    case_name: str | None = typer.Option(None, "--case-name"),
) -> None:
    from clearagent.evals.generate import write_eval_case_from_trace

    try:
        write_eval_case_from_trace(
            SQLiteTraceStore(trace_db),
            run_id,
            out,
            suite_name=suite_name,
            case_name=case_name,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(str(out))


@app.command("trace-report")
def trace_report(
    run_id: str,
    out: Path | None = typer.Option(None, "--out"),
    trace_db: Path = DEFAULT_TRACE_DB,
) -> None:
    from clearagent.reports import render_trace_report

    try:
        report = render_trace_report(SQLiteTraceStore(trace_db), run_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if out:
        out.write_text(report, encoding="utf-8")
        console.print(str(out))
    else:
        console.print(report)


@app.command("iterate")
def iterate(
    agent_path: str,
    suite_path: str,
    model: list[str] | None = typer.Option(None, "--model"),
    temperature: list[float] | None = typer.Option(None, "--temperature"),
) -> None:
    from clearagent.evals.iteration import run_eval_iterations

    load_dotenv()
    agent = import_object(agent_path)
    suite = EvalSuite.from_yaml(suite_path)
    temperatures: list[float | None] | None = list(temperature) if temperature else None
    summary = run_eval_iterations(
        agent,
        suite,
        models=model or None,
        temperatures=temperatures,
    )
    typer.echo(json.dumps(summary, indent=2))


@trace_app.command("list")
def trace_list(trace_db: Path = DEFAULT_TRACE_DB) -> None:
    store = SQLiteTraceStore(trace_db)
    table = Table("Run ID", "Agent", "Status", "Started")
    for run in store.list_runs():
        table.add_row(run["id"], run["agent_name"], run["status"], run["started_at"])
    console.print(table)


@trace_app.command("show")
def trace_show(run_id: str, trace_db: Path = DEFAULT_TRACE_DB) -> None:
    store = SQLiteTraceStore(trace_db)
    run = store.get_run(run_id)
    if not run:
        raise typer.BadParameter(f"Missing run {run_id}")
    console.print(f"Run: {run['id']}")
    console.print(f"Agent: {run['agent_name']}")
    console.print(f"Input: {run['root_input']}")
    console.print(f"Final: {run['final_output']}")


@trace_app.command("turns")
def trace_turns(run_id: str, trace_db: Path = DEFAULT_TRACE_DB) -> None:
    store = SQLiteTraceStore(trace_db)
    turns = store.get_turns(run_id)
    if not turns:
        raise typer.BadParameter(f"Missing turns for run {run_id}")
    table = Table("Turn", "Node", "Status", "Final")
    for turn in turns:
        table.add_row(str(turn["turn_index"]), turn["node_name"], turn["status"], turn["final_output"] or "")
    console.print(table)


@app.command()
def request(run_id: str, turn: int = 0, trace_db: Path = DEFAULT_TRACE_DB) -> None:
    payload = _request_json(run_id, turn, trace_db)
    typer.echo(json.dumps(payload, indent=2))


@app.command("replay-request")
def replay_request(
    run_id: str,
    turn: int = 0,
    out: Path = typer.Option(..., "--out"),
    trace_db: Path = DEFAULT_TRACE_DB,
) -> None:
    payload = _request_json(run_id, turn, trace_db)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@app.command("replay")
def replay_call(run_id: str, turn: int = 0, trace_db: Path = DEFAULT_TRACE_DB) -> None:
    from clearagent.replay import replay_model_call

    load_dotenv()
    try:
        response = replay_model_call(trace_db, run_id, turn=turn)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(response.output_text or "")


@app.command("diff")
def diff_call(run_id: str, turn: int = 0, trace_db: Path = DEFAULT_TRACE_DB) -> None:
    from clearagent.replay import diff_model_call

    load_dotenv()
    try:
        diff = diff_model_call(trace_db, run_id, turn=turn)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    table = Table("Field", "Before", "After")
    table.add_row("output", diff.before_output or "", diff.after_output or "")
    table.add_row("finish_reason", diff.before_finish_reason or "", diff.after_finish_reason or "")
    table.add_row(
        "usage",
        json.dumps(diff.before_usage or {}, sort_keys=True),
        json.dumps(diff.after_usage or {}, sort_keys=True),
    )
    console.print(table)
    if diff.changed:
        raise typer.Exit(1)


def _request_json(run_id: str, turn: int, trace_db: Path) -> dict[str, Any]:
    store = SQLiteTraceStore(trace_db)
    row = store.get_model_call_for_turn(run_id, turn)
    if not row:
        raise typer.BadParameter(f"Missing model request for run {run_id} turn {turn}")
    try:
        payload = json.loads(row["request_json"])
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Malformed stored model request for run {run_id} turn {turn}") from exc
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"Malformed stored model request for run {run_id} turn {turn}")
    return payload


@promptfoo_app.command("export")
def promptfoo_export(agent_path: str, suite_path: str, out: Path) -> None:
    from clearagent.evals.promptfoo_export import export_promptfoo_config

    try:
        export_promptfoo_config(agent_path, EvalSuite.from_yaml(suite_path), out)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(str(out))


@promptfoo_app.command("target")
def promptfoo_target(agent_path: str, out: Path) -> None:
    from clearagent.evals.promptfoo_export import write_promptfoo_target

    try:
        write_promptfoo_target(agent_path, out)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(str(out))


@baseline_app.command("save")
def baseline_save(suite_run_id: str, name: str = typer.Option(..., "--name"), trace_db: Path = DEFAULT_TRACE_DB) -> None:
    from clearagent.evals.baseline import save_baseline

    try:
        baseline_id = save_baseline(SQLiteTraceStore(trace_db), suite_run_id, name=name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(baseline_id)


@baseline_app.command("compare")
def baseline_compare(baseline_name: str, suite_run_id: str, trace_db: Path = DEFAULT_TRACE_DB) -> None:
    from clearagent.evals.baseline import compare_baseline

    try:
        comparison = compare_baseline(SQLiteTraceStore(trace_db), baseline_name, suite_run_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Regressions: {', '.join(comparison.regressions) or 'none'}")
    console.print(f"Improvements: {', '.join(comparison.improvements) or 'none'}")

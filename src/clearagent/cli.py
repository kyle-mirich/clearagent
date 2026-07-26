import importlib
import json
import sys
from contextlib import nullcontext, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from clearagent import __version__
from clearagent.chat.store import DEFAULT_CHAT_DB
from clearagent.config import tracing_config
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalSuite
from clearagent.storage.protocol import TraceRun, TraceTurn
from clearagent.storage.sqlite import DEFAULT_TRACE_DB, SQLiteTraceStore

app = typer.Typer(help="Build, run, evaluate, and inspect local-first AI agents.")
trace_app = typer.Typer(help="Inspect locally stored trace runs and turns.")
promptfoo_app = typer.Typer(help="Export ClearAgent evals for Promptfoo.")
baseline_app = typer.Typer(help="Save and compare evaluation baselines.")
app.add_typer(trace_app, name="trace")
app.add_typer(promptfoo_app, name="promptfoo")
app.add_typer(baseline_app, name="baseline")
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"clearagent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed ClearAgent version and exit.",
    ),
) -> None:
    """Build, run, evaluate, and inspect local-first AI agents."""


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
    """Create a starter project configuration in the current directory."""
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
def run(
    agent_path: str = typer.Argument(..., help="Agent import path in module:object form."),
    input: str = typer.Argument(..., help="Input text to send to the agent."),
    no_trace: bool = typer.Option(
        False,
        "--no-trace",
        help="Run without recording a local trace.",
    ),
) -> None:
    """Run an importable agent once and print its final output."""
    load_dotenv()
    agent = import_object(agent_path)
    config_trace, config_db = tracing_config()
    agent.trace_db_path = config_db
    result = agent.run(input, trace=config_trace and not no_trace)
    console.print(result.output)


@app.command()
def chat(
    agent_path: str = typer.Argument(..., help="Agent import path in module:object form."),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Loopback address on which to serve the chat UI.",
    ),
    port: int = typer.Option(8000, "--port", help="TCP port on which to serve the chat UI."),
    chat_db: Path = typer.Option(
        DEFAULT_CHAT_DB,
        "--chat-db",
        help="SQLite database used for chat sessions.",
    ),
    allow_settings_mutation: bool = typer.Option(
        False,
        "--allow-settings-mutation",
        help="Allow the chat UI to change supported agent settings.",
    ),
) -> None:
    """Serve the local chat UI for an importable agent."""
    import uvicorn

    from clearagent.chat.app import create_chat_app

    load_dotenv()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter(
            "ClearAgent chat is local-only. Keep --host on a loopback address."
        )
    agent = import_object(agent_path)
    config_trace, config_db = tracing_config()
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
    agent_path: str = typer.Argument(..., help="Agent import path in module:object form."),
    suite_path: str | None = typer.Argument(None, help="Path to the eval suite YAML file."),
    trace_db: Path | None = typer.Option(
        None,
        "--trace-db",
        help="SQLite trace database; defaults to project configuration.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the complete evaluation report as JSON.",
    ),
) -> None:
    """Run an evaluation suite against an importable agent."""
    if agent_path == "all":
        message = "No eval discovery config found. Pass <agent_path> <suite_path>."
        if json_output:
            _write_json({"error": message})
        else:
            console.print(message)
        raise typer.Exit(2)
    if suite_path is None:
        raise typer.BadParameter("suite_path is required unless using 'eval all'.")
    incidental_output = redirect_stdout(sys.stderr) if json_output else nullcontext()
    with incidental_output:
        load_dotenv()
        agent = import_object(agent_path)
        _, config_db = tracing_config()
        agent.trace_db_path = trace_db or config_db
        report = EvalRunner(agent).run_suite(EvalSuite.from_yaml(suite_path))
    if json_output:
        _write_json(report.model_dump(mode="json"))
    else:
        console.print(f"Suite: {report.suite_name}")
        for result in report.results:
            status = "PASS" if result.passed else "FAIL"
            variant = f" variant={result.variant}" if result.variant else ""
            console.print(f"{status} {result.case_name}{variant} run_id={result.run_id}")
            if not result.passed:
                console.print(f"Output: {result.final_output}")
                console.print(
                    f"Checks: {[check for check in result.checks if not check['passed']]}"
                )
        console.print(f"{report.passed} passed, {report.failed} failed")
    if report.failed:
        raise typer.Exit(1)


@app.command("trace-to-eval")
def trace_to_eval(
    run_id: str = typer.Argument(..., help="Trace run ID to convert."),
    out: Path = typer.Option(..., "--out", help="Destination eval suite YAML file."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
    suite_name: str | None = typer.Option(
        None,
        "--suite-name",
        help="Name for the generated eval suite.",
    ),
    case_name: str | None = typer.Option(
        None,
        "--case-name",
        help="Name for the generated eval case.",
    ),
) -> None:
    """Generate a starter eval case from a recorded trace."""
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
    run_id: str = typer.Argument(..., help="Trace run ID to render."),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write Markdown to this path instead of standard output.",
    ),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
) -> None:
    """Render a Markdown report for a recorded trace."""
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
    agent_path: str = typer.Argument(..., help="Agent import path in module:object form."),
    suite_path: str = typer.Argument(..., help="Path to the eval suite YAML file."),
    model: list[str] | None = typer.Option(
        None,
        "--model",
        help="Model URI to evaluate; repeat to compare multiple models.",
    ),
    temperature: list[float] | None = typer.Option(
        None,
        "--temperature",
        help="Temperature to evaluate; repeat to compare multiple values.",
    ),
) -> None:
    """Evaluate model and temperature variants for one suite."""
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
def trace_list(
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a stable run-summary object as JSON.",
    ),
) -> None:
    """List recorded trace runs, newest first."""
    store = SQLiteTraceStore(trace_db)
    runs = store.list_runs()
    if json_output:
        _write_json({"runs": [_trace_run_summary(run) for run in runs]})
        return
    table = Table("Run ID", "Agent", "Status", "Started")
    for run in runs:
        table.add_row(run["id"], run["agent_name"], run["status"], run["started_at"])
    console.print(table)


@trace_app.command("show")
def trace_show(
    run_id: str = typer.Argument(..., help="Trace run ID to inspect."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a stable run-detail object as JSON.",
    ),
) -> None:
    """Show the input, output, status, timing, and usage for one run."""
    store = SQLiteTraceStore(trace_db)
    run = store.get_run(run_id)
    if not run:
        raise typer.BadParameter(f"Missing run {run_id}")
    if json_output:
        _write_json(_trace_run_detail(run))
        return
    console.print(f"Run: {run['id']}")
    console.print(f"Agent: {run['agent_name']}")
    console.print(f"Input: {run['root_input']}")
    console.print(f"Final: {run['final_output']}")


@trace_app.command("turns")
def trace_turns(
    run_id: str = typer.Argument(..., help="Trace run ID whose turns should be listed."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a stable turn-summary object as JSON.",
    ),
) -> None:
    """List model/tool-loop turns for one recorded run."""
    store = SQLiteTraceStore(trace_db)
    turns = store.get_turns(run_id)
    if not turns:
        raise typer.BadParameter(f"Missing turns for run {run_id}")
    if json_output:
        _write_json(
            {
                "run_id": run_id,
                "turns": [_trace_turn_summary(turn) for turn in turns],
            }
        )
        return
    table = Table("Turn", "Node", "Status", "Final")
    for turn in turns:
        table.add_row(
            str(turn["turn_index"]),
            turn["node_name"],
            turn["status"],
            turn["final_output"] or "",
        )
    console.print(table)


@app.command()
def request(
    run_id: str = typer.Argument(..., help="Trace run ID containing the model request."),
    turn: int = typer.Option(0, "--turn", help="Zero-based turn index to inspect."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
) -> None:
    """Print the stored provider request for one trace turn as JSON."""
    payload = _request_json(run_id, turn, trace_db)
    typer.echo(json.dumps(payload, indent=2))


@app.command("replay-request")
def replay_request(
    run_id: str = typer.Argument(..., help="Trace run ID containing the model request."),
    turn: int = typer.Option(0, "--turn", help="Zero-based turn index to export."),
    out: Path = typer.Option(..., "--out", help="Destination JSON file."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
) -> None:
    """Write a stored provider request to a replayable JSON file."""
    payload = _request_json(run_id, turn, trace_db)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@app.command("replay")
def replay_call(
    run_id: str = typer.Argument(..., help="Trace run ID containing the model request."),
    turn: int = typer.Option(0, "--turn", help="Zero-based turn index to replay."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
) -> None:
    """Replay a recorded provider request and print its output."""
    from clearagent.replay import replay_model_call

    load_dotenv()
    try:
        response = replay_model_call(trace_db, run_id, turn=turn)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(response.output_text or "")


@app.command("diff")
def diff_call(
    run_id: str = typer.Argument(..., help="Trace run ID containing the model request."),
    turn: int = typer.Option(0, "--turn", help="Zero-based turn index to replay and compare."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the replay comparison as JSON.",
    ),
) -> None:
    """Replay a model call and compare its response with the stored response."""
    from clearagent.replay import diff_model_call

    try:
        incidental_output = redirect_stdout(sys.stderr) if json_output else nullcontext()
        with incidental_output:
            load_dotenv()
            diff = diff_model_call(trace_db, run_id, turn=turn)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _write_json(diff.model_dump(mode="json"))
    else:
        table = Table("Field", "Before", "After")
        table.add_row("output", diff.before_output or "", diff.after_output or "")
        table.add_row(
            "finish_reason",
            diff.before_finish_reason or "",
            diff.after_finish_reason or "",
        )
        table.add_row(
            "usage",
            json.dumps(diff.before_usage or {}, sort_keys=True),
            json.dumps(diff.after_usage or {}, sort_keys=True),
        )
        console.print(table)
    if diff.changed:
        raise typer.Exit(1)


def _write_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _trace_run_summary(run: TraceRun) -> dict[str, Any]:
    return {
        "run_id": run["id"],
        "agent_name": run["agent_name"],
        "graph_name": run["graph_name"],
        "status": run["status"],
        "started_at": run["started_at"],
        "ended_at": run["ended_at"],
    }


def _trace_run_detail(run: TraceRun) -> dict[str, Any]:
    prompt_tokens = run["total_prompt_tokens"]
    completion_tokens = run["total_completion_tokens"]
    total_tokens = (
        None
        if prompt_tokens is None and completion_tokens is None
        else (prompt_tokens or 0) + (completion_tokens or 0)
    )
    return {
        **_trace_run_summary(run),
        "root_input": run["root_input"],
        "final_output": run["final_output"],
        "latency_ms": run["total_latency_ms"],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": run["total_cost_usd"],
        },
    }


def _trace_turn_summary(turn: TraceTurn) -> dict[str, Any]:
    return {
        "turn_id": turn["id"],
        "turn_index": turn["turn_index"],
        "node_name": turn["node_name"],
        "status": turn["status"],
        "final_output": turn["final_output"],
        "started_at": turn["started_at"],
        "ended_at": turn["ended_at"],
        "latency_ms": turn["latency_ms"],
    }


def _request_json(run_id: str, turn: int, trace_db: Path) -> dict[str, Any]:
    store = SQLiteTraceStore(trace_db)
    row = store.get_model_call_for_turn(run_id, turn)
    if not row:
        raise typer.BadParameter(f"Missing model request for run {run_id} turn {turn}")
    try:
        payload = json.loads(row["request_json"])
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            f"Malformed stored model request for run {run_id} turn {turn}"
        ) from exc
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"Malformed stored model request for run {run_id} turn {turn}")
    return payload


@promptfoo_app.command("export")
def promptfoo_export(
    agent_path: str = typer.Argument(..., help="Agent import path in module:object form."),
    suite_path: str = typer.Argument(..., help="Path to the eval suite YAML file."),
    out: Path = typer.Argument(..., help="Destination Promptfoo configuration file."),
) -> None:
    """Export an eval suite as a Promptfoo configuration."""
    from clearagent.evals.promptfoo_export import export_promptfoo_config

    export_promptfoo_config(agent_path, EvalSuite.from_yaml(suite_path), out)
    console.print(str(out))


@promptfoo_app.command("target")
def promptfoo_target(
    agent_path: str = typer.Argument(..., help="Agent import path in module:object form."),
    out: Path = typer.Argument(..., help="Destination Python target module."),
) -> None:
    """Generate a Promptfoo Python target for an importable agent."""
    from clearagent.evals.promptfoo_export import write_promptfoo_target

    write_promptfoo_target(agent_path, out)
    console.print(str(out))


@baseline_app.command("save")
def baseline_save(
    suite_run_id: str = typer.Argument(..., help="Evaluation suite run ID to save."),
    name: str = typer.Option(..., "--name", help="Reusable name for the baseline."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to update.",
    ),
) -> None:
    """Save one evaluation suite run as a named baseline."""
    from clearagent.evals.baseline import save_baseline

    try:
        baseline_id = save_baseline(SQLiteTraceStore(trace_db), suite_run_id, name=name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(baseline_id)


@baseline_app.command("compare")
def baseline_compare(
    baseline_name: str = typer.Argument(..., help="Named baseline to compare."),
    suite_run_id: str = typer.Argument(..., help="Evaluation suite run ID to compare."),
    trace_db: Path = typer.Option(
        DEFAULT_TRACE_DB,
        "--trace-db",
        help="SQLite trace database to read.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the baseline comparison as JSON.",
    ),
) -> None:
    """Compare an evaluation suite run with a named baseline."""
    from clearagent.evals.baseline import compare_baseline

    try:
        comparison = compare_baseline(SQLiteTraceStore(trace_db), baseline_name, suite_run_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _write_json(asdict(comparison))
    else:
        console.print(f"Regressions: {', '.join(comparison.regressions) or 'none'}")
        console.print(f"Improvements: {', '.join(comparison.improvements) or 'none'}")

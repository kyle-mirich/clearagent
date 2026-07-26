import importlib
import sys

import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from clearagent import create_agent
from clearagent.cli import app
from clearagent.providers.base import FakeProvider, ProviderResponse, Usage
from clearagent.replay import ModelCallDiff
from clearagent.storage.sqlite import SQLiteTraceStore


def _write_agent_module(tmp_path, name: str, *outputs: str, model: str = "fake:model"):
    module_path = tmp_path / f"{name}.py"
    responses = ",\n        ".join(
        f"ProviderResponse.fake_text({output!r})" for output in outputs
    )
    module_path.write_text(
        f"""from clearagent import create_agent
from clearagent.providers.base import FakeProvider, ProviderResponse

agent = create_agent(
    name={name!r},
    model={model!r},
    provider=FakeProvider([
        {responses}
    ]),
)
""",
        encoding="utf-8",
    )
    return module_path


def _write_suite(tmp_path, name: str, *, expected: str, matrix: bool = False):
    suite = {
        "name": name,
        "cases": [
            {
                "name": "expected output",
                "input": "hello",
                "checks": [{"contains": expected}],
            }
        ],
    }
    if matrix:
        suite["matrix"] = {"temperatures": [0.0]}
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    return path


def _write_config(tmp_path, *, enabled: bool, db_path: str):
    config_path = tmp_path / ".clearagent" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f'[tracing]\nenabled = {str(enabled).lower()}\ndb_path = "{db_path}"\n',
        encoding="utf-8",
    )
    return config_path


def _completed_suite_run(store: SQLiteTraceStore, *, passed: bool) -> str:
    suite_run_id = store.start_eval_suite_run(
        suite_name="cli-baseline",
        suite_type="output",
        agent_name="support",
        model="fake:model",
    )
    run_id = store.start_run(agent_name="support", root_input="hello")
    store.end_run(run_id, final_output="ok" if passed else "bad")
    store.save_eval_case_result(
        suite_run_id=suite_run_id,
        run_id=run_id,
        suite_name="cli-baseline",
        case_name="case one",
        input="hello",
        final_output="ok" if passed else "bad",
        passed=passed,
        checks=[{"name": "contains", "passed": passed}],
        latency_ms=1,
        cost_usd=0.0,
    )
    store.end_eval_suite_run(
        suite_run_id,
        passed=1 if passed else 0,
        failed=0 if passed else 1,
    )
    return suite_run_id


def test_init_is_idempotent_and_run_honors_config_and_no_trace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    _write_agent_module(tmp_path, "cli_run_agent", "traced output", "untraced output")
    runner = CliRunner()

    initialized = runner.invoke(app, ["init"])

    config_path = tmp_path / ".clearagent" / "config.toml"
    assert initialized.exit_code == 0
    assert "Created .clearagent/config.toml" in initialized.output
    assert config_path.exists()

    custom_db = tmp_path / "custom" / "traces.sqlite"
    custom_config = (
        f'[project]\nname = "kept"\n\n[tracing]\nenabled = true\n'
        f'db_path = "{custom_db}"\n'
    )
    config_path.write_text(custom_config, encoding="utf-8")

    initialized_again = runner.invoke(app, ["init"])
    traced = runner.invoke(app, ["run", "cli_run_agent:agent", "hello"])
    untraced = runner.invoke(
        app,
        ["run", "cli_run_agent:agent", "hello again", "--no-trace"],
    )

    assert initialized_again.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == custom_config
    assert traced.exit_code == 0
    assert "traced output" in traced.output
    assert untraced.exit_code == 0
    assert "untraced output" in untraced.output
    runs = SQLiteTraceStore(custom_db).list_runs()
    assert len(runs) == 1
    assert runs[0]["root_input"] == "hello"


def test_chat_starts_only_local_server_with_configured_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    _write_agent_module(
        tmp_path,
        "cli_chat_agent",
        "hello",
        model="openai:gpt-4.1-mini",
    )
    trace_db = tmp_path / "configured.sqlite"
    chat_db = tmp_path / "chat.sqlite"
    _write_config(tmp_path, enabled=False, db_path=str(trace_db))
    captured = {}

    def fake_run(asgi_app, *, host, port):
        captured.update(app=asgi_app, host=host, port=port)

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = CliRunner().invoke(
        app,
        [
            "chat",
            "cli_chat_agent:agent",
            "--host",
            "localhost",
            "--port",
            "9123",
            "--chat-db",
            str(chat_db),
            "--allow-settings-mutation",
        ],
    )

    assert result.exit_code == 0
    assert captured["host"] == "localhost"
    assert captured["port"] == 9123
    assert TestClient(captured["app"]).get("/api/health").json() == {
        "status": "ok",
        "agent": "cli_chat_agent",
    }
    imported_agent = importlib.import_module("cli_chat_agent").agent
    assert imported_agent.trace is False
    assert imported_agent.trace_db_path == trace_db
    assert chat_db.exists()


def test_eval_all_pass_matrix_and_failure_outputs_have_stable_exit_codes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    _write_agent_module(tmp_path, "cli_eval_pass_agent", "expected")
    _write_agent_module(tmp_path, "cli_eval_fail_agent", "wrong")
    passing_suite = _write_suite(tmp_path, "passing", expected="expected", matrix=True)
    failing_suite = _write_suite(tmp_path, "failing", expected="expected")
    runner = CliRunner()

    discovered = runner.invoke(app, ["eval", "all"])
    passed = runner.invoke(
        app,
        ["eval", "cli_eval_pass_agent:agent", str(passing_suite)],
    )
    failed = runner.invoke(
        app,
        ["eval", "cli_eval_fail_agent:agent", str(failing_suite)],
    )

    assert discovered.exit_code == 0
    assert "No eval discovery config found" in discovered.output
    assert passed.exit_code == 0
    assert "PASS expected output variant=" in passed.output
    assert "1 passed, 0 failed" in passed.output
    assert failed.exit_code == 1
    assert "FAIL expected output" in failed.output
    assert "Output: wrong" in failed.output
    assert "Checks:" in failed.output
    assert "0 passed, 1 failed" in failed.output


def test_trace_commands_report_missing_and_malformed_state_cleanly(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="fake:model",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    runner = CliRunner()

    report = runner.invoke(
        app,
        ["trace-report", result.run_id, "--trace-db", str(db_path)],
    )
    missing_eval = runner.invoke(
        app,
        [
            "trace-to-eval",
            "run_missing",
            "--out",
            str(tmp_path / "missing.yaml"),
            "--trace-db",
            str(db_path),
        ],
    )
    missing_report = runner.invoke(
        app,
        ["trace-report", "run_missing", "--trace-db", str(db_path)],
    )
    missing_show = runner.invoke(
        app,
        ["trace", "show", "run_missing", "--trace-db", str(db_path)],
    )
    missing_turns = runner.invoke(
        app,
        ["trace", "turns", "run_missing", "--trace-db", str(db_path)],
    )
    missing_request = runner.invoke(
        app,
        ["request", "run_missing", "--trace-db", str(db_path)],
    )

    assert report.exit_code == 0
    assert "# ClearAgent Trace Report" in report.output
    for failed, message in (
        (missing_eval, "Missing run run_missing"),
        (missing_report, "Missing run run_missing"),
        (missing_show, "Missing run run_missing"),
        (missing_turns, "Missing turns for run run_missing"),
        (missing_request, "Missing model request for run run_missing turn 0"),
    ):
        assert failed.exit_code != 0
        assert message in failed.output

    store = SQLiteTraceStore(db_path)
    model_call = store.get_model_call_for_turn(result.run_id, 0)
    with store.connect() as db:
        db.execute(
            "UPDATE model_calls SET request_json=? WHERE id=?",
            ("[]", model_call["id"]),
        )
    malformed = runner.invoke(
        app,
        ["request", result.run_id, "--trace-db", str(db_path)],
    )
    assert malformed.exit_code != 0
    assert "Malformed stored model request" in malformed.output


def test_replay_and_diff_render_success_and_changed_exit_status(monkeypatch):
    replayed = ProviderResponse.fake_text("after")
    replayed.usage = Usage(prompt_tokens=2, completion_tokens=1, total_tokens=3)
    unchanged = ModelCallDiff(
        changed=False,
        before_output="same",
        after_output="same",
        before_finish_reason="stop",
        after_finish_reason="stop",
        before_usage={"total_tokens": 3},
        after_usage={"total_tokens": 3},
    )
    changed = unchanged.model_copy(
        update={"changed": True, "before_output": "before", "after_output": "after"}
    )
    diffs = iter([unchanged, changed])
    monkeypatch.setattr("clearagent.replay.replay_model_call", lambda *args, **kwargs: replayed)
    monkeypatch.setattr("clearagent.replay.diff_model_call", lambda *args, **kwargs: next(diffs))
    runner = CliRunner()

    replay = runner.invoke(app, ["replay", "run_1"])
    same = runner.invoke(app, ["diff", "run_1"])
    different = runner.invoke(app, ["diff", "run_1"])

    assert replay.exit_code == 0
    assert "after" in replay.output
    assert same.exit_code == 0
    assert "same" in same.output
    assert different.exit_code == 1
    assert "before" in different.output
    assert "after" in different.output


def test_promptfoo_commands_create_outputs_and_reject_bad_agent_paths(tmp_path):
    suite_path = _write_suite(tmp_path, "promptfoo", expected="hello")
    config_out = tmp_path / "promptfooconfig.yaml"
    target_out = tmp_path / "nested" / "target.py"
    runner = CliRunner()

    exported = runner.invoke(
        app,
        [
            "promptfoo",
            "export",
            "package.agent:agent",
            str(suite_path),
            str(config_out),
        ],
    )
    targeted = runner.invoke(
        app,
        ["promptfoo", "target", "package.agent:agent", str(target_out)],
    )
    rejected = runner.invoke(
        app,
        ["promptfoo", "target", "missing-colon", str(tmp_path / "bad.py")],
    )
    rejected_export = runner.invoke(
        app,
        [
            "promptfoo",
            "export",
            "missing-colon",
            str(suite_path),
            str(tmp_path / "bad.yaml"),
        ],
    )

    assert exported.exit_code == 0
    assert exported.output == f"{config_out}\n"
    assert yaml.safe_load(config_out.read_text(encoding="utf-8"))["tests"][0][
        "description"
    ] == "expected output"
    assert targeted.exit_code == 0
    assert targeted.output == f"{target_out}\n"
    assert "from package.agent import agent" in target_out.read_text(encoding="utf-8")
    assert rejected.exit_code != 0
    assert "agent path must use module:object format" in rejected.output
    assert not (tmp_path / "bad.py").exists()
    assert rejected_export.exit_code != 0
    assert "agent path must use module:object format" in rejected_export.output
    assert not (tmp_path / "bad.yaml").exists()


def test_baseline_commands_save_and_report_regression(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    store = SQLiteTraceStore(db_path)
    baseline_run = _completed_suite_run(store, passed=True)
    current_run = _completed_suite_run(store, passed=False)
    runner = CliRunner()

    saved = runner.invoke(
        app,
        [
            "baseline",
            "save",
            baseline_run,
            "--name",
            "v1",
            "--trace-db",
            str(db_path),
        ],
    )
    compared = runner.invoke(
        app,
        [
            "baseline",
            "compare",
            "v1",
            current_run,
            "--trace-db",
            str(db_path),
        ],
    )

    assert saved.exit_code == 0
    assert "baseline_" in saved.output
    assert compared.exit_code == 0
    assert "Regressions: case one" in compared.output
    assert "Improvements: none" in compared.output


def test_imported_module_dependency_error_is_not_misreported_as_missing_agent_module(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    (tmp_path / "broken_agent_module.py").write_text(
        "import dependency_that_does_not_exist\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["run", "broken_agent_module:agent", "hello"],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, ModuleNotFoundError)
    assert result.exception.name == "dependency_that_does_not_exist"
    assert "Could not import module 'broken_agent_module'" not in result.output


def test_run_rejects_empty_module_or_object_in_agent_path():
    runner = CliRunner()

    missing_module = runner.invoke(app, ["run", ":agent", "hello"])
    missing_object = runner.invoke(app, ["run", "agent_module:", "hello"])

    assert missing_module.exit_code != 0
    assert "agent path must use module:object format" in missing_module.output
    assert missing_object.exit_code != 0
    assert "agent path must use module:object format" in missing_object.output


def test_run_reports_invalid_project_config_as_cli_parameter_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    _write_agent_module(tmp_path, "cli_bad_config_agent", "unused")
    config_path = tmp_path / ".clearagent" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("tracing = false\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["run", "cli_bad_config_agent:agent", "hello"],
    )

    assert result.exit_code != 0
    assert "ClearAgent config [tracing] must be a mapping" in result.output

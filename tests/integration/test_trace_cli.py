import json
import sys

import yaml
from typer.testing import CliRunner

from clearagent import create_agent
from clearagent.cli import app
from clearagent.providers.base import FakeProvider, ProviderResponse
from clearagent.storage.sqlite import SQLiteTraceStore


def test_trace_cli_lists_and_exports_request(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    out_path = tmp_path / "request.json"
    runner = CliRunner()

    listed = runner.invoke(app, ["trace", "list", "--trace-db", str(db_path)])
    shown = runner.invoke(app, ["trace", "show", result.run_id, "--trace-db", str(db_path)])
    turns = runner.invoke(app, ["trace", "turns", result.run_id, "--trace-db", str(db_path)])
    request = runner.invoke(app, ["request", result.run_id, "--turn", "0", "--trace-db", str(db_path)])
    replay = runner.invoke(
        app,
        [
            "replay-request",
            result.run_id,
            "--turn",
            "0",
            "--out",
            str(out_path),
            "--trace-db",
            str(db_path),
        ],
    )

    assert listed.exit_code == 0
    assert "support" in listed.output
    assert shown.exit_code == 0
    assert turns.exit_code == 0
    assert request.exit_code == 0
    assert json.loads(request.output)["body"]["messages"][-1]["content"] == "hello"
    assert replay.exit_code == 0
    assert json.loads(out_path.read_text(encoding="utf-8")) == json.loads(request.output)


def test_power_feature_cli_exports_eval_report_and_iterations(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(
            [
                ProviderResponse.fake_text("shipped"),
                ProviderResponse.fake_text("shipped"),
            ]
        ),
        trace_db_path=db_path,
    )
    result = agent.run("Where is A123?")
    agent_module = tmp_path / "agent_module.py"
    agent_module.write_text(
        f"""
from clearagent import create_agent
from clearagent.providers.base import FakeProvider, ProviderResponse

agent = create_agent(
    name="support",
    model="openai:gpt-4.1-mini",
    provider=FakeProvider([ProviderResponse.fake_text("shipped")]),
    trace_db_path={str(db_path)!r},
)
""",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
cases:
  - name: shipped order
    input: Where is A123?
    checks:
      - contains: shipped
""",
        encoding="utf-8",
    )
    eval_out = tmp_path / "generated.yaml"
    report_out = tmp_path / "report.md"
    runner = CliRunner()

    generated = runner.invoke(
        app,
        ["trace-to-eval", result.run_id, "--trace-db", str(db_path), "--out", str(eval_out)],
    )
    reported = runner.invoke(
        app,
        ["trace-report", result.run_id, "--trace-db", str(db_path), "--out", str(report_out)],
    )
    iterated = runner.invoke(
        app,
        [
            "iterate",
            f"{agent_module.stem}:agent",
            str(suite_path),
            "--model",
            "openai:gpt-4.1-mini",
            "--temperature",
            "0.0",
        ],
    )

    assert generated.exit_code == 0
    assert yaml.safe_load(eval_out.read_text(encoding="utf-8"))["cases"][0]["input"] == "Where is A123?"
    assert reported.exit_code == 0
    assert "ClearAgent Trace Report" in report_out.read_text(encoding="utf-8")
    assert iterated.exit_code == 0
    assert json.loads(iterated.output)["variants"][0]["passed"] == 1


def test_replay_and_diff_missing_turn_fail_with_clear_cli_message(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    runner = CliRunner()

    replay = runner.invoke(app, ["replay", result.run_id, "--turn", "9", "--trace-db", str(db_path)])
    diff = runner.invoke(app, ["diff", result.run_id, "--turn", "9", "--trace-db", str(db_path)])

    assert replay.exit_code != 0
    assert "Missing model request" in replay.output
    assert diff.exit_code != 0
    assert "Missing model request" in diff.output


def test_request_command_rejects_malformed_stored_request(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    store = SQLiteTraceStore(db_path)
    row = store.get_model_call_for_turn(result.run_id, 0)
    assert row is not None
    with store.connect() as db:
        db.execute(
            "UPDATE model_calls SET request_json=? WHERE id=?",
            ("not json", row["id"]),
        )
    runner = CliRunner()

    request = runner.invoke(app, ["request", result.run_id, "--turn", "0", "--trace-db", str(db_path)])

    assert request.exit_code != 0
    assert "Malformed stored model request" in request.output


def test_diff_command_rejects_malformed_stored_response(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    store = SQLiteTraceStore(db_path)
    row = store.get_model_call_for_turn(result.run_id, 0)
    assert row is not None
    with store.connect() as db:
        db.execute(
            "UPDATE model_calls SET response_json=? WHERE id=?",
            ("not json", row["id"]),
        )
    runner = CliRunner()

    diff = runner.invoke(app, ["diff", result.run_id, "--turn", "0", "--trace-db", str(db_path)])

    assert diff.exit_code != 0
    assert "Malformed stored model response" in diff.output

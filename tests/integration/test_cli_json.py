import json

from typer.testing import CliRunner

from clearagent import create_agent
from clearagent.cli import app
from clearagent.evals.baseline import BaselineComparison
from clearagent.providers import FakeProvider, ProviderResponse
from clearagent.replay import ModelCallDiff


def test_trace_json_commands_have_stable_shapes(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )
    run_result = agent.run("hello")
    assert run_result.run_id is not None
    runner = CliRunner()

    listed = runner.invoke(
        app,
        ["trace", "list", "--trace-db", str(db_path), "--json"],
    )
    shown = runner.invoke(
        app,
        ["trace", "show", run_result.run_id, "--trace-db", str(db_path), "--json"],
    )
    turns = runner.invoke(
        app,
        ["trace", "turns", run_result.run_id, "--trace-db", str(db_path), "--json"],
    )

    assert listed.exit_code == 0
    list_payload = json.loads(listed.output)
    assert set(list_payload) == {"runs"}
    assert set(list_payload["runs"][0]) == {
        "agent_name",
        "ended_at",
        "graph_name",
        "run_id",
        "started_at",
        "status",
    }
    assert list_payload["runs"][0]["run_id"] == run_result.run_id

    assert shown.exit_code == 0
    show_payload = json.loads(shown.output)
    assert set(show_payload) == {
        "agent_name",
        "ended_at",
        "final_output",
        "graph_name",
        "latency_ms",
        "root_input",
        "run_id",
        "started_at",
        "status",
        "usage",
    }
    assert show_payload["root_input"] == "hello"
    assert set(show_payload["usage"]) == {
        "completion_tokens",
        "cost_usd",
        "prompt_tokens",
        "total_tokens",
    }

    assert turns.exit_code == 0
    turns_payload = json.loads(turns.output)
    assert set(turns_payload) == {"run_id", "turns"}
    assert set(turns_payload["turns"][0]) == {
        "ended_at",
        "final_output",
        "latency_ms",
        "node_name",
        "started_at",
        "status",
        "turn_id",
        "turn_index",
    }


def test_eval_json_emits_report_and_preserves_failure_exit(tmp_path, monkeypatch):
    db_path = tmp_path / "traces.sqlite"
    module_path = tmp_path / "json_agent_module.py"
    module_path.write_text(
        f"""
print("USER IMPORT NOISE")

from clearagent import create_agent
from clearagent.providers import FakeProvider, ProviderResponse

agent = create_agent(
    name="support",
    model="openai:gpt-4.1-mini",
    provider=FakeProvider([ProviderResponse.fake_text("shipped")]),
    trace_db_path={str(db_path)!r},
)
""",
        encoding="utf-8",
    )
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
cases:
  - name: delivery status
    input: Where is A123?
    checks:
      - contains: delivered
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "eval",
            "json_agent_module:agent",
            str(suite_path),
            "--trace-db",
            str(db_path),
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "USER IMPORT NOISE" in result.stderr
    assert set(payload) == {
        "agent_name",
        "failed",
        "model",
        "passed",
        "results",
        "skipped",
        "suite_name",
        "suite_run_id",
        "suite_type",
    }
    assert payload["failed"] == 1
    assert payload["results"][0]["passed"] is False


def test_eval_all_json_is_a_parseable_nonzero_error():
    result = CliRunner().invoke(app, ["eval", "all", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": "No eval discovery config found. Pass <agent_path> <suite_path>."
    }


def test_iterate_stdout_remains_parseable_when_user_code_prints(tmp_path, monkeypatch):
    module_path = tmp_path / "iterate_agent_module.py"
    module_path.write_text(
        """
print("USER ITERATE IMPORT NOISE")

from clearagent import create_agent
from clearagent.providers import FakeProvider

agent = create_agent(
    name="support",
    model="openai:gpt-4.1-mini",
    provider=FakeProvider(),
)
""",
        encoding="utf-8",
    )
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
cases:
  - name: delivery status
    input: Where is A123?
    checks:
      - contains: shipped
""",
        encoding="utf-8",
    )

    def noisy_iterations(*args, **kwargs):
        print("PROVIDER ITERATE NOISE")
        return {"suite": "smoke", "total_variants": 0, "variants": []}

    monkeypatch.setattr("clearagent.evals.iteration.run_eval_iterations", noisy_iterations)
    monkeypatch.syspath_prepend(str(tmp_path))

    result = CliRunner().invoke(
        app,
        ["iterate", "iterate_agent_module:agent", str(suite_path)],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "suite": "smoke",
        "total_variants": 0,
        "variants": [],
    }
    assert "USER ITERATE IMPORT NOISE" in result.stderr
    assert "PROVIDER ITERATE NOISE" in result.stderr


def test_diff_json_preserves_changed_exit_code(monkeypatch):
    comparison = ModelCallDiff(
        changed=True,
        before_output="old",
        after_output="new",
        before_finish_reason="stop",
        after_finish_reason="stop",
        before_usage={"total_tokens": 1},
        after_usage={"total_tokens": 2},
    )

    def noisy_diff(*args, **kwargs):
        print("PROVIDER NOISE")
        return comparison

    monkeypatch.setattr("clearagent.replay.diff_model_call", noisy_diff)
    runner = CliRunner()

    result = runner.invoke(app, ["diff", "run_1", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == comparison.model_dump(mode="json")
    assert "PROVIDER NOISE" in result.stderr


def test_baseline_compare_json_has_stable_shape(monkeypatch, tmp_path):
    comparison = BaselineComparison(
        baseline_name="golden",
        suite_run_id="suite_2",
        unchanged_passes=["case-a"],
        unchanged_failures=["case-b"],
        regressions=["case-c"],
        improvements=["case-d"],
    )
    monkeypatch.setattr(
        "clearagent.evals.baseline.compare_baseline",
        lambda *args, **kwargs: comparison,
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "baseline",
            "compare",
            "golden",
            "suite_2",
            "--trace-db",
            str(tmp_path / "traces.sqlite"),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "baseline_name": "golden",
        "improvements": ["case-d"],
        "regressions": ["case-c"],
        "suite_run_id": "suite_2",
        "unchanged_failures": ["case-b"],
        "unchanged_passes": ["case-a"],
    }

from typer.testing import CliRunner

from clearagent.cli import app


def test_cli_help_surfaces_main_commands():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init" in result.output
    assert "chat" in result.output
    assert "eval" in result.output
    assert "trace" in result.output
    assert "Run an importable agent once" in result.output


def test_trace_help_surfaces_trace_subcommands():
    runner = CliRunner()

    result = runner.invoke(app, ["trace", "--help"])

    assert result.exit_code == 0
    assert "list" in result.output
    assert "show" in result.output
    assert "turns" in result.output
    assert "recorded trace runs" in result.output


def test_command_help_describes_options():
    runner = CliRunner()

    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    assert "--no-trace" in result.output
    assert "Run without recording a local trace" in result.output


def test_cli_version_reports_installed_package_version():
    runner = CliRunner()

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith("clearagent ")


def test_eval_missing_suite_path_fails_without_running_agent():
    runner = CliRunner()

    result = runner.invoke(app, ["eval", "examples.customer_support.agent:agent"])

    assert result.exit_code != 0
    assert "suite_path is required" in result.output


def test_run_malformed_agent_path_fails_with_clear_message():
    runner = CliRunner()

    result = runner.invoke(app, ["run", "examples.customer_support.agent", "hello"])

    assert result.exit_code != 0
    assert "agent path must use module:object format" in result.output


def test_run_missing_agent_object_fails_with_clear_message():
    runner = CliRunner()

    result = runner.invoke(app, ["run", "examples.customer_support.agent:missing_agent", "hello"])

    assert result.exit_code != 0
    assert "Could not find object 'missing_agent'" in result.output


def test_run_missing_agent_module_fails_with_clear_message():
    runner = CliRunner()

    result = runner.invoke(app, ["run", "examples.customer_support.missing:agent", "hello"])

    assert result.exit_code != 0
    assert "Could not import module 'examples.customer_support.missing'" in result.output


def test_chat_rejects_non_loopback_host():
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["chat", "examples.customer_support.agent:agent", "--host", "0.0.0.0"],
    )

    assert result.exit_code != 0
    assert "local-only" in result.output

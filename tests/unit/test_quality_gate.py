import os
from pathlib import Path
import re
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_authoritative_shell_gate_keeps_every_required_layer():
    gate = (PROJECT_ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    commands = [line.strip() for line in gate.splitlines() if line.strip().startswith("uv run")]

    assert gate.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "unset PYTEST_ADDOPTS PYTEST_PLUGINS\n" in gate
    assert commands[0] == "uv run python scripts/check_test_policy.py"
    coverage_run = next(command for command in commands if "coverage run" in command)
    assert "--rcfile=/dev/null" in coverage_run
    assert "--branch" in coverage_run
    assert "--source=clearagent" in coverage_run
    assert "-c pyproject.toml" in coverage_run
    assert "--disable-socket" in coverage_run
    assert "--allow-unix-socket" in coverage_run
    assert "-m pytest -p scripts.pytest_gate_plugin -c pyproject.toml" in coverage_run
    assert coverage_run.endswith(
        "-m pytest -p scripts.pytest_gate_plugin -c pyproject.toml "
        "--strict-config --strict-markers "
        "--disable-socket --allow-unix-socket"
    )
    coverage_command = next(command for command in commands if "coverage report" in command)
    assert "--rcfile=/dev/null" in coverage_command
    threshold = re.search(r"--fail-under=(\d+)", coverage_command)
    assert threshold is not None and int(threshold.group(1)) >= 95
    assert any("scripts/check_changed_coverage.py" in command for command in commands)
    assert (
        "uv run ruff check --config pyproject.toml --no-fix --no-respect-gitignore ."
        in commands
    )
    assert "uv run python -m mypy --config-file pyproject.toml src" in commands
    assert "uv run python scripts/check_docs_links.py" in commands
    assert commands[-1] == "uv run python scripts/check_distribution.py"


def test_authoritative_shell_gate_stops_at_the_first_failed_layer(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation_log = tmp_path / "uv-invocations.txt"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$CLEARAGENT_GATE_TEST_LOG"\n'
        "exit 23\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["CLEARAGENT_GATE_TEST_LOG"] = str(invocation_log)

    completed = subprocess.run(
        ["bash", "scripts/check.sh"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 23
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [
        "run python scripts/check_test_policy.py"
    ]


def test_repository_policy_and_pull_request_template_require_rigorous_tests():
    policy = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    template = (PROJECT_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8"
    )
    testing_policy = " ".join(
        policy.split("## Testing And Main-Branch Safety", 1)[1]
        .split("## Library Consumer Experience", 1)[0]
        .split()
    )
    normalized_template = " ".join(template.split())

    for invariant in (
        "full repository gate must pass",
        "successful path and each relevant failure",
        "exact failure for every bug fix",
        "executable browser or DOM test",
        "build both the sdist and wheel",
        "must not depend on real credentials, external network access",
        "Do not delete or weaken a test",
        "uv run bash scripts/check.sh",
    ):
        assert invariant in testing_policy
    for checkbox in (
        "Tests would detect the previous or broken behavior",
        "failure, boundary, and persisted-state paths",
        "No test, coverage threshold, skip policy, or static-analysis rule was weakened",
        "Browser-client changes have executable interaction coverage",
    ):
        assert checkbox in normalized_template

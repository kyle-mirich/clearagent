from pathlib import Path
import re

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _workflow() -> dict:
    with (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").open(encoding="utf-8") as file:
        return yaml.load(file, Loader=yaml.BaseLoader)


def test_ci_runs_authoritative_gate_for_pull_requests_main_and_merge_queue():
    workflow = _workflow()

    assert workflow["on"] == {
        "push": {"branches": ["main"]},
        "pull_request": "",
        "merge_group": "",
    }
    quality = workflow["jobs"]["quality"]
    assert quality["runs-on"] == "ubuntu-latest"
    assert "pull_request.base.sha" in quality["env"]["CLEARAGENT_COVERAGE_BASE"]
    steps = quality["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == "0"
    commands = [step["run"] for step in steps if "run" in step]
    assert "uv sync --locked --all-extras --dev" in commands
    assert "uv run playwright install --with-deps chromium" in commands
    assert commands[-1] == "uv run bash scripts/check.sh"


def test_ci_fresh_distribution_gate_covers_claimed_operating_systems():
    workflow = _workflow()
    job = workflow["jobs"]["package-smoke"]

    assert job["strategy"]["fail-fast"] == "false"
    assert job["strategy"]["matrix"]["os"] == [
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
    ]
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert commands == [
        "uv sync --locked --dev",
        "uv run python scripts/check_distribution.py",
    ]


def test_ci_third_party_actions_are_pinned_to_commit_shas():
    workflow = _workflow()

    uses = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]
    assert uses
    for action in uses:
        _, reference = action.rsplit("@", 1)
        assert re.fullmatch(r"[0-9a-f]{40}", reference), action

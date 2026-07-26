from pathlib import Path

import pytest

from scripts.check_test_policy import PROJECT_ROOT, find_policy_violations


STRICT_OPTIONS = "--strict-config --strict-markers --disable-socket --allow-unix-socket"


def _policy_project(tmp_path: Path, source: str, *, addopts: str = STRICT_OPTIONS) -> Path:
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        f'[tool.pytest.ini_options]\ntestpaths = ["tests"]\naddopts = "{addopts}"\n',
        encoding="utf-8",
    )
    (tests / "test_example.py").write_text(source, encoding="utf-8")
    return project


def test_current_repository_obeys_the_strict_offline_test_policy():
    assert find_policy_violations(PROJECT_ROOT) == []


def test_policy_accepts_an_exact_literal_loopback_allowlist(tmp_path):
    project = _policy_project(
        tmp_path,
        """\
import pytest

@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_local_server():
    assert True
""",
    )

    assert find_policy_violations(project) == []


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("import pytest\npytest.skip('not today')\n", "pytest.skip is forbidden"),
        (
            "import pytest\npytest.importorskip('optional_package')\n",
            "pytest.importorskip is forbidden",
        ),
        (
            "from pytest import xfail as stop\nstop('not today')\n",
            "pytest.xfail is forbidden",
        ),
        (
            "def test_network(socket_enabled):\n    pass\n",
            "broad socket_enabled fixture is forbidden",
        ),
        (
            "import pytest\n@pytest.mark.enable_socket\ndef test_network():\n    pass\n",
            "pytest.mark.enable_socket is forbidden",
        ),
        (
            "import pytest\n@pytest.mark.allow_hosts(['example.com'])\n"
            "def test_network():\n    pass\n",
            "allow_hosts must contain only literal loopback hosts",
        ),
        (
            "collect_ignore = ['test_contract.py']\n",
            "collection override 'collect_ignore' is forbidden",
        ),
    ],
)
def test_policy_rejects_skip_xfail_and_network_escapes(tmp_path, source, message):
    violations = find_policy_violations(_policy_project(tmp_path, source))

    assert any(message in violation for violation in violations)


@pytest.mark.parametrize(
    "addopts",
    [
        "--strict-config --strict-markers --allow-unix-socket",
        f"{STRICT_OPTIONS} --force-enable-socket",
        f"{STRICT_OPTIONS} --allow-hosts=example.com",
        f"{STRICT_OPTIONS} --ignore=tests/integration",
    ],
)
def test_policy_rejects_weakened_or_broad_pytest_socket_configuration(
    tmp_path, addopts
):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n", addopts=addopts)

    assert find_policy_violations(project)


def test_policy_rejects_narrowed_test_discovery(tmp_path):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n")
    (project / "pyproject.toml").write_text(
        f'[tool.pytest.ini_options]\ntestpaths = ["tests/unit"]\n'
        f'addopts = "{STRICT_OPTIONS}"\n',
        encoding="utf-8",
    )

    assert find_policy_violations(project) == [
        "pyproject.toml: pytest testpaths must be exactly ['tests']"
    ]

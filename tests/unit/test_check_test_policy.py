from pathlib import Path

import pytest

from scripts.check_test_policy import PROJECT_ROOT, find_policy_violations


STRICT_OPTIONS = "--strict-config --strict-markers --disable-socket --allow-unix-socket"


def _policy_project(tmp_path: Path, source: str, *, addopts: str = STRICT_OPTIONS) -> Path:
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
        'pythonpath = ["."]\n'
        f'addopts = "{addopts}"\n'
        "\n[tool.ruff]\n"
        "line-length = 100\n"
        'target-version = "py314"\n'
        "\n[tool.mypy]\n"
        'python_version = "3.14"\n'
        "ignore_missing_imports = true\n",
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
        (
            "pytest_plugins = ['escape_plugin']\n",
            "collection override 'pytest_plugins' is forbidden",
        ),
        (
            "def pytest_sessionfinish(session):\n    session.exitstatus = 0\n",
            "pytest hook 'pytest_sessionfinish' is forbidden",
        ),
        (
            "pytest_sessionfinish = lambda session: setattr(session, 'exitstatus', 0)\n",
            "collection override 'pytest_sessionfinish' is forbidden",
        ),
        (
            "import pytest\n@pytest.hookimpl(specname='pytest_sessionfinish')\n"
            "def finish(session):\n    session.exitstatus = 0\n",
            "pytest.hookimpl is forbidden",
        ),
        (
            "import pytest\n@pytest.mark.parametrize('value', [])\n"
            "def test_empty(value):\n    pass\n",
            "empty parametrize values are forbidden",
        ),
        (
            "import pytest\n@pytest.mark.usefixtures('socket_enabled')\n"
            "def test_network():\n    pass\n",
            "pytest.mark.usefixtures is forbidden",
        ),
        (
            "import pytest\ngetattr(pytest, 'skip')('hidden')\n",
            "dynamic pytest.skip is forbidden",
        ),
        (
            "def test_network(request):\n    request.getfixturevalue('socket_enabled')\n",
            "dynamic fixture lookup is forbidden",
        ),
        (
            "def test_network(*, socket_enabled):\n    pass\n",
            "broad socket_enabled fixture is forbidden",
        ),
        (
            "import subprocess\nimport sys\n"
            "def test_child():\n"
            "    subprocess.run([sys.executable, '-c', 'print(1)'])\n",
            "child process subprocess.run is forbidden",
        ),
        (
            "import pytest\nmark_alias = pytest.mark\n"
            "@mark_alias.usefixtures('socket_enabled')\n"
            "def test_network():\n    pass\n",
            "pytest.mark.usefixtures is forbidden",
        ),
        (
            "import subprocess\nimport sys\nrun_child = subprocess.run\n"
            "def test_child():\n"
            "    run_child([sys.executable, '-c', 'print(1)'])\n",
            "child process subprocess.run is forbidden",
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
        f"{STRICT_OPTIONS} tests/test_safe.py",
    ],
)
def test_policy_rejects_weakened_or_broad_pytest_socket_configuration(tmp_path, addopts):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n", addopts=addopts)

    assert find_policy_violations(project)


def test_policy_rejects_narrowed_test_discovery(tmp_path):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n")
    config_path = project / "pyproject.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'testpaths = ["tests"]', 'testpaths = ["tests/unit"]'
        ),
        encoding="utf-8",
    )

    assert find_policy_violations(project) == [
        "pyproject.toml: pytest testpaths must be exactly ['tests']"
    ]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("pytest.ini", "[pytest]\naddopts =\n"),
        (".pytest.ini", "[pytest]\naddopts =\n"),
        ("tox.ini", "[pytest]\naddopts =\n"),
        ("setup.cfg", "[tool:pytest]\naddopts =\n"),
    ],
)
def test_policy_rejects_alternate_pytest_configuration(tmp_path, filename, content):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n")
    (project / filename).write_text(content, encoding="utf-8")

    violations = find_policy_violations(project)

    assert any(
        violation == f"{filename}: alternate pytest configuration is forbidden; use pyproject.toml"
        for violation in violations
    )


def test_policy_scans_root_conftest_for_collection_bypasses(tmp_path):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n")
    (project / "conftest.py").write_text(
        "def pytest_collection_modifyitems(items):\n    items.clear()\n",
        encoding="utf-8",
    )

    violations = find_policy_violations(project)

    assert any(
        "collection hook 'pytest_collection_modifyitems' is forbidden" in item
        for item in violations
    )


@pytest.mark.parametrize("filename", ["ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini"])
def test_policy_rejects_alternate_static_analysis_configuration(tmp_path, filename):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n")
    (project / filename).write_text("# alternate config\n", encoding="utf-8")

    violations = find_policy_violations(project)

    assert any(
        f"{filename}: alternate analysis configuration is forbidden" in item for item in violations
    )


def test_policy_rejects_ruff_and_mypy_escape_configuration(tmp_path):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n")
    config_path = project / "pyproject.toml"
    config = config_path.read_text(encoding="utf-8")
    config = config.replace(
        'target-version = "py314"\n',
        'target-version = "py314"\nfix = true\n',
    ).replace(
        "ignore_missing_imports = true\n",
        "ignore_missing_imports = true\nstrict_optional = false\n",
    )
    config_path.write_text(config, encoding="utf-8")

    violations = find_policy_violations(project)

    assert any("Ruff configuration must be exactly" in item for item in violations)
    assert any("mypy configuration must be exactly" in item for item in violations)


def test_policy_rejects_extra_pytest_discovery_options(tmp_path):
    project = _policy_project(tmp_path, "def test_ok():\n    assert True\n")
    config_path = project / "pyproject.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            f'addopts = "{STRICT_OPTIONS}"',
            f'addopts = "{STRICT_OPTIONS}"\npython_files = ["test_safe.py"]',
        ),
        encoding="utf-8",
    )

    assert (
        "pyproject.toml: pytest ini option 'python_files' is forbidden"
        in find_policy_violations(project)
    )

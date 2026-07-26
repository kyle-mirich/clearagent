"""Reject test-suite escape hatches that can make a green gate misleading."""

from __future__ import annotations

import ast
import configparser
from pathlib import Path
import shlex
import sys
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PYTEST_OPTIONS = {
    "--strict-config",
    "--strict-markers",
    "--disable-socket",
    "--allow-unix-socket",
}
REQUIRED_PYTEST_OPTION_SEQUENCE = (
    "--strict-config",
    "--strict-markers",
    "--disable-socket",
    "--allow-unix-socket",
)
EXPECTED_RUFF_CONFIG = {"line-length": 100, "target-version": "py314"}
EXPECTED_MYPY_CONFIG = {"python_version": "3.14", "ignore_missing_imports": True}
FORBIDDEN_CALLS = {
    "pytest.skip",
    "pytest.xfail",
    "pytest.importorskip",
    "pytest.exit",
    "pytest.skip.Exception",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "pytest.mark.enable_socket",
    "pytest.mark.usefixtures",
    "pytest.hookimpl",
    "pytest.hookspec",
    "pytest_socket.enable_socket",
    "pluggy.HookimplMarker",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
    "unittest.expectedFailure",
    "unittest.SkipTest",
}
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
FORBIDDEN_PYTEST_OPTIONS = {
    "--collect-only",
    "--continue-on-collection-errors",
    "--failed-first",
    "--last-failed",
    "--lf",
    "--pyargs",
    "-k",
    "-m",
}
FORBIDDEN_COLLECTION_NAMES = {
    "__test__",
    "collect_ignore",
    "collect_ignore_glob",
    "pytest_collection_modifyitems",
    "pytest_ignore_collect",
    "pytest_plugins",
}
CHILD_PROCESS_CALLS = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.popen",
    "os.system",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}
ALLOWED_GIT_SUBCOMMANDS = {"add", "commit", "config", "init", "rev-parse"}


def find_policy_violations(project_root: Path = PROJECT_ROOT) -> list[str]:
    violations = _pytest_config_violations(project_root)
    inspected_paths = set((project_root / "tests").rglob("*.py"))
    root_conftest = project_root / "conftest.py"
    if root_conftest.is_file():
        inspected_paths.add(root_conftest)
    for path in sorted(inspected_paths):
        relative_path = path.relative_to(project_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        except (OSError, SyntaxError) as exc:
            violations.append(f"{relative_path}: cannot inspect test policy: {exc}")
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in FORBIDDEN_COLLECTION_NAMES:
                    violations.append(
                        f"{relative_path}:{node.lineno}: collection hook {node.name!r} is forbidden"
                    )
                elif node.name.startswith("pytest_"):
                    violations.append(
                        f"{relative_path}:{node.lineno}: pytest hook {node.name!r} is forbidden"
                    )
                arguments = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                if any(argument.arg == "socket_enabled" for argument in arguments):
                    violations.append(
                        f"{relative_path}:{node.lineno}: broad socket_enabled fixture is forbidden"
                    )
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and (
                        target.id in FORBIDDEN_COLLECTION_NAMES
                        or target.id.startswith("pytest_")
                    ):
                        violations.append(
                            f"{relative_path}:{node.lineno}: collection override "
                            f"{target.id!r} is forbidden"
                        )
            if isinstance(node, ast.Attribute):
                name = _qualified_name(node, aliases)
                if name in FORBIDDEN_CALLS:
                    violations.append(
                        f"{relative_path}:{node.lineno}: {name} is forbidden in the offline suite"
                    )
            if not isinstance(node, ast.Call):
                continue
            name = _qualified_name(node.func, aliases)
            if name in FORBIDDEN_CALLS and not isinstance(node.func, ast.Attribute):
                violations.append(
                    f"{relative_path}:{node.lineno}: {name} is forbidden in the offline suite"
                )
            if name == "pytest.mark.allow_hosts":
                hosts = _literal_hosts(node)
                if hosts is None or not hosts or not hosts <= LOOPBACK_HOSTS:
                    violations.append(
                        f"{relative_path}:{node.lineno}: allow_hosts must contain only literal "
                        f"loopback hosts {sorted(LOOPBACK_HOSTS)!r}"
                    )
            if name == "pytest.mark.parametrize" and _empty_parametrize_values(node):
                violations.append(
                    f"{relative_path}:{node.lineno}: empty parametrize values are forbidden"
                )
            dynamic_escape = _dynamic_forbidden_call(node, aliases)
            if dynamic_escape is not None:
                violations.append(
                    f"{relative_path}:{node.lineno}: dynamic {dynamic_escape} is forbidden"
                )
            if name and name.endswith(".getfixturevalue"):
                violations.append(
                    f"{relative_path}:{node.lineno}: dynamic fixture lookup is forbidden"
                )
            if name in CHILD_PROCESS_CALLS and not _allowed_child_process(node, name):
                violations.append(
                    f"{relative_path}:{node.lineno}: child process {name} is forbidden in the "
                    "offline suite"
                )
    return sorted(set(violations))


def _pytest_config_violations(project_root: Path) -> list[str]:
    with (project_root / "pyproject.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    pytest_config = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    addopts = pytest_config.get("addopts")
    violations: list[str] = []
    for filename in ("pytest.ini", ".pytest.ini"):
        if (project_root / filename).is_file():
            violations.append(
                f"{filename}: alternate pytest configuration is forbidden; use pyproject.toml"
            )
    for filename, sections in (
        ("tox.ini", {"pytest", "tool:pytest", "mypy", "ruff", "ruff:lint"}),
        ("setup.cfg", {"pytest", "tool:pytest", "mypy", "ruff", "ruff:lint"}),
    ):
        config_path = project_root / filename
        if not config_path.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(config_path, encoding="utf-8")
        except configparser.Error as exc:
            violations.append(f"{filename}: cannot inspect pytest configuration: {exc}")
            continue
        if sections & set(parser.sections()):
            violations.append(
                f"{filename}: alternate pytest configuration is forbidden; use pyproject.toml"
            )
    for filename in ("ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini"):
        if (project_root / filename).is_file():
            violations.append(
                f"{filename}: alternate analysis configuration is forbidden; use pyproject.toml"
            )

    tool_config = config.get("tool", {})
    if tool_config.get("ruff") != EXPECTED_RUFF_CONFIG:
        violations.append(
            f"pyproject.toml: Ruff configuration must be exactly {EXPECTED_RUFF_CONFIG!r}"
        )
    if tool_config.get("mypy") != EXPECTED_MYPY_CONFIG:
        violations.append(
            f"pyproject.toml: mypy configuration must be exactly {EXPECTED_MYPY_CONFIG!r}"
        )
    if pytest_config.get("testpaths") != ["tests"]:
        violations.append("pyproject.toml: pytest testpaths must be exactly ['tests']")
    if pytest_config.get("pythonpath") != ["."]:
        violations.append("pyproject.toml: pytest pythonpath must be exactly ['.']")
    allowed_ini_keys = {"testpaths", "pythonpath", "addopts"}
    violations.extend(
        f"pyproject.toml: pytest ini option {key!r} is forbidden"
        for key in sorted(set(pytest_config) - allowed_ini_keys)
    )
    if not isinstance(addopts, str):
        return violations + ["pyproject.toml: pytest addopts must be a string"]
    option_sequence = shlex.split(addopts)
    options = set(option_sequence)
    missing = sorted(REQUIRED_PYTEST_OPTIONS - options)
    violations.extend(
        f"pyproject.toml: pytest addopts is missing {option}" for option in missing
    )
    violations.extend(
        f"pyproject.toml: pytest addopts token {option!r} is forbidden"
        for option in sorted(options - REQUIRED_PYTEST_OPTIONS)
    )
    if option_sequence != list(REQUIRED_PYTEST_OPTION_SEQUENCE):
        violations.append(
            "pyproject.toml: pytest addopts must be exactly "
            f"{' '.join(REQUIRED_PYTEST_OPTION_SEQUENCE)!r}"
        )
    if len(option_sequence) != len(options):
        violations.append("pyproject.toml: duplicate pytest addopts are forbidden")
    for option in sorted(options):
        if (
            option in FORBIDDEN_PYTEST_OPTIONS
            or option == "--force-enable-socket"
            or option.startswith(("--allow-hosts", "--deselect", "--ignore"))
        ):
            violations.append(f"pyproject.toml: pytest escape option {option!r} is forbidden")
    return violations


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    for _ in range(4):
        changed = False
        for node in ast.walk(tree):
            targets: list[ast.AST] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            if value is None:
                continue
            qualified = _qualified_name(value, aliases)
            if qualified is None:
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id != qualified
                    and aliases.get(target.id) != qualified
                ):
                    aliases[target.id] = qualified
                    changed = True
        if not changed:
            break
    return aliases


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _literal_hosts(call: ast.Call) -> set[str] | None:
    if len(call.args) != 1 or call.keywords:
        return None
    value = call.args[0]
    if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return None
    hosts: set[str] = set()
    for element in value.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        hosts.add(element.value)
    return hosts


def _empty_parametrize_values(call: ast.Call) -> bool:
    value: ast.AST | None = call.args[1] if len(call.args) >= 2 else None
    if value is None:
        value = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "argvalues"),
            None,
        )
    return isinstance(value, ast.List | ast.Tuple | ast.Set) and not value.elts


def _dynamic_forbidden_call(call: ast.Call, aliases: dict[str, str]) -> str | None:
    if _qualified_name(call.func, aliases) != "getattr" or len(call.args) < 2:
        return None
    owner = _qualified_name(call.args[0], aliases)
    attribute = call.args[1]
    if owner is None or not isinstance(attribute, ast.Constant) or not isinstance(
        attribute.value, str
    ):
        return None
    dynamic_name = f"{owner}.{attribute.value}"
    return dynamic_name if dynamic_name in FORBIDDEN_CALLS | CHILD_PROCESS_CALLS else None


def _allowed_child_process(call: ast.Call, name: str) -> bool:
    if name != "subprocess.run" or not call.args:
        return False
    if any(
        keyword.arg in {"shell", "executable"}
        and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
        for keyword in call.keywords
    ):
        return False
    command = call.args[0]
    if not isinstance(command, ast.List | ast.Tuple) or len(command.elts) < 2:
        return False
    executable, subcommand = command.elts[:2]
    if not all(
        isinstance(value, ast.Constant) and isinstance(value.value, str)
        for value in (executable, subcommand)
    ):
        return False
    if executable.value == "git":
        return subcommand.value in ALLOWED_GIT_SUBCOMMANDS
    return executable.value == "bash" and subcommand.value == "scripts/check.sh"


def main() -> int:
    violations = find_policy_violations()
    if violations:
        print("test policy check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print("test policy check passed: strict offline suite with no skip or xfail escapes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

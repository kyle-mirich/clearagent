"""Reject test-suite escape hatches that can make a green gate misleading."""

from __future__ import annotations

import ast
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
FORBIDDEN_CALLS = {
    "pytest.skip",
    "pytest.xfail",
    "pytest.importorskip",
    "pytest.skip.Exception",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "pytest.mark.enable_socket",
    "pytest_socket.enable_socket",
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
}


def find_policy_violations(project_root: Path = PROJECT_ROOT) -> list[str]:
    violations = _pytest_config_violations(project_root)
    for path in sorted((project_root / "tests").rglob("*.py")):
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
                if any(argument.arg == "socket_enabled" for argument in node.args.args):
                    violations.append(
                        f"{relative_path}:{node.lineno}: broad socket_enabled fixture is forbidden"
                    )
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in FORBIDDEN_COLLECTION_NAMES:
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
    return sorted(set(violations))


def _pytest_config_violations(project_root: Path) -> list[str]:
    with (project_root / "pyproject.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    pytest_config = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    addopts = pytest_config.get("addopts")
    violations: list[str] = []
    if pytest_config.get("testpaths") != ["tests"]:
        violations.append("pyproject.toml: pytest testpaths must be exactly ['tests']")
    if not isinstance(addopts, str):
        return violations + ["pyproject.toml: pytest addopts must be a string"]
    options = set(shlex.split(addopts))
    missing = sorted(REQUIRED_PYTEST_OPTIONS - options)
    violations.extend(
        f"pyproject.toml: pytest addopts is missing {option}" for option in missing
    )
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

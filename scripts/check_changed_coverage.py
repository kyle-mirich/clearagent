"""Require every changed executable package line to be exercised by tests."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import token
import tokenize
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
SUPPRESSION_COMMENT = re.compile(
    r"^#\s*(?:"
    r"pragma:\s*no\s+(?:cover|branch)\b|"
    r"(?:ruff:\s*)?noqa\b|"
    r"type:\s*ignore\b|"
    r"mypy:\s*"
    r")",
    re.IGNORECASE,
)
GLOBAL_COVERAGE_MINIMUM = 95.0


class ChangedCoverageError(RuntimeError):
    """Raised when changed-line coverage cannot be proven."""


@dataclass(frozen=True)
class CoverageFailures:
    uncovered_lines: dict[str, list[int]]
    excluded_lines: dict[str, list[int]]
    uncovered_branches: dict[str, list[tuple[int, int]]]
    weak_files: dict[str, float]

    def any(self) -> bool:
        return any(
            (
                self.uncovered_lines,
                self.excluded_lines,
                self.uncovered_branches,
                self.weak_files,
            )
        )


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Return added line numbers for each Python package file in a unified diff."""
    changed: dict[str, set[int]] = {}
    current_path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t", 1)[0]
            current_path = None if path == "/dev/null" else path.removeprefix("b/")
            continue
        match = HUNK_HEADER.match(line)
        if match is None or current_path is None:
            continue
        count = int(match.group("count") or "1")
        if count:
            start = int(match.group("start"))
            changed.setdefault(current_path, set()).update(range(start, start + count))
    return changed


def changed_coverage_failures(
    changed: dict[str, set[int]],
    coverage_data: dict[str, Any],
    *,
    touched: set[str] | None = None,
    minimum_file_coverage: float = 90.0,
) -> tuple[CoverageFailures, int]:
    """Return changed-code and touched-file coverage failures."""
    files = coverage_data.get("files")
    if not isinstance(files, dict):
        raise ChangedCoverageError("coverage JSON does not contain a files mapping")

    normalized_files = {str(path).replace("\\", "/"): value for path, value in files.items()}
    uncovered_lines: dict[str, list[int]] = {}
    excluded_lines: dict[str, list[int]] = {}
    uncovered_branches: dict[str, list[tuple[int, int]]] = {}
    weak_files: dict[str, float] = {}
    executable_count = 0
    touched_paths = touched if touched is not None else set(changed)
    for path in sorted(touched_paths):
        line_numbers = changed.get(path, set())
        normalized_path = path.replace("\\", "/")
        report = _coverage_file_report(normalized_path, normalized_files)
        if report is None:
            raise ChangedCoverageError(f"coverage JSON has no entry for changed file {path!r}")
        if not isinstance(report, dict):
            raise ChangedCoverageError(f"coverage JSON entry for {path!r} is malformed")

        executed = _line_numbers(report, "executed_lines", path)
        missing_lines = _line_numbers(report, "missing_lines", path)
        excluded = _line_numbers(report, "excluded_lines", path)
        if executed & missing_lines:
            raise ChangedCoverageError(
                f"coverage JSON marks lines as both executed and missing for {path!r}"
            )
        executable = line_numbers & (executed | missing_lines)
        executable_count += len(executable)
        uncovered = sorted(executable & missing_lines)
        if uncovered:
            uncovered_lines[path] = uncovered
        changed_exclusions = sorted(line_numbers & excluded)
        if changed_exclusions:
            excluded_lines[path] = changed_exclusions

        missing_branches = _branch_pairs(report.get("missing_branches", []), path)
        changed_missing_branches = sorted(
            branch for branch in missing_branches if branch[0] in line_numbers
        )
        if changed_missing_branches:
            uncovered_branches[path] = changed_missing_branches

        percent = _combined_percent(report, path)
        if percent + 1e-9 < minimum_file_coverage:
            weak_files[path] = percent

    return (
        CoverageFailures(
            uncovered_lines=uncovered_lines,
            excluded_lines=excluded_lines,
            uncovered_branches=uncovered_branches,
            weak_files=weak_files,
        ),
        executable_count,
    )


def _coverage_file_report(normalized_path: str, normalized_files: dict[str, Any]) -> Any | None:
    report = normalized_files.get(normalized_path)
    if report is not None:
        return report
    matches = [
        value
        for candidate, value in normalized_files.items()
        if candidate.endswith(f"/{normalized_path}")
    ]
    return matches[0] if len(matches) == 1 else None


def _branch_pairs(value: Any, path: str) -> set[tuple[int, int]]:
    if not isinstance(value, list):
        raise ChangedCoverageError(f"coverage JSON branches for {path!r} are malformed")
    pairs: set[tuple[int, int]] = set()
    for branch in value:
        if not isinstance(branch, list | tuple) or len(branch) != 2:
            raise ChangedCoverageError(f"coverage JSON branches for {path!r} are malformed")
        pairs.add((int(branch[0]), int(branch[1])))
    return pairs


def _line_numbers(report: dict[str, Any], key: str, path: str) -> set[int]:
    if key not in report or not isinstance(report[key], list):
        raise ChangedCoverageError(f"coverage JSON {key} for {path!r} is malformed")
    lines: set[int] = set()
    for value in report[key]:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ChangedCoverageError(f"coverage JSON {key} for {path!r} is malformed")
        lines.add(value)
    return lines


def _combined_percent(report: dict[str, Any], path: str) -> float:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ChangedCoverageError(f"coverage JSON summary for {path!r} is malformed")
    try:
        covered = int(summary["covered_lines"]) + int(summary["covered_branches"])
        total = int(summary["num_statements"]) + int(summary["num_branches"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ChangedCoverageError(f"coverage JSON summary for {path!r} is malformed") from exc
    return 100.0 if total == 0 else covered * 100.0 / total


def validate_coverage_data(
    coverage_data: dict[str, Any], *, minimum_global_coverage: float = GLOBAL_COVERAGE_MINIMUM
) -> None:
    """Reject incomplete, line-only, or rounded-up coverage reports."""
    meta = coverage_data.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        raise ChangedCoverageError("coverage JSON must prove branch coverage")
    files = coverage_data.get("files")
    if not isinstance(files, dict):
        raise ChangedCoverageError("coverage JSON does not contain a files mapping")
    summed = {
        "covered_lines": 0,
        "num_statements": 0,
        "covered_branches": 0,
        "num_branches": 0,
    }
    for path, report in files.items():
        if not isinstance(report, dict):
            raise ChangedCoverageError(f"coverage JSON entry for {path!r} is malformed")
        executed = _line_numbers(report, "executed_lines", str(path))
        missing = _line_numbers(report, "missing_lines", str(path))
        _line_numbers(report, "excluded_lines", str(path))
        _branch_pairs(report.get("executed_branches"), str(path))
        missing_branches = _branch_pairs(report.get("missing_branches"), str(path))
        summary = report.get("summary")
        if not isinstance(summary, dict):
            raise ChangedCoverageError(f"coverage JSON summary for {path!r} is malformed")
        try:
            values = {
                key: int(summary[key])
                for key in (
                    "covered_lines",
                    "num_statements",
                    "missing_lines",
                    "covered_branches",
                    "num_branches",
                    "missing_branches",
                )
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ChangedCoverageError(f"coverage JSON summary for {path!r} is malformed") from exc
        if any(value < 0 for value in values.values()):
            raise ChangedCoverageError(f"coverage JSON summary for {path!r} is malformed")
        if (
            len(executed) != values["covered_lines"]
            or len(missing) != values["missing_lines"]
            or values["covered_lines"] + values["missing_lines"] != values["num_statements"]
            or len(missing_branches) != values["missing_branches"]
            or values["covered_branches"] + values["missing_branches"] != values["num_branches"]
        ):
            raise ChangedCoverageError(f"coverage JSON summary for {path!r} is inconsistent")
        for key in summed:
            summed[key] += values[key]
    totals = coverage_data.get("totals")
    if not isinstance(totals, dict):
        raise ChangedCoverageError("coverage JSON totals are malformed")
    try:
        covered = int(totals["covered_lines"]) + int(totals["covered_branches"])
        total = int(totals["num_statements"]) + int(totals["num_branches"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ChangedCoverageError("coverage JSON totals are malformed") from exc
    actual_totals = {
        "covered_lines": int(totals["covered_lines"]),
        "num_statements": int(totals["num_statements"]),
        "covered_branches": int(totals["covered_branches"]),
        "num_branches": int(totals["num_branches"]),
    }
    if actual_totals != summed:
        raise ChangedCoverageError(
            f"coverage JSON totals are inconsistent: expected={summed!r} actual={actual_totals!r}"
        )
    percent = 100.0 if total == 0 else covered * 100.0 / total
    if percent + 1e-9 < minimum_global_coverage:
        raise ChangedCoverageError(
            f"combined global coverage is {percent:.6f}%, below {minimum_global_coverage:.2f}%"
        )


def map_changed_lines_to_coverage_anchors(
    changed: dict[str, set[int]],
    coverage_data: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, set[int]]:
    """Map changed multiline syntax to the executable statement that owns it."""
    root = PROJECT_ROOT if project_root is None else project_root
    files = coverage_data.get("files")
    if not isinstance(files, dict):
        raise ChangedCoverageError("coverage JSON does not contain a files mapping")
    normalized_files = {str(path).replace("\\", "/"): value for path, value in files.items()}
    mapped = {path: set(lines) for path, lines in changed.items()}
    for path, physical_lines in sorted(changed.items()):
        report = _coverage_file_report(path.replace("\\", "/"), normalized_files)
        if not isinstance(report, dict):
            raise ChangedCoverageError(
                f"coverage JSON has no valid entry for changed file {path!r}"
            )
        anchors = (
            _line_numbers(report, "executed_lines", path)
            | _line_numbers(report, "missing_lines", path)
            | _line_numbers(report, "excluded_lines", path)
        )
        source_path = root / path
        try:
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(source_path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise ChangedCoverageError(
                f"could not parse changed Python file {path!r}: {exc}"
            ) from exc

        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        positioned_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(getattr(node, "lineno", None), int)
            and isinstance(getattr(node, "end_lineno", None), int)
        ]
        semantic_lines = _semantic_source_lines(source, path)
        docstring_lines = _docstring_source_lines(tree)
        stub_lines = _stub_source_lines(tree)
        mapped[path].intersection_update(semantic_lines)
        mapped[path].difference_update(docstring_lines)
        mapped[path].difference_update(stub_lines)
        for line_number in sorted(physical_lines):
            if (
                line_number in anchors
                or line_number not in semantic_lines
                or line_number in docstring_lines
                or line_number in stub_lines
            ):
                continue
            candidates = sorted(
                (
                    node
                    for node in positioned_nodes
                    if node.lineno <= line_number <= node.end_lineno
                ),
                key=lambda node: (node.end_lineno - node.lineno, -node.lineno),
            )
            anchor = _ancestor_coverage_anchor(candidates, parents, anchors)
            if anchor is None:
                raise ChangedCoverageError(
                    f"changed Python syntax at {path}:{line_number} has no coverage statement anchor"
                )
            mapped[path].add(anchor)
    return mapped


def _semantic_source_lines(source: str, path: str) -> set[int]:
    ignored = {
        token.ENCODING,
        token.ENDMARKER,
        token.INDENT,
        token.DEDENT,
        token.NEWLINE,
        tokenize.NL,
        token.COMMENT,
    }
    semantic: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for source_token in tokens:
            if source_token.type in ignored:
                continue
            semantic.update(range(source_token.start[0], source_token.end[0] + 1))
    except (IndentationError, tokenize.TokenError) as exc:
        raise ChangedCoverageError(
            f"could not tokenize changed Python file {path!r}: {exc}"
        ) from exc
    return semantic


def _docstring_source_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def _stub_source_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if len(node.body) != 1:
            continue
        statement = node.body[0]
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis
        ):
            continue
        lines.update(range(node.lineno, node.end_lineno + 1))
    return lines


def _ancestor_coverage_anchor(
    candidates: list[ast.AST],
    parents: dict[ast.AST, ast.AST],
    anchors: set[int],
) -> int | None:
    for candidate in candidates:
        current: ast.AST | None = candidate
        while current is not None:
            line_number = getattr(current, "lineno", None)
            if line_number in anchors:
                return line_number
            current = parents.get(current)
    return None


def _git_output(arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ChangedCoverageError(
            f"git {' '.join(arguments)} failed with exit code {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def resolve_base(explicit_base: str | None = None) -> str:
    """Resolve the comparison commit for CI and local feature branches."""
    candidate = explicit_base or os.environ.get("CLEARAGENT_COVERAGE_BASE")
    if candidate and set(candidate) != {"0"}:
        return _git_output(["rev-parse", "--verify", f"{candidate}^{{commit}}"])

    for reference in ("origin/main", "main"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{reference}^{{commit}}"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return _git_output(["merge-base", "HEAD", reference])
    return _git_output(["rev-parse", "HEAD"])


def load_changes(base: str) -> tuple[dict[str, set[int]], set[str], set[str]]:
    """Return changed source lines, touched source files, and all changed paths."""
    diff = _git_output(
        [
            "diff",
            "--unified=0",
            "--no-color",
            "--find-renames",
            "--diff-filter=ACMRT",
            base,
            "--",
            "src/clearagent",
        ]
    )
    changed = {
        path: lines for path, lines in parse_changed_lines(diff).items() if path.endswith(".py")
    }
    tracked_paths = set(
        filter(
            None,
            _git_output(
                [
                    "diff",
                    "--name-only",
                    "--no-color",
                    "--find-renames",
                    "--diff-filter=ACMRTD",
                    base,
                ]
            ).splitlines(),
        )
    )
    untracked_paths = set(
        filter(
            None,
            _git_output(["ls-files", "--others", "--exclude-standard"]).splitlines(),
        )
    )
    all_changed_paths = tracked_paths | untracked_paths
    touched = {
        path
        for path in all_changed_paths
        if path.startswith("src/clearagent/")
        and path.endswith(".py")
        and (PROJECT_ROOT / path).is_file()
    }
    for path in sorted(touched & untracked_paths):
        source_path = PROJECT_ROOT / path
        line_count = len(source_path.read_text(encoding="utf-8").splitlines())
        changed[path] = set(range(1, line_count + 1))
    return changed, touched, all_changed_paths


def load_changed_python_lines(base: str) -> dict[str, set[int]]:
    """Return changed physical lines for every Python file checked by static analysis."""
    diff = _git_output(
        [
            "diff",
            "--unified=0",
            "--no-color",
            "--find-renames",
            "--diff-filter=ACMRT",
            base,
            "--",
            "*.py",
        ]
    )
    changed = parse_changed_lines(diff)
    untracked_paths = set(
        filter(
            None,
            _git_output(["ls-files", "--others", "--exclude-standard"]).splitlines(),
        )
    )
    for path in sorted(untracked_paths):
        source_path = PROJECT_ROOT / path
        if not path.endswith(".py") or not source_path.is_file():
            continue
        line_count = len(source_path.read_text(encoding="utf-8").splitlines())
        changed[path] = set(range(1, line_count + 1))
    return changed


def require_browser_test_for_static_changes(changed_paths: set[str]) -> None:
    static_changed = sorted(
        path for path in changed_paths if path.startswith("src/clearagent/chat/static/")
    )
    browser_test_changed = any(
        path.startswith("tests/browser/") and path.endswith(".py") for path in changed_paths
    )
    if static_changed and not browser_test_changed:
        raise ChangedCoverageError(
            "chat static assets changed without an executable browser test change: "
            f"{static_changed!r}"
        )


def find_changed_suppressions(
    changed: dict[str, set[int]], project_root: Path = PROJECT_ROOT
) -> dict[str, list[int]]:
    failures: dict[str, list[int]] = {}
    for path, changed_lines in sorted(changed.items()):
        source_path = project_root / path
        if not source_path.is_file():
            continue
        try:
            source = source_path.read_text(encoding="utf-8")
            comments = {
                source_token.start[0]: source_token.string
                for source_token in tokenize.generate_tokens(io.StringIO(source).readline)
                if source_token.type == token.COMMENT
            }
        except (OSError, UnicodeError, IndentationError, tokenize.TokenError) as exc:
            raise ChangedCoverageError(
                f"could not inspect suppressions in {path!r}: {exc}"
            ) from exc
        rejected = sorted(
            line_number
            for line_number in changed_lines
            if line_number in comments and SUPPRESSION_COMMENT.search(comments[line_number])
        )
        if rejected:
            failures[path] = rejected
    return failures


def find_changed_coverage_pragmas(
    changed: dict[str, set[int]], project_root: Path = PROJECT_ROOT
) -> dict[str, list[int]]:
    """Backward-compatible helper returning changed suppression directives."""
    return find_changed_suppressions(changed, project_root)


def run(coverage_json: Path, *, base: str | None = None) -> None:
    resolved_base = resolve_base(base)
    with coverage_json.open(encoding="utf-8") as report_file:
        coverage_data = json.load(report_file)
    validate_coverage_data(coverage_data)
    changed, touched, changed_paths = load_changes(resolved_base)
    require_browser_test_for_static_changes(changed_paths)
    changed_python = load_changed_python_lines(resolved_base)
    suppression_failures = find_changed_suppressions(changed_python)
    if suppression_failures:
        raise ChangedCoverageError(
            "changed coverage or static-analysis suppressions are forbidden:\n"
            + _line_failure_details(suppression_failures)
        )
    mapped_changed = map_changed_lines_to_coverage_anchors(changed, coverage_data)
    failures, executable_count = changed_coverage_failures(
        mapped_changed,
        coverage_data,
        touched=touched,
    )
    if failures.any():
        sections: list[str] = []
        if failures.uncovered_lines:
            sections.append(
                "uncovered changed executable lines:\n"
                + _line_failure_details(failures.uncovered_lines)
            )
        if failures.excluded_lines:
            sections.append(
                "changed lines excluded from coverage:\n"
                + _line_failure_details(failures.excluded_lines)
            )
        if failures.uncovered_branches:
            sections.append(
                "uncovered branches originating on changed lines:\n"
                + "\n".join(
                    f"  {path}: "
                    + ", ".join(f"{origin}->{destination}" for origin, destination in branches)
                    for path, branches in failures.uncovered_branches.items()
                )
            )
        if failures.weak_files:
            sections.append(
                "touched product files below 90% combined coverage:\n"
                + "\n".join(
                    f"  {path}: {percent:.2f}%" for path, percent in failures.weak_files.items()
                )
            )
        raise ChangedCoverageError(
            "\n".join(sections)
            + "\nAdd tests that execute every changed behavior; do not exclude it."
        )
    print(
        f"changed coverage passed: {executable_count} executable line(s), "
        f"{len(touched)} touched product file(s) since {resolved_base[:12]}"
    )


def _line_failure_details(failures: dict[str, list[int]]) -> str:
    return "\n".join(
        f"  {path}: {', '.join(str(line) for line in lines)}" for path, lines in failures.items()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--base")
    args = parser.parse_args()
    try:
        run(args.coverage_json, base=args.base)
    except (ChangedCoverageError, OSError, json.JSONDecodeError) as error:
        print(f"changed-line coverage failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

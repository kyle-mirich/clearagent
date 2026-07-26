"""Require every changed executable package line to be exercised by tests."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
COVERAGE_PRAGMA = re.compile(r"#\s*pragma:\s*no\s+(?:cover|branch)\b", re.IGNORECASE)


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

        executed = {int(line) for line in report.get("executed_lines", [])}
        missing_lines = {int(line) for line in report.get("missing_lines", [])}
        excluded = {int(line) for line in report.get("excluded_lines", [])}
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


def _coverage_file_report(
    normalized_path: str, normalized_files: dict[str, Any]
) -> Any | None:
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


def _combined_percent(report: dict[str, Any], path: str) -> float:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ChangedCoverageError(f"coverage JSON summary for {path!r} is malformed")
    try:
        covered = int(summary["covered_lines"]) + int(summary.get("covered_branches", 0))
        total = int(summary["num_statements"]) + int(summary.get("num_branches", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ChangedCoverageError(
            f"coverage JSON summary for {path!r} is malformed"
        ) from exc
    return 100.0 if total == 0 else covered * 100.0 / total


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
    changed = parse_changed_lines(diff)
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
        if path.startswith("src/clearagent/") and path.endswith(".py")
        and (PROJECT_ROOT / path).is_file()
    }
    for path in sorted(touched & untracked_paths):
        source_path = PROJECT_ROOT / path
        line_count = len(source_path.read_text(encoding="utf-8").splitlines())
        changed[path] = set(range(1, line_count + 1))
    return changed, touched, all_changed_paths


def require_browser_test_for_static_changes(changed_paths: set[str]) -> None:
    static_changed = sorted(
        path for path in changed_paths if path.startswith("src/clearagent/chat/static/")
    )
    browser_test_changed = any(
        path.startswith("tests/browser/") and path.endswith(".py")
        for path in changed_paths
    )
    if static_changed and not browser_test_changed:
        raise ChangedCoverageError(
            "chat static assets changed without an executable browser test change: "
            f"{static_changed!r}"
        )


def find_changed_coverage_pragmas(
    changed: dict[str, set[int]], project_root: Path = PROJECT_ROOT
) -> dict[str, list[int]]:
    failures: dict[str, list[int]] = {}
    for path, changed_lines in sorted(changed.items()):
        source_path = project_root / path
        if not source_path.is_file():
            continue
        lines = source_path.read_text(encoding="utf-8").splitlines()
        rejected = sorted(
            line_number
            for line_number in changed_lines
            if line_number <= len(lines) and COVERAGE_PRAGMA.search(lines[line_number - 1])
        )
        if rejected:
            failures[path] = rejected
    return failures


def run(coverage_json: Path, *, base: str | None = None) -> None:
    resolved_base = resolve_base(base)
    with coverage_json.open(encoding="utf-8") as report_file:
        coverage_data = json.load(report_file)
    changed, touched, changed_paths = load_changes(resolved_base)
    require_browser_test_for_static_changes(changed_paths)
    pragma_failures = find_changed_coverage_pragmas(changed)
    if pragma_failures:
        raise ChangedCoverageError(
            "changed coverage-suppression pragmas are forbidden:\n"
            + _line_failure_details(pragma_failures)
        )
    failures, executable_count = changed_coverage_failures(
        changed,
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
                    f"  {path}: {percent:.2f}%"
                    for path, percent in failures.weak_files.items()
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
        f"  {path}: {', '.join(str(line) for line in lines)}"
        for path, lines in failures.items()
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

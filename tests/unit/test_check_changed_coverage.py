import json
from pathlib import Path
import subprocess

import pytest

import scripts.check_changed_coverage as checker
from scripts.check_changed_coverage import (
    ChangedCoverageError,
    changed_coverage_failures,
    find_changed_coverage_pragmas,
    find_changed_suppressions,
    map_changed_lines_to_coverage_anchors,
    parse_changed_lines,
    require_browser_test_for_static_changes,
    validate_coverage_data,
)


def test_parse_changed_lines_reads_only_new_hunk_ranges():
    diff = """\
diff --git a/src/clearagent/example.py b/src/clearagent/example.py
--- a/src/clearagent/example.py
+++ b/src/clearagent/example.py
@@ -4,2 +4,3 @@
-old
+new
+branch
 context
@@ -20 +21,0 @@
-deleted
"""

    assert parse_changed_lines(diff) == {"src/clearagent/example.py": {4, 5, 6}}


def test_changed_coverage_accepts_executed_code_and_ignores_non_executable_lines():
    changed = {"src/clearagent/example.py": {4, 5, 6}}
    coverage = {
        "files": {
            "src/clearagent/example.py": {
                "executed_lines": [4, 6],
                "missing_lines": [],
                "excluded_lines": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 2,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        }
    }

    failures, executable_count = changed_coverage_failures(changed, coverage)

    assert failures.any() is False
    assert executable_count == 2


def test_changed_coverage_reports_each_deliberately_uncovered_line():
    changed = {"src/clearagent/example.py": {4, 5, 6}}
    coverage = {
        "files": {
            "src/clearagent/example.py": {
                "executed_lines": [4],
                "missing_lines": [5, 6, 9],
                "excluded_lines": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 1,
                    "num_statements": 4,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        }
    }

    failures, executable_count = changed_coverage_failures(
        changed,
        coverage,
        minimum_file_coverage=0,
    )

    assert failures.uncovered_lines == {"src/clearagent/example.py": [5, 6]}
    assert executable_count == 3


def test_changed_coverage_rejects_changed_file_absent_from_report():
    with pytest.raises(ChangedCoverageError, match="no entry.*missing.py"):
        changed_coverage_failures(
            {"src/clearagent/missing.py": {1}},
            {"files": {}},
        )


def test_changed_coverage_rejects_malformed_line_arrays():
    path = "src/clearagent/example.py"
    malformed = {
        "files": {
            path: {
                "missing_lines": [],
                "excluded_lines": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 1,
                    "num_statements": 1,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        }
    }

    with pytest.raises(ChangedCoverageError, match="executed_lines.*malformed"):
        changed_coverage_failures({path: {1}}, malformed)


def test_changed_coverage_rejects_changed_exclusions_and_partial_branches():
    path = "src/clearagent/example.py"
    coverage = {
        "files": {
            path: {
                "executed_lines": [4, 5],
                "missing_lines": [],
                "excluded_lines": [5],
                "missing_branches": [[4, 8]],
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 2,
                    "covered_branches": 1,
                    "num_branches": 2,
                },
            }
        }
    }

    failures, _ = changed_coverage_failures(
        {path: {4, 5}},
        coverage,
        minimum_file_coverage=0,
    )

    assert failures.excluded_lines == {path: [5]}
    assert failures.uncovered_branches == {path: [(4, 8)]}


def test_touched_file_floor_catches_deletion_only_changes_hidden_by_global_coverage():
    path = "src/clearagent/weak.py"
    coverage = {
        "files": {
            path: {
                "executed_lines": [1] * 89,
                "missing_lines": list(range(90, 101)),
                "excluded_lines": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 89,
                    "num_statements": 100,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        }
    }

    failures, executable_count = changed_coverage_failures(
        {},
        coverage,
        touched={path},
    )

    assert executable_count == 0
    assert failures.weak_files == {path: 89.0}


def test_static_assets_require_a_changed_executable_browser_test():
    static_change = {"src/clearagent/chat/static/app.js"}

    with pytest.raises(ChangedCoverageError, match="without an executable browser test"):
        require_browser_test_for_static_changes(static_change)

    require_browser_test_for_static_changes(static_change | {"tests/browser/test_chat_ui.py"})


def test_changed_coverage_rejects_line_and_branch_suppression_pragmas(tmp_path):
    source = tmp_path / "src" / "clearagent" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "covered = True\n"
        "uncovered = False  # pragma: no cover\n"
        "if covered:  # pragma: no branch\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert find_changed_coverage_pragmas(
        {"src/clearagent/example.py": {1, 2, 3}},
        tmp_path,
    ) == {"src/clearagent/example.py": [2, 3]}


def test_changed_suppressions_reject_static_analysis_escapes_but_not_string_literals(
    tmp_path: Path,
):
    source = tmp_path / "tests" / "test_example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "literal = '# noqa and # type: ignore are data'\n"
        "lint = missing_name  # noqa: F821\n"
        "ruff = missing_name  # ruff: noqa\n"
        "typed = missing_name  # type: ignore[name-defined]\n"
        "# mypy: ignore-errors\n",
        encoding="utf-8",
    )

    assert find_changed_suppressions({"tests/test_example.py": {1, 2, 3, 4, 5}}, tmp_path) == {
        "tests/test_example.py": [2, 3, 4, 5]
    }


def test_multiline_syntax_maps_to_its_statement_and_branch_anchor(tmp_path: Path):
    path = "src/clearagent/example.py"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text(
        "SETTINGS = {\n"
        "    'first': True,\n"
        "    'second': False,\n"
        "}\n"
        "\n"
        "def choose(\n"
        "    value: str = 'first',\n"
        ") -> str:\n"
        "    return value\n"
        "\n"
        "# comment-only change\n",
        encoding="utf-8",
    )
    coverage = {
        "files": {
            path: {
                "executed_lines": [1, 6, 9],
                "missing_lines": [],
                "excluded_lines": [],
                "missing_branches": [[1, 6]],
                "summary": {
                    "covered_lines": 3,
                    "num_statements": 3,
                    "covered_branches": 1,
                    "num_branches": 2,
                },
            }
        }
    }

    mapped = map_changed_lines_to_coverage_anchors(
        {path: {3, 7, 11}}, coverage, project_root=tmp_path
    )
    failures, executable_count = changed_coverage_failures(
        mapped, coverage, minimum_file_coverage=0
    )

    assert mapped[path] == {1, 3, 6, 7}
    assert executable_count == 2
    assert failures.uncovered_branches == {path: [(1, 6)]}


def test_protocol_stub_lines_are_not_treated_as_coverage_exclusions(tmp_path: Path):
    path = "src/clearagent/protocol.py"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "class Example(Protocol):\n"
        "    def run(\n"
        "        self, value: str,\n"
        "    ) -> str: ...\n"
        "value = 1\n",
        encoding="utf-8",
    )
    coverage = {
        "files": {
            path: {
                "executed_lines": [1, 2, 6],
                "missing_lines": [],
                "excluded_lines": [3, 4, 5],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 3,
                    "num_statements": 3,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        }
    }

    mapped = map_changed_lines_to_coverage_anchors(
        {path: {3, 4, 5, 6}}, coverage, project_root=tmp_path
    )
    failures, executable_count = changed_coverage_failures(
        mapped, coverage, minimum_file_coverage=0
    )

    assert mapped[path] == {6}
    assert executable_count == 1
    assert failures.any() is False


def test_coverage_validation_requires_branches_consistent_arrays_and_exact_global_floor():
    path = "src/clearagent/example.py"
    valid = {
        "meta": {"branch_coverage": True},
        "files": {
            path: {
                "executed_lines": list(range(1, 95000)),
                "missing_lines": list(range(95000, 100001)),
                "excluded_lines": [],
                "executed_branches": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 94999,
                    "num_statements": 100000,
                    "missing_lines": 5001,
                    "covered_branches": 0,
                    "num_branches": 0,
                    "missing_branches": 0,
                },
            }
        },
        "totals": {
            "covered_lines": 94999,
            "num_statements": 100000,
            "covered_branches": 0,
            "num_branches": 0,
        },
    }

    with pytest.raises(ChangedCoverageError, match="94.999000%"):
        validate_coverage_data(valid)

    valid["meta"]["branch_coverage"] = False
    with pytest.raises(ChangedCoverageError, match="must prove branch coverage"):
        validate_coverage_data(valid)


def _coverage_file(*, executed: list[int], missing: list[int]) -> dict[str, object]:
    return {
        "executed_lines": executed,
        "missing_lines": missing,
        "excluded_lines": [],
        "executed_branches": [],
        "missing_branches": [],
        "summary": {
            "covered_lines": len(executed),
            "num_statements": len(executed) + len(missing),
            "missing_lines": len(missing),
            "covered_branches": 0,
            "num_branches": 0,
            "missing_branches": 0,
        },
    }


def _coverage_document(files: dict[str, dict[str, object]]) -> dict[str, object]:
    covered = sum(int(report["summary"]["covered_lines"]) for report in files.values())
    statements = sum(int(report["summary"]["num_statements"]) for report in files.values())
    return {
        "meta": {"branch_coverage": True},
        "files": files,
        "totals": {
            "covered_lines": covered,
            "num_statements": statements,
            "covered_branches": 0,
            "num_branches": 0,
        },
    }


def test_real_gate_rejects_untracked_and_uncovered_changes_then_accepts_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    package = tmp_path / "src" / "clearagent"
    package.mkdir(parents=True)
    tracked = package / "tracked.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    typing_marker = package / "py.typed"
    typing_marker.write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "gate@example.invalid"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Gate Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    tracked.write_text("value = 2\nchanged = 3\n", encoding="utf-8")
    typing_marker.write_text("typed package marker\n", encoding="utf-8")
    untracked = package / "untracked.py"
    untracked.write_text("new_value = 4\n", encoding="utf-8")
    report_path = tmp_path / "coverage.json"
    monkeypatch.setattr(checker, "PROJECT_ROOT", tmp_path)
    filler = _coverage_file(executed=list(range(1, 101)), missing=[])

    report_path.write_text(
        json.dumps(
            _coverage_document(
                {
                    "src/clearagent/filler.py": filler,
                    "src/clearagent/tracked.py": _coverage_file(executed=[1, 2], missing=[]),
                }
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(ChangedCoverageError, match="untracked.py"):
        checker.run(report_path, base=base)

    report_path.write_text(
        json.dumps(
            _coverage_document(
                {
                    "src/clearagent/filler.py": filler,
                    "src/clearagent/tracked.py": _coverage_file(executed=[1], missing=[2]),
                    "src/clearagent/untracked.py": _coverage_file(executed=[1], missing=[]),
                }
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(ChangedCoverageError, match="tracked.py: 2"):
        checker.run(report_path, base=base)

    report_path.write_text(
        json.dumps(
            _coverage_document(
                {
                    "src/clearagent/filler.py": filler,
                    "src/clearagent/tracked.py": _coverage_file(executed=[1, 2], missing=[]),
                    "src/clearagent/untracked.py": _coverage_file(executed=[1], missing=[]),
                }
            )
        ),
        encoding="utf-8",
    )
    checker.run(report_path, base=base)

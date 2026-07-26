import pytest

from scripts.check_changed_coverage import (
    ChangedCoverageError,
    changed_coverage_failures,
    find_changed_coverage_pragmas,
    parse_changed_lines,
    require_browser_test_for_static_changes,
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

    require_browser_test_for_static_changes(
        static_change | {"tests/browser/test_chat_ui.py"}
    )


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

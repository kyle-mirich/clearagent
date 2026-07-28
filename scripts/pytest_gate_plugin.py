"""Runtime pytest invariants for the authoritative ClearAgent quality gate."""

from __future__ import annotations

from typing import Any

import pytest


class NoOutcomeEscapePlugin:
    """Turn skipped or expected outcomes into a failing gate."""

    def __init__(self) -> None:
        self.forbidden_outcomes: dict[str, str] = {}
        self.failed_reports: set[str] = set()

    def pytest_runtest_logreport(self, report: Any) -> None:
        if getattr(report, "failed", False):
            self.failed_reports.add(report.nodeid)
        was_xfail = getattr(report, "wasxfail", None)
        if report.skipped:
            outcome = "xfail" if was_xfail else "skip"
            self.forbidden_outcomes.setdefault(report.nodeid, outcome)
        elif was_xfail:
            self.forbidden_outcomes.setdefault(report.nodeid, "xpass")

    def pytest_deselected(self, items: list[Any]) -> None:
        for item in items:
            self.forbidden_outcomes.setdefault(item.nodeid, "deselected")

    def pytest_collectreport(self, report: Any) -> None:
        if getattr(report, "failed", False):
            self.failed_reports.add(report.nodeid)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session: Any):
        yield
        self.enforce_session(session)

    def enforce_session(self, session: Any) -> None:
        failures: list[str] = []
        if session.testscollected == 0:
            failures.append("the authoritative gate collected zero tests")
        failures.extend(f"{nodeid}: failed report" for nodeid in sorted(self.failed_reports))
        failures.extend(
            f"{nodeid}: forbidden {outcome} outcome"
            for nodeid, outcome in sorted(self.forbidden_outcomes.items())
        )
        if not failures:
            return

        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.ensure_newline()
            reporter.write_sep("=", "ClearAgent gate outcome violations", red=True)
            for failure in failures:
                reporter.write_line(failure, red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config: Any) -> None:
    config.pluginmanager.register(NoOutcomeEscapePlugin(), "clearagent-no-outcome-escapes")

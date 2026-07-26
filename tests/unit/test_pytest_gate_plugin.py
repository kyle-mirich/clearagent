from types import SimpleNamespace

import pytest

from scripts.pytest_gate_plugin import NoOutcomeEscapePlugin


class _Reporter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def ensure_newline(self) -> None:
        return None

    def write_sep(self, separator: str, title: str, **kwargs) -> None:
        self.lines.append(f"{separator}:{title}")

    def write_line(self, line: str, **kwargs) -> None:
        self.lines.append(line)


def _session(testscollected: int):
    reporter = _Reporter()
    plugin_manager = SimpleNamespace(
        get_plugin=lambda name: reporter if name == "terminalreporter" else None
    )
    session = SimpleNamespace(
        testscollected=testscollected,
        exitstatus=pytest.ExitCode.OK,
        config=SimpleNamespace(pluginmanager=plugin_manager),
    )
    return session, reporter


@pytest.mark.parametrize(
    ("report", "outcome"),
    [
        (SimpleNamespace(nodeid="test_skip", skipped=True), "skip"),
        (SimpleNamespace(nodeid="test_xfail", skipped=True, wasxfail="reason"), "xfail"),
        (SimpleNamespace(nodeid="test_xpass", skipped=False, wasxfail="reason"), "xpass"),
    ],
)
def test_runtime_plugin_fails_skipped_and_expected_outcomes(report, outcome):
    plugin = NoOutcomeEscapePlugin()
    session, reporter = _session(testscollected=1)

    plugin.pytest_runtest_logreport(report)
    plugin.enforce_session(session)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert reporter.lines[-1] == f"{report.nodeid}: forbidden {outcome} outcome"


def test_runtime_plugin_fails_zero_collection_and_allows_normal_passes():
    plugin = NoOutcomeEscapePlugin()
    empty_session, empty_reporter = _session(testscollected=0)

    plugin.enforce_session(empty_session)

    assert empty_session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert empty_reporter.lines[-1] == "the authoritative gate collected zero tests"

    passing_session, passing_reporter = _session(testscollected=1)
    plugin.pytest_runtest_logreport(SimpleNamespace(nodeid="test_pass", skipped=False))
    plugin.enforce_session(passing_session)
    assert passing_session.exitstatus == pytest.ExitCode.OK
    assert passing_reporter.lines == []


def test_runtime_plugin_fails_deselected_tests():
    plugin = NoOutcomeEscapePlugin()
    session, reporter = _session(testscollected=1)

    plugin.pytest_deselected([SimpleNamespace(nodeid="test_hidden")])
    plugin.enforce_session(session)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert reporter.lines[-1] == "test_hidden: forbidden deselected outcome"


def test_runtime_plugin_reasserts_failure_exit_status():
    plugin = NoOutcomeEscapePlugin()
    session, reporter = _session(testscollected=1)

    plugin.pytest_runtest_logreport(
        SimpleNamespace(nodeid="test_broken", skipped=False, failed=True)
    )
    session.exitstatus = pytest.ExitCode.OK
    plugin.enforce_session(session)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert reporter.lines[-1] == "test_broken: failed report"

from pathlib import Path

from clearagent.agent import Agent
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalSuite


def assert_eval_suite_passes(agent: Agent, suite_path: str | Path, *, trace_db_path=None) -> None:
    report = EvalRunner(agent, trace_db_path=trace_db_path).run_suite(EvalSuite.from_yaml(suite_path))
    if report.failed:
        lines = [f"ClearAgent suite {report.suite_name} failed:"]
        for result in report.results:
            if result.passed:
                continue
            failed_checks = [check for check in result.checks if not check["passed"]]
            variant = f" variant={result.variant}" if result.variant else ""
            lines.append(
                f"- {result.case_name}: {failed_checks}{variant} output={result.final_output!r} "
                f"run_id={result.run_id} trace_db={result.trace_db_path}"
            )
        raise AssertionError("\n".join(lines))


def pytest_addoption(parser):
    parser.addoption("--clearagent-trace-db", action="store", default=None)
    parser.addoption("--clearagent-no-trace", action="store_true", default=False)
    parser.addoption("--clearagent-model", action="store", default=None)


def pytest_configure(config):
    config.addinivalue_line("markers", "clearagent: ClearAgent eval test")
    config.addinivalue_line("markers", "clearagent_suite(path): ClearAgent eval suite")
    config.addinivalue_line("markers", "clearagent_live_model: requires a live provider API key")

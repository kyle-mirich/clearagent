import pytest

from clearagent import create_agent
from clearagent.providers.base import FakeProvider, ProviderResponse
from clearagent.pytest_plugin import assert_eval_suite_passes


def write_suite(path, expected):
    path.write_text(
        f"""
name: smoke
type: output
cases:
  - name: expected output
    input: hello
    checks:
      - contains: {expected}
""",
        encoding="utf-8",
    )


def test_pytest_helper_passes_and_fails_readably(tmp_path):
    passing = tmp_path / "passing.yaml"
    failing = tmp_path / "failing.yaml"
    write_suite(passing, "hello")
    write_suite(failing, "missing")

    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello"), ProviderResponse.fake_text("hello")]),
        trace_db_path=tmp_path / "traces.sqlite",
    )

    assert_eval_suite_passes(agent, passing)
    with pytest.raises(AssertionError) as error:
        assert_eval_suite_passes(agent, failing)

    message = str(error.value)
    assert "smoke" in message
    assert "expected output" in message
    assert "contains" in message
    assert "run_" in message
    assert "traces.sqlite" in message


def test_pytest_helper_failure_message_includes_matrix_variant(tmp_path):
    suite_path = tmp_path / "matrix.yaml"
    suite_path.write_text(
        """
name: matrix
type: output
matrix:
  models:
    - openai:gpt-4.1-mini
    - openrouter:openai/gpt-4o-mini
cases:
  - name: expected output
    input: hello
    checks:
      - contains: shipped
""",
        encoding="utf-8",
    )

    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
        trace_db_path=tmp_path / "traces.sqlite",
    )

    with pytest.raises(AssertionError) as error:
        assert_eval_suite_passes(
            agent,
            suite_path,
            trace_db_path=tmp_path / "traces.sqlite",
        )

    message = str(error.value)
    assert "variant={'model': 'openai:gpt-4.1-mini'}" in message

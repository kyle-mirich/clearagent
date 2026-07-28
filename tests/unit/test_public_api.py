from importlib import reload
from importlib.metadata import PackageNotFoundError
from importlib.resources import files

import clearagent
from pydantic import ValidationError
import pytest
from clearagent.providers import (
    FakeProvider,
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ResponseFormat,
    ToolCall,
    Usage,
)
from clearagent.providers import base
from clearagent.storage import (
    EvalCaseResultRecord,
    ModelCallRecord,
    ToolCallRecord,
    TraceRun,
    TraceTurn,
)
from clearagent.types import RunResult


def test_root_public_api_stays_small_and_versioned():
    assert clearagent.__all__ == ["__version__", "create_agent", "tool"]
    assert clearagent.__version__


def test_root_version_falls_back_outside_an_installed_distribution(monkeypatch):
    def missing_distribution(_: str) -> str:
        raise PackageNotFoundError

    with monkeypatch.context() as context:
        context.setattr("importlib.metadata.version", missing_distribution)
        reload(clearagent)
        assert clearagent.__version__ == "0+unknown"

    reload(clearagent)
    assert clearagent.__version__ != "0+unknown"


def test_provider_models_have_canonical_package_imports():
    assert FakeProvider is base.FakeProvider
    assert Provider is base.Provider
    assert ProviderError is base.ProviderError
    assert ProviderRequest is base.ProviderRequest
    assert ProviderResponse is base.ProviderResponse
    assert ResponseFormat is base.ResponseFormat
    assert ToolCall is base.ToolCall
    assert Usage is base.Usage


def test_run_result_validates_usage_and_tool_call_shapes():
    result = RunResult(
        output="done",
        run_id="run_1",
        trace_db_path=None,
        trace_store=object(),
        tool_calls=[{"name": "lookup_order"}],
        usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        latency_ms=10,
    )

    assert result.usage == Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    assert result.tool_calls == [{"name": "lookup_order"}]
    assert "trace_store" not in result.model_dump()


@pytest.mark.parametrize(
    ("tool_calls", "usage"),
    [
        ([{"name": "lookup_order", "id": "call_1", "latency_ms": 7}], None),
        ([], {"input_tokens": 2, "cached_tokens": 1}),
    ],
)
def test_run_result_rejects_unknown_fields_instead_of_discarding_them(tool_calls, usage):
    with pytest.raises(ValidationError):
        RunResult(
            output="done",
            run_id=None,
            trace_db_path=None,
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=10,
        )


def test_run_result_accepts_usage_cost_alias():
    result = RunResult(
        output="done",
        run_id=None,
        trace_db_path=None,
        tool_calls=[],
        usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5, "cost": 0.1},
        latency_ms=10,
    )

    assert result.usage == Usage(
        prompt_tokens=2,
        completion_tokens=3,
        total_tokens=5,
        cost_usd=0.1,
    )


def test_run_result_schema_excludes_in_process_trace_store():
    schema = RunResult.model_json_schema()

    assert "trace_store" not in schema["properties"]


def test_package_declares_typing_support():
    assert files("clearagent").joinpath("py.typed").is_file()


def test_trace_store_read_records_publish_required_keys():
    assert TraceRun.__required_keys__ == {
        "id",
        "agent_name",
        "graph_name",
        "root_input",
        "final_output",
        "status",
        "started_at",
        "ended_at",
        "total_latency_ms",
        "total_prompt_tokens",
        "total_completion_tokens",
        "total_cost_usd",
        "metadata_json",
    }
    assert {"input_messages_json", "output_messages_json", "error_json"} <= (
        TraceTurn.__required_keys__
    )
    assert {"request_json", "response_json", "usage_json", "error_json"} <= (
        ModelCallRecord.__required_keys__
    )
    assert {"args_json", "result_json", "error_json"} <= ToolCallRecord.__required_keys__
    assert {"checks_json", "variant_json", "failure_json", "passed"} <= (
        EvalCaseResultRecord.__required_keys__
    )

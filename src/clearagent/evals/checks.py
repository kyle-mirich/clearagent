import json
import re
from typing import Any

from jsonschema import SchemaError, ValidationError, validate
from pydantic import BaseModel

from clearagent.types import RunResult
from clearagent.storage.sqlite import SQLiteTraceStore


class CheckResult(BaseModel):
    name: str
    passed: bool
    message: str
    expected: Any = None
    actual: Any = None


def run_checks(checks: list[dict[str, Any]], result: RunResult) -> list[CheckResult]:
    results = []
    for check in checks:
        try:
            results.append(_run_check(check, result))
        except (TypeError, ValueError, ValidationError) as exc:
            name = next(iter(check), "invalid") if isinstance(check, dict) else "invalid"
            results.append(
                CheckResult(
                    name=str(name),
                    passed=False,
                    message=f"Invalid {name!r} check: {exc}",
                    expected=check,
                )
            )
    return results


def _run_check(check: dict[str, Any], result: RunResult) -> CheckResult:
    if not isinstance(check, dict):
        raise TypeError("check must be a mapping")
    if len(check) != 1:
        return CheckResult(name="invalid", passed=False, message="Check must have one key.")
    name, expected = next(iter(check.items()))
    output = result.output or ""
    tools = [call.get("name") for call in result.tool_calls]

    if name == "contains":
        if not isinstance(expected, str):
            raise TypeError("contains must be a string")
        return _check(name, expected in output, f"contains {expected!r}", expected, output)
    if name == "contains_any":
        invalid = _invalid_list_check(name, expected)
        if invalid:
            return invalid
        return _check(name, any(item in output for item in expected), f"contains any of {expected!r}", expected, output)
    if name == "not_contains":
        if not isinstance(expected, str):
            raise TypeError("not_contains must be a string")
        return _check(name, expected not in output, f"does not contain {expected!r}", expected, output)
    if name == "regex":
        if not isinstance(expected, str):
            raise TypeError("regex must be a string")
        return _regex_check(expected, output)
    if name == "equals":
        return _check(name, output == expected, "equals expected output", expected, output)
    if name == "json_schema":
        if not isinstance(expected, dict):
            raise TypeError("json_schema must be a mapping")
        if result.structured_output is not None:
            try:
                validate(result.structured_output, expected)
            except (SchemaError, ValidationError) as exc:
                return CheckResult(
                    name=name,
                    passed=False,
                    message=str(exc),
                    expected=expected,
                    actual=result.structured_output,
                )
            return CheckResult(
                name=name,
                passed=True,
                message="json_schema passed",
                expected=expected,
                actual=result.structured_output,
            )
        try:
            validate(json.loads(output), expected)
        except (json.JSONDecodeError, SchemaError, ValidationError) as exc:
            return CheckResult(name=name, passed=False, message=str(exc), expected=expected, actual=output)
        return CheckResult(name=name, passed=True, message="json_schema passed", expected=expected, actual=output)
    if name == "refuses":
        if not isinstance(expected, bool):
            raise TypeError("refuses must be a boolean")
        refusal = any(token in output.lower() for token in ["cannot", "can't", "unable", "sorry"])
        return _check(name, refusal is expected, "refusal-like output", expected, output)
    if name == "expected_tools":
        invalid = _invalid_list_check(name, expected)
        if invalid:
            return invalid
        missing = [tool for tool in expected if tool not in tools]
        return _check(name, not missing, f"expected tools {expected!r}", expected, tools)
    if name == "forbidden_tools":
        invalid = _invalid_list_check(name, expected)
        if invalid:
            return invalid
        forbidden = [tool for tool in expected if tool in tools]
        return _check(name, not forbidden, f"forbidden tools {expected!r}", expected, tools)
    if name == "latency_under_ms":
        return _check(name, result.latency_ms < int(expected), f"latency under {expected}ms", expected, result.latency_ms)
    if name == "cost_under":
        if result.cost_usd is None:
            return CheckResult(
                name=name,
                passed=False,
                message="cost is unavailable from this provider response",
                expected=expected,
                actual=None,
            )
        return _check(name, result.cost_usd < float(expected), f"cost under {expected}", expected, result.cost_usd)
    if name == "structured_output":
        if not isinstance(expected, bool):
            raise TypeError("structured_output must be a boolean")
        actual = result.structured_output
        if actual is None:
            try:
                actual = json.loads(output)
            except json.JSONDecodeError:
                actual = None
        return _check(name, (actual is not None) is expected, "structured output parsed", expected, actual)
    if name == "trace_provider":
        model_calls = _model_calls(result)
        providers = [call["provider"] for call in model_calls]
        return _check(name, expected in providers, f"trace provider {expected!r}", expected, providers)
    if name == "max_turns":
        turns = _turns(result)
        return _check(name, len(turns) <= int(expected), f"at most {expected} turns", expected, len(turns))
    if name == "called_tool":
        traced_tools = [call["tool_name"] for call in _tool_calls(result)]
        return _check(name, expected in traced_tools, f"called tool {expected!r}", expected, traced_tools)
    if name == "not_called_tool":
        traced_tools = [call["tool_name"] for call in _tool_calls(result)]
        return _check(name, expected not in traced_tools, f"did not call tool {expected!r}", expected, traced_tools)
    return CheckResult(name=name, passed=False, message=f"Unknown check {name!r}.")


def _check(name: str, passed: bool, message: str, expected: Any, actual: Any) -> CheckResult:
    return CheckResult(name=name, passed=passed, message=message, expected=expected, actual=actual)


def _invalid_list_check(name: str, expected: Any) -> CheckResult | None:
    if isinstance(expected, list):
        return None
    return CheckResult(
        name=name,
        passed=False,
        message=f"{name} must be a list.",
        expected=expected,
        actual=None,
    )


def _regex_check(pattern: str, output: str) -> CheckResult:
    try:
        matched = re.search(pattern, output) is not None
    except re.error as exc:
        return CheckResult(
            name="regex",
            passed=False,
            message=f"invalid regex: {exc}",
            expected=pattern,
            actual=output,
        )
    return _check("regex", matched, f"matches regex {pattern!r}", pattern, output)


def _trace_store(result: RunResult) -> SQLiteTraceStore | None:
    if not result.run_id or not result.trace_db_path:
        raise ValueError("trace data is unavailable")
    store = SQLiteTraceStore(result.trace_db_path)
    if store.get_run(result.run_id) is None:
        raise ValueError(f"trace run {result.run_id!r} was not found")
    return store


def _turns(result: RunResult) -> list[dict[str, Any]]:
    store = _trace_store(result)
    return store.get_turns(result.run_id) if store and result.run_id else []


def _model_calls(result: RunResult) -> list[dict[str, Any]]:
    store = _trace_store(result)
    return store.list_model_calls(result.run_id) if store and result.run_id else []


def _tool_calls(result: RunResult) -> list[dict[str, Any]]:
    store = _trace_store(result)
    return store.list_tool_calls(result.run_id) if store and result.run_id else []

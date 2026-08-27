import json
import re
from typing import Any

from jsonschema import ValidationError, validate
from pydantic import BaseModel

from clearagent.runtime.types import RunResult


class CheckResult(BaseModel):
    name: str
    passed: bool
    message: str
    expected: Any = None
    actual: Any = None


def run_checks(checks: list[dict[str, Any]], result: RunResult) -> list[CheckResult]:
    return [_run_check(check, result) for check in checks]


def _run_check(check: dict[str, Any], result: RunResult) -> CheckResult:
    if len(check) != 1:
        return CheckResult(name="invalid", passed=False, message="Check must have one key.")
    name, expected = next(iter(check.items()))
    output = result.output or ""
    tools = [call.get("name") for call in result.tool_calls]

    if name == "contains":
        invalid = _invalid_string_check(name, expected)
        if invalid:
            return invalid
        return _check(name, expected in output, f"contains {expected!r}", expected, output)
    if name == "contains_any":
        invalid = _invalid_list_check(name, expected)
        if invalid:
            return invalid
        return _check(name, any(item in output for item in expected), f"contains any of {expected!r}", expected, output)
    if name == "not_contains":
        invalid = _invalid_string_check(name, expected)
        if invalid:
            return invalid
        return _check(name, expected not in output, f"does not contain {expected!r}", expected, output)
    if name == "regex":
        return _regex_check(str(expected), output)
    if name == "equals":
        return _check(name, output == expected, "equals expected output", expected, output)
    if name == "json_schema":
        if result.structured_output is not None:
            try:
                validate(result.structured_output, expected)
            except ValidationError as exc:
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
        except (json.JSONDecodeError, ValidationError) as exc:
            return CheckResult(name=name, passed=False, message=str(exc), expected=expected, actual=output)
        return CheckResult(name=name, passed=True, message="json_schema passed", expected=expected, actual=output)
    if name == "refuses":
        refusal = any(token in output.lower() for token in ["cannot", "can't", "unable", "sorry"])
        return _check(name, refusal is bool(expected), "refusal-like output", expected, output)
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
        return _check(name, result.cost_usd < float(expected), f"cost under {expected}", expected, result.cost_usd)
    if name == "structured_output":
        actual = result.structured_output
        if actual is None:
            try:
                actual = json.loads(output)
            except json.JSONDecodeError:
                actual = None
        return _check(name, (actual is not None) is bool(expected), "structured output parsed", expected, actual)
    return CheckResult(name=name, passed=False, message=f"Unknown check {name!r}.")


def _check(name: str, passed: bool, message: str, expected: Any, actual: Any) -> CheckResult:
    return CheckResult(name=name, passed=passed, message=message, expected=expected, actual=actual)


def _invalid_list_check(name: str, expected: Any) -> CheckResult | None:
    if isinstance(expected, list) and all(isinstance(item, str) for item in expected):
        return None
    return CheckResult(
        name=name,
        passed=False,
        message=f"{name} must be a list of strings.",
        expected=expected,
        actual=None,
    )


def _invalid_string_check(name: str, expected: Any) -> CheckResult | None:
    if isinstance(expected, str):
        return None
    return CheckResult(
        name=name,
        passed=False,
        message=f"{name} must be a string.",
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


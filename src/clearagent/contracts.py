from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from clearagent.tool import tool_name, validate_tool_arguments


class ToolContractCase(BaseModel):
    name: str
    arguments: dict[str, Any]
    expected: Any = None


class ToolContractResult(BaseModel):
    name: str
    passed: bool
    output: Any = None
    error: str | None = None


def validate_tool_contract(fn: Callable[..., Any], case: ToolContractCase) -> ToolContractResult:
    """Run one deterministic tool contract case without invoking a model."""
    try:
        arguments = validate_tool_arguments(fn, case.arguments)
        output = fn(**arguments)
    except Exception as exc:
        return ToolContractResult(
            name=case.name,
            passed=False,
            error=f"{exc.__class__.__name__}: {exc}",
        )
    if "expected" in case.model_fields_set and output != case.expected:
        return ToolContractResult(
            name=case.name,
            passed=False,
            output=output,
            error=f"Expected {case.expected!r} from tool {tool_name(fn)}, got {output!r}.",
        )
    return ToolContractResult(name=case.name, passed=True, output=output)


def tool_contract_cases(*cases: ToolContractCase) -> list[ToolContractCase]:
    """Return contract cases after rejecting duplicate case names."""
    seen: set[str] = set()
    for case in cases:
        if case.name in seen:
            raise ValueError(f"Duplicate tool contract case {case.name!r}.")
        seen.add(case.name)
    return list(cases)

import pytest

from clearagent.contracts import ToolContractCase, tool_contract_cases, validate_tool_contract


def test_explicit_null_expectation_rejects_non_null_tool_output():
    def returns_text() -> str:
        return "unexpected"

    result = validate_tool_contract(
        returns_text,
        ToolContractCase(name="returns null", arguments={}, expected=None),
    )

    assert result.passed is False
    assert result.output == "unexpected"
    assert result.error == "Expected None from tool returns_text, got 'unexpected'."


def test_omitted_expectation_allows_any_successful_tool_output():
    def returns_text() -> str:
        return "unchecked"

    result = validate_tool_contract(
        returns_text,
        ToolContractCase(name="successful call", arguments={}),
    )

    assert result.passed is True
    assert result.output == "unchecked"
    assert result.error is None


def test_tool_exception_is_returned_as_a_failed_contract_result():
    def raises_error(value: str) -> str:
        raise RuntimeError(f"cannot handle {value}")

    result = validate_tool_contract(
        raises_error,
        ToolContractCase(name="tool error", arguments={"value": "bad"}),
    )

    assert result.passed is False
    assert result.output is None
    assert result.error == "RuntimeError: cannot handle bad"


def test_unique_contract_cases_preserve_caller_order_and_identity():
    first = ToolContractCase(name="first", arguments={})
    second = ToolContractCase(name="second", arguments={})

    cases = tool_contract_cases(first, second)

    assert cases == [first, second]
    assert cases[0] is first
    assert cases[1] is second


def test_duplicate_contract_case_is_rejected_after_unique_prefix():
    first = ToolContractCase(name="first", arguments={})

    with pytest.raises(ValueError, match="Duplicate tool contract case 'first'"):
        tool_contract_cases(first, ToolContractCase(name="second", arguments={}), first)

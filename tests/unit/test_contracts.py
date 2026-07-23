import pytest

from clearagent.contracts import ToolContractCase, tool_contract_cases, validate_tool_contract
from clearagent.tool import tool


@tool
def lookup_order(order_id: str, include_history: bool = False) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "include_history": include_history}


def test_validate_tool_contract_accepts_valid_case():
    case = ToolContractCase(
        name="lookup basic order",
        arguments={"order_id": "A123"},
        expected={"order_id": "A123", "include_history": False},
    )

    result = validate_tool_contract(lookup_order, case)

    assert result.passed is True
    assert result.output == {"order_id": "A123", "include_history": False}


def test_validate_tool_contract_rejects_invalid_arguments():
    case = ToolContractCase(
        name="unknown argument",
        arguments={"order_id": "A123", "extra": True},
    )

    result = validate_tool_contract(lookup_order, case)

    assert result.passed is False
    assert "Invalid arguments" in result.error


def test_tool_contract_cases_require_unique_names():
    with pytest.raises(ValueError, match="Duplicate tool contract case"):
        tool_contract_cases(
            ToolContractCase(name="same", arguments={"order_id": "A123"}),
            ToolContractCase(name="same", arguments={"order_id": "B456"}),
        )

from enum import Enum
from typing import Literal

import pytest

from clearagent.agent import Agent
from clearagent.runtime.providers.base import FakeProvider, ProviderResponse, ToolCall
from clearagent.runtime.tools import tool_schema, validate_tool_arguments


class Priority(Enum):
    LOW = "low"
    HIGH = "high"


def assign_ticket(
    ticket_id: str,
    priority: Priority,
    labels: list[str],
    category: Literal["billing", "shipping"] = "billing",
    notify: bool | None = None,
) -> dict:
    """Assign a support ticket."""
    return {
        "ticket_id": ticket_id,
        "priority": priority.value,
        "labels": labels,
        "category": category,
        "notify": notify,
    }


def test_tool_schema_supports_enums_literals_arrays_defaults_and_optional_values():
    schema = tool_schema(assign_ticket)["function"]["parameters"]

    assert schema["properties"]["priority"] == {
        "type": "string",
        "enum": ["low", "high"],
    }
    assert schema["properties"]["labels"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert schema["properties"]["category"] == {
        "type": "string",
        "enum": ["billing", "shipping"],
        "default": "billing",
    }
    assert schema["properties"]["notify"] == {
        "anyOf": [{"type": "boolean"}, {"type": "null"}],
        "default": None,
    }
    assert schema["required"] == ["ticket_id", "priority", "labels"]


def test_validate_tool_arguments_coerces_valid_arguments_and_rejects_invalid_values():
    validated = validate_tool_arguments(
        assign_ticket,
        {
            "ticket_id": "T123",
            "priority": "high",
            "labels": ["vip"],
            "category": "shipping",
        },
    )

    assert validated == {
        "ticket_id": "T123",
        "priority": Priority.HIGH,
        "labels": ["vip"],
        "category": "shipping",
        "notify": None,
    }

    with pytest.raises(ValueError, match="Invalid arguments for tool assign_ticket"):
        validate_tool_arguments(
            assign_ticket,
            {"ticket_id": "T123", "priority": "urgent", "labels": ["vip"]},
        )


def test_agent_validates_provider_tool_arguments_before_executing_tool(tmp_path):
    def typed_tool(count: int) -> str:
        return f"count={count}"

    agent = Agent(
        name="agent",
        model="fake:model",
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(id="call_1", name="typed_tool", arguments={"count": "not-int"})
                )
            ]
        ),
        tools=[typed_tool],
        trace_db_path=tmp_path / "traces.sqlite",
    )

    with pytest.raises(ValueError, match="Invalid arguments for tool typed_tool"):
        agent.run("run typed tool")

from enum import Enum
from typing import Any, Literal

from clearagent.tool import tool_name, tool_schema


class RetryCode(Enum):
    NEVER = 0
    RETRY = 1


class FeatureFlag(Enum):
    OFF = False
    ON = True


def provider_contract_tool(
    unconstrained: Any,
    integer_choice: Literal[1, 2],
    float_choice: Literal[0.25, 0.5],
    boolean_choice: Literal[True, False],
    mixed_choice: Literal["automatic", 3],
    coordinates: tuple[float, ...],
    attributes: dict[str, int],
    ratio: float,
    enabled: bool,
    raw_mapping: dict,
    raw_items: list,
    retry: RetryCode = RetryCode.NEVER,
    feature: FeatureFlag = FeatureFlag.OFF,
) -> str:
    """Exercise every supported provider-facing schema shape."""
    return str(unconstrained)


def test_tool_schema_preserves_supported_provider_wire_types_and_enum_defaults():
    schema = tool_schema(provider_contract_tool)["function"]

    assert schema["description"] == "Exercise every supported provider-facing schema shape."
    assert schema["parameters"]["properties"] == {
        "unconstrained": {"type": "string"},
        "integer_choice": {"enum": [1, 2], "type": "integer"},
        "float_choice": {"enum": [0.25, 0.5], "type": "number"},
        "boolean_choice": {"enum": [True, False], "type": "boolean"},
        "mixed_choice": {"enum": ["automatic", 3]},
        "coordinates": {"type": "array", "items": {"type": "number"}},
        "attributes": {"type": "object"},
        "ratio": {"type": "number"},
        "enabled": {"type": "boolean"},
        "raw_mapping": {"type": "object"},
        "raw_items": {"type": "array"},
        "retry": {"enum": [0, 1], "type": "integer", "default": 0},
        "feature": {"enum": [False, True], "default": False},
    }
    assert schema["parameters"]["required"] == [
        "unconstrained",
        "integer_choice",
        "float_choice",
        "boolean_choice",
        "mixed_choice",
        "coordinates",
        "attributes",
        "ratio",
        "enabled",
        "raw_mapping",
        "raw_items",
    ]


def test_tool_name_decorates_plain_callable_with_its_public_function_name():
    def undecorated(value: str) -> str:
        return value

    assert tool_name(undecorated) == "undecorated"
    assert tool_schema(undecorated)["function"]["name"] == "undecorated"

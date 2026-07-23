import inspect
from collections.abc import Callable
from enum import Enum
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import ConfigDict, ValidationError, create_model


def _json_schema(annotation: Any) -> dict[str, Any]:
    if annotation in (Any, inspect._empty):
        return {"type": "string"}
    if annotation is None or annotation is type(None):
        return {"type": "null"}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        values = list(args)
        schema: dict[str, Any] = {"enum": values}
        value_types = {type(value) for value in values}
        if value_types == {str}:
            schema["type"] = "string"
        elif value_types == {int}:
            schema["type"] = "integer"
        elif value_types == {float}:
            schema["type"] = "number"
        elif value_types == {bool}:
            schema["type"] = "boolean"
        return schema

    if origin in (Union, UnionType):
        return {"anyOf": [_json_schema(arg) for arg in args]}

    if origin in (list, tuple):
        item_annotation = args[0] if args else Any
        return {"type": "array", "items": _json_schema(item_annotation)}

    if origin is dict:
        return {"type": "object"}

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        values = [member.value for member in annotation]
        schema = {"enum": values}
        if all(isinstance(value, str) for value in values):
            schema["type"] = "string"
        elif all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            schema["type"] = "integer"
        return schema

    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is dict:
        return {"type": "object"}
    if annotation is list:
        return {"type": "array"}
    return {"type": "string"}


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate a callable with the schema ClearAgent sends to model providers."""
    signature = inspect.signature(fn)
    hints = get_type_hints(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        properties[name] = _json_schema(hints.get(name, str))
        if parameter.default is inspect._empty:
            required.append(name)
        else:
            properties[name]["default"] = _json_default(parameter.default)

    fn._clearagent_tool_definition = {  # type: ignore[attr-defined]
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": inspect.getdoc(fn) or "",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
    return fn


def tool_name(fn: Callable[..., Any]) -> str:
    """Return a callable's provider-facing tool name."""
    if not hasattr(fn, "_clearagent_tool_definition"):
        fn = tool(fn)
    return fn._clearagent_tool_definition["function"]["name"]  # type: ignore[attr-defined]


def tool_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """Return the OpenAI-compatible function schema for a callable."""
    if not hasattr(fn, "_clearagent_tool_definition"):
        fn = tool(fn)
    return fn._clearagent_tool_definition  # type: ignore[attr-defined]


def validate_tool_arguments(fn: Callable[..., Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce model-supplied arguments against a callable's type hints."""
    signature = inspect.signature(fn)
    hints = get_type_hints(fn)
    fields: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        annotation = hints.get(name, Any)
        default = ... if parameter.default is inspect._empty else parameter.default
        fields[name] = (annotation, default)
    model = create_model(
        f"{fn.__name__}ToolArguments",
        __config__=ConfigDict(extra="forbid", use_enum_values=False),
        **fields,
    )
    try:
        validated = model.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(f"Invalid arguments for tool {tool_name(fn)}: {exc}") from exc
    return {name: getattr(validated, name) for name in signature.parameters}

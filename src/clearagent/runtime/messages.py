from typing import Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_provider_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        if tool_calls := self.metadata.get("tool_calls"):
            data["tool_calls"] = tool_calls
        return data


def normalize_messages(system_prompt: str | None, user_input: str | list[Message]) -> list[Message]:
    messages: list[Message] = []
    if system_prompt:
        messages.append(Message(role="system", content=system_prompt))
    if isinstance(user_input, str):
        messages.append(Message(role="user", content=user_input))
    else:
        messages.extend(user_input)
    return messages


def dump_messages(messages: list[Message]) -> list[dict[str, Any]]:
    return [message.to_provider_dict() for message in messages]

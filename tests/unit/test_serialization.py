from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from clearagent.serialization import json_safe, stringify


class Priority(Enum):
    HIGH = "high"


class Payload(BaseModel):
    name: str


@dataclass
class Result:
    path: Path
    labels: set[str]


class Printable:
    def __str__(self) -> str:
        return "printable"


class ReverseIterationSet(set):
    def __iter__(self):
        return iter(["zulu", "alpha"])


def test_json_safe_normalizes_supported_and_fallback_values():
    value = {
        "model": Payload(name="demo"),
        "result": Result(path=Path("report.json"), labels={"safe"}),
        "priority": Priority.HIGH,
        "tuple": (1, 2),
        "fallback": Printable(),
    }

    normalized = json_safe(value)

    assert normalized["model"] == {"name": "demo"}
    assert normalized["result"] == {"path": "report.json", "labels": ["safe"]}
    assert normalized["priority"] == "high"
    assert normalized["tuple"] == [1, 2]
    assert normalized["fallback"] == "printable"


def test_stringify_preserves_strings_and_compacts_other_values():
    assert stringify("already text") == "already text"
    assert stringify({"ok": True}) == '{"ok":true}'


def test_json_safe_canonicalizes_set_values_instead_of_preserving_hash_order():
    value = {"labels": ReverseIterationSet({"alpha", "zulu"})}

    assert json_safe(value) == {"labels": ["alpha", "zulu"]}
    assert stringify(value) == '{"labels":["alpha","zulu"]}'

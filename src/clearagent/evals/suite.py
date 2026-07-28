from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    """One input and its deterministic output or trace checks."""

    name: str
    input: str
    expected: str | None = None
    reference_notes: str | None = None
    split: str | None = None
    tags: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(min_length=1)


class EvalSuite(BaseModel):
    """A named collection of eval cases with optional defaults and matrix variants."""

    name: str
    type: str = "output"
    description: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    matrix: dict[str, list[Any]] = Field(default_factory=dict)
    cases: list[EvalCase] = Field(min_length=1)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EvalSuite":
        """Load and validate an eval suite from a YAML file."""
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid eval suite YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Eval suite YAML must contain a mapping.")
        _require_string(data, "name", "Eval suite")
        if not isinstance(data.get("cases"), list):
            raise ValueError("Eval suite field 'cases' must be a list.")
        if not data["cases"]:
            raise ValueError("Eval suite field 'cases' must contain at least one case.")
        defaults = _optional_mapping(data, "defaults", "Eval suite")
        default_tags = defaults.get("tags") or []
        matrix = _optional_mapping(data, "matrix", "Eval suite")
        _require_optional_list(matrix, "models", "Eval suite matrix")
        _require_optional_list(matrix, "temperatures", "Eval suite matrix")
        cases = []
        seen_case_names: set[str] = set()
        for index, raw_case in enumerate(data["cases"]):
            if not isinstance(raw_case, dict):
                raise ValueError("Each eval case must be a mapping.")
            merged = dict(raw_case)
            case_label = str(merged.get("name") or f"#{index + 1}")
            _require_string(merged, "name", f"Eval case {case_label!r}")
            _require_string(merged, "input", f"Eval case {case_label!r}")
            if merged["name"] in seen_case_names:
                raise ValueError(f"Duplicate eval case name {merged['name']!r}.")
            seen_case_names.add(merged["name"])
            if not isinstance(merged.get("checks"), list):
                raise ValueError(f"Eval case {case_label!r} field 'checks' must be a list.")
            if not merged["checks"]:
                raise ValueError(
                    f"Eval case {case_label!r} field 'checks' must contain at least one check."
                )
            if "tags" not in merged and default_tags:
                merged["tags"] = default_tags
            cases.append(EvalCase(**merged))
        return cls(
            name=data["name"],
            type=data.get("type", "output"),
            description=data.get("description"),
            defaults=defaults,
            matrix=matrix,
            cases=cases,
        )


def _require_string(data: dict[str, Any], field: str, label: str) -> None:
    if not isinstance(data.get(field), str) or not data[field]:
        raise ValueError(f"{label} field {field!r} is required.")


def _optional_mapping(data: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    value = data.get(field)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} field {field!r} must be a mapping.")
    return value


def _require_optional_list(data: dict[str, Any], field: str, label: str) -> None:
    if field in data and not isinstance(data[field], list):
        raise ValueError(f"{label} field {field!r} must be a list.")


def require_runnable_suite(suite: EvalSuite) -> None:
    """Reject vacuous mutable suite state before any provider work begins."""
    if not suite.cases:
        raise ValueError("Eval suite must contain at least one case.")
    for case in suite.cases:
        if not case.checks:
            raise ValueError(f"Eval case {case.name!r} must contain at least one check.")

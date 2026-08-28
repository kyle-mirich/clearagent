import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BudgetProfile = Literal["quick", "standard", "deep"]
FeedbackKind = Literal["positive", "negative", "correction"]
PlanningMode = Literal["auto", "approval"]
AgentShape = Literal["chat", "structured", "tools"]
BoundedAnswer = Annotated[str, Field(max_length=2_000)]
MAX_SCHEMA_PAYLOAD_BYTES = 16_000


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentKnowledge(RequestModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["website", "docs", "dataset"]
    label: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20_000)


class PlanningSource(RequestModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["website", "docs", "dataset"]
    label: str = Field(min_length=1, max_length=500)
    detail: str = Field(default="", max_length=20_000)
    preview: str | None = Field(default=None, max_length=20_000)


class PlanningRequest(RequestModel):
    goal: str = Field(min_length=20, max_length=20_000)
    answers: dict[str, BoundedAnswer] = Field(default_factory=dict, max_length=10)
    sources: list[PlanningSource] = Field(default_factory=list, max_length=20)
    mode: PlanningMode = "auto"
    agent_shape: AgentShape = "chat"
    output_schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=12)

    @field_validator("output_schema")
    @classmethod
    def bound_output_schema_payload(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and _json_size(value) > MAX_SCHEMA_PAYLOAD_BYTES:
            raise ValueError("output schema exceeds the hosted payload limit")
        return value

    @field_validator("tools")
    @classmethod
    def bound_tools_payload(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for tool in value:
            if _json_size(tool) > MAX_SCHEMA_PAYLOAD_BYTES:
                raise ValueError("tool definition exceeds the hosted payload limit")
        return value

    @model_validator(mode="after")
    def require_readable_supplied_knowledge(self) -> "PlanningRequest":
        if any(not (source.preview or source.detail).strip() for source in self.sources):
            raise ValueError("uploaded knowledge sources must contain readable text")
        return self


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class ProjectRecord(BaseModel):
    id: str
    owner_id: str
    name: str
    goal: str
    status: str
    settings: dict[str, Any]
    promoted_agent_version_id: str | None = None
    created_at: str
    updated_at: str


class SavedAgentConfig(BaseModel):
    schema_version: int = 1
    project_id: str
    agent_version_id: str
    source_run_id: str
    version_number: int
    name: str
    description: str
    prompt: str
    knowledge: list[AgentKnowledge] = Field(default_factory=list)
    module_shape: AgentShape = "chat"
    output_schema: dict[str, Any]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    agent_prd: dict[str, Any]
    validation_metrics: dict[str, Any]
    holdout_metrics: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class SavedAgentSummary(BaseModel):
    project_id: str
    name: str
    description: str
    ready: bool
    agent_version_id: str | None = None
    knowledge_count: int = 0
    updated_at: str


class RunRecord(BaseModel):
    id: str
    project_id: str
    owner_id: str
    status: str
    stage: str
    progress: float
    budget_profile: BudgetProfile
    seed: int
    dataset_size: int | None = None
    run_config: dict[str, Any] = Field(default_factory=dict)
    task_spec: dict[str, Any] | None = None
    dataset: dict[str, Any] | None = None
    best_agent_version_id: str | None = None
    baseline_validation_score: float | None = None
    best_validation_score: float | None = None
    baseline_test_score: float | None = None
    optimized_test_score: float | None = None
    promotion_decision: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


class EventRecord(BaseModel):
    sequence: int
    type: str
    stage: str
    message: str
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FeedbackRecord(BaseModel):
    id: str
    project_id: str
    version_id: str
    kind: FeedbackKind
    input: str
    feedback: str
    corrected_output: dict[str, Any] | None = None
    created_at: str

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BudgetProfile = Literal["quick", "standard", "deep"]
BuildLevel = Literal["low", "medium", "high"]
FeedbackKind = Literal["positive", "negative", "correction"]
PlanningMode = Literal["auto", "approval"]
AgentShape = Literal["chat", "structured", "tools"]
BoundedAnswer = Annotated[str, Field(max_length=2_000)]
MAX_PROJECT_SETTINGS_BYTES = 64_000
MAX_PLAYGROUND_INPUT_BYTES = 32_000
MAX_SCHEMA_PAYLOAD_BYTES = 16_000


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(RequestModel):
    name: str | None = Field(default=None, max_length=200)
    goal: str = Field(min_length=20, max_length=20_000)
    settings: dict[str, Any] = Field(default_factory=dict, max_length=30)

    @field_validator("settings")
    @classmethod
    def bound_settings_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _json_size(value) > MAX_PROJECT_SETTINGS_BYTES:
            raise ValueError("project settings exceed the hosted payload limit")
        return value


class ProjectFromPlanCreate(RequestModel):
    name: str | None = Field(default=None, max_length=200)
    planning: dict[str, Any]
    approved: bool = False


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


class RunCreate(RequestModel):
    level: BuildLevel = "medium"
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    n: int | None = Field(default=None, ge=5, le=500)


class PlaygroundRunCreate(RequestModel):
    input: dict[str, Any] = Field(max_length=50)
    run_judge: bool = False

    @field_validator("input")
    @classmethod
    def bound_input_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _json_size(value) > MAX_PLAYGROUND_INPUT_BYTES:
            raise ValueError("playground input exceeds the hosted payload limit")
        return value


class FeedbackCreate(RequestModel):
    version_id: str | None = Field(default=None, min_length=1, max_length=200)
    kind: FeedbackKind
    input: str = Field(min_length=1, max_length=4_000)
    feedback: str = Field(min_length=1, max_length=4_000)
    corrected_output: dict[str, Any] | None = None


class AgentCitation(BaseModel):
    source_id: str
    source_type: Literal["website", "docs", "dataset"]
    label: str
    quote: str
    snippet: str | None = None
    score: float | None = None
    url: str | None = None
    chunk_id: str | None = None
    chunk_index: int | None = None
    start_char: int | None = None
    end_char: int | None = None


class AgentRetrievalResult(BaseModel):
    source_id: str
    source_type: Literal["website", "docs", "dataset"]
    label: str
    snippet: str
    score: float
    url: str | None = None
    chunk_id: str | None = None
    chunk_index: int | None = None
    start_char: int | None = None
    end_char: int | None = None


class AgentTraceStep(BaseModel):
    action: Literal["knowledge_search", "source_read", "answer_compose"]
    query: str
    source_ids: list[str] = Field(default_factory=list)
    summary: str
    results: list[AgentRetrievalResult] = Field(default_factory=list)


class AgentJudgeResult(BaseModel):
    id: str
    name: str
    score: float = Field(ge=0, le=1)
    verdict: Literal["pass", "watch", "fail"]
    rationale: str
    trace_action: str | None = None


class StructuredAgentOutput(BaseModel):
    answer: str = Field(max_length=16_000)
    citations: list[AgentCitation] = Field(default_factory=list)
    trace: list[AgentTraceStep] = Field(default_factory=list)
    judges: list[AgentJudgeResult] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class WebsiteScrapeCreate(RequestModel):
    url: str = Field(min_length=4, max_length=2048)


class ChatMessage(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class ChatSource(RequestModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["website", "docs", "dataset"]
    label: str = Field(min_length=1, max_length=500)
    detail: str = Field(default="", max_length=2_000)
    preview: str | None = Field(default=None, max_length=12_000)


class ChatStreamCreate(RequestModel):
    message: str = Field(min_length=1, max_length=4_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    sources: list[ChatSource] = Field(default_factory=list, max_length=20)
    agent_title: str | None = Field(default=None, max_length=200)
    agent_description: str | None = Field(default=None, max_length=1000)
    agent_instruction: str | None = Field(default=None, max_length=18_000)
    showcase: bool = False
    run_judges: bool = True


class SavedAgentChatCreate(RequestModel):
    message: str = Field(min_length=1, max_length=4_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    sources: list[ChatSource] = Field(default_factory=list, max_length=20)
    run_judges: bool = True


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

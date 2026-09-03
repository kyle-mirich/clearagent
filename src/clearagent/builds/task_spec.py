import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from clearagent.models import AgentKnowledge


PRDStatement = Annotated[str, Field(min_length=1, max_length=500)]


class RubricDimension(BaseModel):
    id: str
    description: str
    weight: float = Field(ge=0, le=1)


class PRDDocument(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=500)
    source_type: Literal["website", "docs", "dataset"]
    summary: str = Field(min_length=1, max_length=500)


class AgentPRD(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=200,
        description="Short product-facing agent name.",
    )
    desired_outcome: str = Field(
        min_length=20,
        max_length=20_000,
        description=(
            "Concrete user-visible outcome the running agent should produce; describe the desired "
            "behavior, never repeat a build/create command."
        ),
    )
    intended_users: str = Field(
        min_length=3,
        max_length=1_000,
        description=(
            "Noun phrase naming the people who will chat with the agent, not an instruction, task, "
            "input/output description, or sentence beginning with a verb."
        ),
    )
    business_rules: list[PRDStatement] = Field(
        min_length=1,
        max_length=5,
        description="Task-specific policies or behavioral rules the agent must follow.",
    )
    capabilities: list[PRDStatement] = Field(
        min_length=1,
        max_length=16,
        description="Actions and response modes the configured runtime genuinely supports.",
    )
    boundaries: list[PRDStatement] = Field(
        min_length=1,
        max_length=4,
        description="Prohibited, unsupported, or escalation-required behavior.",
    )
    success_criteria: list[PRDStatement] = Field(
        min_length=2,
        max_length=4,
        description="Observable criteria that apply to normal, ambiguous, and boundary cases.",
    )
    documents: list[PRDDocument] = Field(default_factory=list, max_length=20)

    @field_validator("title")
    @classmethod
    def require_product_facing_title(cls, value: str) -> str:
        if "_" in value or value == value.lower():
            raise ValueError("title must be a human-readable product name, not an identifier")
        return value.strip()

    @field_validator("desired_outcome")
    @classmethod
    def require_runtime_outcome(cls, value: str) -> str:
        if re.match(r"^\s*(?:build|create|make)\b", value, re.IGNORECASE):
            raise ValueError("desired_outcome must describe runtime value, not repeat a build command")
        return value.strip()

    @field_validator("intended_users")
    @classmethod
    def require_people_not_instructions(cls, value: str) -> str:
        if re.match(
            r"^\s*(?:answer|provide|help|identify|return|generate|summarize|use|follow|given)\b",
            value,
            re.IGNORECASE,
        ):
            raise ValueError("intended_users must name people as a noun phrase, not describe a task")
        return value.strip()


class RequiredBehavior(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    expectation: str = Field(min_length=1, max_length=1_000)


class GradedBehavior(BaseModel):
    id: str
    expectation: str
    weight: float = Field(ge=0, le=1)


class QualityContract(BaseModel):
    required_behaviors: list[RequiredBehavior] = Field(default_factory=list, max_length=12)
    graded_behaviors: list[GradedBehavior] = Field(default_factory=list, min_length=2, max_length=4)


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


class TaskSpec(BaseModel):
    version: int = 2
    name: str
    goal: str
    objective: str
    background: str = ""
    task_type: str = "chat_agent"
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    rubric: list[RubricDimension]
    constraints: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    synthetic_coverage_plan: list[str] = Field(default_factory=list)
    seed_instruction: str
    module_shape: str = "chat"
    tool_definitions: list[ToolDefinition] = Field(default_factory=list)
    knowledge_sources: list[AgentKnowledge] = Field(default_factory=list, max_length=20)
    agent_prd: AgentPRD | None = None
    quality_contract: QualityContract | None = None

    @field_validator("rubric")
    @classmethod
    def require_rubric(cls, value: list[RubricDimension]) -> list[RubricDimension]:
        if not 2 <= len(value) <= 4:
            raise ValueError("rubric must contain between two and four dimensions")
        return value

    @model_validator(mode="after")
    def validate_weights(self) -> "TaskSpec":
        total = sum(dimension.weight for dimension in self.rubric)
        if abs(total - 1.0) > 0.001:
            raise ValueError("rubric weights must sum to 1")
        return self


class ClarificationQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=10, max_length=300)
    options: list[str] = Field(min_length=3, max_length=3)


class PlanningResult(BaseModel):
    status: Literal["ready", "needs_clarification", "awaiting_approval"]
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=5)
    task_spec: TaskSpec | None = None
    agent_prd: AgentPRD | None = None
    auto_approved: bool = False
    model_calls: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> "PlanningResult":
        if self.status in {"ready", "awaiting_approval"} and self.task_spec is None:
            raise ValueError("completed planning results require a task specification")
        if self.status in {"ready", "awaiting_approval"} and self.agent_prd is None:
            raise ValueError("completed planning results require an Agent PRD")
        if self.status == "needs_clarification" and not self.questions:
            raise ValueError("clarification results require at least one question")
        if self.status == "needs_clarification" and self.task_spec is not None:
            raise ValueError("clarification results cannot include a task specification")
        return self


def runtime_outcome_from_goal(goal: str) -> str:
    outcome = re.sub(
        r"^\s*(?:build|create|make)\s+(?:an?\s+)?(?:agent|assistant)?\s*(?:that|to)?\s*",
        "",
        goal,
        flags=re.IGNORECASE,
    ).strip()
    replacements = {
        "answers ": "Answer ",
        "helps ": "Help ",
        "returns ": "Return ",
        "provides ": "Provide ",
    }
    for prefix, replacement in replacements.items():
        if outcome.lower().startswith(prefix):
            outcome = replacement + outcome[len(prefix):]
            break
    return outcome if len(outcome) >= 20 else f"Provide useful, reliable help for: {goal.strip()}"

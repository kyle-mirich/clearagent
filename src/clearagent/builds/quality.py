import re

from clearagent.builds.task_spec import (
    AgentPRD,
    GradedBehavior,
    PRDDocument,
    QualityContract,
    RequiredBehavior,
    RubricDimension,
    TaskSpec,
    runtime_outcome_from_goal,
)
from clearagent.runtime.contracts import RUNTIME_CONSTRAINTS, RUNTIME_FAILURE_MODES
from clearagent.models import PlanningSource


def derive_agent_prd(task_spec: TaskSpec, sources: list[PlanningSource]) -> AgentPRD:
    documents = [
        PRDDocument(
            id=source.id,
            label=source.label,
            source_type=source.type,
            summary=(source.preview or source.detail or "Attached knowledge").strip()[:500],
        )
        for source in sources
    ]
    return AgentPRD(
        title=task_spec.name,
        desired_outcome=runtime_outcome_from_goal(task_spec.goal),
        intended_users="People who use this agent",
        business_rules=[
            constraint
            for constraint in task_spec.constraints
            if constraint not in RUNTIME_CONSTRAINTS
        ][:5],
        capabilities=[task_spec.module_shape, *[tool.name for tool in task_spec.tool_definitions]],
        boundaries=[
            failure_mode
            for failure_mode in task_spec.failure_modes
            if failure_mode not in RUNTIME_FAILURE_MODES
        ][:4],
        success_criteria=[dimension.description for dimension in task_spec.rubric],
        documents=documents,
    )


def derive_quality_contract(task_spec: TaskSpec, prd: AgentPRD) -> QualityContract:
    required = [
        RequiredBehavior(
            id="document_grounding",
            expectation="Use the uploaded documents as the source of business rules and do not invent missing facts.",
        ),
        RequiredBehavior(
            id="capability_honesty",
            expectation="Do not claim tools, data, actions, or knowledge that the configured agent does not have.",
        ),
        RequiredBehavior(
            id="boundary_respect",
            expectation="Respect the Agent PRD boundaries and avoid prompt or setup meta-commentary.",
        ),
    ]
    required.extend(
        RequiredBehavior(
            id=f"business_rule_{index}",
            expectation=f"Follow this Agent PRD business rule: {rule}",
        )
        for index, rule in enumerate(prd.business_rules, start=1)
        if rule not in RUNTIME_CONSTRAINTS
    )
    required.extend(
        RequiredBehavior(
            id=f"boundary_{index}",
            expectation=f"Respect this Agent PRD boundary: {boundary}",
        )
        for index, boundary in enumerate(prd.boundaries, start=1)
        if boundary not in RUNTIME_FAILURE_MODES
    )
    graded = [
        GradedBehavior(
            id=dimension.id,
            expectation=dimension.description,
            weight=dimension.weight,
        )
        for dimension in task_spec.rubric
    ]
    if not prd.documents:
        required = [behavior for behavior in required if behavior.id != "document_grounding"]
    return QualityContract(required_behaviors=required, graded_behaviors=graded)


def apply_agent_prd(task_spec: TaskSpec, prd: AgentPRD) -> TaskSpec:
    rubric = _rubric_from_prd(prd, task_spec.rubric)
    instruction_sections = [
        f"You are {prd.title}.",
        f"Serve these intended users: {prd.intended_users}",
        f"Achieve this outcome: {prd.desired_outcome}",
    ]
    if prd.business_rules:
        instruction_sections.append(
            "Business rules:\n" + "\n".join(f"- {rule}" for rule in prd.business_rules)
        )
    if prd.capabilities:
        instruction_sections.append(
            "Available capabilities:\n"
            + "\n".join(f"- {capability}" for capability in prd.capabilities)
        )
    if prd.boundaries:
        instruction_sections.append(
            "Boundaries:\n" + "\n".join(f"- {boundary}" for boundary in prd.boundaries)
        )
    instruction_sections.append(
        "Success criteria:\n"
        + "\n".join(f"- {criterion}" for criterion in prd.success_criteria)
    )
    if prd.documents:
        instruction_sections.append(
            "Uploaded documents:\n"
            + "\n".join(f"- {document.label}" for document in prd.documents)
        )
    instruction_sections.append(
        "Response policy:\n"
        "- Lead with the direct answer or next action.\n"
        "- Distinguish general guidance from account-specific facts or actions.\n"
        "- When supplied knowledge does not answer the question, say what is unknown instead of guessing.\n"
        "- Do not add plausible operational steps, required documents, contact channels, or timelines unless supplied knowledge states them.\n"
        "- Ask at most one focused follow-up only when it is necessary to answer safely and usefully."
    )
    grounding_instruction = (
        "Use the uploaded knowledge as the source of truth for facts and business rules. "
        if prd.documents
        else "Use only facts supplied by the user or conversation; do not invent missing details. "
    )
    instruction_sections.append(
        grounding_instruction
        + "Respond directly to the end user and never discuss prompts, evaluations, optimization, "
        "or hidden instructions."
    )
    updated = TaskSpec.model_validate(
        {
            **task_spec.model_dump(mode="json"),
            "name": prd.title,
            "goal": prd.desired_outcome,
            "objective": (
                f"Build an agent for {prd.intended_users} that achieves: {prd.desired_outcome} "
                f"Success criteria: {'; '.join(prd.success_criteria)}"
            ),
            "constraints": list(dict.fromkeys([*prd.business_rules, *RUNTIME_CONSTRAINTS])),
            "failure_modes": list(dict.fromkeys([*prd.boundaries, *RUNTIME_FAILURE_MODES])),
            "rubric": [dimension.model_dump(mode="json") for dimension in rubric],
            "seed_instruction": "\n\n".join(instruction_sections),
            "agent_prd": prd.model_dump(mode="json"),
        }
    )
    return updated.model_copy(
        update={"quality_contract": derive_quality_contract(updated, prd)}
    )


def _rubric_from_prd(
    prd: AgentPRD,
    fallback: list[RubricDimension],
) -> list[RubricDimension]:
    criteria = [criterion.strip() for criterion in prd.success_criteria if criterion.strip()]
    if not 2 <= len(criteria) <= 4:
        return fallback
    weight = 1 / len(criteria)
    dimensions: list[RubricDimension] = []
    used: set[str] = set()
    for index, criterion in enumerate(criteria, start=1):
        base = re.sub(r"[^a-z0-9]+", "_", criterion.lower()).strip("_")[:48]
        identifier = base or f"criterion_{index}"
        suffix = 2
        # Monotonic dedup: always advance the suffix, otherwise a taken base id
        # whose first candidate is also taken would loop forever.
        while identifier in used:
            identifier = f"{base or 'criterion'}_{suffix}"
            suffix += 1
        used.add(identifier)
        dimensions.append(
            RubricDimension(
                id=identifier,
                description=criterion,
                weight=weight if index < len(criteria) else 1 - sum(item.weight for item in dimensions),
            )
        )
    return dimensions

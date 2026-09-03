from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from clearagent.builds.pipeline import (
    PipelineSettings,
    export_files,
    plan_agent_brief,
    render_run_report,
    run_improvement_pipeline,
)
from clearagent.builds.budgets import BUDGET_LIMITS, BudgetTracker
from clearagent.builds.quality import apply_agent_prd, derive_agent_prd
from clearagent.builds.task_spec import ToolDefinition
from clearagent.runtime.contracts import RUNTIME_OUTPUT_SCHEMA
from clearagent.config import Settings
from clearagent.models import (
    AgentKnowledge,
    PlanningRequest,
    PlanningSource,
    SavedAgentConfig,
    SavedAgentSummary,
)
from clearagent.store import Store


class Build:

    def __init__(
        self,
        settings: Settings,
        *,
        tool_registry: Mapping[str, Callable[..., Any]] | None = None,
    ):
        self._settings = settings
        self._tool_registry = dict(tool_registry or {})

    @property
    def pipeline_settings(self) -> PipelineSettings:
        return PipelineSettings(
            deterministic_mode=self._settings.deterministic_mode,
            planner_model=self._settings.planner_model,
            synthetic_model=self._settings.synthetic_model,
            task_model=self._settings.task_model,
            judge_model=self._settings.judge_model,
            reflection_model=self._settings.reflection_model,
            openrouter_api_key=self._settings.openrouter_api_key,
            gepa_max_tokens=self._settings.gepa_max_tokens,
            task_max_tokens=self._settings.task_max_tokens,
            max_concurrency=self._settings.max_concurrency,
            synthetic_max_concurrency=self._settings.synthetic_max_concurrency,
            reasoning_effort=self._settings.reasoning_effort,
            provider_sort=self._settings.provider_sort,
            promotion_margin=self._settings.promotion_margin,
            debug=self._settings.debug,
            tool_registry=self._tool_registry,
        )

    def plan(
        self,
        request: PlanningRequest,
        *,
        on_model_call: Callable[[dict[str, Any]], None] | None = None,
    ):
        model_calls: list[dict[str, Any]] = []

        def record_model_call(payload: dict[str, Any]) -> None:
            model_calls.append(payload)
            if on_model_call is not None:
                on_model_call(payload)

        planning_context = self._planning_goal(request.goal, request.sources)
        planning = plan_agent_brief(
            request.goal,
            replace(self.pipeline_settings, on_model_call=record_model_call),
            request.answers,
            planning_context=planning_context,
        )
        if planning.task_spec is None:
            return planning.model_copy(update={"model_calls": model_calls})
        shape_to_task_type = {
            "chat": "chat_agent",
            "structured": "structured_agent",
            "tools": "tool_agent",
        }
        task_spec = planning.task_spec.model_copy(
            update={
                "goal": request.goal,
                "background": planning_context,
                "task_type": shape_to_task_type[request.agent_shape],
                "module_shape": request.agent_shape,
                "output_schema": request.output_schema or RUNTIME_OUTPUT_SCHEMA,
                "tool_definitions": [
                    ToolDefinition.model_validate(tool) for tool in request.tools
                ],
                "knowledge_sources": [
                    AgentKnowledge(
                        id=source.id,
                        type=source.type,
                        label=source.label,
                        content=(source.preview or source.detail).strip(),
                    )
                    for source in request.sources
                ],
            }
        )
        derived_prd = derive_agent_prd(task_spec, request.sources)
        if planning.agent_prd is None:
            prd = derived_prd
        else:
            actual_capabilities = [
                request.agent_shape,
                *[tool.name for tool in task_spec.tool_definitions],
            ]
            prd = planning.agent_prd.model_copy(
                update={
                    "documents": derived_prd.documents,
                    "capabilities": list(
                        dict.fromkeys(
                            [*planning.agent_prd.capabilities, *actual_capabilities]
                        )
                    )[:16],
                }
            )
        task_spec = apply_agent_prd(task_spec, prd)
        if request.mode == "approval":
            return planning.model_copy(
                update={
                    "status": "awaiting_approval",
                    "task_spec": task_spec,
                    "agent_prd": prd,
                    "auto_approved": False,
                    "model_calls": model_calls,
                }
            )
        return planning.model_copy(
            update={
                "status": "ready",
                "task_spec": task_spec,
                "agent_prd": prd,
                "auto_approved": True,
                "model_calls": model_calls,
            }
        )

    def execute(self, store: Store, run_id: str) -> None:
        run = store.get_run(run_id)
        run_settings = self.pipeline_settings_for(run.budget_profile)
        run_improvement_pipeline(store, run_id, run_settings)

    def pipeline_settings_for(self, budget_profile: str) -> PipelineSettings:
        limits = BUDGET_LIMITS.get(budget_profile, BUDGET_LIMITS["standard"])
        budget_tracker = BudgetTracker(limits)
        if self.pipeline_settings.task_model.startswith("openai:"):
            budget_tracker = BudgetTracker(
                replace(
                    limits,
                    max_model_calls=2_147_483_647,
                    max_total_tokens=OPENAI_SAFETY_TOKEN_LIMIT,
                    max_cost_usd=float("inf"),
                )
            )
        return replace(
            self.pipeline_settings,
            gepa_max_tokens=limits.gepa_max_tokens,
            task_max_tokens=limits.task_max_tokens,
            budget_tracker=budget_tracker,
        )

    def report(self, store: Store, run_id: str, owner_id: str) -> str:
        return render_run_report(
            store.get_run(run_id, owner_id=owner_id),
            events=store.list_events(run_id),
        )

    def export(self, store: Store, run_id: str, owner_id: str) -> dict[str, str]:
        run = store.get_run(run_id, owner_id=owner_id)
        best_version = (
            store.get_agent_version(version_id=run.best_agent_version_id, owner_id=owner_id)
            if run.best_agent_version_id
            else None
        )
        instruction = best_version["instruction_text"] if best_version else None
        files = export_files(
            run,
            optimized_instruction=instruction,
            events=store.list_events(run_id),
        )
        agent = self.load_agent(store, run.project_id, owner_id)
        if agent is not None:
            files["clearagent-export/agent.json"] = agent.model_dump_json(indent=2) + "\n"
        return files

    def load_agent(
        self,
        store: Store,
        project_id: str,
        owner_id: str,
    ) -> SavedAgentConfig | None:
        project = store.get_project(project_id, owner_id=owner_id)
        version = store.get_promoted_version(project_id=project_id, owner_id=owner_id)
        if version is None:
            return None
        task_spec = project.settings.get("task_spec") or {}
        prd = project.settings.get("agent_prd") or task_spec.get("agent_prd") or {}
        return SavedAgentConfig(
            project_id=project.id,
            agent_version_id=str(version["id"]),
            source_run_id=str(version["run_id"]),
            version_number=int(version["version_number"]),
            name=project.name,
            description=str(prd.get("desired_outcome") or project.goal),
            prompt=str(version["instruction_text"]),
            knowledge=[
                AgentKnowledge.model_validate(source)
                for source in task_spec.get("knowledge_sources", [])
            ],
            module_shape=task_spec.get("module_shape", "chat"),
            output_schema=task_spec.get("output_schema", RUNTIME_OUTPUT_SCHEMA),
            tools=list(task_spec.get("tool_definitions", [])),
            agent_prd=prd,
            validation_metrics=dict(version.get("validation_metrics") or {}),
            holdout_metrics=version.get("test_metrics"),
            created_at=str(version["created_at"]),
            updated_at=project.updated_at,
        )

    def list_agents(self, store: Store, owner_id: str) -> list[SavedAgentSummary]:
        summaries = []
        for project in store.list_projects(owner_id=owner_id):
            task_spec = project.settings.get("task_spec") or {}
            prd = project.settings.get("agent_prd") or task_spec.get("agent_prd") or {}
            summaries.append(
                SavedAgentSummary(
                    project_id=project.id,
                    name=project.name,
                    description=str(prd.get("desired_outcome") or project.goal),
                    ready=project.promoted_agent_version_id is not None,
                    agent_version_id=project.promoted_agent_version_id,
                    knowledge_count=len(task_spec.get("knowledge_sources", [])),
                    updated_at=project.updated_at,
                )
            )
        return summaries

    @staticmethod
    def _planning_goal(goal: str, sources: list[PlanningSource]) -> str:
        sections = [goal.strip()]
        remaining = 40_000
        for source in sources:
            content = (source.preview or source.detail).strip()
            if not content or remaining <= 0:
                continue
            excerpt = content[: min(6_000, remaining)]
            sections.append(f"Knowledge source ({source.type}) {source.label}:\n{excerpt}")
            remaining -= len(excerpt)
        return "\n\n".join(sections)
OPENAI_SAFETY_TOKEN_LIMIT = 2_000_000

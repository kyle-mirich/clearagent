import json

import pytest

from clearagent.runtime.providers.base import FakeProvider, ProviderResponse, ToolCall
from clearagent.runtime.tools import tool
from clearagent.builds.scoring import CandidateEvaluation, CaseJudgment
from clearagent.builds.admission import candidate_is_eligible
from clearagent.builds.pipeline import (
    CombinedJudgment,
    DimensionJudgment,
    RequiredBehaviorJudgment,
    PipelineSettings,
    SYNTHETIC_BATCH_SIZE,
    SYNTHETIC_MAX_OUTPUT_TOKENS,
    _apply_deterministic_judges,
    _combine_judgment,
    _evaluate_case_live,
    _evaluate_case_offline,
    _normalize_generated_checks,
    _provider_compatible_schema,
    _reflection_lm,
    _reflection_system_prompt,
    _required_behaviors_for_example,
    _safe_pipeline_error,
    _select_deployment,
)
from pydantic import BaseModel, ValidationError


def test_combined_judge_uses_declared_weights_and_reports_failures():
    result = _combine_judgment(
        {"id": "case-1"},
        {"answer": "A useful answer."},
        CombinedJudgment(
            dimensions=[
                DimensionJudgment(
                    id="success",
                    score=0.8,
                    rationale="The requested outcome is mostly achieved.",
                ),
                DimensionJudgment(
                    id="grounding",
                    score=0.2,
                    rationale="One claim is not supported by supplied context.",
                    failure_tags=["unsupported_claim"],
                ),
            ],
            overall_reasoning="The response is useful but includes an unsupported claim.",
        ),
        [
            {"id": "success", "description": "Achieves the task.", "weight": 0.75},
            {"id": "grounding", "description": "Uses supplied facts.", "weight": 0.25},
        ],
    )

    assert result.score == 0.65
    assert result.passed is False
    assert result.failure_tags == ["unsupported_claim"]
    assert "success (0.80)" in result.reasoning


def test_deterministic_checks_gate_a_candidate_without_inflating_its_score():
    judgment = CaseJudgment(
        example_id="case-1",
        score=0.8,
        passed=True,
        reasoning="The combined LLM judge found the response useful and grounded.",
        actual_output={"answer": "This accidentally mentions the system prompt."},
    )

    result = _apply_deterministic_judges(
        judgment,
        [{"not_contains": "system prompt"}, {"contains": "mentions"}],
    )

    assert result.score == 0.8
    assert result.passed is False
    assert result.required_behavior_passed is False
    assert result.failure_tags == ["check_not_contains"]
    assert "not_contains=fail" in result.reasoning


def test_generated_checks_cannot_add_brittle_promotion_gates():
    checks = _normalize_generated_checks(
        [
            {"not_contains": ["system prompt", "internal chain"]},
            {"contains": ["baseline", "held-out"]},
            {"contains_any": ["promoted", "selected", 3]},
            {"refuses": "yes"},
            {"unknown": "ignored"},
        ]
    )

    assert checks == [
        {"not_contains": "system prompt"},
        {"not_contains": "hidden instructions"},
    ]


def test_missing_judge_dimension_scores_zero_and_is_actionable():
    result = _combine_judgment(
        {"id": "case-1"},
        {"answer": "Answer"},
        CombinedJudgment(
            dimensions=[
                DimensionJudgment(
                    id="success",
                    score=1,
                    rationale="The requested outcome is fully achieved.",
                ),
                DimensionJudgment(
                    id="extra",
                    score=1,
                    rationale="A dimension that was not requested by the rubric.",
                ),
            ],
            overall_reasoning="The response was evaluated against the available dimensions.",
        ),
        [
            {"id": "success", "description": "Achieves the task.", "weight": 0.5},
            {"id": "clarity", "description": "Is clear.", "weight": 0.5},
        ],
    )

    assert result.score == 0.5
    assert result.failure_tags == ["missing_judge_clarity"]
    assert "judge omitted this dimension" in result.reasoning


def test_required_quality_behavior_gates_case_promotion():
    result = _combine_judgment(
        {"id": "case-1"},
        {"answer": "A polished answer."},
        CombinedJudgment(
            dimensions=[
                DimensionJudgment(id="success", score=1, rationale="The answer is useful."),
                DimensionJudgment(id="clarity", score=1, rationale="The answer is clear."),
            ],
            required_behaviors=[
                RequiredBehaviorJudgment(
                    id="document_grounding",
                    passed=False,
                    rationale="The answer invents a policy not present in the document.",
                )
            ],
            overall_reasoning="The response is polished but violates a required behavior.",
        ),
        [
            {"id": "success", "description": "Useful.", "weight": 0.5},
            {"id": "clarity", "description": "Clear.", "weight": 0.5},
        ],
        [{"id": "document_grounding", "expectation": "Use the uploaded document."}],
    )

    assert result.score == 1
    assert result.passed is False
    assert result.required_behavior_passed is False
    assert "required_behavior_document_grounding" in result.required_behavior_failures


def test_case_judging_uses_only_assigned_required_behaviors():
    selected = _required_behaviors_for_example(
        {
            "quality_contract": {
                "required_behaviors": [
                    {"id": "grounding", "expectation": "Use the document."},
                    {"id": "boundary", "expectation": "Respect the boundary."},
                ]
            }
        },
        {"required_behavior_ids": ["boundary"]},
    )

    assert selected == [{"id": "boundary", "expectation": "Respect the boundary."}]


def test_held_out_selection_never_replaces_a_better_incumbent():
    winner = _select_deployment(
        seed=("seed", 0.7),
        optimized=("optimized", 0.8),
        incumbent=("incumbent", 0.9),
    )

    assert winner == ("incumbent", "incumbent", 0.9)


def test_held_out_selection_prefers_incumbent_on_tie():
    winner = _select_deployment(
        seed=("seed", 0.8),
        optimized=("optimized", 0.8),
        incumbent=("incumbent", 0.8),
    )

    assert winner == ("incumbent", "incumbent", 0.8)


def test_held_out_selection_keeps_seed_when_candidate_fails_required_behavior():
    winner = _select_deployment(
        seed=("seed", 0.8, True),
        optimized=("optimized", 0.99, False),
        incumbent=None,
        promotion_margin=0.03,
    )

    assert winner == ("seed", "seed", 0.8)


def test_selection_chooses_the_only_version_that_passes_required_behaviors():
    winner = _select_deployment(
        seed=("seed", 0.95, False),
        optimized=("optimized", 0.75, True),
        incumbent=None,
        promotion_margin=0.03,
    )

    assert winner == ("optimized", "optimized", 0.75)


def test_selection_fails_when_no_version_passes_required_behaviors():
    with pytest.raises(RuntimeError, match="No agent version passed quality admission"):
        _select_deployment(
            seed=("seed", 0.95, False),
            optimized=("optimized", 0.99, False),
            incumbent=None,
            promotion_margin=0.03,
        )


def test_quality_admission_requires_graded_and_required_behavior_passes():
    passing_case = CaseJudgment(
        example_id="pass",
        score=0.9,
        passed=True,
        reasoning="The output passes graded and required behavior checks.",
    )
    failing_case = passing_case.model_copy(update={"score": 0.4, "passed": False})
    validation = CandidateEvaluation(
        score=0.9,
        pass_rate=1.0,
        required_pass_rate=1.0,
        required_passed=True,
        reasoning="Validation passed all quality checks.",
        case_results=[passing_case],
    )
    healthy_holdout = validation.model_copy(update={"reasoning": "Holdout passed all quality checks."})
    weak_holdout = CandidateEvaluation(
        score=0.4,
        pass_rate=0.0,
        required_pass_rate=1.0,
        required_passed=True,
        reasoning="Required behaviors passed but graded holdout quality did not.",
        case_results=[failing_case],
    )

    assert candidate_is_eligible(None) is False
    assert candidate_is_eligible(weak_holdout) is False
    assert candidate_is_eligible(healthy_holdout) is True

    # Below either gate on the holdout split: not deployable, regardless of
    # how strong the validation split looked.
    low_pass_rate_holdout = healthy_holdout.model_copy(update={"pass_rate": 0.79})
    low_required_rate_holdout = healthy_holdout.model_copy(update={"required_pass_rate": 0.79})
    exact_threshold_holdout = healthy_holdout.model_copy(update={"pass_rate": 0.8, "required_pass_rate": 0.8})

    assert candidate_is_eligible(low_pass_rate_holdout) is False
    assert candidate_is_eligible(low_required_rate_holdout) is False
    assert candidate_is_eligible(exact_threshold_holdout) is True

    # Gating is rate-based on purpose: one isolated required-behavior miss
    # among many passing cases must not veto an otherwise strong candidate.
    strict_boolean_miss = healthy_holdout.model_copy(update={"required_passed": False})
    assert candidate_is_eligible(strict_boolean_miss) is True


def test_provider_schema_keeps_structure_and_drops_provider_incompatible_keywords():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {"type": "string", "minLength": 2, "maxLength": 20},
            }
        },
        "required": ["items"],
    }

    assert _provider_compatible_schema(schema)["properties"]["items"] == {
        "type": "array",
        "items": {"type": "string"},
    }


def test_synthetic_batches_fit_the_structured_output_ceiling():
    # Six cases per call keeps the call count (and cost) low; the 100k output
    # ceiling guarantees the batch can never truncate mid-JSON.
    assert SYNTHETIC_BATCH_SIZE == 6
    assert SYNTHETIC_MAX_OUTPUT_TOKENS == 100_000


def test_gepa_reflection_keeps_runtime_knowledge_separate(monkeypatch):
    captured = {}

    def fake_complete(model, settings, messages):
        captured["messages"] = messages
        return "```\nImproved reusable instruction.\n```"

    monkeypatch.setattr("clearagent.builds.pipeline._complete_text", fake_complete)
    reflection = _reflection_lm(
        PipelineSettings(
            deterministic_mode=False,
            reflection_model="fake:reflection",
            openrouter_api_key="test-key",
        )
    )

    result = reflection("GEPA evaluation context")

    assert result == "```\nImproved reusable instruction.\n```"
    assert captured["messages"][0].role == "system"
    assert captured["messages"][0].content == _reflection_system_prompt()
    assert "never copy document passages" in captured["messages"][0].content
    assert captured["messages"][1].content == "GEPA evaluation context"
    list_result = reflection([{"role": "user", "content": "Context in list"}])
    assert list_result == "```\nImproved reusable instruction.\n```"
    assert captured["messages"][1].content == "Context in list"


def test_validation_errors_do_not_expose_generated_case_content():
    class Payload(BaseModel):
        answer: str

    try:
        Payload.model_validate_json('{"answer":"hidden test content')
    except ValidationError as exc:
        message = _safe_pipeline_error(exc)
    else:  # pragma: no cover - protects the test fixture itself
        raise AssertionError("Expected malformed JSON to fail validation.")

    assert message == "A model returned malformed structured output after three attempts."
    assert "hidden test content" not in message


def test_candidate_evaluation_retries_malformed_json_with_task_token_limit(monkeypatch):
    provider = FakeProvider(
        [
            ProviderResponse.fake_text('{"answer":"' + ("x" * 130_000)),
            ProviderResponse.fake_text(json.dumps({"answer": "A bounded useful answer."})),
            ProviderResponse.fake_text(
                json.dumps(
                    {
                        "dimensions": [
                            {
                                "id": "success",
                                "score": 0.9,
                                "rationale": "The requested result is delivered.",
                                "failure_tags": [],
                            },
                            {
                                "id": "clarity",
                                "score": 0.8,
                                "rationale": "The answer is direct and readable.",
                                "failure_tags": [],
                            },
                        ],
                        "overall_reasoning": "The corrected output satisfies the case.",
                    }
                )
            ),
        ]
    )
    monkeypatch.setattr("clearagent.builds.pipeline.provider_for_model", lambda _: provider)
    settings = PipelineSettings(
        task_model="fake:task-model",
        judge_model="fake:judge-model",
        task_max_tokens=321,
        gepa_max_tokens=654,
    )

    result = _evaluate_case_live(
        "Answer the user directly and clearly.",
        {
            "id": "case-1",
            "input": {"message": "Give me the result."},
            "expected": {"answer": "A useful result."},
            "reference_notes": "Reward a direct and useful answer.",
            "checks": [],
        },
        {
            "goal": "Return a useful result.",
            "background": "Knowledge source (docs) Benefits handbook: Employees receive sixteen weeks of paid parental leave.",
            "constraints": ["Be concise."],
            "rubric": [
                {"id": "success", "description": "Delivers the result.", "weight": 0.6},
                {"id": "clarity", "description": "Uses clear language.", "weight": 0.4},
            ],
        },
        settings,
    )

    assert result.actual_output == {"answer": "A bounded useful answer."}
    assert result.score == 0.86
    assert [request.body["max_tokens"] for request in provider.completed_requests] == [321, 321, 654]
    correction = provider.completed_requests[1].body["messages"][-1]["content"]
    assert "previous response was truncated" in correction
    assert "complete, compact JSON" in correction
    assert any(
        "sixteen weeks of paid parental leave" in str(message["content"])
        for message in provider.completed_requests[0].body["messages"]
    )
    assert "clarification, refusal, or boundary response" in provider.completed_requests[2].body[
        "messages"
    ][0]["content"]


def test_structured_output_value_errors_are_safe_for_run_events():
    error = ValueError(
        "Invalid structured output JSON for 'response': Expecting ',' delimiter: "
        "line 1 column 130717 (char 130716)"
    )

    assert _safe_pipeline_error(error) == (
        "A model returned malformed structured output after three attempts."
    )


def test_structured_output_agent_evaluates_against_its_declared_schema():
    result = _evaluate_case_offline(
        "Return a structured resolution directly and do not invent details.",
        {
            "id": "case-1",
            "input": {"message": "Can I return this item?"},
            "expected": {"resolution": "Return eligibility"},
            "checks": [],
        },
        {
            "goal": "Return a structured resolution.",
            "constraints": ["Be concise."],
            "rubric": [
                {"id": "success", "description": "Delivers the result.", "weight": 0.6},
                {"id": "clarity", "description": "Uses clear language.", "weight": 0.4},
            ],
            "output_schema": {
                "type": "object",
                "properties": {"resolution": {"type": "string"}},
                "required": ["resolution"],
                "additionalProperties": False,
            },
        },
    )

    assert result.actual_output == {"resolution": "Offline response for Can I return this item?"}


def test_tool_agent_executes_registered_tools_before_structured_evaluation(monkeypatch):
    @tool
    def lookup_return(order_number: str) -> dict[str, str]:
        """Look up return eligibility."""
        return {"order_number": order_number, "eligibility": "eligible"}

    provider = FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(id="tool-1", name="lookup_return", arguments={"order_number": "A-123"})
            ),
            ProviderResponse.fake_text(json.dumps({"resolution": "Return is eligible."})),
            ProviderResponse.fake_text(
                json.dumps(
                    {
                        "dimensions": [
                            {"id": "success", "score": 1, "rationale": "The return was resolved."},
                            {"id": "clarity", "score": 1, "rationale": "The result is clear."},
                        ],
                        "overall_reasoning": "The tool result produced a valid structured resolution.",
                    }
                )
            ),
        ]
    )
    monkeypatch.setattr("clearagent.builds.pipeline.provider_for_model", lambda _: provider)
    model_calls = []
    settings = PipelineSettings(
        task_model="fake:task-model",
        judge_model="fake:judge-model",
        task_max_tokens=321,
        tool_registry={"lookup_return": lookup_return},
        on_model_call=model_calls.append,
    )

    result = _evaluate_case_live(
        "Use the return lookup tool when an order number is supplied.",
        {
            "id": "case-1",
            "input": {"message": "Can you check return A-123?"},
            "expected": {"resolution": "Return is eligible."},
            "reference_notes": "Use the tool and return the resolution.",
            "checks": [],
        },
        {
            "name": "Returns Agent",
            "goal": "Resolve return eligibility.",
            "constraints": ["Use tools when available."],
            "rubric": [
                {"id": "success", "description": "Resolves the return.", "weight": 0.6},
                {"id": "clarity", "description": "Uses clear language.", "weight": 0.4},
            ],
            "module_shape": "tools",
            "tool_definitions": [
                {
                    "name": "lookup_return",
                    "description": "Look up return eligibility.",
                    "input_schema": {"type": "object"},
                }
            ],
            "output_schema": {
                "type": "object",
                "properties": {"resolution": {"type": "string"}},
                "required": ["resolution"],
                "additionalProperties": False,
            },
        },
        settings,
    )

    assert result.actual_output == {"resolution": "Return is eligible."}
    assert provider.completed_requests[0].body["tools"][0]["function"]["name"] == "lookup_return"
    assert [request.body["max_tokens"] for request in provider.completed_requests[:2]] == [321, 321]
    assert [call["purpose"] for call in model_calls] == ["task", "task", "judge"]

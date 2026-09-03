from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CaseJudgment(BaseModel):
    example_id: str = Field(min_length=1, max_length=200)
    score: float = Field(ge=0, le=1)
    passed: bool
    reasoning: str = Field(min_length=10, max_length=1000)
    failure_tags: list[str] = Field(default_factory=list, max_length=8)
    required_behavior_passed: bool = True
    required_behavior_failures: list[str] = Field(default_factory=list, max_length=8)
    actual_output: dict[str, Any] | None = None


class CandidateEvaluation(BaseModel):
    score: float = Field(ge=0, le=1)
    pass_rate: float = Field(ge=0, le=1)
    required_pass_rate: float = Field(default=1.0, ge=0, le=1)
    required_passed: bool = True
    reasoning: str = Field(min_length=20, max_length=2000)
    case_results: list[CaseJudgment] = Field(default_factory=list)
    failure_summary: dict[str, int] = Field(default_factory=dict)

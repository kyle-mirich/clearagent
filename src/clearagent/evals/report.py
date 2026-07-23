from pydantic import BaseModel, Field


class EvalCaseResult(BaseModel):
    suite_name: str
    case_name: str
    input: str
    final_output: str
    passed: bool
    checks: list[dict]
    run_id: str | None
    trace_db_path: str | None
    latency_ms: int
    cost_usd: float | None
    variant: dict = Field(default_factory=dict)


class EvalReport(BaseModel):
    suite_name: str
    suite_type: str
    agent_name: str
    model: str
    passed: int
    failed: int
    skipped: int = 0
    results: list[EvalCaseResult]
    suite_run_id: str

    def assert_passed(self) -> None:
        if self.failed:
            failures = "\n".join(
                (
                    f"{item.case_name}: {item.checks}"
                    f"{_format_variant(item.variant)} run_id={item.run_id} trace_db={item.trace_db_path}"
                )
                for item in self.results
                if not item.passed
            )
            raise AssertionError(f"Suite {self.suite_name} failed:\n{failures}")


def _format_variant(variant: dict) -> str:
    return f" variant={variant}" if variant else ""

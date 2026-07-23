from typing import Any

from clearagent.agent import Agent
from clearagent.evals.checks import run_checks
from clearagent.evals.suite import EvalSuite
from clearagent.providers.registry import provider_for_model


def run_eval_iterations(
    agent: Agent,
    suite: EvalSuite,
    *,
    models: list[str] | None = None,
    temperatures: list[float | None] | None = None,
    provider_factory=provider_for_model,
) -> dict[str, Any]:
    variants = _variants(
        models or [agent.model],
        temperatures if temperatures is not None else [agent.temperature],
    )
    original_model = agent.model
    original_provider = agent.provider
    original_temperature = agent.temperature
    summaries: list[dict[str, Any]] = []
    try:
        for variant in variants:
            agent.model = variant["model"]
            agent.temperature = variant["temperature"]
            if variant["model"] != original_model:
                agent.provider = provider_factory(variant["model"])
            passed = 0
            failed = 0
            case_results = []
            for case in suite.cases:
                result = agent.run(case.input)
                checks = run_checks(case.checks, result)
                case_passed = all(check.passed for check in checks)
                passed += 1 if case_passed else 0
                failed += 0 if case_passed else 1
                case_results.append(
                    {
                        "name": case.name,
                        "passed": case_passed,
                        "run_id": result.run_id,
                        "checks": [check.model_dump() for check in checks],
                    }
                )
            summaries.append(
                {
                    "model": variant["model"],
                    "temperature": variant["temperature"],
                    "passed": passed,
                    "failed": failed,
                    "pass_rate": passed / max(len(suite.cases), 1),
                    "cases": case_results,
                }
            )
    finally:
        agent.model = original_model
        agent.provider = original_provider
        agent.temperature = original_temperature
    return {
        "suite": suite.name,
        "total_variants": len(summaries),
        "variants": summaries,
    }


def _variants(models: list[str], temperatures: list[float | None]) -> list[dict[str, Any]]:
    return [
        {"model": model, "temperature": temperature}
        for model in models
        for temperature in temperatures
    ]

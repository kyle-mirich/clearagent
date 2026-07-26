from clearagent.agent import Agent
from clearagent.evals.checks import run_checks
from clearagent.evals.report import EvalCaseResult, EvalReport
from clearagent.evals.suite import EvalCase, EvalSuite, require_runnable_suite
from clearagent.providers.registry import provider_for_model
from clearagent.storage.sqlite import SQLiteTraceStore


class EvalRunner:
    """Execute deterministic eval suites against an agent and persist results."""

    def __init__(self, agent: Agent, *, trace_db_path=None, provider_factory=None):
        self.agent = agent
        self._has_explicit_provider_factory = provider_factory is not None
        self.provider_factory = provider_factory or provider_for_model
        if trace_db_path is not None:
            self.agent.trace_db_path = trace_db_path

    def run_suite(self, suite: EvalSuite) -> EvalReport:
        """Run every case, or dispatch to matrix execution when configured."""
        require_runnable_suite(suite)
        if suite.matrix:
            return self.run_matrix(suite)
        store = SQLiteTraceStore(self.agent.trace_db_path)
        suite_run_id = store.start_eval_suite_run(
            suite_name=suite.name,
            suite_type=suite.type,
            agent_name=self.agent.name,
            model=self.agent.model,
        )
        results: list[EvalCaseResult] = []
        try:
            for case in suite.cases:
                results.append(self._run_case(store, suite_run_id, suite, case))
        except Exception as exc:
            store.end_eval_suite_run(
                suite_run_id,
                passed=sum(1 for item in results if item.passed),
                failed=sum(1 for item in results if not item.passed),
                status="error",
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise
        passed_count = sum(1 for item in results if item.passed)
        failed_count = len(results) - passed_count
        store.end_eval_suite_run(suite_run_id, passed=passed_count, failed=failed_count)
        return EvalReport(
            suite_name=suite.name,
            suite_type=suite.type,
            agent_name=self.agent.name,
            model=self.agent.model,
            passed=passed_count,
            failed=failed_count,
            results=results,
            suite_run_id=suite_run_id,
        )

    def run_matrix(self, suite: EvalSuite) -> EvalReport:
        """Run suite cases across configured model and temperature variants."""
        require_runnable_suite(suite)
        variants = _matrix_variants(suite.matrix or {})
        if not variants:
            variants = [{"model": self.agent.model, "temperature": self.agent.temperature}]
        store = SQLiteTraceStore(self.agent.trace_db_path)
        suite_run_id = store.start_eval_suite_run(
            suite_name=suite.name,
            suite_type=suite.type,
            agent_name=self.agent.name,
            model="matrix",
        )
        original_model = self.agent.model
        original_provider = self.agent.provider
        original_temperature = self.agent.temperature
        results: list[EvalCaseResult] = []
        try:
            for variant in variants:
                model = variant.get("model", original_model)
                temperature = variant.get("temperature", original_temperature)
                effective_variant = {"model": model}
                if "temperature" in variant:
                    effective_variant["temperature"] = temperature
                self.agent.model = model
                if model == original_model and not self._has_explicit_provider_factory:
                    self.agent.provider = original_provider
                else:
                    self.agent.provider = self.provider_factory(model)
                self.agent.temperature = temperature
                for case in suite.cases:
                    results.append(
                        self._run_case(store, suite_run_id, suite, case, variant=effective_variant)
                    )
        except Exception as exc:
            store.end_eval_suite_run(
                suite_run_id,
                passed=sum(1 for item in results if item.passed),
                failed=sum(1 for item in results if not item.passed),
                status="error",
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise
        finally:
            self.agent.model = original_model
            self.agent.provider = original_provider
            self.agent.temperature = original_temperature
        passed_count = sum(1 for item in results if item.passed)
        failed_count = len(results) - passed_count
        store.end_eval_suite_run(suite_run_id, passed=passed_count, failed=failed_count)
        return EvalReport(
            suite_name=suite.name,
            suite_type=suite.type,
            agent_name=self.agent.name,
            model="matrix",
            passed=passed_count,
            failed=failed_count,
            results=results,
            suite_run_id=suite_run_id,
        )

    def _run_case(
        self,
        store: SQLiteTraceStore,
        suite_run_id: str,
        suite: EvalSuite,
        case: EvalCase,
        *,
        variant: dict | None = None,
    ) -> EvalCaseResult:
        latest_before = store.get_latest_run_for_agent(self.agent.name)
        latest_before_id = latest_before["id"] if latest_before else None
        try:
            result = self.agent.run(case.input, trace=True, trace_store=store)
        except Exception as exc:
            run_id = self._latest_or_synthetic_error_run(
                store,
                case.input,
                exc,
                latest_before_id=latest_before_id,
            )
            check_dicts = [
                {
                    "name": "case_error",
                    "passed": False,
                    "message": f"{exc.__class__.__name__}: {exc}",
                    "expected": None,
                    "actual": None,
                }
            ]
            store.save_eval_case_result(
                suite_run_id=suite_run_id,
                run_id=run_id,
                suite_name=suite.name,
                case_name=case.name,
                input=case.input,
                final_output="",
                passed=False,
                checks=check_dicts,
                latency_ms=0,
                cost_usd=None,
                variant=variant,
            )
            return EvalCaseResult(
                suite_name=suite.name,
                case_name=case.name,
                input=case.input,
                final_output="",
                passed=False,
                checks=check_dicts,
                run_id=run_id,
                trace_db_path=str(self.agent.trace_db_path),
                latency_ms=0,
                cost_usd=None,
                variant=variant or {},
            )

        checks = run_checks(case.checks, result)
        passed = all(check.passed for check in checks)
        check_dicts = [check.model_dump() for check in checks]
        store.save_eval_case_result(
            suite_run_id=suite_run_id,
            run_id=result.run_id or "",
            suite_name=suite.name,
            case_name=case.name,
            input=case.input,
            final_output=result.output,
            passed=passed,
            checks=check_dicts,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            variant=variant,
        )
        return EvalCaseResult(
            suite_name=suite.name,
            case_name=case.name,
            input=case.input,
            final_output=result.output,
            passed=passed,
            checks=check_dicts,
            run_id=result.run_id,
            trace_db_path=str(result.trace_db_path) if result.trace_db_path else None,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            variant=variant or {},
        )

    def _latest_or_synthetic_error_run(
        self,
        store: SQLiteTraceStore,
        case_input: str,
        exc: Exception,
        *,
        latest_before_id: str | None,
    ) -> str:
        latest_run = store.get_latest_run_for_agent(self.agent.name)
        if latest_run and latest_run["id"] != latest_before_id:
            return latest_run["id"]
        run_id = store.start_run(agent_name=self.agent.name, root_input=case_input)
        store.end_run(
            run_id,
            status="error",
            error={"type": exc.__class__.__name__, "message": str(exc)},
        )
        return run_id


def _matrix_variants(matrix: dict) -> list[dict]:
    models = matrix.get("models") or []
    temperatures = matrix.get("temperatures") or [None]
    if not models:
        return [
            {"temperature": temperature} if temperature is not None else {}
            for temperature in temperatures
        ]
    variants = []
    for model in models:
        for temperature in temperatures:
            variant = {"model": model}
            if temperature is not None:
                variant["temperature"] = temperature
            variants.append(variant)
    return variants

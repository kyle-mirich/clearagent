import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from clearagent.storage.sqlite import SQLiteTraceStore, _now


@dataclass
class BaselineComparison:
    baseline_name: str
    suite_run_id: str
    unchanged_passes: list[str]
    unchanged_failures: list[str]
    regressions: list[str]
    improvements: list[str]


def save_baseline(store: SQLiteTraceStore, suite_run_id: str, *, name: str) -> str:
    suite = _get_suite_run(store, suite_run_id)
    results = store.list_eval_case_results(suite_run_id)
    payload = {row["case_name"]: bool(row["passed"]) for row in results}
    baseline_id = f"baseline_{uuid4().hex[:12]}"
    with store.connect() as db:
        db.execute(
            """
            INSERT INTO baselines
            (id, name, suite_name, agent_name, model, created_at, results_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                baseline_id,
                name,
                suite["suite_name"],
                suite["agent_name"],
                suite["model"],
                _now(),
                json.dumps(payload),
                json.dumps({"suite_run_id": suite_run_id}),
            ),
        )
    return baseline_id


def compare_baseline(
    store: SQLiteTraceStore, baseline_name: str, suite_run_id: str
) -> BaselineComparison:
    baseline = _get_baseline(store, baseline_name)
    if baseline is None:
        raise ValueError(f"Missing baseline {baseline_name!r}.")
    suite = _get_suite_run(store, suite_run_id)
    baseline_metadata = _load_baseline_metadata(baseline)
    baseline_suite = _get_suite_run(store, baseline_metadata["suite_run_id"])
    if baseline["suite_name"] != suite["suite_name"]:
        raise ValueError("Baseline suite_name does not match the comparison suite run.")
    if baseline_suite["suite_type"] != suite["suite_type"]:
        raise ValueError("Baseline suite_type does not match the comparison suite run.")
    if baseline["agent_name"] != suite["agent_name"]:
        raise ValueError("Baseline agent_name does not match the comparison suite run.")
    if baseline["model"] != suite["model"]:
        raise ValueError("Baseline model does not match the comparison suite run.")
    previous = _load_baseline_results(baseline)
    current = {
        row["case_name"]: bool(row["passed"]) for row in store.list_eval_case_results(suite_run_id)
    }
    if set(previous) != set(current):
        raise ValueError("Baseline case set does not match the comparison suite run.")
    unchanged_passes = []
    unchanged_failures = []
    regressions = []
    improvements = []
    for case_name, previous_passed in previous.items():
        current_passed = current.get(case_name)
        if previous_passed and current_passed:
            unchanged_passes.append(case_name)
        elif not previous_passed and not current_passed:
            unchanged_failures.append(case_name)
        elif previous_passed and current_passed is False:
            regressions.append(case_name)
        elif not previous_passed and current_passed:
            improvements.append(case_name)
    return BaselineComparison(
        baseline_name=baseline_name,
        suite_run_id=suite_run_id,
        unchanged_passes=unchanged_passes,
        unchanged_failures=unchanged_failures,
        regressions=regressions,
        improvements=improvements,
    )


def _get_suite_run(store: SQLiteTraceStore, suite_run_id: str) -> dict[str, Any]:
    with store.connect() as db:
        row = db.execute("SELECT * FROM eval_suite_runs WHERE id=?", (suite_run_id,)).fetchone()
    if not row:
        raise ValueError(f"Missing eval suite run {suite_run_id!r}.")
    return dict(row)


def _get_baseline(store: SQLiteTraceStore, name: str) -> dict[str, Any] | None:
    with store.connect() as db:
        row = db.execute(
            "SELECT * FROM baselines WHERE name=? ORDER BY created_at DESC LIMIT 1", (name,)
        ).fetchone()
    return dict(row) if row else None


def _load_baseline_metadata(baseline: dict[str, Any]) -> dict[str, str]:
    try:
        metadata = json.loads(baseline["metadata_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed baseline metadata for {baseline['name']!r}.") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("suite_run_id"), str):
        raise ValueError(f"Malformed baseline metadata for {baseline['name']!r}.")
    return {"suite_run_id": metadata["suite_run_id"]}


def _load_baseline_results(baseline: dict[str, Any]) -> dict[str, bool]:
    try:
        results = json.loads(baseline["results_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed baseline results for {baseline['name']!r}.") from exc
    if not isinstance(results, dict) or not all(
        isinstance(case_name, str) and isinstance(passed, bool)
        for case_name, passed in results.items()
    ):
        raise ValueError(f"Malformed baseline results for {baseline['name']!r}.")
    return results

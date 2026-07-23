from pathlib import Path
from typing import Any

import yaml

from clearagent.storage.sqlite import SQLiteTraceStore


def generate_eval_case_from_trace(
    store: SQLiteTraceStore,
    run_id: str,
    *,
    suite_name: str | None = None,
    case_name: str | None = None,
) -> str:
    run = store.get_run(run_id)
    if not run:
        raise ValueError(f"Missing run {run_id}")
    final_output = run.get("final_output")
    if not final_output:
        raise ValueError(f"Run {run_id} does not have a final output.")
    data: dict[str, Any] = {
        "name": suite_name or f"{run['agent_name']}-trace",
        "type": "output",
        "cases": [
            {
                "name": case_name or run_id,
                "input": run["root_input"],
                "checks": [{"contains": final_output}],
            }
        ],
    }
    return yaml.safe_dump(data, sort_keys=False)


def write_eval_case_from_trace(
    store: SQLiteTraceStore,
    run_id: str,
    out: str | Path,
    *,
    suite_name: str | None = None,
    case_name: str | None = None,
) -> None:
    Path(out).write_text(
        generate_eval_case_from_trace(
            store,
            run_id,
            suite_name=suite_name,
            case_name=case_name,
        ),
        encoding="utf-8",
    )

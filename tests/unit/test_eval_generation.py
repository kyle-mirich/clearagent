import yaml

from clearagent.evals.generate import generate_eval_case_from_trace
from clearagent.storage.sqlite import SQLiteTraceStore


def test_generate_eval_case_from_trace_uses_run_input_and_output(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    run_id = store.start_run(agent_name="support", root_input="Where is A123?")
    store.end_run(run_id, final_output="Order A123 shipped Friday")

    generated = generate_eval_case_from_trace(store, run_id)

    data = yaml.safe_load(generated)
    assert data == {
        "name": "support-trace",
        "type": "output",
        "cases": [
            {
                "name": run_id,
                "input": "Where is A123?",
                "checks": [{"contains": "Order A123 shipped Friday"}],
            }
        ],
    }

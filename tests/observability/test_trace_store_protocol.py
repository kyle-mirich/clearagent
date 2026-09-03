from typing import assert_type

from clearagent.storage.protocol import TraceStore
from clearagent.storage.sqlite import SQLiteTraceStore


def test_sqlite_trace_store_satisfies_protocol(tmp_path):
    store: TraceStore = SQLiteTraceStore(tmp_path / "traces.sqlite")

    run_id = store.start_run(agent_name="agent", root_input="hello")
    store.end_run(run_id, final_output="world")

    run = store.get_run(run_id)
    assert run is not None
    assert run["final_output"] == "world"
    assert_type(store, TraceStore)

from clearagent.reports import render_trace_report
from clearagent.storage.sqlite import SQLiteTraceStore


def test_render_trace_report_includes_run_turns_and_tools(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    run_id = store.start_run(agent_name="support", root_input="Where is A123?")
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="support",
        input_messages=[{"role": "user", "content": "Where is A123?"}],
    )
    tool_call_id = store.start_tool_call(
        run_id=run_id,
        turn_id=turn_id,
        tool_name="lookup_order",
        args={"order_id": "A123"},
    )
    store.end_tool_call(tool_call_id, result={"status": "shipped"})
    store.end_turn(
        turn_id=turn_id,
        output_messages=[{"role": "assistant", "content": "shipped"}],
        final_output="shipped",
    )
    store.end_run(run_id, final_output="shipped")

    report = render_trace_report(store, run_id)

    assert "# ClearAgent Trace Report" in report
    assert f"Run ID: `{run_id}`" in report
    assert "lookup_order" in report
    assert "Where is A123?" in report
    assert "shipped" in report

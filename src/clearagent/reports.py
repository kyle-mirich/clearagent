import json
import re

from clearagent.storage.protocol import (
    ModelCallRecord,
    ToolCallRecord,
    TraceRun,
    TraceStore,
    TraceTurn,
)


def list_trace_runs_payload(store: TraceStore, *, limit: int = 100) -> list[dict]:
    payload = []
    for run in store.list_runs()[:limit]:
        run_id = run["id"]
        turns = store.get_turns(run_id)
        model_calls = store.list_model_calls(run_id)
        tool_calls = store.list_tool_calls(run_id)
        payload.append(
            {
                "id": run_id,
                "agent_name": run["agent_name"],
                "graph_name": run["graph_name"],
                "status": run["status"],
                "started_at": run["started_at"],
                "ended_at": run["ended_at"],
                "input_preview": _run_input_preview(run, turns),
                "final_output_preview": _preview(run["final_output"]),
                "turn_count": len(turns),
                "model_call_count": len(model_calls),
                "tool_call_count": len(tool_calls),
                "total_latency_ms": run["total_latency_ms"],
                "total_prompt_tokens": run["total_prompt_tokens"],
                "total_completion_tokens": run["total_completion_tokens"],
                "total_cost_usd": run["total_cost_usd"],
            }
        )
    return payload


def render_trace_report(store: TraceStore, run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise ValueError(f"Missing run {run_id}")
    turns = store.get_turns(run_id)
    model_calls = store.list_model_calls(run_id)
    tool_calls = store.list_tool_calls(run_id)

    lines = [
        "# ClearAgent Trace Report",
        "",
        f"Run ID: `{run['id']}`",
        f"Agent: `{run['agent_name']}`",
        f"Status: `{run['status']}`",
        "",
        "## Input",
        "",
        _fenced(run["root_input"] or ""),
        "",
        "## Final Output",
        "",
        _fenced(run["final_output"] or ""),
        "",
        "## Turns",
    ]
    for turn in turns:
        lines.extend(
            [
                "",
                f"### Turn {turn['turn_index']} - {turn['node_name']}",
                "",
                f"Status: `{turn['status']}`",
                "",
                _fenced(turn["final_output"] or ""),
            ]
        )
    lines.extend(["", "## Model Calls"])
    for model_call in model_calls:
        lines.extend(
            [
                "",
                f"- `{model_call['provider']}` / `{model_call['model']}` "
                f"status `{model_call['status']}`",
            ]
        )
    lines.extend(["", "## Tool Calls"])
    if not tool_calls:
        lines.append("")
        lines.append("No tool calls recorded.")
    for tool_call in tool_calls:
        args = _json(tool_call["args_json"])
        result = _json(tool_call["result_json"])
        lines.extend(
            [
                "",
                f"### {tool_call['tool_name']}",
                "",
                f"Status: `{tool_call['status']}`",
                "",
                "Arguments:",
                "",
                _fenced(args, "json"),
                "",
                "Result:",
                "",
                _fenced(result, "json"),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def trace_triage_payload(store: TraceStore, run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise ValueError(f"Missing run {run_id}")
    turns = store.get_turns(run_id)
    model_calls = store.list_model_calls(run_id)
    tool_calls = store.list_tool_calls(run_id)
    failures = []
    if run["status"] != "ok":
        failures.append({"kind": "run", "message": run["metadata_json"]})
    for turn in turns:
        if turn["status"] != "ok":
            failures.append({"kind": "turn", "message": turn["error_json"] or turn["status"]})
    for model_call in model_calls:
        if model_call["status"] != "ok":
            failures.append(
                {
                    "kind": "model_call",
                    "message": model_call["error_json"] or model_call["status"],
                }
            )
    for tool_call in tool_calls:
        if tool_call["status"] != "ok":
            failures.append(
                {
                    "kind": "tool_call",
                    "message": tool_call["error_json"] or tool_call["status"],
                }
            )
    steps = [
        _trace_step(
            turn,
            [call for call in model_calls if call["turn_id"] == turn["id"]],
            [call for call in tool_calls if call["turn_id"] == turn["id"]],
            failures,
        )
        for turn in turns
    ]
    return {
        "run": run,
        "turns": turns,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "steps": steps,
        "failures": failures,
        "report": render_trace_report(store, run_id),
    }


def _trace_step(
    turn: TraceTurn,
    model_calls: list[ModelCallRecord],
    tool_calls: list[ToolCallRecord],
    failures: list[dict],
) -> dict:
    return {
        "id": turn["id"],
        "turn_index": turn["turn_index"],
        "node_name": turn["node_name"],
        "status": turn["status"],
        "started_at": turn["started_at"],
        "ended_at": turn["ended_at"],
        "latency_ms": turn["latency_ms"],
        "final_output": turn["final_output"],
        "input_messages": _json_value(
            turn["input_messages_json"], [], failures, "turn_input", turn["id"]
        ),
        "output_messages": _json_value(
            turn["output_messages_json"], [], failures, "turn_output", turn["id"]
        ),
        "error": _json_value(turn["error_json"], None, failures, "turn_error", turn["id"]),
        "model_calls": [_model_call_payload(call, failures) for call in model_calls],
        "tool_calls": [_tool_call_payload(call, failures) for call in tool_calls],
    }


def _model_call_payload(call: ModelCallRecord, failures: list[dict]) -> dict:
    return {
        **call,
        "request": _json_value(call["request_json"], {}, failures, "model_request", call["id"]),
        "response": _json_value(
            call["response_json"], None, failures, "model_response", call["id"]
        ),
        "usage": _json_value(call["usage_json"], None, failures, "model_usage", call["id"]),
        "error": _json_value(call["error_json"], None, failures, "model_error", call["id"]),
    }


def _tool_call_payload(call: ToolCallRecord, failures: list[dict]) -> dict:
    return {
        **call,
        "arguments": _json_value(call["args_json"], {}, failures, "tool_arguments", call["id"]),
        "result": _json_value(call["result_json"], None, failures, "tool_result", call["id"]),
        "error": _json_value(call["error_json"], None, failures, "tool_error", call["id"]),
    }


def _fenced(value: str, language: str = "") -> str:
    longest_run = max((len(match.group()) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{value}\n{fence}"


def _preview(value: object, limit: int = 160) -> str:
    if value is None:
        raw = ""
    elif isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, sort_keys=True, default=str)
    text = " ".join(raw.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _run_input_preview(run: TraceRun, turns: list[TraceTurn]) -> str:
    if turns:
        messages = _json_value(
            turns[0]["input_messages_json"], [], [], "turn_input", turns[0]["id"]
        )
        if isinstance(messages, list):
            user_messages = [
                message.get("content", "")
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            ]
            if user_messages:
                return _preview(user_messages[-1])
    return _preview(run["root_input"])


def _json_value(
    value: str | None,
    fallback: object,
    failures: list[dict],
    kind: str,
    row_id: str,
) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        failures.append(
            {
                "kind": kind,
                "message": f"Malformed JSON in {row_id}: {exc}",
                "row_id": row_id,
            }
        )
        return fallback


def _json(value: str | None) -> str:
    if not value:
        return "null"
    try:
        return json.dumps(json.loads(value), indent=2, sort_keys=True)
    except json.JSONDecodeError:
        return value

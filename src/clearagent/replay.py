import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError

from clearagent.providers.base import Provider, ProviderRequest, ProviderResponse
from clearagent.providers.registry import provider_for_request
from clearagent.storage.sqlite import DEFAULT_TRACE_DB, SQLiteTraceStore


class ModelCallDiff(BaseModel):
    changed: bool
    before_output: str | None = None
    after_output: str | None = None
    before_finish_reason: str | None = None
    after_finish_reason: str | None = None
    before_usage: dict[str, Any] | None = None
    after_usage: dict[str, Any] | None = None


def replay_model_call(
    trace_db: str | Path = DEFAULT_TRACE_DB,
    run_id: str | None = None,
    *,
    turn: int = 0,
    provider: Provider | None = None,
) -> ProviderResponse:
    if run_id is None:
        raise ValueError("run_id is required.")
    request = _stored_request(trace_db, run_id, turn)
    replay_provider = provider or provider_for_request(request)
    if provider is None and hasattr(replay_provider, "auth_headers_snapshot"):
        request.headers_snapshot = replay_provider.auth_headers_snapshot()
    return replay_provider.complete(request)


def diff_model_call(
    trace_db: str | Path = DEFAULT_TRACE_DB,
    run_id: str | None = None,
    *,
    turn: int = 0,
    provider: Provider | None = None,
) -> ModelCallDiff:
    if run_id is None:
        raise ValueError("run_id is required.")
    store = SQLiteTraceStore(trace_db)
    row = store.get_model_call_for_turn(run_id, turn)
    if not row:
        raise ValueError(f"Missing model request for run {run_id} turn {turn}.")
    if not row["response_json"]:
        raise ValueError(f"Missing stored model response for run {run_id} turn {turn}.")
    before = _stored_response(row["response_json"], run_id, turn)
    after = replay_model_call(trace_db, run_id, turn=turn, provider=provider)
    before_usage = before.get("usage")
    after_usage = after.usage.model_dump() if after.usage else None
    before_output = before.get("output_text")
    after_output = after.output_text
    before_finish_reason = before.get("finish_reason")
    after_finish_reason = after.finish_reason
    return ModelCallDiff(
        changed=(
            before_output != after_output
            or before_finish_reason != after_finish_reason
            or before_usage != after_usage
        ),
        before_output=before_output,
        after_output=after_output,
        before_finish_reason=before_finish_reason,
        after_finish_reason=after_finish_reason,
        before_usage=before_usage,
        after_usage=after_usage,
    )


def _stored_request(trace_db: str | Path, run_id: str, turn: int) -> ProviderRequest:
    store = SQLiteTraceStore(trace_db)
    row = store.get_model_call_for_turn(run_id, turn)
    if not row:
        raise ValueError(f"Missing model request for run {run_id} turn {turn}.")
    try:
        return ProviderRequest.model_validate_json(row["request_json"])
    except ValidationError as exc:
        raise ValueError(f"Malformed stored model request for run {run_id} turn {turn}.") from exc


def _stored_response(response_json: str, run_id: str, turn: int) -> dict[str, Any]:
    try:
        response = json.loads(response_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed stored model response for run {run_id} turn {turn}.") from exc
    if not isinstance(response, dict):
        raise ValueError(f"Malformed stored model response for run {run_id} turn {turn}.")
    return response

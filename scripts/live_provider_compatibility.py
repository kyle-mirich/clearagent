#!/usr/bin/env python3
"""Run bounded, opt-in live provider compatibility checks and record fixtures."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from clearagent import create_agent, tool
from clearagent.evals.baseline import compare_baseline, save_baseline
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalCase, EvalSuite
from clearagent.messages import Message
from clearagent.providers.base import Provider, ProviderRequest, ProviderResponse
from clearagent.providers.registry import provider_for_model
from clearagent.storage.sqlite import SQLiteTraceStore
from clearagent.evals.generate import generate_eval_case_from_trace


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "live_provider_recordings"
OPT_IN_ENV = "CLEARAGENT_LIVE_TESTS"
RECORDING_VERSION = 1
MAX_OUTPUT_TOKENS = 96


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    requested_model: str
    credential_names: tuple[str, ...]
    request_cap: int
    supported_alternative: str | None = None
    model_note: str = ""

    @property
    def model_uri(self) -> str:
        return f"{self.name}:{self.requested_model}"


PROVIDERS = (
    ProviderSpec(
        name="openai",
        requested_model="gpt-5.6-luna",
        credential_names=("OPENAI_API_KEY",),
        request_cap=4,
        model_note="The user-specified OpenAI target.",
    ),
    ProviderSpec(
        name="anthropic",
        requested_model="claude-sonnet-5",
        credential_names=("ANTHROPIC_API_KEY",),
        request_cap=4,
        model_note="Anthropic's API identifier for Claude Sonnet 5.",
    ),
    ProviderSpec(
        name="google",
        requested_model="gemini-3.5-flash-lite",
        credential_names=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        request_cap=4,
        model_note=(
            "Selected as a currently supported inexpensive general-purpose generateContent "
            "model from Google's model catalog."
        ),
    ),
    ProviderSpec(
        name="openrouter",
        requested_model="xiaomi/mimo-v2.5",
        credential_names=("OPENROUTER_API_KEY",),
        request_cap=4,
    ),
)


def live_tests_enabled(environ: dict[str, str] | os._Environ[str] = os.environ) -> bool:
    return environ.get(OPT_IN_ENV) == "1"


def require_live_opt_in(environ: dict[str, str] | os._Environ[str] = os.environ) -> None:
    if not live_tests_enabled(environ):
        raise RuntimeError(
            f"Refusing to make provider requests: set {OPT_IN_ENV}=1 explicitly."
        )


class BoundedProvider:
    """Count provider requests and inject a provider-appropriate output ceiling."""

    def __init__(self, provider: Provider, spec: ProviderSpec):
        self.provider = provider
        self.spec = spec
        self.provider_name = provider.provider_name
        self.api_shape = provider.api_shape
        self.request_count = 0

    def build_request(self, **kwargs: Any) -> ProviderRequest:
        request = self.provider.build_request(**kwargs)
        _apply_output_limit(request, self.spec.name)
        return request

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self._claim_request()
        return self.provider.complete(request)

    def stream_text(self, request: ProviderRequest):
        self._claim_request()
        yield from self.provider.stream_text(request)

    def _claim_request(self) -> None:
        if self.request_count >= self.spec.request_cap:
            raise RuntimeError(
                f"{self.spec.name} live request cap of {self.spec.request_cap} exceeded."
            )
        self.request_count += 1


def _apply_output_limit(request: ProviderRequest, provider_name: str) -> None:
    if provider_name == "openai":
        request.body["max_output_tokens"] = MAX_OUTPUT_TOKENS
    elif provider_name == "openrouter":
        request.body["max_tokens"] = MAX_OUTPUT_TOKENS
    elif provider_name == "anthropic":
        request.body["max_tokens"] = MAX_OUTPUT_TOKENS
    elif provider_name == "google":
        generation_config = request.body.setdefault("generationConfig", {})
        generation_config["maxOutputTokens"] = MAX_OUTPUT_TOKENS


@tool
def compatibility_token(code: str) -> str:
    """Return the deterministic token used by the compatibility suite."""
    if code != "alpha":
        raise ValueError("code must be alpha")
    return "TOOL_OK"


def run_provider(spec: ProviderSpec, working_dir: Path) -> dict[str, Any]:
    started_at = _timestamp()
    recording: dict[str, Any] = {
        "recording": {
            "version": RECORDING_VERSION,
            "recorded_at": started_at,
            "provider": spec.name,
            "requested_model": spec.requested_model,
            "actual_model": None,
            "api_shape": None,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "request_cap": spec.request_cap,
            "retry_count": 0,
            "model_note": spec.model_note,
        },
        "availability": {},
        "capabilities": {},
    }
    credential_name = next((name for name in spec.credential_names if os.environ.get(name)), None)
    if credential_name is None:
        recording["availability"] = {
            "status": "skipped",
            "reason": f"missing credential; set one of {', '.join(spec.credential_names)}",
        }
        recording["recording"]["request_count"] = 0
        recording["recording"]["overall_status"] = "skipped"
        return recording

    bounded = BoundedProvider(provider_for_model(spec.model_uri), spec)
    recording["recording"]["api_shape"] = bounded.api_shape
    model = spec.requested_model
    basic_db = working_dir / f"{spec.name}-basic.sqlite"
    try:
        basic = _run_basic(spec, bounded, model, basic_db)
        recording["availability"] = {"status": "pass", "requested_model": model}
    except Exception as exc:
        recording["availability"] = {
            "status": "failed",
            "requested_model": model,
            "error": _safe_error(exc),
            "supported_alternative": spec.supported_alternative,
        }
        if not spec.supported_alternative:
            recording["capabilities"] = _skipped_capabilities("requested model unavailable")
            recording["recording"]["request_count"] = bounded.request_count
            recording["recording"]["overall_status"] = "failed"
            return recording
        model = spec.supported_alternative
        basic_db = working_dir / f"{spec.name}-alternative-basic.sqlite"
        try:
            basic = _run_basic(spec, bounded, model, basic_db)
        except Exception as alternative_exc:
            recording["availability"]["alternative_status"] = "failed"
            recording["availability"]["alternative_error"] = _safe_error(alternative_exc)
            recording["capabilities"] = _skipped_capabilities("supported alternative failed")
            recording["recording"]["request_count"] = bounded.request_count
            recording["recording"]["actual_model"] = model
            recording["recording"]["overall_status"] = "failed"
            return recording
        recording["availability"]["alternative_status"] = "pass"

    recording["recording"]["actual_model"] = model
    recording["capabilities"]["basic_agent"] = {"status": "pass", **basic}
    recording["capabilities"]["system_and_messages"] = {
        "status": "pass",
        "evidence": "basic_agent used a system prompt and list[Message] input",
    }
    recording["capabilities"]["trace_serialization"] = {
        "status": "pass",
        "evidence": "basic_agent.trace",
    }

    tool_db = working_dir / f"{spec.name}-tool-eval.sqlite"
    try:
        tool_eval = _run_tool_eval(spec, bounded, model, tool_db)
        recording["capabilities"]["tool_round_trip"] = {
            "status": "pass",
            **tool_eval["tool"],
        }
        recording["capabilities"]["evaluation"] = {
            "status": "pass",
            **tool_eval["evaluation"],
        }
        recording["capabilities"]["trace_promotion"] = {
            "status": "pass",
            **tool_eval["promotion"],
        }
        recording["capabilities"]["baseline_quality_gate"] = {
            "status": "pass",
            **tool_eval["baseline"],
        }
    except Exception as exc:
        failed = {"status": "failed", "error": _safe_error(exc)}
        recording["capabilities"]["tool_round_trip"] = failed
        recording["capabilities"]["evaluation"] = failed
        recording["capabilities"]["trace_promotion"] = {
            "status": "skipped",
            "reason": "tool/evaluation flow failed",
        }
        recording["capabilities"]["baseline_quality_gate"] = {
            "status": "skipped",
            "reason": "tool/evaluation flow failed",
        }

    stream_db = working_dir / f"{spec.name}-stream.sqlite"
    try:
        streaming = _run_stream(spec, bounded, model, stream_db)
        recording["capabilities"]["streaming"] = {"status": "pass", **streaming}
    except Exception as exc:
        recording["capabilities"]["streaming"] = {
            "status": "failed",
            "error": _safe_error(exc),
        }

    recording["recording"]["request_count"] = bounded.request_count
    capability_statuses = [
        item["status"] for item in recording["capabilities"].values()
    ]
    capabilities_pass = all(status == "pass" for status in capability_statuses)
    target_unavailable = recording["availability"].get("status") == "failed"
    if capabilities_pass and target_unavailable:
        overall_status = "pass_with_requested_model_unavailable"
    elif capabilities_pass:
        overall_status = "pass"
    else:
        overall_status = "failed"
    recording["recording"]["overall_status"] = overall_status
    return sanitize(recording)


def _run_basic(
    spec: ProviderSpec, provider: BoundedProvider, model: str, db_path: Path
) -> dict[str, Any]:
    agent = create_agent(
        name=f"live_{spec.name}_basic",
        model=f"{spec.name}:{model}",
        provider=provider,
        system_prompt="Follow the user's requested output format exactly.",
        trace_db_path=db_path,
        temperature=None,
        max_turns=1,
    )
    result = agent.run(
        [Message(role="user", content="Reply with exactly LIVE_OK and nothing else.")]
    )
    if result.output.strip() != "LIVE_OK":
        raise AssertionError(f"unexpected deterministic response: {result.output!r}")
    return {
        "output": result.output,
        "usage": result.usage.model_dump() if result.usage else None,
        "trace": _trace_snapshot(db_path, result.run_id),
    }


def _run_tool_eval(
    spec: ProviderSpec, provider: BoundedProvider, model: str, db_path: Path
) -> dict[str, Any]:
    agent = create_agent(
        name=f"live_{spec.name}_tool_eval",
        model=f"{spec.name}:{model}",
        provider=provider,
        system_prompt=(
            "Always call compatibility_token when asked. After receiving its result, reply "
            "with exactly that result and nothing else."
        ),
        tools=[compatibility_token],
        trace_db_path=db_path,
        temperature=None,
        max_turns=3,
    )
    suite = EvalSuite(
        name=f"live_{spec.name}_tool_eval",
        type="output",
        cases=[
            EvalCase(
                name="tool trace and final response",
                input="Call compatibility_token with code alpha, then return its result.",
                checks=[
                    {"equals": "TOOL_OK"},
                    {"expected_tools": ["compatibility_token"]},
                    {"called_tool": "compatibility_token"},
                    {"trace_provider": spec.name},
                    {"max_turns": 2},
                ],
            )
        ],
    )
    report = EvalRunner(agent).run_suite(suite)
    report.assert_passed()
    result = report.results[0]
    if not result.run_id:
        raise AssertionError("eval result did not include a trace run id")
    store = SQLiteTraceStore(db_path)
    promoted_yaml = generate_eval_case_from_trace(
        store,
        result.run_id,
        suite_name=f"recorded_{spec.name}_tool",
        case_name="recorded tool response",
    )
    baseline_name = f"live-{spec.name}-baseline"
    save_baseline(store, report.suite_run_id, name=baseline_name)
    comparison = compare_baseline(store, baseline_name, report.suite_run_id)
    if comparison.regressions:
        raise AssertionError(f"baseline comparison regressed: {comparison.regressions}")
    model_calls = _trace_snapshot(db_path, result.run_id)
    return {
        "tool": {
            "final_output": result.final_output,
            "tool_calls": model_calls["tool_calls"],
            "trace": model_calls,
        },
        "evaluation": {
            "fixture_dataset": suite.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
            "aggregate": {"passed": report.passed, "failed": report.failed},
        },
        "promotion": {
            "generated_eval_yaml": promoted_yaml,
            "parsed": yaml.safe_load(promoted_yaml),
        },
        "baseline": {
            "comparison": {
                "unchanged_passes": comparison.unchanged_passes,
                "unchanged_failures": comparison.unchanged_failures,
                "regressions": comparison.regressions,
                "improvements": comparison.improvements,
            }
        },
    }


def _run_stream(
    spec: ProviderSpec, provider: BoundedProvider, model: str, db_path: Path
) -> dict[str, Any]:
    agent = create_agent(
        name=f"live_{spec.name}_stream",
        model=f"{spec.name}:{model}",
        provider=provider,
        system_prompt="Follow the user's requested output format exactly.",
        trace_db_path=db_path,
        temperature=None,
        max_turns=1,
    )
    chunks = list(agent.stream_text("Reply with exactly STREAM_OK and nothing else."))
    output = "".join(chunks)
    if output.strip() != "STREAM_OK":
        raise AssertionError(f"unexpected streamed response: {output!r}")
    store = SQLiteTraceStore(db_path)
    run = store.get_latest_run_for_agent(agent.name)
    if not run:
        raise AssertionError("stream did not create a trace run")
    return {
        "chunks": chunks,
        "output": output,
        "trace": _trace_snapshot(db_path, run["id"]),
    }


def _trace_snapshot(db_path: Path, run_id: str | None) -> dict[str, Any]:
    if not run_id:
        raise AssertionError("live run did not produce a run id")
    store = SQLiteTraceStore(db_path)
    run = store.get_run(run_id)
    if not run:
        raise AssertionError(f"trace run {run_id!r} was not persisted")
    snapshot = {
        "run": _parse_json_columns(run),
        "turns": [_parse_json_columns(row) for row in store.get_turns(run_id)],
        "model_calls": [
            _parse_json_columns(row) for row in store.list_model_calls(run_id)
        ],
        "tool_calls": [
            _parse_json_columns(row) for row in store.list_tool_calls(run_id)
        ],
    }
    return sanitize(snapshot)


def _parse_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    parsed = dict(row)
    for key, value in tuple(parsed.items()):
        if key.endswith("_json") and isinstance(value, str):
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return parsed


def _skipped_capabilities(reason: str) -> dict[str, dict[str, str]]:
    names = (
        "basic_agent",
        "system_and_messages",
        "tool_round_trip",
        "streaming",
        "trace_serialization",
        "evaluation",
        "trace_promotion",
        "baseline_quality_gate",
    )
    return {name: {"status": "skipped", "reason": reason} for name in names}


def sanitize(value: Any) -> Any:
    """Redact secrets and normalize volatile identifiers in recorded payloads."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            lower = key.lower()
            if lower in {
                "authorization",
                "x-api-key",
                "x-goog-api-key",
                "api_key",
                "token",
                "secret",
                "password",
            }:
                clean[key] = "[REDACTED]"
            elif lower in {
                "id",
                "call_id",
                "run_id",
                "turn_id",
                "model_call_id",
                "tool_call_id",
                "responseid",
                "system_fingerprint",
            }:
                clean[key] = "[VOLATILE_ID]" if item is not None else None
            elif lower == "thoughtsignature":
                clean[key] = "[VOLATILE_SIGNATURE]" if item is not None else None
            elif lower in {"started_at", "ended_at", "created", "created_at"}:
                clean[key] = "[VOLATILE_TIME]" if item is not None else None
            elif lower in {"latency_ms", "total_latency_ms"}:
                clean[key] = 0 if item is not None else None
            else:
                clean[key] = sanitize(item)
        return clean
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"Bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"\brun_[a-f0-9]+\b", "[VOLATILE_ID]", value)
        value = re.sub(r"trace_db=/\S+", "trace_db=[VOLATILE_PATH]", value)
        return value
    return value


def _safe_error(exc: Exception) -> dict[str, str]:
    return sanitize({"type": exc.__class__.__name__, "message": str(exc)})


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _assert_no_configured_secrets(payload: Any) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for name in {
        credential
        for spec in PROVIDERS
        for credential in spec.credential_names
    }:
        secret = os.environ.get(name)
        if secret and secret in serialized:
            raise RuntimeError(f"refusing to record payload containing {name}")


def _summary(recordings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "recording_version": RECORDING_VERSION,
        "recorded_at": _timestamp(),
        "opt_in_flag": f"{OPT_IN_ENV}=1",
        "network_policy": "live command only; default tests consume recordings",
        "retry_count": 0,
        "providers": [
            {
                "provider": item["recording"]["provider"],
                "requested_model": item["recording"]["requested_model"],
                "actual_model": item["recording"]["actual_model"],
                "overall_status": item["recording"]["overall_status"],
                "availability": item["availability"],
                "request_count": item["recording"]["request_count"],
                "request_cap": item["recording"]["request_cap"],
                "capabilities": {
                    name: result["status"]
                    for name, result in item["capabilities"].items()
                },
            }
            for item in recordings
        ],
    }


def run_all(selected: set[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    require_live_opt_in()
    specs = [spec for spec in PROVIDERS if selected is None or spec.name in selected]
    with tempfile.TemporaryDirectory(prefix="clearagent-live-") as temp_dir:
        working_dir = Path(temp_dir)
        recordings = [run_provider(spec, working_dir) for spec in specs]
    summary = _summary(recordings)
    _assert_no_configured_secrets(recordings)
    _assert_no_configured_secrets(summary)
    return recordings, summary


def write_recordings(recordings: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for recording in recordings:
        provider_name = recording["recording"]["provider"]
        path = FIXTURE_DIR / f"{provider_name}.json"
        path.write_text(json.dumps(recording, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path = FIXTURE_DIR / "run-summary.json"
    if summary_path.exists() and len(recordings) < len(PROVIDERS):
        existing = json.loads(summary_path.read_text(encoding="utf-8"))
        providers_by_name = {
            provider["provider"]: provider for provider in existing.get("providers", [])
        }
        providers_by_name.update(
            {provider["provider"]: provider for provider in summary["providers"]}
        )
        summary["providers"] = [
            providers_by_name[spec.name]
            for spec in PROVIDERS
            if spec.name in providers_by_name
        ]
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record",
        action="store_true",
        help="intentionally refresh sanitized fixtures under tests/fixtures",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="optional ignored dotenv file to load before checking credentials",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=[spec.name for spec in PROVIDERS],
        help="run only one provider (repeatable); the default is all providers",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.env_file:
        load_dotenv(args.env_file)
    try:
        recordings, summary = run_all(set(args.provider) if args.provider else None)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.record:
        write_recordings(recordings, summary)
    for provider in summary["providers"]:
        print(
            f"{provider['provider']}: {provider['overall_status']} "
            f"requested={provider['requested_model']} actual={provider['actual_model']} "
            f"requests={provider['request_count']}/{provider['request_cap']}"
        )
    failures = [
        provider
        for provider in summary["providers"]
        if provider["overall_status"] in {"failed", "skipped"}
    ]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

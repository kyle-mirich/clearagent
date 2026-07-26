import json
import subprocess
import sys

import httpx
import pytest
import yaml

from clearagent.evals.report import EvalReport
from clearagent.providers.anthropic import _parse_anthropic_response
from clearagent.providers.base import ProviderRequest
from clearagent.providers.google import _parse_google_response
from clearagent.providers.openai import _parse_openai_responses_response
from clearagent.providers.openai_compatible import _parse_openai_response
from scripts.live_provider_compatibility import (
    PROVIDERS,
    ROOT,
    live_tests_enabled,
    run_provider,
    sanitize,
)


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "live_provider_recordings"


def _recordings():
    return [
        pytest.param(spec, FIXTURE_DIR / f"{spec.name}.json", id=spec.name) for spec in PROVIDERS
    ]


def test_live_command_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("CLEARAGENT_LIVE_TESTS", raising=False)

    completed = subprocess.run(
        [sys.executable, "scripts/live_provider_compatibility.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "Refusing to make provider requests" in completed.stderr
    assert live_tests_enabled() is False


@pytest.mark.parametrize("spec", PROVIDERS, ids=lambda spec: spec.name)
def test_missing_live_credential_is_clear_and_makes_no_request(monkeypatch, tmp_path, spec):
    for credential_name in spec.credential_names:
        monkeypatch.delenv(credential_name, raising=False)

    recording = run_provider(spec, tmp_path)

    assert recording["availability"]["status"] == "skipped"
    assert "missing credential" in recording["availability"]["reason"]
    assert recording["recording"]["request_count"] == 0


@pytest.mark.parametrize(("spec", "path"), _recordings())
def test_recorded_live_response_replays_through_provider_parser(spec, path):
    recording = json.loads(path.read_text(encoding="utf-8"))
    basic = recording["capabilities"]["basic_agent"]
    assert basic["status"] == "pass"
    call = basic["trace"]["model_calls"][0]
    request_data = call["request_json"]
    request = ProviderRequest.model_validate(request_data)
    response_data = call["response_json"]
    raw = response_data["raw"]

    if spec.name == "openai":
        assert [item["role"] for item in request.body["input"]] == ["system", "user"]
    elif spec.name == "anthropic":
        assert request.body["system"]
        assert request.body["messages"][0]["role"] == "user"
    elif spec.name == "google":
        assert request.body["systemInstruction"]
        assert request.body["contents"][0]["role"] == "user"
    else:
        assert [message["role"] for message in request.body["messages"]] == [
            "system",
            "user",
        ]

    if spec.name == "openai":
        response = _parse_openai_responses_response(request, raw)
    elif spec.name == "openrouter":
        response = _parse_openai_response(request, httpx.Response(200, json=raw))
    elif spec.name == "anthropic":
        response = _parse_anthropic_response(request, raw)
    else:
        response = _parse_google_response(request, raw)

    assert response.output_text.strip() == "LIVE_OK"
    assert response.model == recording["recording"]["actual_model"]
    assert response.usage is not None


@pytest.mark.parametrize(("spec", "path"), _recordings())
def test_recorded_tool_trace_eval_and_promotion_remain_usable(spec, path):
    recording = json.loads(path.read_text(encoding="utf-8"))
    tool = recording["capabilities"]["tool_round_trip"]
    evaluation = recording["capabilities"]["evaluation"]
    promotion = recording["capabilities"]["trace_promotion"]
    baseline = recording["capabilities"]["baseline_quality_gate"]

    assert tool["status"] == "pass"
    assert tool["final_output"] == "TOOL_OK"
    assert [call["tool_name"] for call in tool["tool_calls"]] == ["compatibility_token"]
    assert len(tool["trace"]["model_calls"]) == 2
    for call in tool["trace"]["model_calls"]:
        assert call["request_json"]["headers_snapshot"]
        assert set(call["request_json"]["headers_snapshot"].values()) <= {
            "[REDACTED]",
            "2023-06-01",
        }

    report = EvalReport.model_validate(evaluation["report"])
    assert report.passed == 1
    assert report.failed == 0
    assert all(check["passed"] for check in report.results[0].checks)
    assert evaluation["aggregate"] == {"failed": 0, "passed": 1}

    promoted = yaml.safe_load(promotion["generated_eval_yaml"])
    assert promoted == promotion["parsed"]
    assert promoted["cases"][0]["checks"] == [{"contains": "TOOL_OK"}]
    assert baseline["comparison"]["regressions"] == []
    assert baseline["comparison"]["unchanged_passes"] == ["tool trace and final response"]


@pytest.mark.parametrize(("spec", "path"), _recordings())
def test_recorded_stream_and_metadata_are_bounded_and_sanitized(spec, path):
    text = path.read_text(encoding="utf-8")
    recording = json.loads(text)

    assert recording["capabilities"]["streaming"]["status"] == "pass"
    assert recording["capabilities"]["streaming"]["output"].strip() == "STREAM_OK"
    assert recording["recording"]["request_count"] <= spec.request_cap
    assert recording["recording"]["max_output_tokens"] == 96
    assert recording["recording"]["retry_count"] == 0
    assert "Bearer " not in text
    assert "sk-" not in text
    assert "AIza" not in text


def test_run_summary_distinguishes_target_availability_from_live_capabilities():
    summary = json.loads((FIXTURE_DIR / "run-summary.json").read_text(encoding="utf-8"))

    assert summary["opt_in_flag"] == "CLEARAGENT_LIVE_TESTS=1"
    audit = summary["completion_audit"]["development_live_generation_requests"]
    assert audit["total"] == sum(
        audit[name] for name in ("openai", "anthropic", "google", "openrouter")
    )
    assert {provider["provider"] for provider in summary["providers"]} == {
        "openai",
        "anthropic",
        "google",
        "openrouter",
    }
    for provider in summary["providers"]:
        assert provider["request_count"] <= provider["request_cap"]
        assert set(provider["capabilities"].values()) <= {
            "pass",
            "unsupported",
            "skipped",
            "failed",
        }


def test_sanitizer_redacts_credentials_and_volatile_values():
    payload = {
        "authorization": "Bearer secret-token",
        "x-goog-api-key": "secret-google",
        "id": "response-123",
        "call_id": "call-123",
        "thoughtSignature": "opaque-context",
        "started_at": "2026-01-01T00:00:00Z",
        "latency_ms": 42,
        "nested": "Authorization failed for Bearer secret-token",
    }

    assert sanitize(payload) == {
        "authorization": "[REDACTED]",
        "x-goog-api-key": "[REDACTED]",
        "id": "[VOLATILE_ID]",
        "call_id": "[VOLATILE_ID]",
        "thoughtSignature": "[VOLATILE_SIGNATURE]",
        "started_at": "[VOLATILE_TIME]",
        "latency_ms": 0,
        "nested": "Authorization failed for Bearer [REDACTED]",
    }

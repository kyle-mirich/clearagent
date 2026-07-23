from clearagent.evals.checks import run_checks
from clearagent.types import RunResult


def result(output="Order A123 shipped", tools=None, latency_ms=50, cost=0.0):
    return RunResult(
        output=output,
        run_id="run_1",
        trace_db_path=".clearagent/traces.sqlite",
        tool_calls=tools or [],
        usage=None,
        latency_ms=latency_ms,
        cost_usd=cost,
        structured_output=None,
    )


def test_output_checks_pass_and_fail():
    passed = run_checks(
        [
            {"contains": "shipped"},
            {"contains_any": ["lost", "A123"]},
            {"not_contains": "cancelled"},
            {"regex": r"A\d{3}"},
            {"equals": "Order A123 shipped"},
        ],
        result(),
    )
    failed = run_checks([{"contains": "refund"}], result())

    assert all(check.passed for check in passed)
    assert not failed[0].passed
    assert "contains" in failed[0].message


def test_invalid_regex_check_returns_failed_result():
    checks = run_checks([{"regex": "["}], result())

    assert checks[0].name == "regex"
    assert checks[0].passed is False
    assert "invalid regex" in checks[0].message


def test_list_shaped_checks_reject_scalar_values():
    checks = run_checks(
        [
            {"contains_any": "A123"},
            {"expected_tools": "lookup_order"},
            {"forbidden_tools": "refund_order"},
        ],
        result(tools=[{"name": "lookup_order"}]),
    )

    assert all(check.passed is False for check in checks)
    assert all("must be a list" in check.message for check in checks)


def test_malformed_checks_fail_without_crashing():
    checks = run_checks(
        [{"contains": 123}, {"json_schema": "not-a-schema"}, {"latency_under_ms": "slow"}],
        result(),
    )

    assert all(check.passed is False for check in checks)
    assert all("Invalid" in check.message for check in checks)


def test_cost_check_fails_when_provider_did_not_report_cost():
    checks = run_checks([{"cost_under": 1.0}], result(cost=None))

    assert checks[0].passed is False
    assert "unavailable" in checks[0].message


def test_json_schema_refusal_tool_latency_and_cost_checks():
    checks = run_checks(
        [
            {"json_schema": {"type": "object", "required": ["answer"]}},
            {"refuses": True},
            {"expected_tools": ["lookup_order"]},
            {"forbidden_tools": ["refund_order"]},
            {"latency_under_ms": 100},
            {"cost_under": 0.01},
        ],
        result(
            output='{"answer": "I cannot provide medical advice."}',
            tools=[{"name": "lookup_order"}],
            latency_ms=50,
            cost=0.001,
        ),
    )

    assert all(check.passed for check in checks)


def test_structured_output_check_uses_parsed_result():
    checks = run_checks(
        [
            {"structured_output": True},
            {"json_schema": {"type": "object", "required": ["label"]}},
        ],
        result(output='{"label": "billing"}'),
    )

    assert all(check.passed for check in checks)


def test_trace_checks_can_assert_provider_turns_and_tool_calls(tmp_path):
    from clearagent import create_agent, tool
    from clearagent.providers.base import FakeProvider, ProviderResponse, ToolCall

    @tool
    def lookup_order(order_id: str) -> dict:
        return {"order_id": order_id, "status": "shipped"}

    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(id="call_1", name="lookup_order", arguments={"order_id": "A123"})
            ),
            ProviderResponse.fake_text("Order A123 shipped"),
        ]
    )
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=provider,
        tools=[lookup_order],
        trace_db_path=db_path,
    )
    run_result = agent.run("Where is A123?")

    checks = run_checks(
        [
            {"trace_provider": "fake"},
            {"max_turns": 2},
            {"called_tool": "lookup_order"},
            {"not_called_tool": "refund_order"},
        ],
        run_result,
    )

    assert all(check.passed for check in checks)

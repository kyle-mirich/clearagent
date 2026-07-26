"""Exercise ClearAgent strictly from an installed distribution.

This script is launched with an isolated interpreter from a temporary directory.
Keeping it separate from the normal pytest suite makes it possible to prove that
imports, package data, and runtime behavior come from the built wheel rather than
the editable repository checkout.
"""

from __future__ import annotations

import argparse
from importlib import import_module, resources
from pathlib import Path

from fastapi.testclient import TestClient

import clearagent
from clearagent import create_agent, tool
from clearagent.chat.app import create_chat_app
from clearagent.evals import EvalRunner, EvalSuite
from clearagent.evals.generate import write_eval_case_from_trace
from clearagent.providers.base import FakeProvider, ProviderResponse, ToolCall
from clearagent.storage.sqlite import SQLiteTraceStore


DOCUMENTED_PUBLIC_EXPORTS = {
    "clearagent": ("create_agent", "tool"),
    "clearagent.contracts": ("ToolContractCase", "validate_tool_contract"),
    "clearagent.evals": ("EvalRunner", "EvalSuite"),
    "clearagent.graph": ("AgentGraph",),
    "clearagent.providers": ("FakeProvider", "OpenAIResponsesProvider"),
    "clearagent.pytest_plugin": ("assert_eval_suite_passes",),
    "clearagent.storage": ("SQLiteTraceStore", "TraceStore"),
}


@tool
def lookup_order(order_id: str) -> dict[str, str]:
    """Return a deterministic order status for the distribution smoke test."""

    return {"order_id": order_id, "status": "shipped"}


def _assert_installed_import(repository_root: Path) -> None:
    package_file = Path(clearagent.__file__).resolve()
    if package_file.is_relative_to(repository_root):
        raise AssertionError(
            f"clearagent imported from the repository instead of the wheel: {package_file}"
        )
    for module_name, exports in DOCUMENTED_PUBLIC_EXPORTS.items():
        module = import_module(module_name)
        missing = [name for name in exports if not hasattr(module, name)]
        if missing:
            raise AssertionError(
                f"installed module {module_name!r} is missing exports: {missing!r}"
            )


def _exercise_agent_and_trace(working_directory: Path) -> None:
    trace_path = working_directory / "traces.sqlite"
    agent = create_agent(
        name="installed_distribution_smoke",
        model="openai:gpt-4.1-mini",
        tools=[lookup_order],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(
                        id="call_lookup_order",
                        name="lookup_order",
                        arguments={"order_id": "A123"},
                    )
                ),
                ProviderResponse.fake_text("Order A123 shipped."),
            ]
        ),
        trace_db_path=trace_path,
    )

    result = agent.run("Where is order A123?")

    if result.output != "Order A123 shipped.":
        raise AssertionError(f"unexpected installed agent output: {result.output!r}")
    if result.run_id is None:
        raise AssertionError("installed traced agent did not return a run ID")
    if not trace_path.is_file():
        raise AssertionError(f"installed agent did not create its trace database: {trace_path}")

    store = SQLiteTraceStore(trace_path)
    runs = store.list_runs()
    if [run["id"] for run in runs] != [result.run_id]:
        raise AssertionError(f"installed trace did not contain the expected run: {runs!r}")
    tool_calls = store.list_tool_calls(result.run_id)
    if len(tool_calls) != 1 or tool_calls[0]["tool_name"] != "lookup_order":
        raise AssertionError(
            f"installed trace did not contain the expected tool call: {tool_calls!r}"
        )

    generated_eval_path = working_directory / "generated-eval.yaml"
    write_eval_case_from_trace(store, result.run_id, generated_eval_path)
    suite = EvalSuite.from_yaml(generated_eval_path)
    if suite.cases[0].input != "Where is order A123?":
        raise AssertionError(f"installed trace-to-eval produced the wrong input: {suite.cases!r}")

    eval_agent = create_agent(
        name="installed_generated_eval_smoke",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("Order A123 shipped.")]),
        trace_db_path=trace_path,
    )
    report = EvalRunner(eval_agent).run_suite(suite)
    if report.passed != 1 or report.failed != 0:
        raise AssertionError(f"installed generated eval did not pass: {report.model_dump()!r}")


def _exercise_bundled_chat(working_directory: Path) -> None:
    static_root = resources.files("clearagent.chat").joinpath("static")
    for asset_name in ("index.html", "styles.css", "app.js"):
        asset = static_root.joinpath(asset_name)
        if not asset.is_file():
            raise AssertionError(f"installed chat asset is missing: {asset_name}")

    agent = create_agent(
        name="installed_chat_smoke",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
        trace_db_path=working_directory / "chat-traces.sqlite",
    )
    app = create_chat_app(agent, chat_db_path=working_directory / "chat.sqlite")
    client = TestClient(app)

    index_response = client.get("/")
    if index_response.status_code != 200 or "ClearAgent" not in index_response.text:
        raise AssertionError(
            "installed chat root was not served correctly: "
            f"status={index_response.status_code} body={index_response.text[:200]!r}"
        )

    for asset_name in ("styles.css", "app.js"):
        response = client.get(f"/assets/{asset_name}")
        if response.status_code != 200 or not response.content:
            raise AssertionError(
                f"installed chat asset was not served: {asset_name} status={response.status_code}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository_root", type=Path)
    args = parser.parse_args()

    repository_root = args.repository_root.resolve()
    working_directory = Path.cwd().resolve()
    if working_directory.is_relative_to(repository_root):
        raise AssertionError(
            f"installed distribution smoke test must run outside the repository: {working_directory}"
        )

    _assert_installed_import(repository_root)
    _exercise_agent_and_trace(working_directory)
    _exercise_bundled_chat(working_directory)
    print("installed distribution smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

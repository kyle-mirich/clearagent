import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from clearagent.agent import Agent
from clearagent.storage.protocol import TraceStore
from clearagent.storage.sqlite import SQLiteTraceStore
from clearagent.trace_lifecycle import latency_ms
from clearagent.runtime.types import RunResult


class _GraphState(TypedDict):
    payload: str
    turn_offset: int


class AgentGraph:
    def __init__(self, name: str, *, trace_store: TraceStore | None = None):
        self.name = name
        self.nodes: dict[str, Agent] = {}
        self.edges: dict[str, str] = {}
        self.entrypoint: str | None = None
        self.trace_store = trace_store

    def add_node(self, agent: Agent) -> "AgentGraph":
        self.nodes[agent.name] = agent
        return self

    def add_edge(self, from_node: str, to_node: str) -> "AgentGraph":
        self.edges[from_node] = to_node
        return self

    def set_entrypoint(self, node_name: str) -> "AgentGraph":
        self.entrypoint = node_name
        return self

    def run(self, input: str) -> RunResult:
        entrypoint = self.entrypoint
        if entrypoint is None:
            raise ValueError("Graph entrypoint is not set.")
        first_agent = self.nodes[entrypoint]
        # Respect the entrypoint agent's tracing opt-out and any injected
        # store; never mutate shared node agents (unsafe under concurrency).
        trace_db_path = first_agent.trace_db_path
        store = self.trace_store or (
            SQLiteTraceStore(first_agent.trace_db_path) if first_agent.trace else None
        )
        run_id = None
        if store is not None:
            run_id = store.start_run(
                agent_name=first_agent.name,
                graph_name=self.name,
                root_input=input,
            )
        started = time.monotonic()
        runtime: dict[str, Any] = {
            "store": store,
            "run_id": run_id,
            "last_result": None,
        }
        execution_graph = self._execution_graph(runtime)

        try:
            final_state: _GraphState = execution_graph.invoke(
                {"payload": input, "turn_offset": 0}
            )
        except Exception as exc:
            if store is not None and run_id is not None:
                store.end_run(
                    run_id,
                    status="error",
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                    latency_ms=latency_ms(started),
                )
            raise

        last_result: RunResult | None = runtime["last_result"]
        output = last_result.output if last_result else ""
        graph_latency_ms = latency_ms(started)
        if store is not None and run_id is not None:
            store.end_run(run_id, final_output=output, latency_ms=graph_latency_ms)
        _ = final_state
        return RunResult(
            output=output,
            run_id=run_id,
            trace_db_path=trace_db_path if store is not None else None,
            tool_calls=[],
            usage=last_result.usage if last_result else None,
            latency_ms=graph_latency_ms,
        )

    def _execution_graph(self, runtime: dict[str, Any]):
        # Validate the edge path terminates before compiling; LangGraph would
        # otherwise surface cycles as opaque recursion-limit errors.
        current: str | None = self.entrypoint
        visited: set[str] = set()
        while current is not None:
            if current in visited:
                raise ValueError(
                    f"Graph contains a cycle at node {current!r}; "
                    "edges must form a path that terminates."
                )
            visited.add(current)
            current = self.edges.get(current)

        builder = StateGraph(_GraphState)
        for name in visited:
            builder.add_node(name, self._node_for(name, runtime))
        assert self.entrypoint is not None
        builder.add_edge(START, self.entrypoint)
        cursor: str | None = self.entrypoint
        while cursor is not None:
            nxt = self.edges.get(cursor)
            if nxt is None:
                builder.add_edge(cursor, END)
            else:
                builder.add_edge(cursor, nxt)
            cursor = nxt
        return builder.compile()

    def _node_for(self, name: str, runtime: dict[str, Any]):
        agent = self.nodes[name]

        def node(state: _GraphState) -> _GraphState:
            store = runtime["store"]
            run_id = runtime["run_id"]
            last_result = agent.run(
                state["payload"],
                run_id=run_id,
                trace_store=store,
                node_name=name,
                graph_name=self.name,
                end_run=False,
                turn_index_offset=state["turn_offset"],
            )
            runtime["last_result"] = last_result
            if store is not None and run_id is not None:
                turn_offset = _next_turn_offset(store, run_id)
            else:
                turn_offset = state["turn_offset"] + 1
            return {"payload": last_result.output, "turn_offset": turn_offset}

        return node


def _next_turn_offset(store: TraceStore, run_id: str) -> int:
    turns = store.get_turns(run_id)
    if not turns:
        return 0
    return max(turn["turn_index"] for turn in turns) + 1

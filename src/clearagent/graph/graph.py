import time

from clearagent.agent import Agent, merge_usage
from clearagent.providers.base import Usage
from clearagent.storage.sqlite import SQLiteTraceStore
from clearagent.storage.protocol import TraceStore
from clearagent.trace_lifecycle import latency_ms
from clearagent.types import ExecutedToolCall, RunResult


class AgentGraph:
    """A bounded, linear sequence of named agents sharing one traced run."""

    def __init__(self, name: str):
        self.name = name
        self.nodes: dict[str, Agent] = {}
        self.edges: dict[str, str] = {}
        self.entrypoint: str | None = None

    def add_node(self, agent: Agent) -> "AgentGraph":
        """Register an agent node by its unique agent name."""
        if agent.name in self.nodes:
            raise ValueError(f"Graph already contains a node named {agent.name!r}.")
        self.nodes[agent.name] = agent
        return self

    def add_edge(self, from_node: str, to_node: str) -> "AgentGraph":
        """Connect one registered node to the next node in the linear flow."""
        self.edges[from_node] = to_node
        return self

    def set_entrypoint(self, node_name: str) -> "AgentGraph":
        """Select the first registered node to execute."""
        self.entrypoint = node_name
        return self

    def run(
        self,
        input: str,
        *,
        trace: bool | None = None,
        trace_store: TraceStore | None = None,
        max_nodes: int | None = None,
    ) -> RunResult:
        """Execute the linear graph and aggregate output, tools, usage, and tracing."""
        entrypoint = self.entrypoint
        if entrypoint is None:
            raise ValueError("Graph entrypoint is not set.")
        order = self._execution_order(max_nodes=max_nodes)
        first_agent = self.nodes[entrypoint]
        should_trace = first_agent.trace if trace is None else trace
        store = (
            trace_store
            if trace_store is not None
            else (first_agent._store() if should_trace else None)
        )
        run_id = (
            store.start_run(
                agent_name=first_agent.name,
                graph_name=self.name,
                root_input=input,
            )
            if store is not None
            else None
        )
        started = time.monotonic()
        turn_offset = 0
        graph_input = input
        last_result: RunResult | None = None
        all_tool_calls: list[ExecutedToolCall] = []
        usage: Usage | None = None

        try:
            for current in order:
                agent = self.nodes[current]
                last_result = agent.run(
                    graph_input,
                    trace=should_trace,
                    run_id=run_id,
                    trace_store=store,
                    node_name=current,
                    graph_name=self.name,
                    end_run=False,
                    turn_index_offset=turn_offset,
                )
                if store is not None and run_id:
                    turn_offset = _next_turn_offset(store, run_id)
                graph_input = last_result.output
                all_tool_calls.extend(last_result.tool_calls)
                usage = merge_usage(usage, last_result.usage)
        except Exception as exc:
            if store is not None and run_id:
                store.end_run(
                    run_id,
                    status="error",
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                    latency_ms=latency_ms(started),
                    prompt_tokens=usage.prompt_tokens if usage else None,
                    completion_tokens=usage.completion_tokens if usage else None,
                    cost_usd=usage.cost_usd if usage else None,
                )
            raise

        output = last_result.output if last_result else ""
        graph_latency_ms = latency_ms(started)
        if store is not None and run_id:
            store.end_run(
                run_id,
                final_output=output,
                latency_ms=graph_latency_ms,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                cost_usd=usage.cost_usd if usage else None,
            )
        return RunResult(
            output=output,
            run_id=run_id,
            trace_db_path=store.path if isinstance(store, SQLiteTraceStore) else None,
            trace_store=store,
            tool_calls=all_tool_calls,
            usage=usage,
            cost_usd=usage.cost_usd if usage else None,
            latency_ms=graph_latency_ms,
            structured_output=last_result.structured_output if last_result else None,
        )

    def _execution_order(self, *, max_nodes: int | None) -> list[str]:
        if self.entrypoint not in self.nodes:
            raise ValueError(f"Graph entrypoint {self.entrypoint!r} is not a registered node.")
        limit = max_nodes if max_nodes is not None else len(self.nodes)
        if limit < 1:
            raise ValueError("max_nodes must be at least 1.")
        order: list[str] = []
        visited: set[str] = set()
        current: str | None = self.entrypoint
        while current is not None:
            if current not in self.nodes:
                raise ValueError(f"Graph edge targets unknown node {current!r}.")
            if current in visited:
                raise ValueError(f"Graph contains a cycle at node {current!r}.")
            if len(order) >= limit:
                raise ValueError(f"Graph exceeded max_nodes={limit}.")
            visited.add(current)
            order.append(current)
            current = self.edges.get(current)
        return order


def _next_turn_offset(store: TraceStore, run_id: str) -> int:
    turns = store.get_turns(run_id)
    if not turns:
        return 0
    return max(turn["turn_index"] for turn in turns) + 1

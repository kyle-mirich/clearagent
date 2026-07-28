from clearagent import create_agent
from clearagent.graph import AgentGraph
from clearagent.providers import FakeProvider, ProviderResponse


planner = create_agent(
    name="planner",
    model="openai:gpt-4.1-mini",
    system_prompt="Plan the response.",
    provider=FakeProvider([ProviderResponse.fake_text("Plan: answer directly.")]),
)

writer = create_agent(
    name="writer",
    model="openai:gpt-4.1-mini",
    system_prompt="Write the final response.",
    provider=FakeProvider([ProviderResponse.fake_text("Here is the final response.")]),
)

graph = (
    AgentGraph("planner_writer")
    .add_node(planner)
    .add_node(writer)
    .add_edge("planner", "writer")
    .set_entrypoint("planner")
)


if __name__ == "__main__":
    print(graph.run("Draft a refund policy response.").output)

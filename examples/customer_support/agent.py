from clearagent import create_agent, tool
from clearagent.providers import FakeProvider, ProviderResponse, ToolCall


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "status": "shipped", "eta": "Friday"}


agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status and refund questions.",
    tools=[lookup_order],
    provider=FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(
                    id="call_lookup_order", name="lookup_order", arguments={"order_id": "A123"}
                )
            ),
            ProviderResponse.fake_text("Order A123 has shipped and arrives Friday."),
        ]
    ),
)


if __name__ == "__main__":
    print(agent.run("Where is order A123?").output)

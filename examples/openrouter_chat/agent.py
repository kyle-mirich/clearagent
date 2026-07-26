from clearagent import create_agent


agent = create_agent(
    name="openrouter_chat",
    model="openrouter:openai/gpt-4.1-mini",
    system_prompt=(
        "You are a concise ClearAgent demo assistant. Use markdown when it helps the answer scan."
    ),
    max_turns=1,
)

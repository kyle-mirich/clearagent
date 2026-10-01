import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from clearagent import Agent
from clearagent.runtime.providers.langchain_provider import LangchainChatProvider
from clearagent.storage.sqlite import SQLiteTraceStore


def test_text_stream_trace_does_not_invent_token_usage(tmp_path):
    provider = LangchainChatProvider(
        provider_name="openai",
        chat_model=GenericFakeChatModel(
            messages=iter([
                AIMessage(
                    content="done",
                    usage_metadata={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                )
            ])
        ),
    )
    store = SQLiteTraceStore(tmp_path / "trace.sqlite")
    agent = Agent(name="stream", model="openai:test-model", provider=provider, trace_store=store)

    assert "".join(agent.stream_text("hi")) == "done"

    run = store.list_runs()[0]
    call = store.get_model_call_for_turn(run["id"], 0)
    response = json.loads(call["response_json"])
    assert response["usage"] is None
    assert response["raw"].get("usage_known", False) is False

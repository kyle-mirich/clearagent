from pathlib import Path

import clearagent
from clearagent import Build, Settings, Store, create_agent, tool
from clearagent import models
from clearagent.runtime.providers.base import FakeProvider, ProviderResponse


def test_public_imports_work_for_a_library_consumer(tmp_path):
    agent = create_agent(
        name="consumer", model="openai:test", trace_db_path=tmp_path / "traces.sqlite",
        provider=FakeProvider([ProviderResponse.fake_text("consumer works")]),
    )
    assert agent.run("hello").output == "consumer works"
    assert (tmp_path / "traces.sqlite").exists()
    assert Build and Store and tool
    assert Path(clearagent.__file__).with_name("py.typed").is_file()


def test_engine_settings_do_not_require_studio_infrastructure(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    settings = Settings(_env_file=None)
    assert settings.task_model
    assert not {"auth_mode", "qdrant_api_key", "session_builds_per_day"} & Settings.model_fields.keys()


def test_studio_http_schemas_are_not_part_of_the_engine():
    for name in ("ProjectCreate", "RunCreate", "ChatStreamCreate", "WebsiteScrapeCreate"):
        assert not hasattr(models, name)

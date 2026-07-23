from tests.integration.test_openrouter_live_eval import _run_live_openrouter_eval


def test_live_openrouter_eval_requires_key_and_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("CLEARAGENT_RUN_LIVE", raising=False)

    assert _run_live_openrouter_eval() is False

    monkeypatch.setenv("CLEARAGENT_RUN_LIVE", "1")

    assert _run_live_openrouter_eval() is True


def test_live_openrouter_eval_stays_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("CLEARAGENT_RUN_LIVE", "1")

    assert _run_live_openrouter_eval() is False

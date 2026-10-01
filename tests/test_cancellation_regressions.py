import pytest

from clearagent.builds import pipeline
from clearagent.store import Store


def _queued_run(store):
    project = store.create_project(
        owner_id="owner", goal="Build an agent that answers clearly.", name="Cancellation test", settings={},
    )
    return store.create_run(
        owner_id="owner", project_id=project.id, idempotency_key="cancel-regression",
        budget_profile="quick", seed=7,
    )[0]


@pytest.mark.parametrize("cancel_during_write", [False, True])
def test_cancel_is_preserved_when_inflight_generation_fails(tmp_path, monkeypatch, cancel_during_write):
    store = Store(f"sqlite:///{tmp_path / 'cancel.sqlite'}")
    run = _queued_run(store)
    original_update = store.update_run

    def cancel():
        original_update(run.id, status="canceled", stage="canceled", completed_at="2026-10-01T00:00:00Z")
        store.add_event(run_id=run.id, event_type="run_canceled", stage="canceled", message="Canceled.")

    def fail_generation(**_kwargs):
        if not cancel_during_write:
            cancel()
        raise RuntimeError("Provider failed after cancellation")

    def update_run(run_id, **fields):
        if cancel_during_write and fields.get("status") == "failed":
            cancel()
        return original_update(run_id, **fields)

    monkeypatch.setattr(pipeline, "generate_synthetic_examples", fail_generation)
    monkeypatch.setattr(store, "update_run", update_run)
    pipeline.run_improvement_pipeline(store, run.id, pipeline.PipelineSettings(deterministic_mode=True))

    final = store.get_run(run.id)
    assert final.status == "canceled"
    assert final.stage == "canceled"
    assert final.error is None
    assert final.completed_at == "2026-10-01T00:00:00Z"
    assert [event.type for event in store.list_events(run.id)][-1] == "run_canceled"
    assert "run_failed" not in [event.type for event in store.list_events(run.id)]


def test_active_generation_failure_is_still_persisted_and_raised(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'failed.sqlite'}")
    run = _queued_run(store)

    def fail_generation(**_kwargs):
        raise RuntimeError("Generation failed")

    monkeypatch.setattr(pipeline, "generate_synthetic_examples", fail_generation)
    with pytest.raises(RuntimeError, match="Generation failed"):
        pipeline.run_improvement_pipeline(store, run.id, pipeline.PipelineSettings(deterministic_mode=True))
    final = store.get_run(run.id)
    assert final.status == "failed"
    assert final.error == {"type": "RuntimeError", "message": "Generation failed"}
    assert store.list_events(run.id)[-1].type == "run_failed"

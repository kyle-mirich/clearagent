from __future__ import annotations

import os
from uuid import uuid4

import pytest

from clearagent.store import Store


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for the PostgreSQL store contract",
)


def test_store_persists_a_build_run_and_first_event_in_postgres():
    store = Store(TEST_DATABASE_URL)
    owner_id = f"public-test-{uuid4().hex}"
    project = store.create_project(
        owner_id=owner_id,
        name="Postgres engine test",
        goal="Build a locally verified agent using the public engine store.",
        settings={},
    )
    run, created = store.create_run(
        owner_id=owner_id,
        project_id=project.id,
        idempotency_key=uuid4().hex,
        budget_profile="quick",
        seed=7,
    )

    assert created is True
    assert store.get_run(run.id, owner_id=owner_id).status == "queued"
    assert [event.sequence for event in store.list_events(run.id)] == [1]

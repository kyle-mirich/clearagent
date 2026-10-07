from concurrent.futures import ThreadPoolExecutor
import os
import threading
from uuid import uuid4

import pytest

from clearagent.store import RateLimitError, Store


@pytest.fixture(params=["sqlite", "postgres"])
def rate_store(request, tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'rates.sqlite'}"
    if request.param == "postgres":
        database_url = os.getenv("TEST_DATABASE_URL", "")
        if not database_url:
            pytest.skip("TEST_DATABASE_URL is required for PostgreSQL rate limit tests")
    monkeypatch.setattr("clearagent.store.time.time", lambda: 1_800_000_001)
    store = Store(database_url)
    yield store, uuid4().hex
    store.close()


@pytest.mark.parametrize("limited_bucket", ["a", "b"])
def test_rejection_rolls_back_every_bucket(rate_store, limited_bucket):
    store, key = rate_store
    limits = tuple((key, bucket, 1 if bucket == limited_bucket else 4, 60)
                   for bucket in ("a", "b"))
    store.consume_rate_limits(rate_limits=limits)
    for _ in range(3):
        with pytest.raises(RateLimitError) as rejected:
            store.consume_rate_limits(rate_limits=limits)
        assert rejected.value.rate_key == key
        assert rejected.value.bucket == limited_bucket
        assert rejected.value.retry_after == 59
    with store.connect() as db:
        rows = db.execute(
            "SELECT request_count FROM api_rate_limits WHERE rate_key=? ORDER BY bucket",
            (key,),
        ).fetchall()
    assert [row["request_count"] for row in rows] == [1, 1]


def test_concurrent_admission_preserves_all_limits(rate_store):
    store, key = rate_store
    barrier = threading.Barrier(12)

    def consume(number):
        limits = (
            (f"{key}:global", "chat", 4, 60),
            (f"{key}:session-{number % 2}", "chat", 2, 60),
        )
        barrier.wait(timeout=10)
        try:
            store.consume_rate_limits(rate_limits=limits if number % 2 else limits[::-1])
            return True
        except RateLimitError:
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        accepted = list(executor.map(consume, range(12)))
    assert sum(accepted) == 4
    with store.connect() as db:
        rows = db.execute(
            "SELECT request_count FROM api_rate_limits WHERE rate_key LIKE ? ORDER BY rate_key",
            (f"{key}:%",),
        ).fetchall()
    assert [row["request_count"] for row in rows] == [4, 2, 2]


def test_new_window_restores_admission(rate_store, monkeypatch):
    store, key = rate_store
    limits = ((key, "chat", 1, 60),)
    store.consume_rate_limits(rate_limits=limits)
    with pytest.raises(RateLimitError):
        store.consume_rate_limits(rate_limits=limits)
    monkeypatch.setattr("clearagent.store.time.time", lambda: 1_800_000_061)
    store.consume_rate_limits(rate_limits=limits)

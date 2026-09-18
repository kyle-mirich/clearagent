"""Exercise connection ownership against a disposable local PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
import os
import threading

import pytest

from clearagent.store import Store


@pytest.fixture
def pooled_store():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for pool integration tests")
    if "test" not in url:
        pytest.fail("Use a dedicated test database")
    store = Store(url, postgres_pool_size=2)
    try:
        yield store
    finally:
        store.close()


def test_pool_reuses_connection_and_rolls_back_failed_transaction(pooled_store):
    with pooled_store.connect() as db:
        pid = db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
        db.execute("CREATE TEMP TABLE pool_rollback_test (value INTEGER)")

    with pytest.raises(RuntimeError, match="abort"):
        with pooled_store.connect() as db:
            db.execute("INSERT INTO pool_rollback_test VALUES (1)")
            raise RuntimeError("abort transaction")

    with pooled_store.connect() as db:
        assert db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"] == pid
        assert db.execute("SELECT COUNT(*) AS n FROM pool_rollback_test").fetchone()["n"] == 0
        assert db.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "10s"


def test_pool_bounds_connections_under_concurrent_load(pooled_store):
    barrier = threading.Barrier(8)

    def query(_):
        barrier.wait(timeout=5)
        with pooled_store.connect() as db:
            return db.execute("SELECT pg_backend_pid() AS pid, pg_sleep(0.02)").fetchone()["pid"]

    with ThreadPoolExecutor(max_workers=8) as workers:
        pids = list(workers.map(query, range(8)))
    assert 1 <= len(set(pids)) <= 2


def test_pool_replaces_a_closed_connection(pooled_store):
    assert pooled_store._pool is not None
    connection = pooled_store._pool.getconn()
    pid = connection.info.backend_pid
    connection.close()
    pooled_store._pool.putconn(connection)
    with pooled_store.connect() as db:
        assert db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"] != pid


def test_pool_close_prevents_further_borrowing(pooled_store):
    from psycopg_pool import PoolClosed

    pooled_store.close()
    with pytest.raises(PoolClosed):
        pooled_store.ping()


def test_pool_is_closed_when_initialization_fails(monkeypatch):
    from unittest.mock import Mock

    pool = Mock()
    monkeypatch.setattr("clearagent.store.ConnectionPool", Mock(return_value=pool))
    monkeypatch.setattr(Store, "initialize", Mock(side_effect=RuntimeError("migration failed")))
    with pytest.raises(RuntimeError, match="migration failed"):
        Store("postgresql:///unused", postgres_pool_size=2)
    pool.close.assert_called_once()


def test_sqlite_does_not_allocate_a_postgres_pool(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'pool.sqlite'}", postgres_pool_size=2)
    assert store._pool is None
    store.ping()
    store.close()

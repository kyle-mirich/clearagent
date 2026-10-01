from unittest.mock import Mock

import pytest

from clearagent.store import Store


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
@pytest.mark.parametrize("rollback_fails", [False, True])
def test_setup_failure_closes_direct_connection_and_preserves_error(monkeypatch, tmp_path, dialect, rollback_fails):
    connection = Mock()
    original = RuntimeError("connection configuration failed")
    connection.execute.side_effect = original
    if rollback_fails:
        connection.rollback.side_effect = ConnectionError("connection lost during rollback")
    if dialect == "sqlite":
        monkeypatch.setattr("clearagent.store.sqlite3.connect", lambda path: connection)
        url = f"sqlite:///{tmp_path / 'setup.sqlite'}"
    else:
        monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: connection)
        url = "postgresql:///unused_test"
    store = Store(url, auto_migrate=False)
    with pytest.raises(RuntimeError, match="connection configuration failed") as error:
        with store.connect():
            pytest.fail("Setup failure must never yield a usable database")
    assert error.value is original
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()

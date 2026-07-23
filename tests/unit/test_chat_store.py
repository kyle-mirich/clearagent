import sqlite3

import pytest

from clearagent.chat.store import ChatStore


def test_chat_store_persists_sessions_and_messages(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite")

    session = store.create_session(agent_name="support_agent", title="Order question")
    store.add_message(session.id, role="user", content="Where is order A123?")
    store.add_message(session.id, role="assistant", content="It shipped.")

    sessions = store.list_sessions()
    messages = store.list_messages(session.id)

    assert sessions[0].id == session.id
    assert sessions[0].title == "Order question"
    assert sessions[0].agent_name == "support_agent"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].content == "It shipped."


def test_chat_store_updates_session_title_from_first_user_message(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite")

    session = store.create_session(agent_name="agent")
    store.add_message(session.id, role="user", content="Summarize this markdown document")

    updated = store.get_session(session.id)

    assert updated is not None
    assert updated.title == "Summarize this markdown document"


def test_chat_store_normalizes_explicit_session_title(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite")

    session = store.create_session(agent_name="agent", title="  Order   question   ")

    assert session.title == "Order question"


def test_chat_store_falls_back_to_new_chat_for_blank_explicit_title(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite")

    session = store.create_session(agent_name="agent", title="   ")

    assert session.title == "New chat"


def test_chat_store_closes_connections_after_context_exit(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite")

    with store.connect() as db:
        db.execute("SELECT 1")

    try:
        db.execute("SELECT 1")
    except sqlite3.ProgrammingError as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("connection should be closed")


def test_chat_store_upgrades_legacy_schema_with_missing_columns(tmp_path):
    db_path = tmp_path / "legacy-chat.sqlite"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE chat_sessions (
              id TEXT PRIMARY KEY,
              agent_name TEXT NOT NULL,
              title TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE chat_messages (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL
            );
            PRAGMA user_version = 0;
            """
        )

    store = ChatStore(db_path)
    session = store.create_session(agent_name="agent")
    message = store.add_message(session.id, role="user", content="Hello")

    with store.connect() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        row = db.execute(
            "SELECT updated_at FROM chat_sessions WHERE id=?", (session.id,)
        ).fetchone()

    assert version == 1
    assert row["updated_at"] is not None
    assert message.created_at is not None


def test_chat_store_rejects_unknown_message_role_before_persisting(tmp_path):
    db_path = tmp_path / "chat.sqlite"
    store = ChatStore(db_path)
    session = store.create_session(agent_name="agent")

    with pytest.raises(ValueError, match="Unsupported chat message role"):
        store.add_message(session.id, role="invalid", content="Oops")

    assert store.list_messages(session.id) == []

from contextlib import contextmanager
from collections.abc import Iterator
import sqlite3
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel


DEFAULT_CHAT_DB = Path(".clearagent/chat.sqlite")
CHAT_SCHEMA_VERSION = 2
ChatRole = Literal["user", "assistant", "system"]
CHAT_ROLES: set[str] = {"user", "assistant", "system"}

CHAT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_sessions (
  id TEXT PRIMARY KEY,
  agent_name TEXT NOT NULL,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  activity_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES chat_sessions(id)
);
"""

CHAT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
  ON chat_sessions(updated_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
  ON chat_messages(session_id, created_at);
"""

CHAT_COLUMNS = {
    "chat_sessions": {
        "id": "TEXT PRIMARY KEY",
        "agent_name": "TEXT NOT NULL DEFAULT ''",
        "title": "TEXT NOT NULL DEFAULT 'New chat'",
        "created_at": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
        "activity_order": "INTEGER NOT NULL DEFAULT 0",
    },
    "chat_messages": {
        "id": "TEXT PRIMARY KEY",
        "session_id": "TEXT NOT NULL DEFAULT ''",
        "role": "TEXT NOT NULL DEFAULT 'user'",
        "content": "TEXT NOT NULL DEFAULT ''",
        "created_at": "TEXT NOT NULL DEFAULT ''",
    },
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ChatSession(BaseModel):
    id: str
    agent_name: str
    title: str
    created_at: str
    updated_at: str


class ChatMessage(BaseModel):
    id: str
    session_id: str
    role: ChatRole
    content: str
    created_at: str


class ChatStore:
    """Local SQLite persistence for chat sessions and messages."""

    def __init__(self, path: str | Path = DEFAULT_CHAT_DB):
        self.path = Path(path)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(CHAT_SCHEMA_SQL)
            _ensure_columns(db, CHAT_COLUMNS)
            db.executescript(CHAT_INDEX_SQL)
            db.execute(f"PRAGMA user_version = {CHAT_SCHEMA_VERSION}")

    def create_session(self, *, agent_name: str, title: str = "New chat") -> ChatSession:
        session_id = _id("chat")
        now = _now()
        normalized_title = _title_from_message(title)
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO chat_sessions
                (id, agent_name, title, created_at, updated_at, activity_order)
                VALUES (?, ?, ?, ?, ?,
                    (SELECT COALESCE(MAX(activity_order), 0) + 1 FROM chat_sessions))
                """,
                (session_id, agent_name, normalized_title, now, now),
            )
        session = self.get_session(session_id)
        if session is None:
            raise RuntimeError("Failed to create chat session.")
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        return ChatSession(**dict(row)) if row else None

    def list_sessions(self) -> list[ChatSession]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM chat_sessions
                ORDER BY activity_order DESC, updated_at DESC, created_at DESC, rowid DESC
                """
            ).fetchall()
        return [ChatSession(**dict(row)) for row in rows]

    def add_message(self, session_id: str, *, role: ChatRole, content: str) -> ChatMessage:
        if role not in CHAT_ROLES:
            raise ValueError(
                "Unsupported chat message role "
                f"{role!r}. Expected one of: assistant, system, user."
            )
        if self.get_session(session_id) is None:
            raise ValueError(f"Unknown chat session {session_id!r}.")

        message_id = _id("msg")
        now = _now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO chat_messages (id, session_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, now),
            )
            db.execute(
                """
                UPDATE chat_sessions
                SET updated_at=?, activity_order=(
                    SELECT COALESCE(MAX(activity_order), 0) + 1 FROM chat_sessions
                )
                WHERE id=?
                """,
                (now, session_id),
            )
            if role == "user":
                current = db.execute(
                    "SELECT title FROM chat_sessions WHERE id=?", (session_id,)
                ).fetchone()
                if current and current["title"] == "New chat":
                    db.execute(
                        "UPDATE chat_sessions SET title=? WHERE id=?",
                        (_title_from_message(content), session_id),
                    )

        message = self.get_message(message_id)
        if message is None:
            raise RuntimeError("Failed to create chat message.")
        return message

    def get_message(self, message_id: str) -> ChatMessage | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chat_messages WHERE id=?", (message_id,)).fetchone()
        return ChatMessage(**dict(row)) if row else None

    def list_messages(self, session_id: str) -> list[ChatMessage]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id=?
                ORDER BY rowid ASC
                """,
                (session_id,),
            ).fetchall()
        return [ChatMessage(**dict(row)) for row in rows]


def _title_from_message(content: str) -> str:
    title = " ".join(content.split())
    if len(title) > 64:
        return f"{title[:61]}..."
    return title or "New chat"


def _ensure_columns(
    db: sqlite3.Connection,
    table_columns: dict[str, dict[str, str]],
) -> None:
    for table_name, columns in table_columns.items():
        existing = {
            row["name"]
            for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, definition in columns.items():
            if column_name not in existing:
                db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

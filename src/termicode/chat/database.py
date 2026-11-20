"""SQLite persistence engine for TermiCode AI conversation sessions and memory."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from termicode.providers.redaction import redact_secrets


class SessionDatabaseError(Exception):
    """Base exception for session database errors."""


class SessionNotFoundError(SessionDatabaseError):
    """Raised when an operation references a non-existent session ID."""


class SessionDatabase:
    """Manages SQLite storage for multi-turn chat sessions, messages, and memory."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a configured SQLite connection with foreign keys enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Initialize database schema tables and indexes."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    step_logs TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);"
            )

    def create_session(
        self,
        session_id: str | None = None,
        title: str = "New Session",
        set_active: bool = True,
    ) -> str:
        """Create a new conversation session record.

        Args:
            session_id: Optional custom session ID. Generates 'sess_<8 hex chars>' if omitted.
            title: Human-readable title or topic summary.
            set_active: If True, marks this session as the active session.

        Returns:
            The created session ID.
        """
        sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC).isoformat()

        with self._get_connection() as conn:
            if set_active:
                conn.execute("UPDATE sessions SET is_active = 0;")

            conn.execute(
                """
                INSERT INTO sessions (id, title, summary, created_at, updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (sid, title, None, now, now, 1 if set_active else 0),
            )

        return sid

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve metadata for a specific session."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id, title, summary, created_at, updated_at, is_active "
                "FROM sessions WHERE id = ?;",
                (session_id,),
            ).fetchone()

            if not row:
                return None

            count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?;",
                (session_id,),
            ).fetchone()
            msg_count = count_row["cnt"] if count_row else 0

            return {
                "id": row["id"],
                "title": row["title"],
                "summary": row["summary"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "is_active": bool(row["is_active"]),
                "message_count": msg_count,
            }

    def get_active_session(self) -> dict[str, Any] | None:
        """Retrieve metadata for the currently active session in this workspace."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id, title, summary, created_at, updated_at, is_active "
                "FROM sessions WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 1;"
            ).fetchone()

            if not row:
                return None

            return self.get_session(row["id"])

    def set_active_session(self, session_id: str) -> bool:
        """Switch active session pointer to target session ID.

        Returns:
            True if session exists and is now active; False if session was not found.
        """
        with self._get_connection() as conn:
            exists = conn.execute("SELECT 1 FROM sessions WHERE id = ?;", (session_id,)).fetchone()
            if not exists:
                return False

            conn.execute("UPDATE sessions SET is_active = 0;")
            conn.execute("UPDATE sessions SET is_active = 1 WHERE id = ?;", (session_id,))
            return True

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all conversation sessions ordered by most recently updated first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.title, s.summary, s.created_at, s.updated_at, s.is_active,
                       COUNT(m.id) as message_count
                FROM sessions s
                LEFT JOIN messages m ON s.id = m.session_id
                GROUP BY s.id
                ORDER BY s.updated_at DESC;
                """
            ).fetchall()

            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "summary": r["summary"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "is_active": bool(r["is_active"]),
                    "message_count": r["message_count"],
                }
                for r in rows
            ]

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its associated messages.

        If the deleted session was active, marks the latest remaining session as active.

        Returns:
            True if deleted; False if session did not exist.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT is_active FROM sessions WHERE id = ?;", (session_id,)
            ).fetchone()
            if not row:
                return False

            was_active = bool(row["is_active"])
            conn.execute("DELETE FROM messages WHERE session_id = ?;", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?;", (session_id,))

            if was_active:
                latest = conn.execute(
                    "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1;"
                ).fetchone()
                if latest:
                    conn.execute("UPDATE sessions SET is_active = 1 WHERE id = ?;", (latest["id"],))

            return True

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        timestamp: str | None = None,
        step_logs: list[dict[str, Any]] | None = None,
    ) -> int:
        """Insert a message turn into the database with mandatory secret redaction.

        Guarantees that sensitive credentials (API keys, tokens) are NEVER stored in plaintext.
        """
        # 1. Enforce strict secret redaction
        clean_content = redact_secrets(content)
        serialized_steps = None
        if step_logs:
            raw_steps_json = json.dumps(step_logs)
            serialized_steps = redact_secrets(raw_steps_json)

        now = timestamp or datetime.now(UTC).isoformat()

        with self._get_connection() as conn:
            # Verify session exists
            sess = conn.execute(
                "SELECT title FROM sessions WHERE id = ?;", (session_id,)
            ).fetchone()
            if not sess:
                raise SessionNotFoundError(
                    f"Cannot add message to non-existent session '{session_id}'"
                )

            # Auto-title session from first user query if still named 'New Session'
            if role == "user" and sess["title"] == "New Session":
                words = clean_content.strip().split("\n")[0]
                new_title = words[:45] + ("..." if len(words) > 45 else "")
                conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ?;",
                    (new_title or "Conversation", session_id),
                )

            cursor = conn.execute(
                """
                INSERT INTO messages (session_id, role, content, timestamp, step_logs)
                VALUES (?, ?, ?, ?, ?);
                """,
                (session_id, role, clean_content, now, serialized_steps),
            )
            msg_id = cursor.lastrowid or 0

            # Update session modified timestamp
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?;", (now, session_id))

        return msg_id

    def get_messages(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve messages for a session in chronological order."""
        with self._get_connection() as conn:
            query = (
                "SELECT id, role, content, timestamp, step_logs FROM messages "
                "WHERE session_id = ? ORDER BY id ASC"
            )
            if limit is not None:
                query += f" LIMIT {int(limit)}"

            rows = conn.execute(query, (session_id,)).fetchall()

            results: list[dict[str, Any]] = []
            for r in rows:
                steps = None
                if r["step_logs"]:
                    try:
                        steps = json.loads(r["step_logs"])
                    except Exception:
                        steps = None

                results.append(
                    {
                        "id": r["id"],
                        "role": r["role"],
                        "content": r["content"],
                        "timestamp": r["timestamp"],
                        "step_logs": steps or [],
                    }
                )

            return results

    def get_message_count(self, session_id: str) -> int:
        """Count total messages currently stored for a session."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?;", (session_id,)
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def update_summary(self, session_id: str, summary: str) -> None:
        """Update the condensed memory summary for pruned historical turns."""
        clean_summary = redact_secrets(summary)
        now = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?;",
                (clean_summary, now, session_id),
            )

    def prune_older_messages(self, session_id: str, keep_last: int) -> list[dict[str, Any]]:
        """Prune messages exceeding keep_last limit, returning pruned records for summarization.

        Args:
            session_id: Target session ID.
            keep_last: Number of most recent messages to retain in active window.

        Returns:
            List of pruned message dicts.
        """
        all_msgs = self.get_messages(session_id)
        if len(all_msgs) <= keep_last:
            return []

        prune_count = len(all_msgs) - keep_last
        to_prune = all_msgs[:prune_count]
        prune_ids = [m["id"] for m in to_prune]

        with self._get_connection() as conn:
            placeholders = ",".join("?" for _ in prune_ids)
            conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders});",
                prune_ids,
            )

        return to_prune

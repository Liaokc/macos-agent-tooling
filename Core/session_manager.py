"""
Session Manager — Python Core
SQLite-backed session storage with async interface.

Architecture: intelligence/macos-agent-tooling-ARCHITECTURE.md
"""

import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from shared_types import DBMessage, Message, Session, SessionSummary


DB_PATH = os.environ.get("AGENT_TOOLING_DB", str(Path.home() / ".macos-agent-tooling" / "sessions.db"))


def _get_sync_connection() -> sqlite3.Connection:
    """Create a synchronous SQLite connection."""
    if DB_PATH and DB_PATH != ":memory:":
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def _run_sync(fn, *args):
    """Run a synchronous SQLite operation in a thread pool."""
    return await asyncio.to_thread(fn, *args)


def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            deleted_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
    """)


class SessionManager:
    """
    Manages chat sessions and messages with SQLite persistence.
    Thread-safe async interface using asyncio.to_thread.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_done = False

    async def _ensure_init(self):
        if not self._init_done:
            conn = _get_sync_connection()
            _init_db(conn)
            conn.close()
            self._init_done = True

    # ─────────────────────────────────────────────────────────────────
    # Session CRUD
    # ─────────────────────────────────────────────────────────────────

    async def create_session(self, model: str, title: str = "New Chat") -> Session:
        """Create a new session. Returns the created Session."""
        await self._ensure_init()

        sid = uuid.uuid4().hex[:16]
        now = int(time.time())

        def _do():
            conn = _get_sync_connection()
            try:
                conn.execute(
                    "INSERT INTO sessions (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, title, model, now, now),
                )
                conn.commit()
            finally:
                conn.close()
            return Session(id=sid, title=title, model=model, created_at=now, updated_at=now)

        return await _run_sync(_do)

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID. Returns None if not found or deleted."""
        await self._ensure_init()

        def _do() -> Session | None:
            conn = _get_sync_connection()
            try:
                row = conn.execute(
                    "SELECT id, title, model, created_at, updated_at, deleted_at FROM sessions WHERE id = ? AND deleted_at IS NULL",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return None
                return Session(
                    id=row[0],
                    title=row[1],
                    model=row[2],
                    created_at=row[3],
                    updated_at=row[4],
                    deleted_at=row[5],
                )
            finally:
                conn.close()

        return await _run_sync(_do)

    async def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        """List all active sessions, newest first."""
        await self._ensure_init()

        def _do() -> list[SessionSummary]:
            conn = _get_sync_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT s.id, s.title, s.model, s.created_at, s.updated_at,
                           COUNT(m.id) as message_count
                    FROM sessions s
                    LEFT JOIN messages m ON m.session_id = s.id
                    WHERE s.deleted_at IS NULL
                    GROUP BY s.id
                    ORDER BY s.updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [
                    SessionSummary(
                        id=row[0],
                        title=row[1],
                        model=row[2],
                        created_at=row[3],
                        updated_at=row[4],
                        message_count=row[5],
                    )
                    for row in rows
                ]
            finally:
                conn.close()

        return await _run_sync(_do)

    async def update_session(self, session_id: str, title: str | None = None) -> bool:
        """Update session title. Returns True if updated."""
        await self._ensure_init()

        def _do() -> bool:
            conn = _get_sync_connection()
            try:
                now = int(time.time())
                if title is not None:
                    n = conn.execute(
                        "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                        (title, now, session_id),
                    ).rowcount
                else:
                    n = conn.execute(
                        "UPDATE sessions SET updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                        (now, session_id),
                    ).rowcount
                conn.commit()
                return n > 0
            finally:
                conn.close()

        return await _run_sync(_do)

    async def delete_session(self, session_id: str) -> bool:
        """Soft-delete a session. Returns True if deleted."""
        await self._ensure_init()

        def _do() -> bool:
            conn = _get_sync_connection()
            try:
                now = int(time.time())
                n = conn.execute(
                    "UPDATE sessions SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (now, now, session_id),
                ).rowcount
                conn.commit()
                return n > 0
            finally:
                conn.close()

        return await _run_sync(_do)

    # ─────────────────────────────────────────────────────────────────
    # Messages
    # ─────────────────────────────────────────────────────────────────

    async def add_message(
        self, session_id: str, role: str, content: str
    ) -> DBMessage:
        """Add a message to a session. Also updates session.updated_at."""
        await self._ensure_init()

        mid = uuid.uuid4().hex[:16]
        now = int(time.time())

        def _do() -> DBMessage:
            conn = _get_sync_connection()
            try:
                msg = DBMessage(id=mid, session_id=session_id, role=role, content=content, created_at=now)
                conn.execute(
                    "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                    (mid, session_id, role, content, now),
                )
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
                conn.commit()
                return msg
            finally:
                conn.close()

        return await _run_sync(_do)

    async def get_messages(self, session_id: str) -> list[DBMessage]:
        """Get all messages for a session, ordered by created_at."""
        await self._ensure_init()

        def _do() -> list[DBMessage]:
            conn = _get_sync_connection()
            try:
                rows = conn.execute(
                    "SELECT id, session_id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                    (session_id,),
                ).fetchall()
                return [DBMessage(id=r[0], session_id=r[1], role=r[2], content=r[3], created_at=r[4]) for r in rows]
            finally:
                conn.close()

        return await _run_sync(_do)

    async def delete_message(self, message_id: str) -> bool:
        """Delete a specific message."""
        await self._ensure_init()

        def _do() -> bool:
            conn = _get_sync_connection()
            try:
                n = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,)).rowcount
                conn.commit()
                return n > 0
            finally:
                conn.close()

        return await _run_sync(_do)

    # ─────────────────────────────────────────────────────────────────
    # Export
    # ─────────────────────────────────────────────────────────────────

    async def export_session(self, session_id: str, path: str) -> bool:
        """Export a session as JSON to the given path. Returns True on success."""
        await self._ensure_init()

        def _do() -> bool:
            conn = _get_sync_connection()
            try:
                row = conn.execute(
                    "SELECT id, title, model, created_at, updated_at FROM sessions WHERE id = ? AND deleted_at IS NULL",
                    (session_id,),
                ).fetchone()
                if not row:
                    return False

                messages = conn.execute(
                    "SELECT id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                    (session_id,),
                ).fetchall()

                export_data = {
                    "session": {
                        "id": row[0],
                        "title": row[1],
                        "model": row[2],
                        "created_at": row[3],
                        "updated_at": row[4],
                    },
                    "messages": [
                        {"id": m[0], "role": m[1], "content": m[2], "created_at": m[3]}
                        for m in messages
                    ],
                }

                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(export_data, f, ensure_ascii=False, indent=2)
                return True
            finally:
                conn.close()

        return await _run_sync(_do)


# ─────────────────────────────────────────────────────────────────
# CLI interface for subprocess IPC
# ─────────────────────────────────────────────────────────────────

async def run_cli():
    """
    CLI entry point for subprocess IPC.
    Reads JSON commands from stdin, writes JSON responses to stdout.
    """
    manager = SessionManager()

    while True:
        try:
            line = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, input), timeout=300.0
            )
        except (TimeoutError, EOFError):
            break

        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"ok": False, "error": "Invalid JSON"}), flush=True)
            continue

        cmd = req.get("cmd", "")
        args = req.get("args", {})
        request_id = req.get("request_id", "")

        try:
            if cmd == "create_session":
                session = await manager.create_session(
                    model=args.get("model", "llama3"),
                    title=args.get("title", "New Chat"),
                )
                resp = {"ok": True, "data": session.to_dict(), "request_id": request_id}

            elif cmd == "get_session":
                session = await manager.get_session(args.get("session_id", ""))
                resp = {"ok": True, "data": session.to_dict() if session else None, "request_id": request_id}

            elif cmd == "list_sessions":
                sessions = await manager.list_sessions(limit=args.get("limit", 50))
                resp = {"ok": True, "data": [s.to_dict() for s in sessions], "request_id": request_id}

            elif cmd == "update_session":
                ok = await manager.update_session(
                    session_id=args.get("session_id", ""),
                    title=args.get("title"),
                )
                resp = {"ok": True, "data": {"updated": ok}, "request_id": request_id}

            elif cmd == "delete_session":
                ok = await manager.delete_session(args.get("session_id", ""))
                resp = {"ok": True, "data": {"deleted": ok}, "request_id": request_id}

            elif cmd == "add_message":
                msg = await manager.add_message(
                    session_id=args.get("session_id", ""),
                    role=args.get("role", "user"),
                    content=args.get("content", ""),
                )
                resp = {"ok": True, "data": msg.to_dict(), "request_id": request_id}

            elif cmd == "get_messages":
                msgs = await manager.get_messages(args.get("session_id", ""))
                resp = {"ok": True, "data": [m.to_dict() for m in msgs], "request_id": request_id}

            elif cmd == "export_session":
                ok = await manager.export_session(
                    session_id=args.get("session_id", ""),
                    path=args.get("path", ""),
                )
                resp = {"ok": True, "data": {"exported": ok}, "request_id": request_id}

            else:
                resp = {"ok": False, "error": f"Unknown command: {cmd}", "request_id": request_id}

        except Exception as e:
            resp = {"ok": False, "error": str(e), "request_id": request_id}

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    asyncio.run(run_cli())

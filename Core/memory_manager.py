"""
Memory Manager — macOS Agent Tooling Phase 2
Three-layer memory: Working (SessionManager), Episodic (SQLite FTS5), Semantic (embedding).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

# Embedding model (lazy-loaded)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


@dataclass
class MemoryEntry:
    """Single memory entry."""
    id: str
    content: str
    memory_type: str  # "semantic" | "episodic"
    session_id: str | None
    importance: float  # 0.0–1.0
    created_at: float
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None  # semantic only

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
            "session_id": self.session_id,
            "importance": self.importance,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class SearchResult:
    """Search result with relevance score."""
    entry: MemoryEntry
    score: float  # higher = more relevant


class MemoryManager:
    """
    Three-layer memory manager.

    - Working Memory: managed by SessionManager (session history)
    - Episodic Memory: SQLite FTS5 BM25 full-text search
    - Semantic Memory: embedding-based vector similarity (cosine)

    All storage is local SQLite — no external services required.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.path.join(
            os.path.expanduser("~/.macos-agent-tooling"), "memory.db"
        )
        self._embedding_model = None  # lazy-loaded
        self._init_done = False

    # ─── Embedding ────────────────────────────────────────────────────────────

    async def _get_embedding(self, texts: list[str]) -> list[list[float]]:
        """Generate normalized embedding vectors for texts (cosine-similarity equivalent)."""
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer(EMBEDDING_MODEL)

        # Encode with normalization → cosine similarity = dot product
        embeddings = self._embedding_model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()

    # ─── Database Init ────────────────────────────────────────────────────────

    async def _ensure_init(self):
        if not self._init_done:
            await asyncio.to_thread(self._init_db_sync)
            self._init_done = True

    def _init_db_sync(self):
        """Synchronous DB initialization (runs in thread)."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # ── Episodic Memory (FTS5) ─────────────────────────────────────────────
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS episodic_memories (
                id          TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                session_id  TEXT,
                importance  REAL DEFAULT 0.5,
                created_at  REAL NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
                content,
                session_id,
                content=episodic_memories,
                content_rowid=rowid
            );

            CREATE TRIGGER IF NOT EXISTS episodic_ai
            AFTER INSERT ON episodic_memories BEGIN
                INSERT INTO episodic_fts(rowid, content, session_id)
                VALUES (new.rowid, new.content, new.session_id);
            END;

            CREATE TRIGGER IF NOT EXISTS episodic_ad
            AFTER DELETE ON episodic_memories BEGIN
                INSERT INTO episodic_fts(episodic_fts, rowid, content, session_id)
                VALUES('delete', old.rowid, old.content, old.session_id);
            END;

            CREATE TRIGGER IF NOT EXISTS episodic_au
            AFTER UPDATE ON episodic_memories BEGIN
                INSERT INTO episodic_fts(episodic_fts, rowid, content, session_id)
                VALUES('delete', old.rowid, old.content, old.session_id);
                INSERT INTO episodic_fts(rowid, content, session_id)
                VALUES (new.rowid, new.content, new.session_id);
            END;
        """)

        # ── Semantic Memory (embedding stored as JSON in TEXT) ────────────────
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS semantic_memories (
                id          TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                embedding   TEXT NOT NULL,  -- JSON-encoded float array
                importance  REAL DEFAULT 0.5,
                created_at  REAL NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_semantic_importance
            ON semantic_memories(importance DESC);

            CREATE INDEX IF NOT EXISTS idx_semantic_created
            ON semantic_memories(created_at DESC);
        """)

        conn.commit()
        conn.close()

    # ─── Semantic Memory ───────────────────────────────────────────────────────

    async def add_semantic_memory(
        self,
        content: str,
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> str:
        """Add a semantic (vector-indexed) memory. Returns memory ID."""
        await self._ensure_init()
        mid = uuid.uuid4().hex[:16]
        now = time.time()

        embeddings = await self._get_embedding([content])
        emb_str = json.dumps(embeddings[0])

        def _do():
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT INTO semantic_memories
                   (id, content, embedding, importance, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (mid, content, emb_str, importance, now, json.dumps(metadata or {})),
            )
            conn.commit()
            conn.close()

        await asyncio.to_thread(_do)
        return mid

    async def search_semantic(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Search semantic memories by vector similarity."""
        await self._ensure_init()
        query_emb = await self._get_embedding([query])
        query_vec = query_emb[0]

        def _do() -> list[SearchResult]:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                """SELECT id, content, embedding, importance, created_at, metadata
                   FROM semantic_memories"""
            ).fetchall()
            conn.close()

            results = []
            for row in rows:
                stored_vec: list[float] = json.loads(row[2])
                # Cosine similarity (vectors are already L2-normalized)
                score = sum(a * b for a, b in zip(query_vec, stored_vec))
                results.append(SearchResult(
                    entry=MemoryEntry(
                        id=row[0],
                        content=row[1],
                        memory_type="semantic",
                        session_id=None,
                        importance=row[3],
                        created_at=row[4],
                        metadata=json.loads(row[5]),
                    ),
                    score=score,
                ))

            results.sort(key=lambda x: x.score, reverse=True)
            return results[:top_k]

        return await asyncio.to_thread(_do)

    # ─── Episodic Memory ───────────────────────────────────────────────────────

    async def add_episodic_memory(
        self,
        content: str,
        session_id: str | None = None,
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> str:
        """Add an episodic (FTS-indexed) memory. Returns memory ID."""
        await self._ensure_init()
        mid = uuid.uuid4().hex[:16]
        now = time.time()

        def _do():
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT INTO episodic_memories
                   (id, content, session_id, importance, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (mid, content, session_id, importance, now, json.dumps(metadata or {})),
            )
            conn.commit()
            conn.close()

        await asyncio.to_thread(_do)
        return mid

    async def search_episodic(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Full-text search episodic memories using FTS5 BM25."""
        await self._ensure_init()

        def _do() -> list[SearchResult]:
            conn = sqlite3.connect(self.db_path)
            try:
                rows = conn.execute("""
                    SELECT m.id, m.content, m.session_id, m.importance,
                           m.created_at, m.metadata,
                           bm25(episodic_fts) AS rank
                    FROM episodic_fts f
                    JOIN episodic_memories m ON f.rowid = m.rowid
                    WHERE episodic_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, top_k)).fetchall()
            except sqlite3.OperationalError:
                # FTS table not ready yet
                rows = []
            conn.close()

            return [
                SearchResult(
                    entry=MemoryEntry(
                        id=row[0],
                        content=row[1],
                        memory_type="episodic",
                        session_id=row[2],
                        importance=row[3],
                        created_at=row[4],
                        metadata=json.loads(row[5]),
                    ),
                    score=-row[6],  # BM25: more negative = better
                )
                for row in rows
            ]

        return await asyncio.to_thread(_do)

    # ─── Unified Search ────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        top_k: int = 5,
        memory_types: list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Unified search across memory types.
        Queries semantic + episodic in parallel, merges and re-ranks by score.
        """
        await self._ensure_init()
        types = memory_types or ["semantic", "episodic"]
        results: list[SearchResult] = []

        if "semantic" in types:
            semantic_results = await self.search_semantic(query, top_k=top_k)
            results.extend(semantic_results)

        if "episodic" in types:
            episodic_results = await self.search_episodic(query, top_k=top_k)
            results.extend(episodic_results)

        # Deduplicate by ID (keep highest-scoring entry per ID)
        seen: dict[str, SearchResult] = {}
        for r in sorted(results, key=lambda x: x.score, reverse=True):
            if r.entry.id not in seen:
                seen[r.entry.id] = r

        merged = list(seen.values())
        merged.sort(key=lambda x: x.score, reverse=True)
        return merged[:top_k]

    # ─── Session Summary ──────────────────────────────────────────────────────

    async def summarize_session(
        self,
        session_id: str,
        messages: list[dict],
    ) -> str:
        """
        Generate a 2-3 sentence summary of a session's conversation using Ollama.
        Stores the summary in episodic memory.
        """
        from ollama_bridge import OllamaBridge, Message

        if not messages:
            return ""

        # Last 10 messages, truncated to 200 chars each
        conversation_snippet = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')[:200]}"
            for m in messages[-10:]
        )

        bridge = OllamaBridge()
        summary_parts = []

        prompt = (
            "请用 2-3 句话总结以下对话的主题和关键结论：\n\n"
            + conversation_snippet
        )

        try:
            async for token in bridge.chat(
                [Message(role="user", content=prompt)],
                model="llama3",
            ):
                summary_parts.append(token)
        finally:
            await bridge.close()

        summary = "".join(summary_parts).strip()
        if summary:
            await self.add_episodic_memory(
                content=f"Session {session_id} summary: {summary}",
                session_id=session_id,
                importance=0.7,
                metadata={"type": "session_summary"},
            )
        return summary

    # ─── Pruning ──────────────────────────────────────────────────────────────

    async def prune_old_memories(self, cutoff_days: int = 30) -> int:
        """Delete low-importance memories older than cutoff_days. Returns count deleted."""
        await self._ensure_init()
        cutoff = time.time() - cutoff_days * 86400

        def _do() -> int:
            conn = sqlite3.connect(self.db_path)
            c = conn.execute(
                """DELETE FROM episodic_memories
                   WHERE created_at < ? AND importance < 0.5""",
                (cutoff,),
            )
            d = conn.execute(
                """DELETE FROM semantic_memories
                   WHERE created_at < ? AND importance < 0.5""",
                (cutoff,),
            )
            conn.commit()
            conn.close()
            return c.rowcount + d.rowcount

        return await asyncio.to_thread(_do)

    # ─── Count ────────────────────────────────────────────────────────────────

    async def get_counts(self) -> dict[str, int]:
        """Return count of entries in each memory store."""
        await self._ensure_init()

        def _do() -> dict[str, int]:
            conn = sqlite3.connect(self.db_path)
            episodic_count = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories"
            ).fetchone()[0]
            semantic_count = conn.execute(
                "SELECT COUNT(*) FROM semantic_memories"
            ).fetchone()[0]
            conn.close()
            return {"episodic": episodic_count, "semantic": semantic_count}

        return await asyncio.to_thread(_do)

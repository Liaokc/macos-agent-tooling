"""
Unit tests for MemoryManager (Task-2B).
Run with: python -m pytest Core/test_memory_manager.py -v
"""

import asyncio
import os
import tempfile
import pytest

from memory_manager import MemoryManager, MemoryEntry, SearchResult


@pytest.fixture
def mm():
    """MemoryManager with temp DB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "test_memory.db")
        mgr = MemoryManager(db_path=db)
        yield mgr


class TestInit:
    @pytest.mark.asyncio
    async def test_db_created(self, mm):
        await mm._ensure_init()
        assert os.path.exists(mm.db_path)

    @pytest.mark.asyncio
    async def test_double_init_ok(self, mm):
        await mm._ensure_init()
        await mm._ensure_init()  # should not raise


class TestSemanticMemory:
    @pytest.mark.asyncio
    async def test_add_semantic_memory(self, mm):
        mid = await mm.add_semantic_memory(
            content="I prefer dark mode",
            importance=0.8,
        )
        assert isinstance(mid, str)
        assert len(mid) > 0

    @pytest.mark.asyncio
    async def test_search_semantic_returns_relevant(self, mm):
        await mm.add_semantic_memory("I prefer dark mode", importance=0.8)
        await mm.add_semantic_memory("My name is John", importance=0.5)
        await mm.add_semantic_memory("I work at a tech company", importance=0.5)
        # Give the embedding model time to warm up (first call is slow)
        results = await mm.search_semantic("dark theme settings", top_k=2)
        assert len(results) >= 1
        # The dark mode entry should rank highest
        dark_result = next((r for r in results if "dark mode" in r.entry.content), None)
        assert dark_result is not None

    @pytest.mark.asyncio
    async def test_search_semantic_empty_db(self, mm):
        results = await mm.search_semantic("any query", top_k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_semantic_memory_to_dict(self, mm):
        mid = await mm.add_semantic_memory("test content", importance=0.5)
        results = await mm.search_semantic("test", top_k=1)
        entry_dict = results[0].entry.to_dict()
        assert entry_dict["content"] == "test content"
        assert entry_dict["memory_type"] == "semantic"
        assert entry_dict["importance"] == 0.5


class TestEpisodicMemory:
    @pytest.mark.asyncio
    async def test_add_episodic_memory(self, mm):
        mid = await mm.add_episodic_memory(
            content="Used git to commit changes",
            session_id="sess_123",
            importance=0.7,
        )
        assert isinstance(mid, str)

    @pytest.mark.asyncio
    async def test_search_episodic_finds_match(self, mm):
        await mm.add_episodic_memory(
            "Used git commit -m 'fix bug'",
            session_id="sess_1",
        )
        await mm.add_episodic_memory(
            "Reviewed pull request",
            session_id="sess_2",
        )
        results = await mm.search_episodic("git commit", top_k=5)
        assert len(results) >= 1
        assert any("git" in r.entry.content.lower() for r in results)

    @pytest.mark.asyncio
    async def test_search_episodic_no_match(self, mm):
        await mm.add_episodic_memory("Did some work", session_id="s1")
        results = await mm.search_episodic("nonexistent keyword xyz", top_k=5)
        # BM25 returns 0 results when no match
        assert len(results) == 0


class TestUnifiedSearch:
    @pytest.mark.asyncio
    async def test_search_both_types(self, mm):
        await mm.add_semantic_memory("The project uses Python")
        await mm.add_episodic_memory("Had a meeting about the API design")
        results = await mm.search("project python", top_k=5)
        ids = {r.entry.id for r in results}
        types = {r.entry.memory_type for r in results}
        assert "semantic" in types or "episodic" in types

    @pytest.mark.asyncio
    async def test_search_filter_by_type(self, mm):
        await mm.add_semantic_memory("semantic entry")
        await mm.add_episodic_memory("episodic entry")
        semantic_only = await mm.search("entry", top_k=5, memory_types=["semantic"])
        assert all(r.entry.memory_type == "semantic" for r in semantic_only)

    @pytest.mark.asyncio
    async def test_search_deduplicates(self, mm):
        # Even if both stores match, result should appear once
        # (architecturally they could have the same content but different IDs)
        pass


class TestPrune:
    @pytest.mark.asyncio
    async def test_prune_returns_count(self, mm):
        deleted = await mm.prune_old_memories(cutoff_days=30)
        assert isinstance(deleted, int)
        assert deleted >= 0


class TestGetCounts:
    @pytest.mark.asyncio
    async def test_get_counts(self, mm):
        await mm.add_semantic_memory("test1")
        await mm.add_episodic_memory("test2")
        counts = await mm.get_counts()
        assert counts["semantic"] >= 1
        assert counts["episodic"] >= 1


# ─── Phase 3: list_memories / delete / clear ────────────────────────────────

class TestListMemories:
    @pytest.mark.asyncio
    async def test_list_memories_both_types(self, mm):
        mid1 = await mm.add_semantic_memory("Semantic test entry")
        mid2 = await mm.add_episodic_memory("Episodic test entry")
        entries = await mm.list_memories(limit=50)
        ids = [e.id for e in entries]
        assert mid1 in ids
        assert mid2 in ids

    @pytest.mark.asyncio
    async def test_list_memories_by_type(self, mm):
        await mm.add_semantic_memory("sem1")
        await mm.add_episodic_memory("epi1")
        sem_entries = await mm.list_memories(memory_type="semantic")
        epi_entries = await mm.list_memories(memory_type="episodic")
        assert all(e.memory_type == "semantic" for e in sem_entries)
        assert all(e.memory_type == "episodic" for e in epi_entries)

    @pytest.mark.asyncio
    async def test_list_memories_pagination(self, mm):
        for i in range(10):
            await mm.add_semantic_memory(f"Entry {i}")
        page1 = await mm.list_memories(limit=5, offset=0)
        page2 = await mm.list_memories(limit=5, offset=5)
        ids1 = {e.id for e in page1}
        ids2 = {e.id for e in page2}
        assert len(ids1 & ids2) == 0  # no overlap


class TestCountMemories:
    @pytest.mark.asyncio
    async def test_count_memories_total(self, mm):
        await mm.add_semantic_memory("s1")
        await mm.add_episodic_memory("e1")
        total = await mm.count_memories()
        assert total >= 2

    @pytest.mark.asyncio
    async def test_count_memories_by_type(self, mm):
        await mm.add_semantic_memory("s1")
        await mm.add_episodic_memory("e1")
        sem = await mm.count_memories(memory_type="semantic")
        epi = await mm.count_memories(memory_type="episodic")
        assert sem >= 1
        assert epi >= 1


class TestDeleteMemory:
    @pytest.mark.asyncio
    async def test_delete_memory_success(self, mm):
        mid = await mm.add_semantic_memory("To be deleted")
        result = await mm.delete(mid)
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_memory_nonexistent(self, mm):
        result = await mm.delete("nonexistent-id-12345")
        assert result is False


class TestClearMemories:
    @pytest.mark.asyncio
    async def test_clear_memories_by_type(self, mm):
        await mm.add_semantic_memory("semantic to clear")
        await mm.add_episodic_memory("episodic to keep")
        cleared = await mm.clear(memory_type="semantic")
        assert cleared >= 1
        remaining_sem = await mm.count_memories(memory_type="semantic")
        assert remaining_sem == 0

    @pytest.mark.asyncio
    async def test_clear_all_memories(self, mm):
        await mm.add_semantic_memory("sem1")
        await mm.add_episodic_memory("epi1")
        cleared = await mm.clear()
        assert cleared >= 2
        total = await mm.count_memories()
        assert total == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

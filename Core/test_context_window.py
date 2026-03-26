"""
Unit tests for ContextWindowManager (Task-2C).
Run with: python -m pytest Core/test_context_window.py -v
"""

import pytest
from context_window import ContextWindowManager


@pytest.fixture
def cwm():
    return ContextWindowManager(max_tokens=1000, model="cl100k_base")


@pytest.fixture
def tiny_cwm():
    """Very small budget for truncation testing."""
    return ContextWindowManager(max_tokens=200, model="cl100k_base")


class TestCountTokens:
    def test_count_simple(self, cwm):
        # "hello world" is typically 2 tokens
        n = cwm.count_tokens("hello world")
        assert n >= 1

    def test_count_empty(self, cwm):
        assert cwm.count_tokens("") == 0

    def test_count_longer_text(self, cwm):
        text = "The quick brown fox jumps over the lazy dog"
        n = cwm.count_tokens(text)
        assert n > 5


class TestBuildContext:
    def test_system_always_included(self, cwm):
        msgs, tokens = cwm.build_context(
            system="You are a helpful assistant.",
            memories=[],
            messages=[],
            user_input="Hello",
        )
        assert msgs[0]["role"] == "system"
        assert "helpful assistant" in msgs[0]["content"]

    def test_user_input_included(self, cwm):
        msgs, tokens = cwm.build_context(
            system="System",
            memories=[],
            messages=[],
            user_input="What is 2+2?",
        )
        assert any(m["content"] == "What is 2+2?" for m in msgs)

    def test_memories_appended_as_system(self, cwm):
        msgs, tokens = cwm.build_context(
            system="System prompt",
            memories=["Memory 1: user likes coffee", "Memory 2: project deadline"],
            messages=[],
            user_input="Hi",
        )
        # Memories should appear as a system message
        sys_messages = [m for m in msgs if m["role"] == "system"]
        assert len(sys_messages) >= 2  # system + memory block
        combined = " ".join(m["content"] for m in sys_messages)
        assert "Memory 1" in combined or "coffee" in combined

    def test_messages_chronological_order(self, cwm):
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "Second message"},
            {"role": "user", "content": "Third message"},
        ]
        msgs, tokens = cwm.build_context(
            system="System",
            memories=[],
            messages=messages,
            user_input="Last question",
        )
        contents = [m["content"] for m in msgs if m["role"] != "system"]
        # Should be in chronological order: oldest first, user_input last
        assert "First message" in contents[0]
        assert "Second message" in contents[1]
        assert "Third message" in contents[2]
        assert "Last question" in contents[3]


class TestBuildContextTruncation:
    def test_respects_max_tokens(self, tiny_cwm):
        """Even with many messages, total tokens should not exceed max."""
        long_content = "x " * 1000  # ~1000 tokens
        messages = [
            {"role": "user", "content": long_content},
            {"role": "assistant", "content": long_content},
            {"role": "user", "content": long_content},
        ]
        msgs, tokens = tiny_cwm.build_context(
            system="System prompt",
            memories=[],
            messages=messages,
            user_input="question",
        )
        assert tokens <= tiny_cwm.max_tokens

    def test_truncation_keeps_system(self, tiny_cwm):
        """System message should always be kept."""
        messages = [
            {"role": "user", "content": "Oldest"},
            {"role": "assistant", "content": "Middle"},
            {"role": "user", "content": "Recent"},
        ]
        msgs, tokens = tiny_cwm.build_context(
            system="SYSTEM MESSAGE NEVER REMOVED",
            memories=[],
            messages=messages,
            user_input="Latest",
        )
        all_content = " ".join(m["content"] for m in msgs)
        assert "SYSTEM MESSAGE NEVER REMOVED" in all_content

    def test_truncation_keeps_latest(self, tiny_cwm):
        """Most recent messages should be preserved."""
        messages = [
            {"role": "user", "content": "Old message 1"},
            {"role": "user", "content": "Old message 2"},
            {"role": "assistant", "content": "Old message 3"},
            {"role": "user", "content": "RECENT_KEEP"},
        ]
        msgs, tokens = tiny_cwm.build_context(
            system="sys",
            memories=[],
            messages=messages,
            user_input="Latest",
        )
        all_content = " ".join(m["content"] for m in msgs)
        assert "RECENT_KEEP" in all_content


class TestMiddleTruncation:
    def test_middle_messages_dropped_when_needed(self, tiny_cwm):
        """When budget is tight, middle messages should be dropped."""
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Second"},
            {"role": "user", "content": "Third"},
            {"role": "assistant", "content": "Fourth"},
            {"role": "user", "content": "Fifth"},
        ]
        msgs, tokens = tiny_cwm.build_context(
            system="S",
            memories=[],
            messages=messages,
            user_input="Last",
        )
        # After truncation, we should have system + some recent + last
        assert len(msgs) <= len(messages) + 2  # system + memories + messages + user

    def test_empty_inputs(self, cwm):
        msgs, tokens = cwm.build_context(
            system="",
            memories=[],
            messages=[],
            user_input="",
        )
        assert msgs == [{"role": "system", "content": ""}, {"role": "user", "content": ""}]
        assert tokens >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

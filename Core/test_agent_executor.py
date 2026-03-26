"""
Unit tests for AgentExecutor (Task-2D).
Run with: python -m pytest Core/test_agent_executor.py -v
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent_executor import (
    AgentExecutor,
    AgentEventType,
    AgentConfig,
    AgentEvent,
    SYSTEM_PROMPT,
)


@pytest.fixture
def mock_bridge():
    """Mock OllamaBridge with async generator support."""
    return MagicMock()


@pytest.fixture
def mock_memory():
    """Mock MemoryManager."""
    mem = MagicMock()
    mem.search = AsyncMock(return_value=[])
    return mem


@pytest.fixture
def mock_tool_executor():
    """Mock ToolExecutor."""
    tools = MagicMock()
    tools.get_tool_schemas = MagicMock(return_value=[
        {
            "name": "bash",
            "description": "Run bash",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "done",
            "description": "Done",
            "input_schema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    ])
    return tools


@pytest.fixture
def executor(mock_bridge, mock_memory, mock_tool_executor):
    cfg = AgentConfig(max_iterations=5, max_context_tokens=4096)
    return AgentExecutor(
        ollama_bridge=mock_bridge,
        memory_manager=mock_memory,
        tool_executor=mock_tool_executor,
        config=cfg,
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_token_stream(tokens):
    """Create a real async generator yielding tokens."""
    async def stream():
        for t in tokens:
            yield t
    return stream()


# ─── Tool Call Parsing ────────────────────────────────────────────────────────

class TestParseToolCalls:
    """Unit tests for _parse_tool_calls."""

    def test_parses_single_tool_call(self, executor):
        text = '<tool_calls><tool name="bash">{"command": "ls -la"}</tool></tool_calls>'
        calls = executor._parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "bash"
        assert calls[0]["arguments"]["command"] == "ls -la"

    def test_parses_multiple_tool_calls(self, executor):
        text = (
            '<tool_calls>'
            '<tool name="bash">{"command": "ls"}</tool>'
            '<tool name="bash">{"command": "pwd"}</tool>'
            '</tool_calls>'
        )
        calls = executor._parse_tool_calls(text)
        assert len(calls) == 2
        assert calls[0]["name"] == "bash"
        assert calls[1]["name"] == "bash"

    def test_handles_json_parse_failure(self, executor):
        text = '<tool_calls><tool name="bash">not valid json</tool></tool_calls>'
        calls = executor._parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "bash"
        assert calls[0]["arguments"] == {"raw": "not valid json"}

    def test_no_tool_calls_returns_empty(self, executor):
        text = "This is a plain text response with no tools."
        calls = executor._parse_tool_calls(text)
        assert calls == []

    def test_missing_closing_tag_returns_empty(self, executor):
        text = '<tool_calls><tool name="bash">{"command": "ls"}</tool>'
        calls = executor._parse_tool_calls(text)
        assert calls == []

    def test_missing_open_tag_returns_empty(self, executor):
        text = '<tool name="bash">{"command": "ls"}</tool></tool_calls>'
        calls = executor._parse_tool_calls(text)
        assert calls == []


# ─── Execute: No Tool Calls ──────────────────────────────────────────────────

class TestExecuteNoToolCalls:
    """Test when LLM returns plain text (no tool calls)."""

    @pytest.mark.asyncio
    async def test_final_response_yields_done(self, executor, mock_bridge):
        """Plain text response should trigger DONE immediately."""
        # Return a fresh async generator each time bridge.chat() is called
        mock_bridge.chat = lambda *a, **kw: make_token_stream(["This is my ", "final answer."])

        events = []
        async for event in executor.execute("Hello", "session_1"):
            events.append(event)

        types = [e.type for e in events]
        assert AgentEventType.DONE in types
        done_event = next(e for e in events if e.type == AgentEventType.DONE)
        assert "final answer" in done_event.data["response"]

    @pytest.mark.asyncio
    async def test_text_tokens_streamed(self, executor, mock_bridge):
        """TEXT events should contain individual tokens."""
        mock_bridge.chat = lambda *a, **kw: make_token_stream(["hello ", "world"])

        events = []
        async for event in executor.execute("Hi", "session_1"):
            events.append(event)

        text_events = [e for e in events if e.type == AgentEventType.TEXT]
        tokens = [e.data["token"] for e in text_events]
        assert any("hello" in t for t in tokens)
        assert any("world" in t for t in tokens)


# ─── Execute: With Tool Calls ────────────────────────────────────────────────

class TestExecuteWithToolCalls:
    """Test when LLM returns tool calls."""

    @pytest.mark.asyncio
    async def test_tool_call_yields_tool_call_and_result(self, executor, mock_bridge, mock_tool_executor):
        """Tool calls should yield TOOL_CALL then TOOL_RESULT events."""
        mock_tool_executor.execute = AsyncMock(
            return_value=MagicMock(
                tool="bash",
                input_args={"command": "ls"},
                output="file1\nfile2",
                error="",
                success=True,
                to_dict=lambda: {
                    "tool": "bash",
                    "args": {"command": "ls"},
                    "output": "file1\nfile2",
                    "error": "",
                    "success": True,
                },
                to_observation=lambda: "[bash] Output:\nfile1\nfile2",
            )
        )

        call_count = 0

        async def mock_stream(messages, model):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield '<tool_calls><tool name="bash">{"command": "ls"}</tool></tool_calls>'
            else:
                yield "Done with the task."

        mock_bridge.chat = mock_stream

        events = []
        async for event in executor.execute("List files", "session_1"):
            events.append(event)

        types = [e.type for e in events]
        assert AgentEventType.TOOL_CALL in types
        assert AgentEventType.TOOL_RESULT in types

        tool_call_event = next(e for e in events if e.type == AgentEventType.TOOL_CALL)
        assert tool_call_event.data["tool"] == "bash"

    @pytest.mark.asyncio
    async def test_done_tool_stops_loop(self, executor, mock_bridge, mock_tool_executor):
        """Calling done() tool should stop the loop immediately."""
        mock_tool_executor.execute = AsyncMock(
            return_value=MagicMock(
                tool="done",
                input_args={"message": "Task complete!"},
                output="Task complete!",
                error="",
                success=True,
                to_dict=lambda: {
                    "tool": "done",
                    "args": {"message": "Task complete!"},
                    "output": "Task complete!",
                    "error": "",
                    "success": True,
                },
                to_observation=lambda: "[done] Output:\nTask complete!",
            )
        )

        async def mock_stream(messages, model):
            yield '<tool_calls><tool name="done">{"message": "Task complete!"}</tool></tool_calls>'

        mock_bridge.chat = mock_stream

        events = []
        async for event in executor.execute("Done", "session_1"):
            events.append(event)

        types = [e.type for e in events]
        assert AgentEventType.DONE in types
        tool_calls = [e for e in events if e.type == AgentEventType.TOOL_CALL]
        assert len(tool_calls) == 1


# ─── Max Iterations ───────────────────────────────────────────────────────────

class TestMaxIterations:
    """Test max_iterations boundary."""

    @pytest.mark.asyncio
    async def test_max_iterations_error(self, executor, mock_bridge, mock_tool_executor):
        """When max_iterations reached, should yield ERROR."""
        executor.config.max_iterations = 2

        mock_tool_executor.execute = AsyncMock(
            return_value=MagicMock(
                tool="bash",
                input_args={"command": "ls"},
                output="result",
                error="",
                success=True,
                to_dict=lambda: {
                    "tool": "bash", "args": {}, "output": "result", "error": "", "success": True,
                },
                to_observation=lambda: "[bash] Output:\nresult",
            )
        )

        async def mock_stream(messages, model):
            # Always return a tool call to keep looping
            yield '<tool_calls><tool name="bash">{"command": "ls"}</tool></tool_calls>'

        mock_bridge.chat = mock_stream

        events = []
        async for event in executor.execute("Repeat", "session_1"):
            events.append(event)

        types = [e.type for e in events]
        assert AgentEventType.ERROR in types
        err = next(e for e in events if e.type == AgentEventType.ERROR)
        assert "Max iterations" in err.data["message"]


# ─── AgentConfig ─────────────────────────────────────────────────────────────

class TestAgentConfig:
    def test_default_config(self):
        cfg = AgentConfig()
        assert cfg.model == "llama3"
        assert cfg.max_iterations == 10
        assert cfg.max_context_tokens == 8192
        assert cfg.temperature == 0.7

    def test_custom_config(self):
        cfg = AgentConfig(model="llama3.2", max_iterations=5)
        assert cfg.model == "llama3.2"
        assert cfg.max_iterations == 5


# ─── Stop ─────────────────────────────────────────────────────────────────────

class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_flag(self, executor):
        await executor.stop()
        assert executor._stop_requested is True

    @pytest.mark.asyncio
    async def test_get_available_tools(self, executor, mock_tool_executor):
        schemas = executor.get_available_tools()
        assert len(schemas) == 2
        names = {s["name"] for s in schemas}
        assert "bash" in names
        assert "done" in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Agent Executor — macOS Agent Tooling Phase 2
ReAct-style agent loop with streaming output.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator

from shared_types import Message
from ollama_bridge import OllamaBridge
from memory_manager import MemoryManager
from tool_executor import ToolExecutor, ToolResult
from context_window import ContextWindowManager


# ─── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a helpful macOS AI assistant, running locally with access to the user's sandboxed workspace (~/.macos-agent-workspace).

You have access to the following tools. ALWAYS use tools when they can help complete the user's request.

{tool_schemas}

Rules:
1. Use tools when appropriate — don't guess, use read_file/list_dir/bash to explore
2. For file operations, always check existing files before creating new ones
3. bash commands run in a sandboxed workspace — only allowlisted commands work
4. When you have completed the task, call the `done` tool with your final answer
5. If a tool fails, analyze the error and try an alternative approach
6. Be concise — truncate long outputs to 2000 characters
7. If you need to read a file, prefer `read_file` over `bash cat`

Output format for tool calls:
<tool_calls>
<tool name="tool_name">{"arg": "value"}</tool>
</tool_calls>

Output format for final answer:
<tool_calls>
<tool name="done">{"message": "Your final answer here"}</tool>
</tool_calls>"""


# ─── Event Types ─────────────────────────────────────────────────────────────

class AgentEventType(Enum):
    TOOL_CALL = "tool_call"       # LLM requested a tool
    TOOL_RESULT = "tool_result"  # Tool execution result
    TEXT = "text"                # Text token (streaming)
    DONE = "done"                # Agent finished successfully
    ERROR = "error"              # Error occurred


@dataclass
class AgentEvent:
    type: AgentEventType
    data: dict

    def to_dict(self) -> dict:
        return {"type": self.type.value, "data": self.data}


@dataclass
class AgentConfig:
    model: str = "llama3"
    max_iterations: int = 10
    max_context_tokens: int = 8192
    temperature: float = 0.7
    stream: bool = True


# ─── AgentExecutor ───────────────────────────────────────────────────────────

class AgentExecutor:
    """
    ReAct-style agent execution engine.

    Flow:
      1. Build messages (system + memories + history + user input)
      2. Call Ollama (streaming)
      3. Parse response for <tool_calls> blocks
      4. If tool_calls found:
           execute each tool → yield TOOL_RESULT
           append observation → loop back to step 1
         If no tool_calls:
           yield DONE → stop
      5. Stop conditions:
           - LLM returns final response (no tool_calls)
           - done() tool called
           - max_iterations reached
           - error
    """

    def __init__(
        self,
        ollama_bridge: OllamaBridge,
        memory_manager: MemoryManager,
        tool_executor: ToolExecutor,
        context_window: ContextWindowManager | None = None,
        config: AgentConfig | None = None,
    ):
        self.bridge = ollama_bridge
        self.memory = memory_manager
        self.tools = tool_executor
        self.context_window = context_window or ContextWindowManager(
            max_tokens=config.max_context_tokens if config else 8192
        )
        self.config = config or AgentConfig()

        # Session message history
        self._messages: list[Message] = []
        self._stop_requested = False

    # ─── Main Loop ─────────────────────────────────────────────────────────────

    async def execute(
        self,
        user_input: str,
        session_id: str,
        system_override: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """
        Execute the ReAct loop, yielding AgentEvent stream.
        """
        iteration = 0
        self._stop_requested = False

        # Build system prompt with tool schemas
        tool_schemas = self.tools.get_tool_schemas()
        schema_text = "\n".join(
            f"**{s['name']}**: {s['description']}\n"
            f"  args: {json.dumps(s['input_schema']['properties'], indent=4)}"
            for s in tool_schemas
        )
        base_system = system_override or SYSTEM_PROMPT
        system_prompt = base_system.replace("{tool_schemas}", schema_text)

        # Initialise message list
        self._messages = [Message(role="system", content=system_prompt)]

        # Inject relevant memories into system prompt
        try:
            relevant_memories = await self.memory.search(user_input, top_k=5)
            if relevant_memories:
                memory_lines = "\n".join(
                    f"- [{r.entry.memory_type}] {r.entry.content}" for r in relevant_memories
                )
                memory_context = f"\n\nRelevant memories:\n{memory_lines}\n"
                self._messages[0] = Message(
                    role="system",
                    content=self._messages[0].content + memory_context,
                )
        except Exception:
            # Memory retrieval failure — continue without memories
            pass

        # Add user input
        self._messages.append(Message(role="user", content=user_input))

        # ── ReAct Loop ────────────────────────────────────────────────────────
        while iteration < self.config.max_iterations and not self._stop_requested:
            iteration += 1

            # Build context (apply token budget)
            try:
                msg_dicts = [
                    m.to_dict() for m in self._messages
                ]
                filtered_msgs, _ = self.context_window.build_context(
                    system=self._messages[0].content,
                    memories=[],  # already injected
                    messages=msg_dicts[1:],  # skip system (already in context)
                    user_input=user_input,
                )
                # Reconstruct Message objects
                loop_messages = [Message.from_dict(m) for m in filtered_msgs]
            except Exception as e:
                yield AgentEvent(
                    type=AgentEventType.ERROR,
                    data={"message": f"Context build failed: {e}"},
                )
                return

            # Stream LLM response
            response_text = ""
            try:
                async for token in self.bridge.chat(loop_messages, self.config.model):
                    response_text += token
                    yield AgentEvent(type=AgentEventType.TEXT, data={"token": token})
            except Exception as e:
                yield AgentEvent(
                    type=AgentEventType.ERROR,
                    data={"message": f"Ollama error: {e}"},
                )
                return

            if not response_text.strip():
                yield AgentEvent(
                    type=AgentEventType.ERROR,
                    data={"message": "Empty LLM response"},
                )
                return

            # Parse tool calls from response
            tool_calls = self._parse_tool_calls(response_text)

            if not tool_calls:
                # No tools → final response, stop
                self._messages.append(Message(role="assistant", content=response_text))
                yield AgentEvent(
                    type=AgentEventType.DONE,
                    data={"response": response_text},
                )
                return

            # Execute each tool call
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("arguments", {})

                yield AgentEvent(
                    type=AgentEventType.TOOL_CALL,
                    data={"tool": tool_name, "args": tool_args},
                )

                result: ToolResult = await self.tools.execute(tool_name, tool_args)

                yield AgentEvent(
                    type=AgentEventType.TOOL_RESULT,
                    data=result.to_dict(),
                )

                # Append observation to message history
                observation = result.to_observation()
                self._messages.append(Message(
                    role="user",
                    content=f"Tool result: {observation}",
                ))

                # Special handling for done tool
                if tool_name == "done":
                    yield AgentEvent(
                        type=AgentEventType.DONE,
                        data={"response": tool_args.get("message", observation)},
                    )
                    return

        # Max iterations reached
        if iteration >= self.config.max_iterations:
            yield AgentEvent(
                type=AgentEventType.ERROR,
                data={"message": f"Max iterations ({self.config.max_iterations}) reached without final answer"},
            )

    # ─── Tool Call Parsing ───────────────────────────────────────────────────

    def _parse_tool_calls(self, text: str) -> list[dict]:
        """
        Parse <tool_calls>...</tool_calls> blocks from LLM output.
        Supports: <tool name="bash">{"command": "ls"}</tool>

        Returns list of {"name": ..., "arguments": {...}}
        """
        try:
            start = text.find("<tool_calls>")
            end = text.find("</tool_calls>")
            if start == -1 or end == -1:
                return []

            block = text[start + len("<tool_calls>"):end].strip()
            calls: list[dict] = []
            pos = 0

            while True:
                # Find next <tool name="...">
                open_tag = block.find('<tool name="', pos)
                if open_tag == -1:
                    break

                name_start = open_tag + len('<tool name="')
                name_end = block.find('"', name_start)
                tool_name = block[name_start:name_end]

                # Find the closing > of the opening tag
                args_start = block.find(">", name_end) + 1
                close_tag = block.find("</tool>", args_start)
                if close_tag == -1:
                    break

                args_str = block[args_start:close_tag].strip()
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    # Fallback: raw string if JSON parse fails
                    args = {"raw": args_str}

                calls.append({"name": tool_name, "arguments": args})
                pos = close_tag + len("</tool>")

            return calls
        except Exception:
            return []

    # ─── Control ─────────────────────────────────────────────────────────────

    async def stop(self):
        """Request the loop to stop at next iteration."""
        self._stop_requested = True

    # ─── Introspection ────────────────────────────────────────────────────────

    def get_available_tools(self) -> list[dict]:
        """Return tool schemas for external use (e.g., IPC)."""
        return self.tools.get_tool_schemas()

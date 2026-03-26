"""
IPC Layer — Python Core
Subprocess-based IPC bridge for SwiftUI communication.
Swift spawns this as a subprocess and communicates via JSON on stdin/stdout.

Phase 2 Extensions:
  - Tool schemas and execution
  - Memory management (semantic + episodic)
  - Agent executor (ReAct loop, streaming)
"""

import asyncio
import json
import sys
import uuid
from typing import AsyncIterator

# Import core modules
from ollama_bridge import OllamaBridge
from session_manager import SessionManager

# ─────────────────────────────────────────────────────────────────
# Phase 2 module lazy imports (avoid slow startup)
# ─────────────────────────────────────────────────────────────────

_tool_executor = None
_memory_manager = None
_agent_executor = None


def _get_tool_executor():
    global _tool_executor
    if _tool_executor is None:
        from tool_executor import ToolExecutor
        _tool_executor = ToolExecutor()
    return _tool_executor


def _get_memory_manager():
    global _memory_manager
    if _memory_manager is None:
        from memory_manager import MemoryManager
        _memory_manager = MemoryManager()
    return _memory_manager


def _get_agent_executor():
    global _agent_executor
    if _agent_executor is None:
        from agent_executor import AgentExecutor, AgentConfig
        bridge = _get_bridge()
        memory = _get_memory_manager()
        tools = _get_tool_executor()
        _agent_executor = AgentExecutor(
            ollama_bridge=bridge,
            memory_manager=memory,
            tool_executor=tools,
        )
    return _agent_executor


def _get_bridge():
    global bridge
    return bridge


# ─────────────────────────────────────────────────────────────────
# Unified IPC Server
# ─────────────────────────────────────────────────────────────────

session_mgr = SessionManager()


def handle_request_sync(cmd: str, args: dict, request_id: str) -> dict:
    """Synchronous dispatch for non-streaming commands (runs in thread)."""
    async def _run():
        try:
            # ── Phase 1 commands ────────────────────────────────────────────

            if cmd == "ping":
                connected = await bridge._check_connection()
                return {"ok": True, "data": {"connected": connected}, "request_id": request_id}

            elif cmd == "list_models":
                models = await bridge.list_models()
                return {"ok": True, "data": [m.to_dict() for m in models], "request_id": request_id}

            elif cmd == "pull_model":
                model = args.get("model", "")
                await bridge.pull_model(model)
                return {"ok": True, "data": {"status": "done"}, "request_id": request_id}

            elif cmd == "generate":
                prompt = args.get("prompt", "")
                model = args.get("model", "llama3")
                tokens = []
                async for token in bridge.generate(prompt, model):
                    tokens.append(token)
                return {"ok": True, "data": {"content": "".join(tokens)}, "request_id": request_id}

            elif cmd == "chat":
                messages_raw = args.get("messages", [])
                model = args.get("model", "llama3")
                from shared_types import Message
                messages = [Message.from_dict(m) for m in messages_raw]
                tokens = []
                async for token in bridge.chat(messages, model):
                    tokens.append(token)
                return {"ok": True, "data": {"content": "".join(tokens)}, "request_id": request_id}

            elif cmd == "get_stats":
                stats = await bridge.get_stats()
                return {"ok": True, "data": stats.to_dict(), "request_id": request_id}

            elif cmd == "get_metal_utilization":
                util = await bridge.get_metal_utilization()
                return {"ok": True, "data": {"metal_utilization": util}, "request_id": request_id}

            elif cmd == "create_session":
                session = await session_mgr.create_session(
                    model=args.get("model", "llama3"),
                    title=args.get("title", "New Chat"),
                )
                return {"ok": True, "data": session.to_dict(), "request_id": request_id}

            elif cmd == "get_session":
                session = await session_mgr.get_session(args.get("session_id", ""))
                return {"ok": True, "data": session.to_dict() if session else None, "request_id": request_id}

            elif cmd == "list_sessions":
                sessions = await session_mgr.list_sessions(limit=args.get("limit", 50))
                return {"ok": True, "data": [s.to_dict() for s in sessions], "request_id": request_id}

            elif cmd == "update_session":
                ok = await session_mgr.update_session(
                    session_id=args.get("session_id", ""),
                    title=args.get("title"),
                )
                return {"ok": True, "data": {"updated": ok}, "request_id": request_id}

            elif cmd == "delete_session":
                ok = await session_mgr.delete_session(args.get("session_id", ""))
                return {"ok": True, "data": {"deleted": ok}, "request_id": request_id}

            elif cmd == "add_message":
                msg = await session_mgr.add_message(
                    session_id=args.get("session_id", ""),
                    role=args.get("role", "user"),
                    content=args.get("content", ""),
                )
                return {"ok": True, "data": msg.to_dict(), "request_id": request_id}

            elif cmd == "get_messages":
                msgs = await session_mgr.get_messages(args.get("session_id", ""))
                return {"ok": True, "data": [m.to_dict() for m in msgs], "request_id": request_id}

            elif cmd == "export_session":
                ok = await session_mgr.export_session(
                    session_id=args.get("session_id", ""),
                    path=args.get("path", ""),
                )
                return {"ok": True, "data": {"exported": ok}, "request_id": request_id}

            # ── Phase 2 commands ────────────────────────────────────────────

            elif cmd == "get_tools":
                tools = _get_tool_executor()
                schemas = tools.get_tool_schemas()
                return {"ok": True, "data": {"tools": schemas}, "request_id": request_id}

            elif cmd == "memory_search":
                memory_mgr = _get_memory_manager()
                query = args.get("query", "")
                top_k = args.get("top_k", 5)
                memory_types = args.get("types", None)  # ["semantic", "episodic"]
                results = await memory_mgr.search(query, top_k=top_k, memory_types=memory_types)
                return {
                    "ok": True,
                    "data": {
                        "results": [
                            {"entry": r.entry.to_dict(), "score": r.score}
                            for r in results
                        ]
                    },
                    "request_id": request_id,
                }

            elif cmd == "memory_add":
                memory_mgr = _get_memory_manager()
                content = args.get("content", "")
                memory_type = args.get("type", "semantic")
                importance = args.get("importance", 0.5)
                metadata = args.get("metadata", {})
                session_id = args.get("session_id", None)
                if memory_type == "semantic":
                    mid = await memory_mgr.add_semantic_memory(content, importance=importance, metadata=metadata)
                else:
                    mid = await memory_mgr.add_episodic_memory(
                        content, session_id=session_id, importance=importance, metadata=metadata
                    )
                return {"ok": True, "data": {"id": mid}, "request_id": request_id}

            elif cmd == "memory_counts":
                memory_mgr = _get_memory_manager()
                counts = await memory_mgr.get_counts()
                return {"ok": True, "data": counts, "request_id": request_id}

            elif cmd == "agent_execute":
                # Non-streaming agent execution
                agent = _get_agent_executor()
                task = args.get("task", "")
                session_id = args.get("session_id", "")
                model = args.get("model", "llama3")
                # Update model if changed
                from agent_executor import AgentConfig
                agent.config.model = model
                response_parts = []
                async for event in agent.execute(task, session_id):
                    et = event.type.value
                    if et == "done":
                        response_parts.append(event.data.get("response", ""))
                    elif et == "error":
                        response_parts.append(f"[ERROR] {event.data.get('message', 'unknown')}")
                return {
                    "ok": True,
                    "data": {"result": "".join(response_parts)},
                    "request_id": request_id,
                }

            else:
                return {"ok": False, "error": f"Unknown command: {cmd}", "request_id": request_id}

        except Exception as e:
            return {"ok": False, "error": str(e), "request_id": request_id}

    return asyncio.run(_run())


async def handle_chat_stream(args: dict, request_id: str) -> AsyncIterator[str]:
    """Streaming chat response — outputs plain JSON lines."""
    messages_raw = args.get("messages", [])
    model = args.get("model", "llama3")
    from shared_types import Message
    messages = [Message.from_dict(m) for m in messages_raw]
    from ollama_bridge import OllamaError
    try:
        async for token in bridge.chat(messages, model):
            yield json.dumps({"token": token}) + "\n"
        yield json.dumps({"done": True}) + "\n"
    except OllamaError as e:
        yield json.dumps({"error": str(e)}) + "\n"


async def handle_agent_stream(args: dict, request_id: str) -> AsyncIterator[str]:
    """Streaming agent execution — outputs JSON lines per AgentEvent."""
    from agent_executor import AgentExecutor, AgentConfig
    agent = _get_agent_executor()
    task = args.get("task", "")
    session_id = args.get("session_id", "")
    model = args.get("model", "llama3")
    agent.config.model = model

    try:
        async for event in agent.execute(task, session_id):
            yield json.dumps({"event": event.type.value, "data": event.data}) + "\n"
    except Exception as e:
        yield json.dumps({"event": "error", "data": {"message": str(e)}}) + "\n"


async def run_server():
    """
    Main server loop: reads JSON requests from stdin, writes JSON responses to stdout.
    Line-delimited JSON-RPC-like protocol.

    Streaming commands (output JSON lines):
      - _stream: chat streaming
      - _agent_stream: agent execution streaming
    """
    loop = asyncio.get_event_loop()

    while True:
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, sys.stdin.readline), timeout=300.0
            )
        except TimeoutError:
            break
        except EOFError:
            break

        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"ok": False, "error": "Invalid JSON"}), flush=True)
            continue

        cmd = args = request_id = ""
        if isinstance(req, dict):
            cmd = req.get("cmd", "")
            args = req.get("args", {})
            request_id = req.get("request_id", "")
        else:
            cmd = str(req)

        if cmd == "_stream":
            # Chat streaming
            async for chunk in handle_chat_stream(args.get("args", {}), request_id):
                print(chunk, end="", flush=True)
            sys.stdout.close()
        elif cmd == "_agent_stream":
            # Agent streaming
            async for chunk in handle_agent_stream(args.get("args", {}), request_id):
                print(chunk, end="", flush=True)
            sys.stdout.close()
        else:
            resp = handle_request_sync(cmd, args, request_id)
            print(json.dumps(resp), flush=True)

    await bridge.close()


if __name__ == "__main__":
    asyncio.run(run_server())

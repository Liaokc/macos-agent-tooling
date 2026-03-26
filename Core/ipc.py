"""
IPC Layer — Python Core
Subprocess-based IPC bridge for SwiftUI communication.
Swift spawns this as a subprocess and communicates via JSON on stdin/stdout.

Architecture: intelligence/macos-agent-tooling-ARCHITECTURE.md
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
# Unified IPC Server
# ─────────────────────────────────────────────────────────────────

bridge = OllamaBridge()
session_mgr = SessionManager()


def handle_request_sync(cmd: str, args: dict, request_id: str) -> dict:
    """Synchronous dispatch for non-streaming commands (runs in thread)."""
    import asyncio

    async def _run():
        try:
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


async def run_server():
    """
    Main server loop: reads JSON requests from stdin, writes JSON responses to stdout.
    Line-delimited JSON-RPC-like protocol.
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
            stream = args.get("_stream", False)
        else:
            cmd = str(req)

        if cmd == "_stream":
            # Streaming response mode — outputs plain JSON lines, then closes stdout
            stream_args = args.get("args", {})
            async for chunk in handle_chat_stream(stream_args, request_id):
                print(chunk, end="", flush=True)
            sys.stdout.close()  # Close stdout so Swift reader sees EOF
        else:
            resp = handle_request_sync(cmd, args, request_id)
            print(json.dumps(resp), flush=True)

    await bridge.close()


if __name__ == "__main__":
    asyncio.run(run_server())

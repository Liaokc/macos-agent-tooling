"""
Ollama Bridge — Python Core
Handles all communication with the local ollama server.
Uses httpx for async HTTP requests to the Ollama API.

Architecture: intelligence/macos-agent-tooling-ARCHITECTURE.md
"""

import asyncio
import json
import time
import uuid
from typing import AsyncIterator

import httpx

from shared_types import GenerateOptions, HardwareStats, Message, ModelInfo


class OllamaBridge:
    """
    Bridge to local ollama server.
    Handles model listing, downloading, generation, and hardware stats.
    """

    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._stats_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=60.0)
        return self._client

    async def _get_stats_client(self) -> httpx.AsyncClient:
        if self._stats_client is None or self._stats_client.is_closed:
            self._stats_client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        return self._stats_client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if self._stats_client and not self._stats_client.is_closed:
            await self._stats_client.aclose()

    async def _check_connection(self) -> bool:
        """Check if ollama server is reachable."""
        try:
            client = await self._get_client()
            resp = await client.get("/")
            return resp.status_code == 200
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────────────
    # Model Management
    # ─────────────────────────────────────────────────────────────────

    async def list_models(self) -> list[ModelInfo]:
        """
        List all available models from ollama.
        Returns list of ModelInfo sorted by modified_at descending.
        """
        client = await self._get_client()
        resp = await client.get("/api/tags")
        resp.raise_for_status()
        data = resp.json()
        models = []
        for m in data.get("models", []):
            models.append(
                ModelInfo(
                    name=m.get("name", ""),
                    size=m.get("size", 0),
                    modified_at=m.get("modified_at", time.time()),
                    digest=m.get("digest", ""),
                )
            )
        models.sort(key=lambda x: x.modified_at, reverse=True)
        return models

    async def pull_model(self, model: str, progress_cb=None) -> None:
        """
        Pull (download) a model from ollama registry.
        progress_cb: callback(float) — receives progress 0.0–1.0
        Raises: OllamaError on failure
        """
        client = await self._get_client()
        async with client.stream("POST", "/api/pull", json={"name": model}) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                raise OllamaError(f"Pull failed: {resp.status_code} {text}")

            accumulated = 0
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Progress reporting
                total = obj.get("total", 0)
                completed = obj.get("completed", 0)
                if total > 0:
                    progress = completed / total
                    accumulated = progress
                    if progress_cb:
                        await asyncio.to_thread(progress_cb, progress)

                # Done signal
                if obj.get("status") == "success":
                    if progress_cb:
                        await asyncio.to_thread(progress_cb, 1.0)
                    return

    async def delete_model(self, model: str) -> None:
        """Delete a model from local storage."""
        client = await self._get_client()
        resp = await client.delete("/api/delete", json={"name": model})
        resp.raise_for_status()

    # ─────────────────────────────────────────────────────────────────
    # Generation
    # ─────────────────────────────────────────────────────────────────

    async def generate(
        self, prompt: str, model: str, opts: GenerateOptions | None = None
    ) -> AsyncIterator[str]:
        """
        Generate a response (non-chat, single prompt).
        Yields tokens as they arrive.
        """
        client = await self._get_client()
        if opts is None:
            opts = GenerateOptions(model=model, prompt=prompt)
        else:
            opts.prompt = prompt
            opts.model = model

        payload = {
            "model": opts.model,
            "prompt": opts.prompt,
            "system": opts.system,
            "options": {
                "temperature": opts.temperature,
                "top_p": opts.top_p,
                "top_k": opts.top_k,
                "num_predict": opts.num_predict,
            },
            "stream": True,
            "stop": opts.stop,
        }

        resp = await client.post("/api/generate", json=payload)
        resp.raise_for_status()

        async for line in resp.aiter_lines():
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("error"):
                raise OllamaError(f"Generation error: {obj['error']}")

            token = obj.get("response", "")
            if token:
                yield token

            if obj.get("done", False):
                return

    async def chat(
        self, messages: list[Message], model: str
    ) -> AsyncIterator[str]:
        """
        Multi-turn chat.
        Yields tokens as they arrive.
        """
        client = await self._get_client()
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
        }

        resp = await client.post("/api/chat", json=payload)
        resp.raise_for_status()

        async for line in resp.aiter_lines():
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("error"):
                raise OllamaError(f"Chat error: {obj['error']}")

            token = obj.get("message", {}).get("content", "")
            if token:
                yield token

            if obj.get("done", False):
                return

    # ─────────────────────────────────────────────────────────────────
    # Hardware Stats
    # ─────────────────────────────────────────────────────────────────

    async def get_stats(self) -> HardwareStats:
        """
        Get hardware utilization stats.
        Returns CPU, memory, and GPU stats.
        Falls back to 0 values if stats unavailable.
        """
        try:
            client = await self._get_stats_client()
            resp = await client.get("/api/stats")
            resp.raise_for_status()
            data = resp.json()

            # CPU / Memory
            cpu_percent = data.get("cpu_percent", 0.0)
            memory_info = data.get("memory", {})
            memory_used = memory_info.get("used", 0)
            memory_total = memory_info.get("total", 0)

            # GPU stats (if available)
            gpu_stats = data.get("gpu_info", [])

            return HardwareStats(
                cpu_percent=cpu_percent,
                memory_used=memory_used,
                memory_total=memory_total,
                gpu_stats=gpu_stats,
            )
        except Exception:
            # Fallback: return zeros
            return HardwareStats(
                cpu_percent=0.0,
                memory_used=0,
                memory_total=0,
                gpu_stats=[],
            )

    async def get_metal_utilization(self) -> float:
        """
        Get Metal GPU utilization percentage.
        Queries the ollama ps endpoint for GPU info.
        """
        try:
            client = await self._get_stats_client()
            resp = await client.get("/ps")
            resp.raise_for_status()
            data = resp.json()
            # ollama doesn't expose Metal util directly,
            # but gpu_info usually has utilization
            gpu_info = data.get("gpu_info", [])
            if gpu_info:
                return gpu_info[0].get("utilization_percent", 0.0)
            return 0.0
        except Exception:
            return 0.0


class OllamaError(Exception):
    """Raised when ollama returns an error."""

    pass


# ─────────────────────────────────────────────────────────────────
# CLI interface for subprocess IPC
# ─────────────────────────────────────────────────────────────────

async def run_cli():
    """
    CLI entry point: reads JSON-RPC-like commands from stdin,
    writes JSON responses to stdout.
    Used by the Swift IPC subprocess bridge.
    """
    bridge = OllamaBridge()
    loop = asyncio.get_event_loop()

    while True:
        try:
            line = await asyncio.wait_for(loop.run_in_executor(None, input), timeout=300.0)
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
            resp = {"ok": False, "error": "Invalid JSON"}
            print(json.dumps(resp), flush=True)
            continue

        cmd = req.get("cmd", "")
        args = req.get("args", {})
        request_id = req.get("request_id", "")

        try:
            if cmd == "list_models":
                models = await bridge.list_models()
                result = [m.to_dict() for m in models]
                resp = {"ok": True, "data": result, "request_id": request_id}

            elif cmd == "pull_model":
                model = args.get("model", "")
                progress_values = []

                def store_progress(p: float):
                    progress_values.append(p)

                await bridge.pull_model(model, progress_cb=store_progress)
                resp = {"ok": True, "data": {"progress": progress_values[-1] if progress_values else 1.0}, "request_id": request_id}

            elif cmd == "chat":
                messages_raw = args.get("messages", [])
                messages = [Message.from_dict(m) for m in messages_raw]
                model = args.get("model", "llama3")
                tokens = []
                async for token in bridge.chat(messages, model):
                    tokens.append(token)
                resp = {"ok": True, "data": {"content": "".join(tokens)}, "request_id": request_id}

            elif cmd == "generate":
                prompt = args.get("prompt", "")
                model = args.get("model", "llama3")
                tokens = []
                async for token in bridge.generate(prompt, model):
                    tokens.append(token)
                resp = {"ok": True, "data": {"content": "".join(tokens)}, "request_id": request_id}

            elif cmd == "get_stats":
                stats = await bridge.get_stats()
                resp = {"ok": True, "data": stats.to_dict(), "request_id": request_id}

            elif cmd == "ping":
                connected = await bridge._check_connection()
                resp = {"ok": True, "data": {"connected": connected}, "request_id": request_id}

            else:
                resp = {"ok": False, "error": f"Unknown command: {cmd}", "request_id": request_id}

        except Exception as e:
            resp = {"ok": False, "error": str(e), "request_id": request_id}

        print(json.dumps(resp), flush=True)

    await bridge.close()


if __name__ == "__main__":
    asyncio.run(run_cli())

"""
Tool Registry — macOS Agent Tooling Phase 3
Unified tool registration with built-in tools + Phase 3 extensions.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Awaitable

# Re-export ToolResult for convenience
try:
    from tool_executor import ToolResult
except ImportError:
    from typing import Any
    @dataclass
    class ToolResult:
        tool: str
        input_args: dict
        output: str = ""
        error: str = ""
        success: bool = True

        def to_dict(self) -> dict:
            return {
                "tool": self.tool, "args": self.input_args,
                "output": self.output, "error": self.error, "success": self.success,
            }


# ─── Tool Schema Templates ────────────────────────────────────────────────────

WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": "Search the web using DuckDuckGo HTML (no API key required). Returns top results with snippets.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

READ_MULTIPLE_FILES_SCHEMA = {
    "name": "read_multiple_files",
    "description": "Read multiple files from the sandboxed workspace. Each file is read up to 200 lines.",
    "input_schema": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file paths (relative to workspace, max 20 files)",
            },
        },
        "required": ["paths"],
    },
}

HTTP_REQUEST_SCHEMA = {
    "name": "http_request",
    "description": "Perform an HTTP GET or POST request. Only localhost or 127.0.0.1 URLs are allowed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to request (localhost only)"},
            "method": {
                "type": "string",
                "description": "HTTP method: GET or POST (default: GET)",
                "default": "GET",
            },
            "body": {
                "type": "string",
                "description": "Request body for POST (default: empty)",
                "default": "",
            },
        },
        "required": ["url"],
    },
}

OSASCRIPT_SCHEMA = {
    "name": "osascript",
    "description": "Execute an AppleScript script. DANGEROUS — requires explicit user confirmation before execution.",
    "input_schema": {
        "type": "object",
        "properties": {
            "script": {"type": "string", "description": "AppleScript source code to execute"},
        },
        "required": ["script"],
    },
}

TASK_COMPLETION_SCHEMA = {
    "name": "task_completion",
    "description": "Mark a task as complete with a summary. Useful for structuring multi-step tasks.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Summary of what was accomplished"},
            "result": {"type": "string", "description": "Final result or output"},
        },
        "required": ["summary"],
    },
}

CUSTOM_TOOLS_PATH = os.path.expanduser("~/.macos-agent-tooling/custom_tools.json")


# ─── ToolTemplate ─────────────────────────────────────────────────────────────

@dataclass
class ToolTemplate:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[ToolResult]]
    requires_confirmation: bool = False
    enabled: bool = True


# ─── ToolRegistry ─────────────────────────────────────────────────────────────

class ToolRegistry:
    """
    Central tool registry for all available tools.

    Built-in tools from Phase 2: bash, read_file, write_file, list_dir, done
    Phase 3 extensions: web_search, read_multiple_files, http_request, osascript, task_completion
    User-defined tools: loaded from ~/.macos-agent-tooling/custom_tools.json
    """

    def __init__(self, workspace: str = "~/.macos-agent-workspace"):
        self.workspace = os.path.expanduser(workspace)
        self._tools: dict[str, ToolTemplate] = {}
        self._register_builtin()
        self._load_custom_tools()

    # ─── Phase 2 Built-in Tools ─────────────────────────────────────────────

    def _register_builtin(self):
        """Register Phase 2 built-in tools via tool_executor."""
        try:
            from tool_executor import (
                ToolExecutor, BASH_SCHEMA, READ_FILE_SCHEMA,
                WRITE_FILE_SCHEMA, LIST_DIR_SCHEMA, DONE_SCHEMA,
            )
            # Phase 2 built-in tools — wrap ToolExecutor.execute() call
            for schema in [BASH_SCHEMA, READ_FILE_SCHEMA, WRITE_FILE_SCHEMA, LIST_DIR_SCHEMA, DONE_SCHEMA]:
                name = schema["name"]

                def make_handler(tool_name: str):
                    async def handler(**kwargs) -> ToolResult:
                        te = ToolExecutor(workspace=self.workspace)
                        return await te.execute(tool_name, kwargs)
                    return handler

                self._tools[name] = ToolTemplate(
                    name=name,
                    description=schema["description"],
                    input_schema=schema["input_schema"],
                    handler=make_handler(name),
                    requires_confirmation=(name == "bash"),
                    enabled=True,
                )
        except ImportError:
            # tool_executor not available yet — skip Phase 2 tools
            pass

        # Phase 3 tools
        self._tools["web_search"] = ToolTemplate(
            name="web_search",
            description=WEB_SEARCH_SCHEMA["description"],
            input_schema=WEB_SEARCH_SCHEMA["input_schema"],
            handler=self._web_search,
            requires_confirmation=False,
            enabled=True,
        )
        self._tools["read_multiple_files"] = ToolTemplate(
            name="read_multiple_files",
            description=READ_MULTIPLE_FILES_SCHEMA["description"],
            input_schema=READ_MULTIPLE_FILES_SCHEMA["input_schema"],
            handler=self._read_multiple_files,
            requires_confirmation=False,
            enabled=True,
        )
        self._tools["http_request"] = ToolTemplate(
            name="http_request",
            description=HTTP_REQUEST_SCHEMA["description"],
            input_schema=HTTP_REQUEST_SCHEMA["input_schema"],
            handler=self._http_request,
            requires_confirmation=False,
            enabled=True,
        )
        self._tools["osascript"] = ToolTemplate(
            name="osascript",
            description=OSASCRIPT_SCHEMA["description"],
            input_schema=OSASCRIPT_SCHEMA["input_schema"],
            handler=self._osascript,
            requires_confirmation=True,  # dangerous
            enabled=True,
        )
        self._tools["task_completion"] = ToolTemplate(
            name="task_completion",
            description=TASK_COMPLETION_SCHEMA["description"],
            input_schema=TASK_COMPLETION_SCHEMA["input_schema"],
            handler=self._task_completion,
            requires_confirmation=False,
            enabled=True,
        )

    # ─── Phase 3 Tool Implementations ──────────────────────────────────────

    async def _web_search(self, query: str, max_results: int = 5) -> ToolResult:
        """Search DuckDuckGo HTML and extract results."""
        import urllib.parse
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "-L", "--max-time", "10", "--user-agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            html = stdout.decode("utf-8", errors="replace")
            results = self._parse_ddg_results(html, max_results)
            return ToolResult("web_search", {"query": query, "max_results": max_results}, results, success=True)
        except asyncio.TimeoutError:
            return ToolResult("web_search", {"query": query}, "", "Timeout after 15s", success=False)
        except Exception as e:
            return ToolResult("web_search", {"query": query}, "", str(e), success=False)

    def _parse_ddg_results(self, html: str, max_results: int) -> str:
        """Extract title + snippet from DuckDuckGo HTML."""
        results = []
        # Extract <a class="result__a" href="...">Title</a>
        titles = re.findall(r'<a class="result__a"[^>]*>([^<]+)</a>', html)
        # Extract snippet from <a class="result__snippet" href="...">Snippet</a>
        snippets = re.findall(r'<a class="result__snippet"[^>]*>([^<]+)</a>', html)
        for i, (title, snippet) in enumerate(zip(titles, snippets)):
            if i >= max_results:
                break
            results.append(f"{i+1}. {title.strip()}\n   {snippet.strip()}")
        if not results:
            # Fallback: try generic extraction
            clean = re.sub(r'<[^>]+>', ' ', html)
            clean = re.sub(r'\s+', ' ', clean)
            return clean[:500]
        return "\n".join(results)

    async def _read_multiple_files(self, paths: list[str]) -> ToolResult:
        """Read up to 20 files from the sandboxed workspace."""
        contents = []
        for path in paths[:20]:
            safe = self._validate_path(path)
            if safe is None:
                contents.append(f"{path}: PATH_OUTSIDE_WORKSPACE")
                continue
            if not os.path.exists(safe):
                contents.append(f"{path}: FILE_NOT_FOUND")
                continue
            try:
                with open(safe, "r", encoding="utf-8", errors="replace") as f:
                    lines = [f.readline() for _ in range(200)]
                    content = "".join(lines)
                    if len(content) > 50000:
                        content = content[:50000] + "\n... (truncated)"
                    contents.append(f"=== {path} ===\n{content}")
            except Exception as e:
                contents.append(f"{path}: {str(e)}")
        return ToolResult(
            "read_multiple_files", {"paths": paths},
            "\n\n".join(contents), success=True,
        )

    async def _http_request(self, url: str, method: str = "GET", body: str = "") -> ToolResult:
        """HTTP request — only localhost URLs allowed."""
        allowed_prefixes = ["http://localhost", "http://127.0.0.1"]
        if not any(url.startswith(p) for p in allowed_prefixes):
            return ToolResult(
                "http_request", {"url": url, "method": method},
                "", "Only localhost URLs are allowed for security",
                success=False,
            )
        try:
            cmd = ["curl", "-s", "-X", method, url]
            if body:
                cmd += ["-d", body]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            if proc.returncode != 0:
                return ToolResult("http_request", {"url": url, "method": method}, out, err, success=False)
            return ToolResult("http_request", {"url": url, "method": method}, out, err, success=True)
        except asyncio.TimeoutError:
            return ToolResult("http_request", {"url": url}, "", "Timeout after 10s", success=False)
        except Exception as e:
            return ToolResult("http_request", {"url": url}, "", str(e), success=False)

    async def _osascript(self, script: str) -> ToolResult:
        """Execute AppleScript — requires_confirmation is handled by caller."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            return ToolResult(
                "osascript", {"script": script[:100]},
                out, err, success=(proc.returncode == 0),
            )
        except asyncio.TimeoutError:
            return ToolResult("osascript", {"script": script[:100]}, "", "Timeout after 30s", success=False)
        except Exception as e:
            return ToolResult("osascript", {"script": script[:100]}, "", str(e), success=False)

    async def _task_completion(self, summary: str, result: str = "") -> ToolResult:
        """Built-in: mark task complete."""
        output = f"[Task Complete] {summary}"
        if result:
            output += f"\nResult: {result}"
        return ToolResult("task_completion", {"summary": summary, "result": result}, output, success=True)

    # ─── Path Validation ──────────────────────────────────────────────────

    def _validate_path(self, path: str) -> str | None:
        """Ensure path resolves within workspace."""
        abs_workspace = os.path.abspath(self.workspace)
        try:
            abs_path = os.path.abspath(os.path.join(self.workspace, path))
            if not abs_path.startswith(abs_workspace + os.sep) and abs_path != abs_workspace:
                return None
            return abs_path
        except Exception:
            return None

    # ─── User Custom Tools ─────────────────────────────────────────────────

    def _load_custom_tools(self):
        """Load user-defined tools from JSON config."""
        if not os.path.exists(CUSTOM_TOOLS_PATH):
            return
        try:
            with open(CUSTOM_TOOLS_PATH) as f:
                data = json.load(f)
            for tool_def in data.get("custom_tools", []):
                # Custom tool definitions — requires separate handler registration
                # For Phase 3, we store the metadata but require future handler support
                pass
        except Exception:
            pass

    # ─── Public API ────────────────────────────────────────────────────────

    def register(self, template: ToolTemplate):
        """Register a new tool."""
        self._tools[template.name] = template

    def unregister(self, name: str):
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get_schemas(self) -> list[dict]:
        """Return JSON schemas for all enabled tools."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values() if t.enabled
        ]

    def get_tool(self, name: str) -> ToolTemplate | None:
        """Get a tool template by name."""
        return self._tools.get(name)

    def get_confirmation_required(self, name: str) -> bool:
        """Check if a tool requires user confirmation before execution."""
        t = self._tools.get(name)
        return t.requires_confirmation if t else False

    def list_tools(self) -> list[str]:
        """Return list of all registered tool names."""
        return list(self._tools.keys())

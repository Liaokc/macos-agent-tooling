"""
Tool Executor — macOS Agent Tooling Phase 2
Provides sandboxed bash execution and file I/O tools.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass, field
from typing import AsyncIterator

# Allowlisted bash commands (first token must be in this set)
ALLOWED_COMMANDS: set[str] = {
    "ls", "cat", "head", "tail", "grep", "find", "wc", "echo",
    "pwd", "mkdir", "rm", "cp", "mv", "touch", "chmod",
    "git", "python3", "pip3", "curl", "wget",
}

SANDBOX_WORKSPACE = os.path.expanduser("~/.macos-agent-workspace")
MAX_COMMAND_DURATION = 30  # seconds
MAX_OUTPUT_BYTES = 1024 * 1024  # 1 MB per stream


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool: str
    input_args: dict
    output: str = ""
    error: str = ""
    success: bool = True

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "args": self.input_args,
            "output": self.output,
            "error": self.error,
            "success": self.success,
        }

    def to_observation(self) -> str:
        """Convert to an LLM-readable observation string."""
        if self.success:
            # Truncate to 2000 chars to prevent context overflow
            truncated = self.output[:2000] + ("\n... (truncated)" if len(self.output) > 2000 else "")
            return f"[{self.tool}] Output:\n{truncated}"
        return f"[{self.tool}] Error:\n{self.error}"


# ─── Tool Schemas ────────────────────────────────────────────────────────────

BASH_SCHEMA = {
    "name": "bash",
    "description": "Execute a bash command in a sandboxed workspace. "
                   "Only allowlisted commands are permitted.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute (first token must be allowlisted)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30, max: 120)",
                "default": 30,
            },
            "working_dir": {
                "type": "string",
                "description": "Working directory (default: sandbox workspace)",
            },
        },
        "required": ["command"],
    },
}

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": "Read the contents of a file. Path must be within the sandboxed workspace.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path within workspace",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum number of lines to read (default: 500)",
                "default": 500,
            },
        },
        "required": ["path"],
    },
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": "Write content to a file. Path must be within the sandboxed workspace.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path within workspace",
            },
            "content": {
                "type": "string",
                "description": "Content to write",
            },
            "mode": {
                "type": "string",
                "description": "File mode: 'w' (overwrite) or 'a' (append). Default: 'w'",
                "default": "w",
            },
        },
        "required": ["path", "content"],
    },
}

LIST_DIR_SCHEMA = {
    "name": "list_dir",
    "description": "List contents of a directory. Path must be within the sandboxed workspace.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path within workspace (default: '.')",
                "default": ".",
            },
        },
    },
}

DONE_SCHEMA = {
    "name": "done",
    "description": "Mark the task as complete and return the final result to the user.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The final answer or result to present to the user",
            },
        },
        "required": ["message"],
    },
}


class ToolExecutor:
    """
    Unified tool execution engine.

    Built-in tools:
    - bash: Sandboxed bash command execution (allowlist-protected)
    - read_file / write_file / list_dir: Workspace-bounded file operations
    - done: Signal task completion

    Each tool returns a ToolResult with success/output/error fields.
    """

    def __init__(self, workspace: str = SANDBOX_WORKSPACE):
        self.workspace = workspace
        self._tools: dict[str, callable] = {}
        self._ensure_workspace()
        self._register_builtin_tools()

    def _ensure_workspace(self):
        os.makedirs(self.workspace, exist_ok=True)

    def _register_builtin_tools(self):
        self._tools["bash"] = self._exec_bash
        self._tools["read_file"] = self._read_file
        self._tools["write_file"] = self._write_file
        self._tools["list_dir"] = self._list_dir
        self._tools["done"] = self._done

    def get_tool_schemas(self) -> list[dict]:
        """Return JSON schemas for all registered tools (供 LLM 使用)."""
        return [
            BASH_SCHEMA,
            READ_FILE_SCHEMA,
            WRITE_FILE_SCHEMA,
            LIST_DIR_SCHEMA,
            DONE_SCHEMA,
        ]

    # ─── Path Validation ─────────────────────────────────────────

    def _validate_path(self, path: str) -> str | None:
        """
        Validate that a path resolves to within self.workspace.
        Returns absolute path if safe, or None if path traversal detected.
        """
        abs_workspace = os.path.abspath(self.workspace)
        try:
            # Resolve the path relative to workspace
            abs_path = os.path.abspath(os.path.join(self.workspace, path))
            # Must start with workspace prefix (after normalization)
            if not abs_path.startswith(abs_workspace + os.sep) and abs_path != abs_workspace:
                return None
            return abs_path
        except Exception:
            return None

    # ─── bash ─────────────────────────────────────────────────────

    async def _exec_bash(
        self,
        command: str,
        timeout: int = 30,
        working_dir: str | None = None,
    ) -> ToolResult:
        """Execute a bash command with allowlist check and sandbox."""
        if not command.strip():
            return ToolResult("bash", {"command": command}, "", "Empty command", success=False)

        # Parse command to check first token
        try:
            parts = shlex.split(command)
        except ValueError:
            return ToolResult(
                "bash", {"command": command}, "",
                f"Invalid shell syntax in command",
                success=False,
            )

        if not parts:
            return ToolResult("bash", {"command": command}, "", "Empty command", success=False)

        cmd_name = parts[0]
        if cmd_name not in ALLOWED_COMMANDS:
            return ToolResult(
                "bash", {"command": command}, "",
                f"Command '{cmd_name}' not in allowlist. "
                f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}",
                success=False,
            )

        # Enforce timeout ceiling
        timeout = min(timeout, MAX_COMMAND_DURATION)
        work_dir = working_dir or self.workspace

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                limit=MAX_OUTPUT_BYTES,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                return ToolResult(
                    "bash", {"command": command, "timeout": timeout}, "",
                    f"Command timed out after {timeout}s",
                    success=False,
                )

            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                return ToolResult(
                    "bash", {"command": command}, out, err, success=False,
                )

            return ToolResult("bash", {"command": command}, out, err, success=True)

        except Exception as e:
            return ToolResult("bash", {"command": command}, "", str(e), success=False)

    # ─── read_file ────────────────────────────────────────────────

    async def _read_file(self, path: str, max_lines: int = 500) -> ToolResult:
        """Read a file within the sandboxed workspace."""
        safe_path = self._validate_path(path)
        if safe_path is None:
            return ToolResult(
                "read_file", {"path": path}, "",
                "Path is outside the sandboxed workspace",
                success=False,
            )

        if not os.path.exists(safe_path):
            return ToolResult(
                "read_file", {"path": path}, "",
                f"File not found: {path}",
                success=False,
            )

        if not os.path.isfile(safe_path):
            return ToolResult(
                "read_file", {"path": path}, "",
                f"Not a regular file: {path}",
                success=False,
            )

        try:
            with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    lines.append(line)
                content = "".join(lines)

            if len(content) > 100_000:
                content = content[:100_000] + f"\n... (truncated at 100K chars, max_lines={max_lines})"

            return ToolResult("read_file", {"path": path, "max_lines": max_lines}, content, success=True)
        except Exception as e:
            return ToolResult("read_file", {"path": path}, "", str(e), success=False)

    # ─── write_file ──────────────────────────────────────────────

    async def _write_file(self, path: str, content: str, mode: str = "w") -> ToolResult:
        """Write content to a file within the sandboxed workspace."""
        safe_path = self._validate_path(path)
        if safe_path is None:
            return ToolResult(
                "write_file", {"path": path}, "",
                "Path is outside the sandboxed workspace",
                success=False,
            )

        if mode not in ("w", "a"):
            return ToolResult(
                "write_file", {"path": path, "mode": mode}, "",
                f"Invalid mode '{mode}'. Use 'w' or 'a'.",
                success=False,
            )

        try:
            # Create parent directories if needed
            parent = os.path.dirname(safe_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            file_mode = "a" if mode == "a" else "w"
            with open(safe_path, file_mode, encoding="utf-8") as f:
                f.write(content)

            action = "Appended" if mode == "a" else "Wrote"
            return ToolResult(
                "write_file", {"path": path},
                f"{action} {len(content)} chars to {path}",
                success=True,
            )
        except Exception as e:
            return ToolResult("write_file", {"path": path}, "", str(e), success=False)

    # ─── list_dir ─────────────────────────────────────────────────

    async def _list_dir(self, path: str = ".") -> ToolResult:
        """List a directory within the sandboxed workspace."""
        safe_path = self._validate_path(path)
        if safe_path is None:
            return ToolResult(
                "list_dir", {"path": path}, "",
                "Path is outside the sandboxed workspace",
                success=False,
            )

        if not os.path.isdir(safe_path):
            return ToolResult(
                "list_dir", {"path": path}, "",
                f"Not a directory: {path}",
                success=False,
            )

        try:
            entries = os.listdir(safe_path)
            lines = "\n".join(sorted(entries))
            return ToolResult("list_dir", {"path": path}, lines, success=True)
        except Exception as e:
            return ToolResult("list_dir", {"path": path}, "", str(e), success=False)

    # ─── done ────────────────────────────────────────────────────

    async def _done(self, message: str) -> ToolResult:
        """Built-in tool: signal task completion. Always succeeds."""
        return ToolResult("done", {"message": message}, message, success=True)

    # ─── Unified Execute Interface ─────────────────────────────────

    async def execute(self, tool_name: str, args: dict) -> ToolResult:
        """
        Execute the named tool with given args.
        Returns ToolResult.
        Unknown tool → ToolResult with error.
        """
        if tool_name not in self._tools:
            return ToolResult(
                tool_name, args, "",
                f"Unknown tool: {tool_name}. "
                f"Available: {list(self._tools.keys())}",
                success=False,
            )
        try:
            # Filter args to those accepted by the tool
            result = await self._tools[tool_name](**args)
            return result
        except TypeError as e:
            # Wrong arguments passed
            return ToolResult(
                tool_name, args, "",
                f"Invalid arguments for {tool_name}: {e}",
                success=False,
            )
        except Exception as e:
            return ToolResult(tool_name, args, "", str(e), success=False)

"""
ConfigManager — macOS Agent Tooling Phase 3
Persistent JSON configuration for Agent settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

CONFIG_PATH = os.path.expanduser("~/.macos-agent-tooling/config.json")

DEFAULT_SYSTEM_PROMPT = """You are a helpful macOS AI assistant, running locally with full access to the user's workspace.

You have access to the following tools:
{tool_schemas}

Rules:
1. Always use tools when they can help complete the user's request
2. For file operations, prefer reading existing files before writing
3. bash commands run in a sandboxed workspace (~/.macos-agent-workspace)
4. When done, call the done tool with your final answer
5. If a tool fails, analyze the error and try an alternative approach
6. Be concise - only show relevant output, truncate long outputs to 2000 chars"""


# ─── AgentConfig ───────────────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    model: str = "llama3"
    max_iterations: int = 10
    temperature: float = 0.7
    memory_semantic_enabled: bool = True
    memory_episodic_enabled: bool = True
    memory_prune_days: int = 30
    system_prompt: str | None = None  # None = use default
    show_thinking: bool = True
    tool_confirmation: bool = True
    sandbox_workspace: str = "~/.macos-agent-workspace"

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "max_iterations": self.max_iterations,
            "temperature": self.temperature,
            "memory_semantic_enabled": self.memory_semantic_enabled,
            "memory_episodic_enabled": self.memory_episodic_enabled,
            "memory_prune_days": self.memory_prune_days,
            "system_prompt": self.system_prompt,
            "show_thinking": self.show_thinking,
            "tool_confirmation": self.tool_confirmation,
            "sandbox_workspace": self.sandbox_workspace,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        return cls(
            model=d.get("model", "llama3"),
            max_iterations=d.get("max_iterations", 10),
            temperature=d.get("temperature", 0.7),
            memory_semantic_enabled=d.get("memory_semantic_enabled", True),
            memory_episodic_enabled=d.get("memory_episodic_enabled", True),
            memory_prune_days=d.get("memory_prune_days", 30),
            system_prompt=d.get("system_prompt"),
            show_thinking=d.get("show_thinking", True),
            tool_confirmation=d.get("tool_confirmation", True),
            sandbox_workspace=d.get("sandbox_workspace", "~/.macos-agent-workspace"),
        )

    def get_system_prompt(self) -> str:
        return self.system_prompt or DEFAULT_SYSTEM_PROMPT


# ─── ConfigManager ────────────────────────────────────────────────────────────

class ConfigManager:
    """
    Persistent configuration manager backed by JSON.

    Handles:
    - Auto-create config directory and default config on first load
    - Atomic partial updates via update(**kwargs)
    - Corrupted JSON fallback to defaults
    """

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._config: AgentConfig = AgentConfig()
        self._ensure_dir()
        self._load()

    def _ensure_dir(self):
        """Create config directory if needed."""
        dir_path = os.path.dirname(self.path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

    def _load(self):
        """Load config from JSON file, falling back to defaults on error."""
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    data = json.load(f)
                self._config = AgentConfig.from_dict(data)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                # Corrupted or incompatible config — fall back to defaults
                self._config = AgentConfig()

    def save(self):
        """Save current config to JSON file atomically."""
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._config.to_dict(), f, indent=2)
        os.replace(tmp, self.path)  # atomic on POSIX

    def get(self) -> AgentConfig:
        """Return the current config."""
        return self._config

    def update(self, **kwargs) -> AgentConfig:
        """
        Atomically update a subset of config keys.
        Only updates keys that exist in AgentConfig.
        Returns the updated config.
        """
        d = self._config.to_dict()
        for k, v in kwargs.items():
            if k in d:
                d[k] = v
        self._config = AgentConfig.from_dict(d)
        self.save()
        return self._config

    def reset(self):
        """Reset to default config."""
        self._config = AgentConfig()
        self.save()

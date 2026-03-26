"""
Shared data types for macOS Agent Tooling.
These types are used across Swift IPC and Python Core.
"""

from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class ModelInfo:
    name: str
    size: int  # bytes
    modified_at: float  # Unix timestamp
    digest: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "modified_at": self.modified_at,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelInfo":
        return cls(
            name=d["name"],
            size=d.get("size", 0),
            modified_at=d.get("modified_at", time.time()),
            digest=d.get("digest", ""),
        )


@dataclass
class GenerateOptions:
    model: str
    prompt: str = ""
    system: str = ""
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    num_predict: int = 2048
    stop: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt": self.prompt,
            "system": self.system,
            "options": {
                "temperature": self.temperature,
                "top_p": self.top_p,
                "top_k": self.top_k,
                "num_predict": self.num_predict,
            },
            "stop": self.stop,
        }


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(role=d["role"], content=d["content"])


@dataclass
class HardwareStats:
    cpu_percent: float
    memory_used: int  # bytes
    memory_total: int  # bytes
    gpu_stats: list[dict] = field(default_factory=list)  # per-GPU stats

    @property
    def memory_percent(self) -> float:
        if self.memory_total == 0:
            return 0.0
        return (self.memory_used / self.memory_total) * 100

    def to_dict(self) -> dict:
        return {
            "cpu_percent": self.cpu_percent,
            "memory_used": self.memory_used,
            "memory_total": self.memory_total,
            "memory_percent": self.memory_percent,
            "gpu_stats": self.gpu_stats,
        }


@dataclass
class Session:
    id: str
    title: str
    model: str
    created_at: int  # Unix timestamp (seconds)
    updated_at: int  # Unix timestamp (seconds)
    deleted_at: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d["id"],
            title=d["title"],
            model=d["model"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d.get("deleted_at"),
        )


@dataclass
class SessionSummary:
    id: str
    title: str
    model: str
    created_at: int
    updated_at: int
    message_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionSummary":
        return cls(
            id=d["id"],
            title=d["title"],
            model=d["model"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            message_count=d.get("message_count", 0),
        )


@dataclass
class DBMessage:
    id: str
    session_id: str
    role: str
    content: str
    created_at: int  # Unix timestamp (seconds)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DBMessage":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            role=d["role"],
            content=d["content"],
            created_at=d["created_at"],
        )


# IPC Command types
@dataclass
class IPCRequest:
    cmd: str
    args: dict = field(default_factory=dict)
    request_id: str = ""

    def to_dict(self) -> dict:
        return {"cmd": self.cmd, "args": self.args, "request_id": self.request_id}

    @classmethod
    def from_dict(cls, d: dict) -> "IPCRequest":
        return cls(cmd=d["cmd"], args=d.get("args", {}), request_id=d.get("request_id", ""))


@dataclass
class IPCResponse:
    ok: bool
    data: Any = None
    error: str | None = None
    request_id: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "request_id": self.request_id,
        }

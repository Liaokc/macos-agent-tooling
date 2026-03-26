# macOS 本地Agent工具链 — Phase 2 架构设计
> CTO 输出 | 版本: v0.1 | 日期: 2026-03-26

---

## Status: 进行中

## 关联文档
- Phase 1 架构：`intelligence/macos-agent-tooling-ARCHITECTURE.md`
- Builder 任务包：`intelligence/macos-agent-tooling-BUILDER-TASKS-PHASE2.md`

---

## 1. Phase 1 回顾 & Phase 2 起点

### Phase 1 已完成
| 模块 | 状态 | 文件 |
|------|------|------|
| Ollama Bridge | ✅ 完成 | `Core/ollama_bridge.py` |
| Session Manager | ✅ 完成 | `Core/session_manager.py` |
| IPC Layer | ✅ 完成 | `Core/ipc.py` |
| SwiftUI Chat UI | ✅ 完成 | `App/Views/ChatView.swift` |
| Swift ↔ Python Bridge | ✅ 完成 | `App/IPC/AgentBridge.swift` |

### Phase 1 尚未实现（Phase 2 要填补的空缺）
- Agent 执行循环（LLM → 工具调用 → 执行 → 结果）
- 记忆管理层（向量存储 + 上下文注入）
- 工具执行层（bash sandbox、文件操作）
- ReAct 风格的 Agent loop

---

## 2. Phase 2 模块总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SwiftUI App (不变)                                │
│         ChatView ←→ AgentBridge (IPC, 不变)                        │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ JSON-RPC (stdin/stdout, 不变)
┌────────────────────────────────▼────────────────────────────────────┐
│                   Core/Python (Phase 2 新增)                         │
│                                                                      │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │ ToolExecutor  │  │  MemoryManager   │  │  AgentExecutor       │   │
│  │               │←→│  (Phase 2 新增)   │←→│  (Phase 2 新增)       │   │
│  │ - bash sandbox│  │                  │  │                      │   │
│  │ - file I/O    │  │ - SQLite FTS5    │  │ - ReAct loop         │   │
│  │ - web search  │  │ - embeddings     │  │ - 工具调用协议        │   │
│  └───────┬───────┘  │ - 3层记忆架构    │  │ - stream 输出        │   │
│          │          └────────┬─────────┘  └──────────┬───────────┘   │
│          │                   │                        │              │
│          │     ┌─────────────┘                        │              │
│          │     │  (OllamaBridge / SessionManager 复用 Phase 1)       │
│          │     │                                                        │
└──────────┼─────┼──────────────────────────────────────────────────────┘
           │     │
           ▼     ▼
      (Ollama / SQLite — 外部依赖)
```

---

## 3. Tool Executor（工具执行层）

### 3.1 工具调用协议设计

每个工具遵循统一 schema：

```json
{
  "name": "bash",
  "description": "Execute a bash command in a sandboxed environment",
  "input_schema": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "The bash command to execute"
      },
      "timeout": {
        "type": "integer",
        "description": "Timeout in seconds (default: 30)",
        "default": 30
      },
      "working_dir": {
        "type": "string",
        "description": "Working directory (default: user home)"
      }
    },
    "required": ["command"]
  }
}
```

### 3.2 内置工具列表

| 工具名 | 描述 | 安全性 |
|--------|------|--------|
| `bash` | 执行 bash 命令 | ⚠️ sandboxed，权限白名单 |
| `read_file` | 读取文件内容 | 路径必须在 workspace 内 |
| `write_file` | 写入文件内容 | 路径必须在 workspace 内 |
| `list_dir` | 列出目录内容 | 路径必须在 workspace 内 |
| `web_search` | 网页搜索 | 可选，默认关闭 |
| `done` | 标记任务完成，输出最终结果 | 内置 |

### 3.3 Sandboxed Bash 实现

```python
# tool_executor.py

import asyncio
import os
import shlex
from typing import AsyncIterator

# 允许的命令白名单（可配置）
ALLOWED_COMMANDS = {
    "ls", "cat", "head", "tail", "grep", "find", "wc", "echo",
    "pwd", "cd", "mkdir", "rm", "cp", "mv", "touch", "chmod",
    "git", "python3", "pip3", "curl", "wget", "osascript",
}

SANDBOX_WORKSPACE = os.path.expanduser("~/macos-agent-workspace")
MAX_COMMAND_DURATION = 30  # seconds

class ToolResult:
    """工具执行结果"""
    def __init__(self, tool: str, input_args: dict, output: str, error: str = "", success: bool = True):
        self.tool = tool
        self.input_args = input_args
        self.output = output
        self.error = error
        self.success = success

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "args": self.input_args,
            "output": self.output,
            "error": self.error,
            "success": self.success,
        }

    def to_observation(self) -> str:
        """转换为 LLM 可读的 observation string"""
        if self.success:
            return f"[{self.tool}] Output:\n{self.output[:2000]}"  # 截断防止上下文溢出
        return f"[{self.tool}] Error:\n{self.error}"

class ToolExecutor:
    """
    工具执行器。
    - 内置工具：bash, read_file, write_file, list_dir
    - 每个工具返回 ToolResult
    """

    def __init__(self, workspace: str = SANDBOX_WORKSPACE):
        self.workspace = workspace
        self._tools: dict[str, callable] = {}
        self._register_builtin_tools()

    def _register_builtin_tools(self):
        self._tools["bash"] = self._exec_bash
        self._tools["read_file"] = self._read_file
        self._tools["write_file"] = self._write_file
        self._tools["list_dir"] = self._list_dir

    def get_tool_schemas(self) -> list[dict]:
        """返回所有工具的 JSON Schema（供 LLM 使用）"""
        return [
            BASH_SCHEMA, READ_FILE_SCHEMA, WRITE_FILE_SCHEMA, LIST_DIR_SCHEMA
        ]

    # ─── 内置工具实现 ────────────────────────────────────────────

    async def _exec_bash(self, command: str, timeout: int = 30, working_dir: str | None = None) -> ToolResult:
        """执行 bash 命令，带 sandbox"""
        # 安全检查：检查命令是否在白名单
        parts = shlex.split(command)
        if not parts:
            return ToolResult("bash", {"command": command}, "", "Empty command", success=False)

        cmd_name = parts[0]
        if cmd_name not in ALLOWED_COMMANDS:
            return ToolResult(
                "bash", {"command": command}, "",
                f"Command '{cmd_name}' not in allowlist: {ALLOWED_COMMANDS}",
                success=False
            )

        work_dir = working_dir or self.workspace
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                limit=1024 * 1024,  # 1MB stdout/stderr
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(
                    "bash", {"command": command}, "",
                    f"Command timed out after {timeout}s",
                    success=False
                )

            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0 and not err:
                return ToolResult("bash", {"command": command}, out, err, success=False)
            return ToolResult("bash", {"command": command}, out, err, success=True)

        except Exception as e:
            return ToolResult("bash", {"command": command}, "", str(e), success=False)

    def _validate_path(self, path: str) -> str | None:
        """验证路径在 workspace 内。返回绝对路径或 None（不安全）"""
        abs_workspace = os.path.abspath(self.workspace)
        try:
            abs_path = os.path.abspath(os.path.join(self.workspace, path))
            if not abs_path.startswith(abs_workspace):
                return None
            return abs_path
        except Exception:
            return None

    async def _read_file(self, path: str, max_lines: int = 500) -> ToolResult:
        safe_path = self._validate_path(path)
        if safe_path is None:
            return ToolResult("read_file", {"path": path}, "", "Path outside workspace", success=False)

        if not os.path.exists(safe_path):
            return ToolResult("read_file", {"path": path}, "", f"File not found: {path}", success=False)

        try:
            with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
                lines = [f.readline() for _ in range(max_lines)]
                content = "".join(lines)
                if len(content) > 100_000:
                    content = content[:100_000] + f"\n... (truncated at 100K chars)"
            return ToolResult("read_file", {"path": path}, content, success=True)
        except Exception as e:
            return ToolResult("read_file", {"path": path}, "", str(e), success=False)

    async def _write_file(self, path: str, content: str, mode: str = "w") -> ToolResult:
        safe_path = self._validate_path(path)
        if safe_path is None:
            return ToolResult("write_file", {"path": path}, "", "Path outside workspace", success=False)

        try:
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            with open(safe_path, mode, encoding="utf-8") as f:
                f.write(content)
            return ToolResult("write_file", {"path": path}, f"Wrote {len(content)} chars to {path}", success=True)
        except Exception as e:
            return ToolResult("write_file", {"path": path}, "", str(e), success=False)

    async def _list_dir(self, path: str = ".") -> ToolResult:
        safe_path = self._validate_path(path)
        if safe_path is None:
            return ToolResult("list_dir", {"path": path}, "", "Path outside workspace", success=False)

        if not os.path.isdir(safe_path):
            return ToolResult("list_dir", {"path": path}, "", f"Not a directory: {path}", success=False)

        try:
            entries = os.listdir(safe_path)
            lines = "\n".join(sorted(entries))
            return ToolResult("list_dir", {"path": path}, lines, success=True)
        except Exception as e:
            return ToolResult("list_dir", {"path": path}, "", str(e), success=False)

    # ─── 统一执行接口 ─────────────────────────────────────────────

    async def execute(self, tool_name: str, args: dict) -> ToolResult:
        """执行指定工具，返回 ToolResult"""
        if tool_name not in self._tools:
            return ToolResult(
                tool_name, args, "",
                f"Unknown tool: {tool_name}. Available: {list(self._tools.keys())}",
                success=False
            )
        try:
            result = await self._tools[tool_name](**args)
            return result
        except Exception as e:
            return ToolResult(tool_name, args, "", str(e), success=False)
```

---

## 4. Memory Manager（记忆管理层）

### 4.1 三层记忆架构

```
┌─────────────────────────────────────────────────────────────┐
│ Working Memory（工作记忆）                                  │
│ - 当前 session 的消息历史                                   │
│ - 最近 N 条对话（可配置窗口大小，默认 20 条）               │
│ - 存在 SessionManager SQLite 里                            │
└──────────────────────────┬────────────────────────────────┘
                           │ inject
┌──────────────────────────▼────────────────────────────────┐
│ Episodic Memory（情景记忆）                                 │
│ - 过去 session 的摘要 + 完整消息                            │
│ - SQLite FTS5 全文检索（BM25 排序）                        │
│ - 按时间/重要性过滤                                        │
└──────────────────────────┬────────────────────────────────┘
                           │ search (query embedding)
┌──────────────────────────▼────────────────────────────────┐
│ Semantic Memory（语义记忆）                                 │
│ - 持久化知识：用户偏好、项目背景、长期目标                  │
│ - ChromaDB 向量存储（sentence-transformers embeddings）     │
│ - top-k 语义检索结果注入上下文                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 向量 Embedding 方案

- **Embedding 模型**：`all-MiniLM-L6-v2`（本地运行，CPU，约 22M 参数）
- **向量维度**：384
- **不需要外部服务**：所有计算本地完成
- **备选**：如果 embedding 模型太慢，用 `ollama` 的 embed 端点

### 4.3 Memory Manager 实现

```python
# memory_manager.py

import asyncio
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

# 向量化（延迟导入，避免启动慢）
EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # sentence-transformers 模型名

@dataclass
class MemoryEntry:
    """单条记忆"""
    id: str
    content: str
    memory_type: str  # "semantic" | "episodic"
    session_id: str | None
    importance: float  # 0.0–1.0
    created_at: float
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None  # 仅 semantic memory

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
            "session_id": self.session_id,
            "importance": self.importance,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

@dataclass
class SearchResult:
    entry: MemoryEntry
    score: float  # 相似度分数

class MemoryManager:
    """
    三层记忆管理器。
    - Working Memory：由 SessionManager 提供（session history）
    - Episodic Memory：SQLite FTS5 BM25 检索
    - Semantic Memory：ChromaDB 向量检索
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.path.join(
            os.path.expanduser("~/.macos-agent-tooling"), "memory.db"
        )
        self._embedding_model = None  # 延迟加载
        self._init_done = False

    # ─── Embedding ────────────────────────────────────────────────

    async def _get_embedding(self, texts: list[str]) -> list[list[float]]:
        """对文本列表生成 embedding 向量"""
        if self._embedding_model is None:
            # 延迟加载 sentence-transformers
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer(EMBEDDING_MODEL)

        embeddings = self._embedding_model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,  # 余弦相似度等价
        )
        return embeddings.tolist()

    # ─── 初始化 ───────────────────────────────────────────────────

    async def _ensure_init(self):
        if not self._init_done:
            await asyncio.to_thread(self._init_db_sync)
            self._init_done = True

    def _init_db_sync(self):
        """同步初始化 DB（在线程中运行）"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Episodic memory 表（全文检索）
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS episodic_memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                session_id TEXT,
                importance REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
                content, session_id, content=episodic_memories, content_rowid=rowid
            );
            CREATE TRIGGER IF NOT EXISTS episodic_ai AFTER INSERT ON episodic_memories BEGIN
                INSERT INTO episodic_fts(rowid, content, session_id)
                VALUES (new.rowid, new.content, new.session_id);
            END;
            CREATE TRIGGER IF NOT EXISTS episodic_ad AFTER DELETE ON episodic_memories BEGIN
                INSERT INTO episodic_fts(episodic_fts, rowid, content, session_id)
                VALUES('delete', old.rowid, old.content, old.session_id);
            END;
            CREATE TRIGGER IF NOT EXISTS episodic_au AFTER UPDATE ON episodic_memories BEGIN
                INSERT INTO episodic_fts(episodic_fts, rowid, content, session_id)
                VALUES('delete', old.rowid, old.content, old.session_id);
                INSERT INTO episodic_fts(rowid, content, session_id)
                VALUES (new.rowid, new.content, new.session_id);
            END;
        """)

        # Semantic memory 表（向量存储，用 SQLite JSON 存向量）
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS semantic_memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_semantic_importance ON semantic_memories(importance DESC);
            CREATE INDEX IF NOT EXISTS idx_semantic_created ON semantic_memories(created_at DESC);
        """)

        conn.commit()
        conn.close()

    # ─── Semantic Memory（向量） ──────────────────────────────────

    async def add_semantic_memory(
        self,
        content: str,
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> str:
        """添加语义记忆（自动生成 embedding）"""
        await self._ensure_init()
        mid = uuid.uuid4().hex[:16]
        now = time.time()

        embeddings = await self._get_embedding([content])
        emb_str = json.dumps(embeddings[0])

        def _do():
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO semantic_memories (id, content, embedding, importance, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (mid, content, emb_str, importance, now, json.dumps(metadata or {})),
            )
            conn.commit()
            conn.close()

        await asyncio.to_thread(_do)
        return mid

    async def search_semantic(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """语义检索：query embedding 与存储向量做余弦相似度"""
        await self._ensure_init()
        query_emb = await self._get_embedding([query])
        query_vec = query_emb[0]

        def _do() -> list[SearchResult]:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT id, content, embedding, importance, created_at, metadata FROM semantic_memories"
            ).fetchall()
            conn.close()

            results = []
            for row in rows:
                stored_vec = json.loads(row[2])
                score = sum(a * b for a, b in zip(query_vec, stored_vec))  # 余弦相似度（已 normalize）
                results.append(SearchResult(
                    entry=MemoryEntry(
                        id=row[0], content=row[1], memory_type="semantic",
                        session_id=None, importance=row[3], created_at=row[4],
                        metadata=json.loads(row[5]),
                    ),
                    score=score,
                ))

            results.sort(key=lambda x: x.score, reverse=True)
            return results[:top_k]

        return await asyncio.to_thread(_do)

    # ─── Episodic Memory（FTS） ────────────────────────────────────

    async def add_episodic_memory(
        self,
        content: str,
        session_id: str | None = None,
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> str:
        """添加情景记忆（FTS5 BM25 索引）"""
        await self._ensure_init()
        mid = uuid.uuid4().hex[:16]
        now = time.time()

        def _do():
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO episodic_memories (id, content, session_id, importance, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (mid, content, session_id, importance, now, json.dumps(metadata or {})),
            )
            conn.commit()
            conn.close()

        await asyncio.to_thread(_do)
        return mid

    async def search_episodic(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """全文检索"""
        await self._ensure_init()

        def _do() -> list[SearchResult]:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("""
                SELECT m.id, m.content, m.session_id, m.importance, m.created_at, m.metadata,
                       bm25(episodic_fts) as rank
                FROM episodic_fts f
                JOIN episodic_memories m ON f.rowid = m.rowid
                WHERE episodic_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, top_k)).fetchall()
            conn.close()

            return [
                SearchResult(
                    entry=MemoryEntry(
                        id=row[0], content=row[1], memory_type="episodic",
                        session_id=row[2], importance=row[3], created_at=row[4],
                        metadata=json.loads(row[5]),
                    ),
                    score=-row[6],  # BM25 rank：越负越好
                )
                for row in rows
            ]

        return await asyncio.to_thread(_do)

    # ─── 统一检索接口 ─────────────────────────────────────────────

    async def search(
        self,
        query: str,
        top_k: int = 5,
        memory_types: list[str] | None = None,
    ) -> list[SearchResult]:
        """
        统一检索接口，同时查 semantic 和 episodic，取并集后按 score 排序。
        memory_types: ["semantic", "episodic"] 或 None（查全部）
        """
        await self._ensure_init()
        types = memory_types or ["semantic", "episodic"]
        results: list[SearchResult] = []

        if "semantic" in types:
            semantic_results = await self.search_semantic(query, top_k=top_k)
            results.extend(semantic_results)

        if "episodic" in types:
            episodic_results = await self.search_episodic(query, top_k=top_k)
            results.extend(episodic_results)

        # 合并排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    # ─── Session 记忆摘要 ────────────────────────────────────────

    async def summarize_session(self, session_id: str, messages: list[dict]) -> str:
        """
        对 session 消息生成摘要，存入 episodic memory。
        用 Ollama 生成摘要（避免额外 API）。
        """
        from ollama_bridge import OllamaBridge

        if not messages:
            return ""

        conversation_text = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')[:200]}" for m in messages[-10:]
        )

        bridge = OllamaBridge()
        summary_parts = []
        prompt = f"请用 2-3 句话总结以下对话的主题和关键结论：\n\n{conversation_text}"
        async for token in bridge.chat(
            [Message(role="user", content=prompt)],
            model="llama3",
        ):
            summary_parts.append(token)
        await bridge.close()

        summary = "".join(summary_parts).strip()
        if summary:
            await self.add_episodic_memory(
                content=f"Session {session_id} summary: {summary}",
                session_id=session_id,
                importance=0.7,
                metadata={"type": "session_summary"},
            )
        return summary

    # ─── 清理 ─────────────────────────────────────────────────────

    async def prune_old_memories(self, cutoff_days: int = 30) -> int:
        """删除超过 cutoff_days 的低重要性记忆"""
        await self._ensure_init()
        cutoff = time.time() - cutoff_days * 86400

        def _do() -> int:
            conn = sqlite3.connect(self.db_path)
            c = conn.execute(
                "DELETE FROM episodic_memories WHERE created_at < ? AND importance < 0.5",
                (cutoff,),
            )
            d = conn.execute(
                "DELETE FROM semantic_memories WHERE created_at < ? AND importance < 0.5",
                (cutoff,),
            )
            conn.commit()
            conn.close()
            return c.rowcount + d.rowcount

        return await asyncio.to_thread(_do)
```

---

## 5. Agent Executor（Agent 执行循环）

### 5.1 ReAct Loop 设计

```
User Input + Session History + Injected Memories
    │
    ▼
┌───────────────────────────────────────────────────────────────┐
│  AgentExecutor.loop()                                         │
│                                                               │
│  1. Build prompt (context window manager)                     │
│     - system prompt                                          │
│     - relevant memories (top-k)                              │
│     - conversation history                                   │
│     - user input                                             │
│                                                               │
│  2. Call Ollama (chat stream)                                 │
│                                                               │
│  3. Parse response for tool_calls                             │
│     - 如果有 tool_calls → 执行工具 → append result → loop   │
│     - 如果没有 → 输出 final response → stop                  │
│                                                               │
│  停止条件（满足任一）：                                        │
│     - LLM 返回空 tool_calls（final response）               │
│     - 达到 max_iterations（默认 10）                         │
│     - LLM 调用了 done() 工具                                 │
│     - 用户取消（stop()）                                      │
└───────────────────────────────────────────────────────────────┘
```

### 5.2 System Prompt 模板

```python
SYSTEM_PROMPT = """You are a helpful macOS AI assistant, running locally with full access to the user's workspace.

You have access to the following tools:
{tool_schemas}

Rules:
1. Always use tools when they can help complete the user's request
2. For file operations, prefer reading existing files before writing
3. bash commands run in a sandboxed workspace (~/.macos-agent-workspace)
4. When done, call the done tool with your final answer
5. If a tool fails, analyze the error and try an alternative approach
6. Be concise - only show relevant output, truncate long outputs to 2000 chars

Output format:
- Tool calls: Use the tool call format (never write tool calls as plain text)
- Final answer: Call the done tool with your response
"""

TOOL_CALL_FORMAT = """To use a tool, respond with:

<tool_calls>
<tool name="tool_name">{json_args}</tool>
</tool_calls>"""
```

### 5.3 AgentExecutor 实现

```python
# agent_executor.py

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator

from shared_types import Message
from ollama_bridge import OllamaBridge
from memory_manager import MemoryManager
from tool_executor import ToolExecutor, ToolResult

SYSTEM_PROMPT = """You are a helpful macOS AI assistant, running locally.
You have access to tools. Use them when appropriate.
When you have completed the task, call the done tool."""


class AgentEventType(Enum):
    TOOL_CALL = "tool_call"       # LLM requested a tool
    TOOL_RESULT = "tool_result"  # Tool execution result
    TEXT = "text"                 # Text token stream
    DONE = "done"                 # Agent finished
    ERROR = "error"               # Error occurred


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
    tool_choice: str = "auto"  # "auto" | "none" | "required"
    stream: bool = True


class AgentExecutor:
    """
    ReAct 风格的 Agent 执行器。
    支持流式输出，工具调用，结果回传。
    """

    def __init__(
        self,
        ollama_bridge: OllamaBridge,
        memory_manager: MemoryManager,
        tool_executor: ToolExecutor,
        config: AgentConfig | None = None,
    ):
        self.bridge = ollama_bridge
        self.memory = memory_manager
        self.tools = tool_executor
        self.config = config or AgentConfig()

        # Session 历史消息
        self._messages: list[Message] = []

    # ─── 流式执行 ────────────────────────────────────────────────

    async def execute(
        self,
        user_input: str,
        session_id: str,
        system_override: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """
        主执行循环。流式返回 AgentEvent。
        """
        iteration = 0
        accumulated_text = ""
        tool_schemas = self.tools.get_tool_schemas()

        # 构建 system prompt
        system_prompt = system_override or SYSTEM_PROMPT
        if tool_schemas:
            schema_text = "\n".join(json.dumps(s, indent=2) for s in tool_schemas)
            system_prompt = system_prompt.replace(
                "{tool_schemas}", f"\n\nAvailable tools:\n{schema_text}"
            )

        # 初始化消息列表
        self._messages = [Message(role="system", content=system_prompt)]

        # 检索相关记忆
        relevant_memories = await self.memory.search(user_input, top_k=5)
        if relevant_memories:
            memory_context = "\n\nRelevant memories:\n" + "\n".join(
                f"- [{r.entry.memory_type}] {r.entry.content}" for r in relevant_memories
            )
            # 注入到 system prompt（作为首条 system 消息的补充）
            self._messages[0].content += memory_context

        # 添加用户输入
        self._messages.append(Message(role="user", content=user_input))

        while iteration < self.config.max_iterations:
            iteration += 1

            # 调用 LLM
            llm_stream = self.bridge.chat(
                messages=self._messages,
                model=self.config.model,
            )

            # 解析 LLM 输出
            response_text = ""
            raw_output = ""

            async for token in llm_stream:
                raw_output += token
                response_text += token
                yield AgentEvent(type=AgentEventType.TEXT, data={"token": token})

            if not raw_output.strip():
                yield AgentEvent(type=AgentEventType.ERROR, data={"message": "Empty LLM response"})
                return

            # 解析 tool_calls（从响应中提取）
            tool_calls = self._parse_tool_calls(response_text)

            if not tool_calls:
                # 没有工具调用 → final response，结束
                self._messages.append(Message(role="assistant", content=response_text))
                yield AgentEvent(type=AgentEventType.DONE, data={"response": response_text})
                return

            # 执行工具调用
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("arguments", {})

                yield AgentEvent(
                    type=AgentEventType.TOOL_CALL,
                    data={"tool": tool_name, "args": tool_args}
                )

                result: ToolResult = await self.tools.execute(tool_name, tool_args)

                yield AgentEvent(
                    type=AgentEventType.TOOL_RESULT,
                    data=result.to_dict()
                )

                # 将结果追加到消息历史
                observation = result.to_observation()
                self._messages.append(Message(
                    role="user",
                    content=f"Tool result: {observation}"
                ))

                # 检查是否是 done 工具
                if tool_name == "done":
                    yield AgentEvent(
                        type=AgentEventType.DONE,
                        data={"response": tool_args.get("message", observation)}
                    )
                    return

        # 达到最大迭代次数
        yield AgentEvent(
            type=AgentEventType.ERROR,
            data={"message": f"Max iterations ({self.config.max_iterations}) reached"}
        )

    # ─── 工具调用解析 ─────────────────────────────────────────────

    def _parse_tool_calls(self, text: str) -> list[dict]:
        """
        从 LLM 输出中解析 tool_calls 块。
        支持格式：
        <tool_calls>
        <tool name="bash">{"command": "ls -la"}</tool>
        </tool_calls>
        """
        try:
            # 查找 <tool_calls>...</tool_calls> 块
            start = text.find("<tool_calls>")
            end = text.find("</tool_calls>")
            if start == -1 or end == -1:
                return []

            block = text[start + len("<tool_calls>"):end].strip()
            # 解析多个 <tool> 标签
            calls = []
            tool_start = 0
            while True:
                t_open = block.find('<tool name="', tool_start)
                if t_open == -1:
                    break
                name_start = t_open + len('<tool name="')
                name_end = block.find('"', name_start)
                tool_name = block[name_start:name_end]

                # Find JSON args: from > after name to </tool>
                args_start = block.find(">", name_end) + 1
                args_end = block.find("</tool>", args_start)
                args_str = block[args_start:args_end].strip()
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = {"raw": args_str}

                calls.append({"name": tool_name, "arguments": args})
                tool_start = args_end + len("</tool>")

            return calls
        except Exception:
            return []

    # ─── 工具注册 ────────────────────────────────────────────────

    def get_available_tools(self) -> list[dict]:
        """返回所有可用工具的 schema"""
        return self.tools.get_tool_schemas()

    async def stop(self):
        """取消当前执行"""
        # 发送停止信号
        self._messages.append(Message(
            role="system",
            content="[STOPPED] The user has cancelled this request."
        ))
```

---

## 6. Context Window Manager

### 6.1 职责

- 构建最终 prompt（system + memories + history + user input）
- 管理 token 预算（不超过 `max_context_tokens`）
- 优先保留：system > recent memories > recent messages > older messages
- 截断策略：从中间开始删除（middle truncation），保留开头和结尾

### 6.2 实现

```python
# context_window.py

import tiktoken
from typing import list

class ContextWindowManager:
    """
    管理上下文窗口，控制注入内容和截断。
    默认模型：cl100k_base（GPT-4 相同 tokenizer，支持 ollama 模型）
    """

    def __init__(self, max_tokens: int = 8192, model: str = "cl100k_base"):
        self.max_tokens = max_tokens
        try:
            self.enc = tiktoken.get_encoding(model)
        except Exception:
            # Fallback: 粗略按字符估算（4 字符 ≈ 1 token）
            self.enc = None

    def count_tokens(self, text: str) -> int:
        if self.enc:
            return len(self.enc.encode(text))
        return len(text) // 4

    def build_context(
        self,
        system: str,
        memories: list[str],  # 检索到的记忆文本列表
        messages: list[dict],  # {"role": ..., "content": ...}
        user_input: str,
    ) -> tuple[list[dict], int]:
        """
        构建上下文，截断到 max_tokens 以内。
        返回：(filtered_messages, total_tokens)
        """
        total_parts = []
        parts_with_roles = []

        # 1. System prompt
        system_tokens = self.count_tokens(system)
        remaining = self.max_tokens - system_tokens

        # 2. User input
        input_tokens = self.count_tokens(user_input)
        remaining -= input_tokens

        # 3. Memories
        memory_texts = []
        for mem in memories:
            mem_tokens = self.count_tokens(mem)
            if remaining - mem_tokens >= 0:
                memory_texts.append(mem)
                remaining -= mem_tokens
            else:
                break

        # 4. Messages（从最新往最旧，保留最近）
        filtered_msgs = []
        for msg in reversed(messages):
            msg_tokens = self.count_tokens(f"{msg['role']}: {msg['content']}")
            if remaining - msg_tokens >= 0:
                filtered_msgs.append(msg)
                remaining -= msg_tokens
            else:
                break

        # 反转回来（按时间顺序）
        filtered_msgs = list(reversed(filtered_msgs))

        # 构建最终消息列表
        final_messages = []
        if memory_texts:
            final_messages.append({
                "role": "system",
                "content": f"Relevant memories:\n" + "\n".join(f"- {m}" for m in memory_texts)
            })
        final_messages.append({"role": "user", "content": user_input})

        # 计算总 token
        total = sum(self.count_tokens(m["content"]) for m in final_messages)
        return final_messages, total
```

---

## 7. IPC 扩展（Phase 2）

### 7.1 新增 IPC 命令

Phase 1 IPC 协议保持兼容，扩展以下命令：

```python
# IPC 扩展命令（添加到 Core/ipc.py）

elif cmd == "agent_execute":
    # 非流式执行（Phase 2）
    task = args.get("task", "")
    session_id = args.get("session_id", "")
    model = args.get("model", "llama3")
    config = AgentConfig(
        model=model,
        max_iterations=args.get("max_iterations", 10),
    )
    executor = AgentExecutor(bridge, memory_mgr, tool_executor, config)
    result_parts = []
    async for event in executor.execute(task, session_id):
        if event.type == AgentEventType.DONE:
            result_parts.append(event.data.get("response", ""))
    resp = {"ok": True, "data": {"result": "".join(result_parts)}, "request_id": request_id}

elif cmd == "agent_stream":
    # 流式执行 — 输出 JSON lines 到 stdout（类似 chat）
    task = args.get("task", "")
    session_id = args.get("session_id", "")
    model = args.get("model", "llama3")
    config = AgentConfig(model=model, max_iterations=args.get("max_iterations", 10))
    executor = AgentExecutor(bridge, memory_mgr, tool_executor, config)
    async for event in executor.execute(task, session_id):
        yield json.dumps({"event": event.type.value, "data": event.data}) + "\n"

elif cmd == "memory_search":
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    results = await memory_mgr.search(query, top_k=top_k)
    resp = {
        "ok": True,
        "data": {"results": [{"entry": r.entry.to_dict(), "score": r.score} for r in results]},
        "request_id": request_id
    }

elif cmd == "memory_add":
    content = args.get("content", "")
    memory_type = args.get("type", "semantic")  # "semantic" | "episodic"
    importance = args.get("importance", 0.5)
    mid = await memory_mgr.add_semantic_memory(content, importance) if memory_type == "semantic" \
        else await memory_mgr.add_episodic_memory(content, importance=importance)
    resp = {"ok": True, "data": {"id": mid}, "request_id": request_id}

elif cmd == "get_tools":
    schemas = tool_executor.get_tool_schemas()
    resp = {"ok": True, "data": {"tools": schemas}, "request_id": request_id}
```

### 7.2 Swift 侧扩展 AgentBridge

```swift
// 新增 AgentBridge 方法（App/IPC/AgentBridge.swift）

struct AgentEvent: Codable {
    let type: String  // "tool_call" | "tool_result" | "text" | "done" | "error"
    let data: [String: AnyCodable]
}

// 流式 Agent 执行
func agentStream(task: String, sessionId: String, model: String = "llama3")
    -> AsyncThrowingStream<AgentEvent, Error> {
    AsyncThrowingStream { cont in
        Task {
            // 实现类似 chatStream 的流式读取
            // 从 Python IPC 的 _agent_stream 命令读取 JSON lines
        }
    }
}

// 非流式 Agent 执行
func agentExecute(task: String, sessionId: String, model: String = "llama3") async throws -> String

// 记忆搜索
func memorySearch(query: String, topK: Int = 5) async throws -> [SearchResult]

// 添加记忆
func memoryAdd(content: String, type: String = "semantic", importance: Float = 0.5) async throws -> String
```

---

## 8. 文件结构（Phase 2 新增）

```
Core/
  ├── ollama_bridge.py       ✅ Phase 1
  ├── session_manager.py     ✅ Phase 1
  ├── ipc.py                 ✅ Phase 1（扩展 Phase 2 命令）
  ├── shared_types.py        ✅ Phase 1（扩展 AgentEvent 相关类型）
  ├── agent_executor.py      🆕 Phase 2
  ├── memory_manager.py      🆕 Phase 2
  ├── tool_executor.py       🆕 Phase 2
  ├── context_window.py      🆕 Phase 2
  └── requirements.txt       （更新依赖）
```

---

## 9. 依赖更新（requirements.txt Phase 2）

```txt
# Phase 1
httpx>=0.28.0

# Phase 2 新增
sentence-transformers>=3.0.0
tiktoken>=0.7.0
```

---

## 10. 给 Builder 的具体任务派单

### Task-2A: Tool Executor（工具执行层）
**文件**: `Core/tool_executor.py`
**负责人**: Builder
**依赖**: 无
**验收条件**:
1. `bash` 工具：命令白名单生效，sandbox 隔离，超时控制
2. `read_file/write_file/list_dir`：workspace 路径验证
3. 工具 schema 可通过 `get_tool_schemas()` 获取
4. `execute()` 返回 `ToolResult`，包含 `success/output/error`
5. 单元测试覆盖所有工具

### Task-2B: Memory Manager（记忆管理层）
**文件**: `Core/memory_manager.py`
**负责人**: Builder
**依赖**: `sentence-transformers`
**验收条件**:
1. `add_semantic_memory()` + `search_semantic()` 向量检索
2. `add_episodic_memory()` + `search_episodic()` FTS5 检索
3. `search()` 统一接口，合并 semantic + episodic 结果
4. `summarize_session()` 调用 Ollama 生成摘要
5. `prune_old_memories()` 清理低重要性旧记忆

### Task-2C: Context Window Manager
**文件**: `Core/context_window.py`
**负责人**: Builder
**依赖**: `tiktoken`
**验收条件**:
1. `count_tokens()` 精确 token 计数
2. `build_context()` 截断策略正确（middle truncation）
3. system + memories + messages + user input 顺序正确

### Task-2D: Agent Executor（ReAct Loop）
**文件**: `Core/agent_executor.py`
**负责人**: Builder
**依赖**: Task-2A, Task-2B
**验收条件**:
1. `execute()` 流式输出 `AgentEvent`（TEXT/TOOL_CALL/TOOL_RESULT/DONE/ERROR）
2. `_parse_tool_calls()` 正确解析 `<tool_calls>` 块
3. 停止条件：tool_calls 为空 / max_iterations / done 工具
4. 记忆检索结果注入到 system prompt

### Task-2E: IPC 扩展 + Swift AgentBridge
**文件**: `Core/ipc.py` + `App/IPC/AgentBridge.swift`
**负责人**: Builder
**依赖**: Task-2A, Task-2B, Task-2D
**验收条件**:
1. IPC 新增 5 个命令：`agent_execute`, `agent_stream`, `memory_search`, `memory_add`, `get_tools`
2. Swift `AgentBridge` 新增 `agentStream()`, `agentExecute()`, `memorySearch()`, `memoryAdd()` 方法
3. 流式响应正确解析 JSON lines

---

## 11. QA 审核节点

| QA 节点 | 触发条件 | 审核重点 |
|---------|---------|---------|
| **QA-2A** | Task-2A 完成 | bash sandbox 安全性、路径穿越防护、工具 schema 格式 |
| **QA-2B** | Task-2B 完成 | FTS 检索准确性、embedding 质量、混合召回效果 |
| **QA-2C** | Task-2C 完成 | token 计数准确性、截断边界case、memory leak |
| **QA-2D** | Task-2D 完成 | ReAct loop 完整性、工具调用解析、stream 输出 |
| **QA-2E** | Task-2E 完成 | IPC 协议兼容性、Swift 端类型安全、端到端流式 |
| **QA-2F** | 全部完成 | 全链路集成测试：Swift UI → IPC → AgentExecutor → Tools/Memory → UI |

---

## 12. 风险与缓解

| 风险 | 缓解策略 |
|------|---------|
| `sentence-transformers` 冷启动慢（首次加载 5-10s） | 延迟加载 + 启动时异步 warmup |
| embedding 模型太大（22M 参数） | 用 `all-MiniLM-L6-v2`（最小模型），CPU 可跑 |
| ChromaDB HTTP 服务管理复杂 | 改用 SQLite + numpy 做向量存储，无外部依赖 |
| LLM 解析 `<tool_calls>` 失败 | 同时支持 JSON tool_calls 格式作为 fallback |
| 流式输出中解析 tool_calls 困难 | 缓冲全文后再解析，不逐 token 解析 |

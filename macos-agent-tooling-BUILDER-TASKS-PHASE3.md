# Phase 3 Builder 任务包
> CTO → Builder | 日期: 2026-03-26

---

## 概览

Phase 3 实现 **Agent Mode UI + Tool Templates + Settings + Memory Management UI**，共 8 个任务。
Task-3A/3B/3C/3D 可并行开发，Task-3E 依赖 3A+3C+3D，Task-3F/3G/3H 依赖 3E，全部完成后 QA-3I。

---

## Task-3A: Agent Executor 扩展（THINKING + ITERATION 事件）

**文件**: `Core/agent_executor.py`（修改）
**依赖**: Phase 2 AgentExecutor
**验收人**: QA-3A

### 修改规格

#### 1. 新增 AgentEventType

```python
class AgentEventType(Enum):
    # Phase 2 已有
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TEXT = "text"
    DONE = "done"
    ERROR = "error"
    # Phase 3 新增
    THINKING = "thinking"       # Agent 推理文本（用户可见）
    ITERATION = "iteration"    # 新一轮 ReAct 开始
```

#### 2. 修改 `_parse_response()` 方法

```python
def _parse_response(self, text: str) -> tuple[list[dict], str]:
    """
    解析 LLM 输出。
    返回 (tool_calls, thinking_text)
    - tool_calls: 解析出的工具调用列表
    - thinking_text: <tool_calls> 块外的所有文本（用户可见的推理）
    """
    start = text.find("<tool_calls>")
    end = text.find("</tool_calls>")
    if start == -1 or end == -1:
        return [], text  # 无 tool_calls，全部是 thinking
    # 块内是 tool_calls，块外是 thinking
    thinking = text[:start].strip() + " " + text[end + len("</tool_calls>"):].strip()
    tool_calls = self._parse_tool_calls_from_block(text[start:end + len("</tool_calls>"):])
    return tool_calls, thinking.strip()
```

#### 3. 修改 `execute()` 主循环

```python
# ReAct loop 中，解析 LLM 响应后：
response_text = ""
async for token in llm_stream:
    response_text += token
    yield AgentEvent(type=AgentEventType.TEXT, data={"token": token})

# 解析：区分 thinking 和 tool_calls
tool_calls, thinking = self._parse_response(response_text)

# 先 yield thinking（如果有）
if thinking:
    yield AgentEvent(type=AgentEventType.THINKING, data={"text": thinking})

# 如果有 tool_calls，yield ITERATION 事件
if tool_calls:
    yield AgentEvent(type=AgentEventType.ITERATION, data={"number": iteration})
    for tc in tool_calls:
        yield AgentEvent(type=AgentEventType.TOOL_CALL, data={...})
        ...
```

#### 4. 单元测试

```python
# Core/test_agent_executor.py 新增

def test_parse_response_no_tool_calls():
    text = "Let me think about this problem..."
    calls, thinking = executor._parse_response(text)
    assert calls == []
    assert "think about this" in thinking

def test_parse_response_with_tool_calls():
    text = "I'll run this command:<tool_calls><tool name=\"bash\">{\"command\": \"ls\"}</tool></tool_calls>Let me check the output."
    calls, thinking = executor._parse_response(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "bash"
    assert "check the output" in thinking

def test_parse_response_thinking_only():
    text = "First, let me understand the structure."
    calls, thinking = executor._parse_response(text)
    assert calls == []
    assert thinking == text
```

---

## Task-3B: Tool Templates（扩展工具集）

**文件**: `Core/tool_registry.py`（新建）+ `Core/tool_executor.py`（改造）
**依赖**: 无
**验收人**: QA-3B

### 详细规格

#### 1. ToolRegistry 类

```python
# Core/tool_registry.py

import json
import os
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any

@dataclass
class ToolTemplate:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable["ToolResult"]]
    requires_confirmation: bool = False
    enabled: bool = True

DEFAULT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": []
}

class ToolRegistry:
    def __init__(self, workspace: str):
        self.workspace = workspace
        self._tools: dict[str, ToolTemplate] = {}
        self._register_builtin_tools()
        self._load_custom_tools()

    # ─── 内置工具注册 ─────────────────────────────────────────────

    def _register_builtin_tools(self):
        # bash（白名单，已在 tool_executor.py 实现）
        # read_file, write_file, list_dir, done
        ...

    # ─── Phase 3 新增工具 ────────────────────────────────────────

    async def _web_search(self, query: str, max_results: int = 5) -> ToolResult:
        """DuckDuckGo HTML 搜索"""
        import urllib.parse, asyncio, re
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "-L", "--max-time", "10", "--user-agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", url,
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
        """从 DuckDuckGo HTML 提取结果"""
        import re
        results = []
        # 提取 <a class="result__a" href="...">Title</a>
        # 提取 <a class="result__snippet" href="...">Snippet</a>
        titles = re.findall(r'<a class="result__a"[^>]*>([^<]+)</a>', html)
        snippets = re.findall(r'<a class="result__snippet"[^>]*>([^<]+)</a>', html)
        for i, (title, snippet) in enumerate(zip(titles, snippets)):
            if i >= max_results:
                break
            results.append(f"{i+1}. {title.strip()}\n   {snippet.strip()}")
        if not results:
            return "No results found."
        return "\n".join(results)

    async def _read_multiple_files(self, paths: list[str]) -> ToolResult:
        """批量读取文件"""
        import asyncio
        contents = []
        for path in paths[:20]:  # 最多 20 个文件
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
        return ToolResult("read_multiple_files", {"paths": paths}, "\n\n".join(contents), success=True)

    async def _http_request(self, url: str, method: str = "GET", body: str = "", headers: dict = {}) -> ToolResult:
        """HTTP 请求（仅 GET/POST，仅 localhost 或用户白名单）"""
        import urllib.parse, asyncio
        allowed_prefixes = ["http://localhost", "http://127.0.0.1"]
        if not any(url.startswith(p) for p in allowed_prefixes):
            return ToolResult("http_request", {"url": url}, "", "Only localhost requests allowed", success=False)
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "-X", method, url,
                *(["-d", body] if body else []),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult("http_request", {"url": url, "method": method}, stdout.decode("utf-8", errors="replace"), success=True)
        except Exception as e:
            return ToolResult("http_request", {"url": url}, "", str(e), success=False)

    async def _osascript(self, script: str) -> ToolResult:
        """执行 AppleScript（requires_confirmation=True）"""
        # 注意：此工具需要 Swift 侧弹窗确认后才能执行
        # Core 侧不做确认，只是标记
        import asyncio
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            return ToolResult("osascript", {"script": script[:100]}, out, err, success=proc.returncode == 0)
        except Exception as e:
            return ToolResult("osascript", {"script": script[:100]}, "", str(e), success=False)

    # ─── 用户自定义工具加载 ──────────────────────────────────────

    CUSTOM_TOOLS_PATH = os.path.expanduser("~/.macos-agent-tooling/custom_tools.json")

    def _load_custom_tools(self):
        """从 JSON 文件加载用户自定义工具"""
        if not os.path.exists(self.CUSTOM_TOOLS_PATH):
            return
        try:
            with open(self.CUSTOM_TOOLS_PATH) as f:
                data = json.load(f)
            for tool_def in data.get("custom_tools", []):
                # 用户自定义工具暂不支持（未来版本）
                pass
        except Exception:
            pass  # 忽略损坏的配置文件

    # ─── 注册接口 ─────────────────────────────────────────────────

    def register(self, template: ToolTemplate):
        self._tools[template.name] = template

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def get_schemas(self) -> list[dict]:
        return [t.input_schema for t in self._tools.values() if t.enabled]

    def get_tool(self, name: str) -> ToolTemplate | None:
        return self._tools.get(name)

    def get_confirmation_required(self, name: str) -> bool:
        t = self._tools.get(name)
        return t.requires_confirmation if t else False
```

### 2. tool_executor.py 改造

```python
# tool_executor.py
# 将 ToolExecutor 改为委托给 ToolRegistry

class ToolExecutor:
    def __init__(self, workspace: str = SANDBOX_WORKSPACE):
        self.registry = ToolRegistry(workspace)

    def get_tool_schemas(self) -> list[dict]:
        return self.registry.get_schemas()

    async def execute(self, tool_name: str, args: dict) -> ToolResult:
        template = self.registry.get_tool(tool_name)
        if template is None:
            return ToolResult(tool_name, args, "", f"Unknown tool: {tool_name}", success=False)
        try:
            result = await template.handler(**args)
            return result
        except Exception as e:
            return ToolResult(tool_name, args, "", str(e), success=False)
```

### 验收测试

```python
# Core/test_tool_registry.py

async def test_web_search():
    registry = ToolRegistry(TEST_WORKSPACE)
    result = await registry._web_search("what is rust programming language", max_results=3)
    assert result.success
    assert len(result.output) > 0
    assert "No results" not in result.output or "rust" in result.output.lower()

async def test_read_multiple_files():
    registry = ToolRegistry(TEST_WORKSPACE)
    # 创建测试文件
    os.makedirs(TEST_WORKSPACE, exist_ok=True)
    with open(os.path.join(TEST_WORKSPACE, "a.txt"), "w") as f:
        f.write("file A")
    with open(os.path.join(TEST_WORKSPACE, "b.txt"), "w") as f:
        f.write("file B")
    result = await registry._read_multiple_files(["a.txt", "b.txt"])
    assert result.success
    assert "file A" in result.output
    assert "file B" in result.output

async def test_path_traversal_blocked():
    registry = ToolRegistry(TEST_WORKSPACE)
    result = await registry._read_multiple_files(["/etc/passwd"])
    assert not result.success or "PATH_OUTSIDE_WORKSPACE" in result.output
```

---

## Task-3C: ConfigManager（配置持久化）

**文件**: `Core/config_manager.py`（新建）
**依赖**: 无
**验收人**: QA-3C

### 详细规格

```python
# Core/config_manager.py

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

@dataclass
class AgentConfig:
    model: str = "llama3"
    max_iterations: int = 10
    temperature: float = 0.7
    memory_semantic_enabled: bool = True
    memory_episodic_enabled: bool = True
    memory_prune_days: int = 30
    system_prompt: str | None = None
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


class ConfigManager:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._config: AgentConfig = AgentConfig()
        self._load()

    def _ensure_dir(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    data = json.load(f)
                self._config = AgentConfig.from_dict(data)
            except (json.JSONDecodeError, TypeError):
                self._config = AgentConfig()  # 损坏则回退默认

    def save(self):
        self._ensure_dir()
        with open(self.path, "w") as f:
            json.dump(self._config.to_dict(), f, indent=2)

    def get(self) -> AgentConfig:
        return self._config

    def update(self, **kwargs):
        """原子更新部分配置，只写入存在的 key"""
        d = self._config.to_dict()
        for k, v in kwargs.items():
            if k in d:
                d[k] = v
        self._config = AgentConfig.from_dict(d)
        self.save()
        return self._config
```

### IPC 扩展

```python
# Core/ipc.py 新增

elif cmd == "get_config":
    from config_manager import ConfigManager
    cm = ConfigManager()
    resp = {"ok": True, "data": {"config": cm.get().to_dict()}, "request_id": request_id}

elif cmd == "update_config":
    from config_manager import ConfigManager
    cm = ConfigManager()
    updated = cm.update(**args)
    resp = {"ok": True, "data": {"config": updated.to_dict()}, "request_id": request_id}
```

### 验收测试

```python
# Core/test_config_manager.py

def test_default_config():
    cm = ConfigManager(path="/tmp/test_config.json")
    cfg = cm.get()
    assert cfg.model == "llama3"
    assert cfg.max_iterations == 10
    assert cfg.show_thinking == True

def test_update_partial():
    cm = ConfigManager(path="/tmp/test_config.json")
    cm.update(model="mistral", temperature=0.5)
    assert cm.get().model == "mistral"
    assert cm.get().temperature == 0.5
    assert cm.get().max_iterations == 10  # unchanged

def test_corrupted_json_fallback():
    with open("/tmp/bad_config.json", "w") as f:
        f.write("{ invalid json")
    cm = ConfigManager(path="/tmp/bad_config.json")
    cfg = cm.get()
    assert cfg.model == "llama3"  # fallback defaults
```

---

## Task-3D: MemoryManager 扩展（list/delete/clear）

**文件**: `Core/memory_manager.py`（扩展）
**依赖**: Phase 2 MemoryManager
**验收人**: QA-3D

### 新增方法

```python
# memory_manager.py 新增

async def list_memories(
    self,
    memory_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MemoryEntry]:
    """分页列出记忆（按 created_at 倒序）"""
    await self._ensure_init()

    def _do() -> list[MemoryEntry]:
        conn = sqlite3.connect(self.db_path)
        if memory_type == "semantic":
            rows = conn.execute(
                "SELECT id, content, importance, created_at, metadata FROM semantic_memories ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        elif memory_type == "episodic":
            rows = conn.execute(
                "SELECT id, content, importance, created_at, metadata FROM episodic_memories ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        else:
            # 合并两个表（用 union）
            sem = conn.execute(
                "SELECT id, content, 'semantic', importance, created_at, metadata FROM semantic_memories"
            ).fetchall()
            epi = conn.execute(
                "SELECT id, content, 'episodic', importance, created_at, metadata FROM episodic_memories"
            ).fetchall()
            all_rows = sorted(sem + epi, key=lambda r: r[4], reverse=True)
            rows = all_rows[offset:offset+limit]
        conn.close()
        return [
            MemoryEntry(
                id=r[0], content=r[1], memory_type=r[2],
                importance=r[3], created_at=r[4],
                metadata=json.loads(r[5]) if isinstance(r[5], str) else r[5],
            )
            for r in rows
        ]

    return await asyncio.to_thread(_do)

async def count_memories(self, memory_type: str | None = None) -> int:
    """统计记忆数量"""
    await self._ensure_init()
    def _do() -> int:
        conn = sqlite3.connect(self.db_path)
        if memory_type == "semantic":
            n = conn.execute("SELECT COUNT(*) FROM semantic_memories").fetchone()[0]
        elif memory_type == "episodic":
            n = conn.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
        else:
            n = conn.execute("SELECT COUNT(*) FROM semantic_memories").fetchone()[0] + \
                conn.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
        conn.close()
        return n
    return await asyncio.to_thread(_do)

async def delete(self, memory_id: str) -> bool:
    """按 ID 删除记忆（semantic 或 episodic）"""
    await self._ensure_init()
    def _do() -> bool:
        conn = sqlite3.connect(self.db_path)
        c1 = conn.execute("DELETE FROM semantic_memories WHERE id = ?", (memory_id,)).rowcount
        c2 = conn.execute("DELETE FROM episodic_memories WHERE id = ?", (memory_id,)).rowcount
        conn.commit()
        conn.close()
        return (c1 + c2) > 0
    return await asyncio.to_thread(_do)

async def clear(self, memory_type: str | None = None) -> int:
    """清空指定类型记忆或全部记忆"""
    await self._ensure_init()
    def _do() -> int:
        conn = sqlite3.connect(self.db_path)
        if memory_type == "semantic":
            n = conn.execute("DELETE FROM semantic_memories").rowcount
        elif memory_type == "episodic":
            n = conn.execute("DELETE FROM episodic_memories").rowcount
        else:
            n = conn.execute("DELETE FROM semantic_memories").rowcount + \
                conn.execute("DELETE FROM episodic_memories").rowcount
        conn.commit()
        conn.close()
        return n
    return await asyncio.to_thread(_do)
```

### IPC 扩展

```python
elif cmd == "memory_list":
    memory_type = args.get("type", None)
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)
    entries = await memory_mgr.list_memories(memory_type=memory_type, limit=limit, offset=offset)
    total = await memory_mgr.count_memories(memory_type=memory_type)
    import os
    db_size = os.path.getsize(memory_mgr.db_path) if os.path.exists(memory_mgr.db_path) else 0
    resp = {
        "ok": True,
        "data": {
            "entries": [
                {
                    "id": e.id, "content": e.content, "memory_type": e.memory_type,
                    "importance": e.importance, "created_at": e.created_at,
                    "metadata": e.metadata,
                }
                for e in entries
            ],
            "total": total,
            "db_size_bytes": db_size
        },
        "request_id": request_id
    }

elif cmd == "memory_delete":
    memory_id = args.get("id", "")
    success = await memory_mgr.delete(memory_id)
    resp = {"ok": True, "data": {"deleted": success}, "request_id": request_id}

elif cmd == "memory_clear":
    memory_type = args.get("type", None)
    count = await memory_mgr.clear(memory_type=memory_type)
    resp = {"ok": True, "data": {"cleared": count}, "request_id": request_id}
```

### 验收测试

```python
async def test_list_memories():
    mm = MemoryManager()
    mid1 = await mm.add_semantic_memory("Test semantic 1")
    mid2 = await mm.add_episodic_memory("Test episodic 1")
    entries = await mm.list_memories(limit=10)
    assert len(entries) >= 2
    assert any(e.id == mid1 for e in entries)
    assert any(e.id == mid2 for e in entries)

async def test_delete_memory():
    mm = MemoryManager()
    mid = await mm.add_semantic_memory("To be deleted")
    assert await mm.delete(mid) == True
    assert await mm.delete("nonexistent") == False

async def test_clear_memories():
    mm = MemoryManager()
    await mm.add_semantic_memory("Semantic to clear")
    await mm.add_episodic_memory("Episodic to clear")
    cleared = await mm.clear(memory_type="semantic")
    assert cleared >= 1
    count = await mm.count_memories(memory_type="semantic")
    assert count == 0
```

---

## Task-3E: Swift AgentBridge 扩展

**文件**: `App/IPC/AgentBridge.swift`（修改）
**依赖**: Task-3A, Task-3C, Task-3D
**验收人**: QA-3E

### 新增类型

```swift
// App/IPC/AgentBridge.swift 新增

// ── Config ────────────────────────────────────────────────────────

struct AgentConfig: Codable {
    var model: String
    var maxIterations: Int
    var temperature: Float
    var memorySemanticEnabled: Bool
    var memoryEpisodicEnabled: Bool
    var memoryPruneDays: Int
    var systemPrompt: String?
    var showThinking: Bool
    var toolConfirmation: Bool
    var sandboxWorkspace: String

    enum CodingKeys: String, CodingKey {
        case model
        case maxIterations = "max_iterations"
        case temperature
        case memorySemanticEnabled = "memory_semantic_enabled"
        case memoryEpisodicEnabled = "memory_episodic_enabled"
        case memoryPruneDays = "memory_prune_days"
        case systemPrompt = "system_prompt"
        case showThinking = "show_thinking"
        case toolConfirmation = "tool_confirmation"
        case sandboxWorkspace = "sandbox_workspace"
    }
}

// ── Memory ───────────────────────────────────────────────────────

struct MemoryEntry: Codable, Identifiable {
    let id: String
    let content: String
    let memoryType: String
    let importance: Float
    let createdAt: Double
    let metadata: [String: String]

    enum CodingKeys: String, CodingKey {
        case id, content
        case memoryType = "memory_type"
        case importance
        case createdAt = "created_at"
        case metadata
    }
}

struct MemoryListResponse: Codable {
    let entries: [MemoryEntry]
    let total: Int
    let dbSizeBytes: Int

    enum CodingKeys: String, CodingKey {
        case entries, total
        case dbSizeBytes = "db_size_bytes"
    }
}

// ── Agent Stream Events ──────────────────────────────────────────

enum AgentStreamEvent {
    case thinking(text: String)
    case iterationStart(number: Int)
    case toolCall(tool: String, args: [String: AnyCodable], callId: String)
    case toolResult(tool: String, output: String, success: Bool, durationMs: Int)
    case textChunk(text: String)
    case done(response: String)
    case error(message: String)
}
```

### 新增方法

```swift
// App/IPC/AgentBridge.swift 新增方法

// ── Config ────────────────────────────────────────────────────────

func getConfig() async throws -> AgentConfig {
    let resp = try await sendRequest(cmd: "get_config", args: [:])
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(String(describing: resp)) }
    let data = resp["data"] as? [String: Any]
    let configData = try JSONSerialization.data(withJSONObject: data?["config"] ?? [:])
    return try JSONDecoder().decode(AgentConfig.self, from: configData)
}

func updateConfig(_ config: AgentConfig) async throws -> AgentConfig {
    let configDict = try config.toDictionary()
    let resp = try await sendRequest(cmd: "update_config", args: configDict)
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(String(describing: resp)) }
    let data = resp["data"] as? [String: Any]
    let configData = try JSONSerialization.data(withJSONObject: data?["config"] ?? [:])
    return try JSONDecoder().decode(AgentConfig.self, from: configData)
}

// ── Memory ───────────────────────────────────────────────────────

func memoryList(type: String? = nil, limit: Int = 50, offset: Int = 0) async throws -> MemoryListResponse {
    var args: [String: Any] = ["limit": limit, "offset": offset]
    if let t = type { args["type"] = t }
    let resp = try await sendRequest(cmd: "memory_list", args: args)
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(String(describing: resp)) }
    let data = resp["data"] as? [String: Any]
    let jsonData = try JSONSerialization.data(withJSONObject: data ?? [:])
    return try JSONDecoder().decode(MemoryListResponse.self, from: jsonData)
}

func memoryDelete(id: String) async throws -> Bool {
    let resp = try await sendRequest(cmd: "memory_delete", args: ["id": id])
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(String(describing: resp)) }
    let data = resp["data"] as? [String: Any]
    return data?["deleted"] as? Bool ?? false
}

func memoryClear(type: String? = nil) async throws -> Int {
    var args: [String: Any] = [:]
    if let t = type { args["type"] = t }
    let resp = try await sendRequest(cmd: "memory_clear", args: args)
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(String(describing: resp)) }
    let data = resp["data"] as? [String: Any]
    return data?["cleared"] as? Int ?? 0
}

// ── Agent Stream ─────────────────────────────────────────────────

func agentStream(task: String, sessionId: String, model: String = "llama3") -> AsyncThrowingStream<AgentStreamEvent, Error> {
    AsyncThrowingStream { cont in
        Task {
            do {
                let args: [String: Any] = [
                    "task": task,
                    "session_id": sessionId,
                    "model": model
                ]
                try await self.startStream(cmd: "_agent_stream", args: args) { [weak self] line in
                    guard let self = self, let data = line.data(using: .utf8),
                          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                          let event = json["event"] as? String,
                          let eventData = json["data"] as? [String: Any] else { return }

                    let streamEvent: AgentStreamEvent?
                    switch event {
                    case "thinking":
                        let text = eventData["text"] as? String ?? ""
                        streamEvent = .thinking(text: text)
                    case "iteration":
                        let number = eventData["number"] as? Int ?? 0
                        streamEvent = .iterationStart(number: number)
                    case "tool_call":
                        let tool = eventData["tool"] as? String ?? ""
                        let args = (eventData["args"] as? [String: Any]) ?? [:]
                        let callId = eventData["call_id"] as? String ?? UUID().uuidString
                        streamEvent = .toolCall(tool: tool, args: args.mapValues { AnyCodable($0) }, callId: callId)
                    case "tool_result":
                        let tool = eventData["tool"] as? String ?? ""
                        let output = eventData["output"] as? String ?? ""
                        let success = eventData["success"] as? Bool ?? false
                        let durationMs = eventData["duration_ms"] as? Int ?? 0
                        streamEvent = .toolResult(tool: tool, output: output, success: success, durationMs: durationMs)
                    case "text":
                        let token = eventData["token"] as? String ?? ""
                        streamEvent = .textChunk(text: token)
                    case "done":
                        let response = eventData["response"] as? String ?? ""
                        streamEvent = .done(response: response)
                    case "error":
                        let message = eventData["message"] as? String ?? ""
                        streamEvent = .error(message: message)
                    default:
                        streamEvent = nil
                    }

                    if let ev = streamEvent {
                        cont.yield(ev)
                    }
                }
            } catch {
                cont.finish(throwing: error)
            }
        }
    }
}

// 辅助扩展
extension Encodable {
    func toDictionary() throws -> [String: Any] {
        let data = try JSONEncoder().encode(self)
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }
}
```

### 验收测试
- `getConfig()` 返回非空 `AgentConfig`
- `updateConfig()` 保存后重新 `getConfig()` 验证
- `memoryList()` 返回 `MemoryListResponse`（entries + total + dbSizeBytes）
- `memoryDelete()` 返回 bool
- `memoryClear()` 返回清空数量
- `agentStream()` 正确解析 THINKING/ITERATION/TOOL_CALL/TOOL_RESULT 事件

---

## Task-3F: Agent Mode UI（SwiftUI）

**文件**: 新建 `App/Views/AgentModeOverlay.swift`, `ToolCallCard.swift`, `AgentThinkingBubble.swift`, `IterationProgressBar.swift`
**依赖**: Task-3E
**验收人**: QA-3F

### AgentModeOverlay

```swift
// App/Views/AgentModeOverlay.swift

struct AgentModeOverlay: View {
    @ObservedObject var viewModel: AgentModeViewModel
    let isExpanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header
            HStack {
                Image(systemName: "cpu")
                Text("Agent Activity")
                    .font(.caption.bold())
                Spacer()
                if !viewModel.activities.isEmpty {
                    IterationProgressBar(
                        current: viewModel.currentIteration,
                        max: viewModel.maxIterations
                    )
                }
            }
            .padding(.horizontal, 12)
            .padding(.top, 8)

            Divider()

            // Activity List
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 6) {
                        ForEach(viewModel.activities) { item in
                            activityView(for: item)
                                .id(item.id)
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.bottom, 8)
                }
                .onChange(of: viewModel.activities.count) { _, _ in
                    if let last = viewModel.activities.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }
        }
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(10)
        .shadow(color: .black.opacity(0.15), radius: 8, y: 2)
    }

    @ViewBuilder
    private func activityView(for item: AgentActivityItem) -> some View {
        switch item {
        case .thinking(let id, let text):
            AgentThinkingBubble(text: text)
        case .iterationStart(let id, let number):
            HStack(spacing: 4) {
                Image(systemName: "arrow.clockwise")
                    .font(.caption2)
                Text("Iteration \(number)")
                    .font(.caption2)
            }
            .foregroundColor(.secondary)
        case .toolCall(let id, let tool, let args, _):
            ToolCallCard(tool: tool, args: args, state: .running, durationMs: nil)
        case .toolResult(let id, let tool, _, let success, let durationMs):
            ToolCallCard(tool: tool, args: [:], state: success ? .done : .error, durationMs: durationMs)
        case .textChunk(let id, let text):
            Text(text)
                .font(.caption)
                .foregroundColor(.secondary)
        case .done(let id, let finalText):
            HStack(spacing: 4) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text("Done")
                    .font(.caption.bold())
                if !finalText.isEmpty {
                    Text("- \(finalText.prefix(50))...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        case .error(let id, let message):
            HStack(spacing: 4) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.red)
                Text(message)
                    .font(.caption)
                    .foregroundColor(.red)
            }
        }
    }
}
```

### ToolCallCard

```swift
// App/Views/ToolCallCard.swift

enum ToolState {
    case pending, running, done, error
}

struct ToolCallCard: View {
    let tool: String
    let args: [String: AnyCodable]
    let state: ToolState
    let durationMs: Int?

    private var icon: String {
        switch state {
        case .pending: return "clock"
        case .running: return "gear"
        case .done: return "checkmark.circle"
        case .error: return "xmark.circle"
        }
    }

    private var color: Color {
        switch state {
        case .pending: return .secondary
        case .running: return .accentColor
        case .done: return .green
        case .error: return .red
        }
    }

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundColor(color)
                .if(state == .running) { view in
                    view.symbolEffect(.pulse)
                }

            Text(tool)
                .font(.caption.monospaced().bold())
                .foregroundColor(color)

            if !args.isEmpty {
                Text(formatArgs(args))
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }

            Spacer()

            if let ms = durationMs {
                Text("\(ms)ms")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            } else if state == .running {
                ProgressView()
                    .scaleEffect(0.4)
                    .frame(width: 12, height: 12)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(color.opacity(0.08))
        .cornerRadius(6)
    }

    private func formatArgs(_ args: [String: AnyCodable]) -> String {
        let pairs = args.prefix(2).map { "\($0.key): \($0.value.value)" }
        let rest = args.count > 2 ? " +\(args.count - 2)" : ""
        return pairs.joined(separator: ", ") + rest
    }
}
```

### AgentThinkingBubble

```swift
// App/Views/AgentThinkingBubble.swift

struct AgentThinkingBubble: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Image(systemName: "brain")
                .font(.caption2)
                .foregroundColor(.purple)

            Text(text.prefix(500))
                .font(.caption)
                .foregroundColor(.secondary)
                .italic()
                .lineLimit(5)

            if text.count > 500 {
                Text("...")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.purple.opacity(0.06))
        .cornerRadius(6)
    }
}
```

### IterationProgressBar

```swift
// App/Views/IterationProgressBar.swift

struct IterationProgressBar: View {
    let current: Int
    let max: Int

    var body: some View {
        HStack(spacing: 4) {
            Text("Step \(current)/\(max)")
                .font(.caption2)
                .foregroundColor(.secondary)

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(Color.secondary.opacity(0.2))
                        .frame(height: 4)
                        .cornerRadius(2)

                    Rectangle()
                        .fill(Color.accentColor)
                        .frame(width: geo.size.width * CGFloat(current) / CGFloat(max), height: 4)
                        .cornerRadius(2)
                }
            }
            .frame(width: 60, height: 4)
        }
    }
}
```

---

## Task-3G: SettingsView（SwiftUI）

**文件**: 新建 `App/Views/SettingsView.swift`
**依赖**: Task-3E
**验收人**: QA-3G

### 规格

```swift
// App/Views/SettingsView.swift

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var config: AgentConfig = AgentConfig(model: "llama3")
    @State private var availableModels: [ModelInfo] = []
    @State private var isSaving: Bool = false
    @State private var showResetAlert: Bool = false

    private let bridge = AgentBridge.shared

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Settings")
                    .font(.headline)
                Spacer()
                Button("Done") { dismiss() }
            }
            .padding()

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    // Model Section
                    settingsSection("Model") {
                        Picker("Model", selection: $config.model) {
                            ForEach(availableModels) { m in
                                Text(m.name).tag(m.name)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(maxWidth: 300)

                        Button("Refresh Models") {
                            Task { await loadModels() }
                        }
                    }

                    // Agent Behavior Section
                    settingsSection("Agent Behavior") {
                        sliderRow("Max Iterations", value: Binding(
                            get: { Double(config.maxIterations) },
                            set: { config.maxIterations = Int($0) }
                        ), range: 1...30, step: 1, display: "\(config.maxIterations)")

                        sliderRow("Temperature", value: $config.temperature, range: 0...1.5, step: 0.1, display: String(format: "%.1f", config.temperature))

                        Toggle("Show Agent Thinking", isOn: $config.showThinking)
                        Toggle("Confirm Dangerous Tools", isOn: $config.toolConfirmation)
                    }

                    // Memory Section
                    settingsSection("Memory") {
                        Toggle("Enable Semantic Memory", isOn: $config.memorySemanticEnabled)
                        Toggle("Enable Episodic Memory", isOn: $config.memoryEpisodicEnabled)

                        sliderRow("Prune After (days)", value: Binding(
                            get: { Double(config.memoryPruneDays) },
                            set: { config.memoryPruneDays = Int($0) }
                        ), range: 7...365, step: 7, display: "\(config.memoryPruneDays)d")

                        NavigationLink("View/Manage Memories") {
                            MemoryManagerView()
                        }
                    }

                    // System Prompt Section
                    settingsSection("System Prompt") {
                        TextEditor(text: Binding(
                            get: { config.systemPrompt ?? "" },
                            set: { config.systemPrompt = $0.isEmpty ? nil : $0 }
                        ))
                        .font(.system(.body, design: .monospaced))
                        .frame(height: 150)
                        .scrollContentBackground(.hidden)
                        .background(Color.textFieldColor)
                        .cornerRadius(6)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))

                        Button("Reset to Default") {
                            showResetAlert = true
                        }
                    }

                    // Sandbox Section
                    settingsSection("Sandbox") {
                        HStack {
                            Text("Workspace:")
                                .foregroundColor(.secondary)
                            TextField("path", text: $config.sandboxWorkspace)
                                .textFieldStyle(.roundedBorder)
                                .frame(maxWidth: 300)
                        }
                    }
                }
                .padding()
            }

            Divider()

            // Save Button
            HStack {
                Spacer()
                Button(isSaving ? "Saving..." : "Save Changes") {
                    Task { await saveConfig() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isSaving || config.model.isEmpty)
            }
            .padding()
        }
        .frame(width: 600, height: 700)
        .task {
            await loadConfig()
            await loadModels()
        }
        .alert("Reset System Prompt?", isPresented: $showResetAlert) {
            Button("Cancel", role: .cancel) {}
            Button("Reset") { config.systemPrompt = nil }
        }
    }

    private func settingsSection<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.subheadline.bold())
                .foregroundColor(.secondary)
            content()
        }
    }

    private func sliderRow(_ label: String, value: Binding<Double>, range: ClosedRange<Double>, step: Double, display: String) -> some View {
        HStack {
            Text(label)
            Slider(value: value, in: range, step: step)
            Text(display)
                .frame(width: 40)
                .foregroundColor(.secondary)
        }
    }

    private func loadConfig() async {
        do {
            config = try await bridge.getConfig()
        } catch {
            // use defaults
        }
    }

    private func loadModels() async {
        do {
            availableModels = try await bridge.listModels()
        } catch {}
    }

    private func saveConfig() async {
        isSaving = true
        do {
            config = try await bridge.updateConfig(config)
        } catch {}
        isSaving = false
    }
}
```

---

## Task-3H: MemoryManagerView（SwiftUI）

**文件**: 新建 `App/Views/MemoryManagerView.swift`
**依赖**: Task-3E
**验收人**: QA-3H

### 规格

```swift
// App/Views/MemoryManagerView.swift

struct MemoryManagerView: View {
    @State private var entries: [MemoryEntry] = []
    @State private var total: Int = 0
    @State private var dbSizeBytes: Int = 0
    @State private var selectedType: String? = nil  // nil = all
    @State private var searchText: String = ""
    @State private var selectedIds: Set<String> = []
    @State private var isLoading: Bool = false
    @State private var showDeleteAlert: Bool = false
    @State private var showClearAlert: Bool = false

    private let bridge = AgentBridge.shared

    var body: some View {
        VStack(spacing: 0) {
            // Usage Stats
            HStack(spacing: 24) {
                statCard("Semantic", count: semanticCount, icon: "brain")
                statCard("Episodic", count: episodicCount, icon: "clock")
                statCard("Total", count: total, icon: "memorychip")
                Spacer()
                Text(formatBytes(dbSizeBytes))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding()
            .background(Color(nsColor: .controlBackgroundColor))

            Divider()

            // Search
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.secondary)
                TextField("Search memories...", text: $searchText)
                    .textFieldStyle(.plain)
                    .onSubmit { Task { await searchMemories() } }
                if !searchText.isEmpty {
                    Button { searchText = ""; Task { await loadMemories() } } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(10)
            .background(Color.textFieldColor)
            .cornerRadius(8)
            .padding(.horizontal)
            .padding(.top, 12)

            // Type Filter
            Picker("Filter", selection: $selectedType) {
                Text("All").tag(nil as String?)
                Text("Semantic").tag("semantic" as String?)
                Text("Episodic").tag("episodic" as String?)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)
            .padding(.vertical, 8)
            .onChange(of: selectedType) { _, _ in
                Task { await loadMemories() }
            }

            // List
            List(entries, selection: $selectedIds) { entry in
                MemoryEntryRow(entry: entry)
                    .tag(entry.id)
            }
            .listStyle(.plain)

            Divider()

            // Actions
            HStack {
                Button("Delete Selected (\(selectedIds.count))") {
                    showDeleteAlert = true
                }
                .disabled(selectedIds.isEmpty)

                Spacer()

                Button("Clear All Semantic") {
                    selectedType = "semantic"
                    showClearAlert = true
                }

                Button("Clear All") {
                    showClearAlert = true
                }
                .foregroundColor(.red)

                Button("Refresh") {
                    Task { await loadMemories() }
                }
            }
            .padding()
        }
        .frame(minWidth: 600, minHeight: 400)
        .task {
            await loadMemories()
        }
        .alert("Delete \(selectedIds.count) memories?", isPresented: $showDeleteAlert) {
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                Task { await deleteSelected() }
            }
        }
        .alert("Clear \(selectedType ?? "all") memories?", isPresented: $showClearAlert) {
            Button("Cancel", role: .cancel) {}
            Button("Clear", role: .destructive) {
                Task { await clearMemories() }
            }
        }
    }

    private var semanticCount: Int { entries.filter { $0.memoryType == "semantic" }.count }
    private var episodicCount: Int { entries.filter { $0.memoryType == "episodic" }.count }

    private func loadMemories() async {
        isLoading = true
        do {
            let resp = try await bridge.memoryList(type: selectedType, limit: 100)
            entries = resp.entries
            total = resp.total
            dbSizeBytes = resp.dbSizeBytes
        } catch {}
        isLoading = false
    }

    private func searchMemories() async {
        guard !searchText.isEmpty else { await loadMemories(); return }
        // Reuse memory_search IPC - search returns relevance-scored results
        do {
            let results = try await bridge.memorySearch(query: searchText, topK: 20)
            // Convert to MemoryEntry if needed (reuse existing bridge method)
            entries = results.compactMap { $0.entry }
            total = entries.count
        } catch {
            await loadMemories()
        }
    }

    private func deleteSelected() async {
        for id in selectedIds {
            _ = try? await bridge.memoryDelete(id: id)
        }
        selectedIds.removeAll()
        await loadMemories()
    }

    private func clearMemories() async {
        _ = try? await bridge.memoryClear(type: selectedType)
        await loadMemories()
    }

    private func statCard(_ label: String, count: Int, icon: String) -> some View {
        VStack(spacing: 2) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundColor(.secondary)
            Text("\(count)")
                .font(.headline)
            Text(label)
                .font(.caption2)
                .foregroundColor(.secondary)
        }
    }

    private func formatBytes(_ bytes: Int) -> String {
        let kb = Double(bytes) / 1024
        if kb < 1024 { return String(format: "%.1f KB", kb) }
        let mb = kb / 1024
        return String(format: "%.1f MB", mb)
    }
}

struct MemoryEntryRow: View {
    let entry: MemoryEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Circle()
                    .fill(entry.memoryType == "semantic" ? Color.purple : Color.orange)
                    .frame(width: 8, height: 8)
                Text(entry.content.prefix(80) + (entry.content.count > 80 ? "..." : ""))
                    .font(.body)
                    .lineLimit(2)
                Spacer()
                Text(entry.memoryType)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            HStack {
                Text(formatDate(entry.createdAt))
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Text("⭐" + String(repeating: "⭐", count: min(Int(entry.importance * 5), 5)))
                    .font(.caption2)
                    .foregroundColor(.orange)
            }
        }
        .padding(.vertical, 4)
    }

    private func formatDate(_ ts: Double) -> String {
        let d = Date(timeIntervalSince1970: ts)
        let f = DateFormatter()
        f.dateStyle = .short
        f.timeStyle = .short
        return f.string(from: d)
    }
}
```

---

## 依赖安装

```bash
# Phase 3 无新增 Python 依赖（web_search 用 curl，无需额外库）
# 所有 Phase 3 Swift 代码使用 Foundation + SwiftUI（无需额外包）
```

---

## 提交规范

1. 每个 Task 完成后，Builder 更新 `TASKS.md` 状态
2. 每个 Task 完成后，必须提交 PR（phase2 → phase3）
3. 每个 Task 必须有对应的测试文件（`Core/test_*.py`）
4. Swift 文件：Xcode 编译通过即可（无需额外 UI 测试）
5. 所有测试通过后才能提交 QA 审核

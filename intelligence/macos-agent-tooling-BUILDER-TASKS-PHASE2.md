# Phase 2 Builder 任务包
> CTO → Builder | 日期: 2026-03-26

---

## 概览

Phase 2 实现 **Agent 执行层 + 记忆管理层**，共 5 个任务，顺序执行。
Task-2A 可独立开发，Task-2B/2C 可并行，Task-2D 依赖 2A+2B+2C，Task-2E 依赖全部。

---

## Task-2A: Tool Executor（工具执行层）

**文件**: `Core/tool_executor.py`（新建）
**依赖**: 无
**验收人**: QA-2A

### 详细规格

#### 1. 目录与 Workspace 设置
```python
SANDBOX_WORKSPACE = os.path.expanduser("~/.macos-agent-workspace")

def _ensure_workspace():
    os.makedirs(SANDBOX_WORKSPACE, exist_ok=True)
```

#### 2. ToolResult 数据类（参考架构文档）
```python
@dataclass
class ToolResult:
    tool: str
    input_args: dict
    output: str
    error: str = ""
    success: bool = True

    def to_dict(self) -> dict: ...
    def to_observation(self) -> str: ...
```

#### 3. bash 工具
- **白名单**: `ls, cat, head, tail, grep, find, wc, echo, pwd, mkdir, rm, cp, mv, touch, chmod, git, python3, pip3, curl, wget, osascript`
- **检查**: `shlex.split(command)[0]` 必须在白名单
- **超时**: `asyncio.create_subprocess_shell` + `asyncio.wait_for(proc.communicate(), timeout=timeout)`
- **Sandbox**: `cwd=SANDBOX_WORKSPACE`
- **输出限制**: `limit=1024*1024`（1MB）

#### 4. 文件工具
- **路径验证**: `_validate_path(path)` — 必须以 `SANDBOX_WORKSPACE` 开头
- **read_file**: `max_lines=500`，超过 100K chars 截断
- **write_file**: 自动创建父目录，`mode="w"`
- **list_dir**: 排序输出

#### 5. 工具注册表
```python
class ToolExecutor:
    def __init__(self, workspace: str = SANDBOX_WORKSPACE)
    def get_tool_schemas(self) -> list[dict]: ...
    async def execute(self, tool_name: str, args: dict) -> ToolResult: ...
```

#### 6. 工具 Schema 格式
```json
{
  "name": "bash",
  "description": "Execute a bash command...",
  "input_schema": {
    "type": "object",
    "properties": {
      "command": {"type": "string", "description": "..."},
      "timeout": {"type": "integer", "default": 30},
      "working_dir": {"type": "string"}
    },
    "required": ["command"]
  }
}
```

### 验收测试
```python
# test_tool_executor.py（需要编写）
async def test_bash_whitelist():
    # ls 应该成功
    r = await executor.execute("bash", {"command": "ls"})
    assert r.success

async def test_bash_blocked():
    # rm -rf / 应该被拦截
    r = await executor.execute("bash", {"command": "rm -rf /"})
    assert not r.success

async def test_path_traversal():
    # 读取 /etc/passwd 应该失败
    r = await executor.execute("read_file", {"path": "/etc/passwd"})
    assert not r.success
```

---

## Task-2B: Memory Manager（记忆管理层）

**文件**: `Core/memory_manager.py`（新建）
**依赖**: `sentence-transformers>=3.0.0`
**验收人**: QA-2B

### 详细规格

#### 1. 数据类
```python
@dataclass
class MemoryEntry:
    id: str; content: str; memory_type: str  # "semantic" | "episodic"
    session_id: str | None; importance: float; created_at: float
    metadata: dict; embedding: list[float] | None

@dataclass
class SearchResult:
    entry: MemoryEntry; score: float
```

#### 2. 数据库初始化
- DB 路径: `~/.macos-agent-tooling/memory.db`
- `episodic_memories` 表 + FTS5 virtual table + 3 个触发器（ai/ad/au）
- `semantic_memories` 表（embedding 存为 JSON string）

#### 3. Embedding
```python
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

async def _get_embedding(self, texts: list[str]) -> list[list[float]]:
    # 延迟加载 sentence_transformers
    # 返回 normalize 后的向量（余弦相似度等价）
    # 维度: 384
```

#### 4. Semantic Memory
```python
async def add_semantic_memory(content, importance=0.5, metadata=None) -> str: ...
async def search_semantic(query, top_k=5) -> list[SearchResult]: ...
# 余弦相似度 = sum(a*b for a,b in zip(query_vec, stored_vec))
```

#### 5. Episodic Memory
```python
async def add_episodic_memory(content, session_id=None, importance=0.5, metadata=None) -> str: ...
async def search_episodic(query, top_k=5) -> list[SearchResult]: ...
# 使用 FTS5 MATCH + bm25() 排序
```

#### 6. 统一检索
```python
async def search(query, top_k=5, memory_types=None) -> list[SearchResult]:
    # 并发查 semantic + episodic
    # 合并去重，按 score 排序
```

#### 7. Session 摘要
```python
async def summarize_session(session_id, messages: list[dict]) -> str:
    # 调用 OllamaBridge 生成 2-3 句摘要
    # 存入 episodic memory
```

#### 8. 清理
```python
async def prune_old_memories(cutoff_days=30) -> int:
    # 删除 created_at < cutoff AND importance < 0.5 的记忆
```

### 验收测试
```python
async def test_semantic_memory_roundtrip():
    mm = MemoryManager()
    mid = await mm.add_semantic_memory("I prefer dark mode")
    results = await mm.search_semantic("dark theme")
    assert any(r.entry.content == "I prefer dark mode" for r in results)

async def test_episodic_fts():
    mm = MemoryManager()
    await mm.add_episodic_memory("Used git to commit changes", session_id="s1")
    results = await mm.search_episodic("git commit")
    assert len(results) > 0
```

---

## Task-2C: Context Window Manager

**文件**: `Core/context_window.py`（新建）
**依赖**: `tiktoken>=0.7.0`
**验收人**: QA-2C

### 详细规格

```python
class ContextWindowManager:
    def __init__(self, max_tokens: int = 8192, model: str = "cl100k_base")
    def count_tokens(self, text: str) -> int: ...
    def build_context(
        self,
        system: str,
        memories: list[str],
        messages: list[dict],  # [{"role": ..., "content": ...}]
        user_input: str,
    ) -> tuple[list[dict], int]: ...
```

#### Token 预算分配
1. system prompt（固定优先）
2. user_input（固定优先）
3. memories（从上往下填，按重要性排序）
4. messages（从最新往最旧，保留能塞下的）

#### 截断策略
- 使用 middle truncation（保留开头和结尾 messages）
- 超出时从中间开始删除

### 验收测试
```python
def test_count_tokens():
    cwm = ContextWindowManager(max_tokens=100)
    assert cwm.count_tokens("hello world") >= 2

def test_build_context_truncation():
    # 构造超长 messages，验证截断
    long_msg = "x" * 10000
    msgs = [{"role": "user", "content": long_msg}]
    ctx, tokens = cwm.build_context("system", [], msgs, "hi")
    assert tokens <= cwm.max_tokens
```

---

## Task-2D: Agent Executor（ReAct Loop）

**文件**: `Core/agent_executor.py`（新建）
**依赖**: Task-2A, Task-2B, Task-2C
**验收人**: QA-2D

### 详细规格

#### 1. 数据类
```python
class AgentEventType(Enum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TEXT = "text"
    DONE = "done"
    ERROR = "error"

@dataclass
class AgentEvent:
    type: AgentEventType; data: dict

@dataclass
class AgentConfig:
    model: str = "llama3"
    max_iterations: int = 10
    max_context_tokens: int = 8192
    temperature: float = 0.7
```

#### 2. AgentExecutor 主类
```python
class AgentExecutor:
    def __init__(
        self,
        ollama_bridge: OllamaBridge,
        memory_manager: MemoryManager,
        tool_executor: ToolExecutor,
        config: AgentConfig | None = None,
    ): ...

    async def execute(
        self,
        user_input: str,
        session_id: str,
        system_override: str | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def stop(self): ...
```

#### 3. ReAct Loop 流程
```
while iteration < max_iterations:
    1. 构建消息列表（system + memories + history）
    2. bridge.chat(messages) → 流式 token
    3. 累加全文到 response_text
    4. _parse_tool_calls(response_text) → list[tool_calls]
    5. if no tool_calls → DONE，return
    6. for each tool_call:
         yield TOOL_CALL
         result = await tools.execute(name, args)
         yield TOOL_RESULT
         messages.append(user=observation)
    7. if tool_name == "done" → DONE，return
    8. loop
```

#### 4. 工具调用解析
- 匹配 `<tool_calls>...</tool_calls>` 块
- 提取 `<tool name="xxx">{json}</tool>` 格式
- JSON 解析失败时 `{"raw": args_str}` 作为 fallback
- 如果没有 `<tool_calls>` 块 → final response

#### 5. System Prompt
```python
SYSTEM_PROMPT = """You are a helpful macOS AI assistant.
Available tools: {tool_schemas}
When done, call the done tool."""
```

### 验收测试
```python
async def test_agent_no_tool_call():
    # 模拟 LLM 返回纯文本（无 tool_calls）
    # 应 yield TEXT + DONE
    pass

async def test_agent_max_iterations():
    # 模拟 LLM 始终返回 tool_calls
    # 应在 max_iterations 后 ERROR
    pass

async def test_parse_tool_calls():
    text = '<tool_calls><tool name="bash">{"command": "ls"}</tool></tool_calls>'
    calls = executor._parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "bash"
```

---

## Task-2E: IPC 扩展 + Swift AgentBridge

**文件**: `Core/ipc.py`（修改）+ `App/IPC/AgentBridge.swift`（修改）
**依赖**: Task-2A, Task-2B, Task-2D
**验收人**: QA-2E

### IPC 扩展命令

在 `Core/ipc.py` 的 `handle_request_sync` 中添加：

#### `get_tools`
```python
elif cmd == "get_tools":
    schemas = tool_executor.get_tool_schemas()
    resp = {"ok": True, "data": {"tools": schemas}}
```

#### `memory_search`
```python
elif cmd == "memory_search":
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    results = await memory_mgr.search(query, top_k=top_k)
    resp = {
        "ok": True,
        "data": {"results": [{"entry": r.entry.to_dict(), "score": r.score} for r in results]}
    }
```

#### `memory_add`
```python
elif cmd == "memory_add":
    content = args.get("content", "")
    memory_type = args.get("type", "semantic")
    importance = args.get("importance", 0.5)
    mid = await memory_mgr.add_semantic_memory(content, importance=importance)
    resp = {"ok": True, "data": {"id": mid}}
```

#### `agent_execute`（非流式）
```python
elif cmd == "agent_execute":
    task = args.get("task", "")
    session_id = args.get("session_id", "")
    model = args.get("model", "llama3")
    # ... instantiate executor and run
```

#### `_agent_stream`（流式）
在 `run_server()` 的 stream 分支添加：
```python
elif cmd == "_agent_stream":
    # 类似 handle_chat_stream，输出 JSON lines
    async for chunk in handle_agent_stream(args, request_id):
        yield json.dumps({"event": event.type.value, "data": event.data}) + "\n"
```

### Swift AgentBridge 扩展

```swift
// App/IPC/AgentBridge.swift

// 新增类型
struct SearchResultEntry: Codable {
    let id: String
    let content: String
    let memoryType: String
    let importance: Float
    let createdAt: Double
    enum CodingKeys: String, CodingKey {
        case id, content, importance, createdAt
        case memoryType = "memory_type"
    }
}

struct SearchResult: Codable {
    let entry: SearchResultEntry
    let score: Float
}

// 新增方法
func memorySearch(query: String, topK: Int = 5) async throws -> [SearchResult] {
    let resp = try await sendRequest(cmd: "memory_search", args: ["query": query, "top_k": topK])
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(...) }
    let data = resp["data"] as? [String: Any]
    let results = data?["results"] as? [[String: Any]] ?? []
    return results.compactMap { try? JSONSerialization.data(withJSONObject: $0) }
        .compactMap { try? JSONDecoder().decode(SearchResult.self, from: $0) }
}

func memoryAdd(content: String, type: String = "semantic", importance: Float = 0.5) async throws -> String {
    let resp = try await sendRequest(cmd: "memory_add", args: ["content": content, "type": type, "importance": importance])
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(...) }
    return (resp["data"] as? [String: Any])?["id"] as? String ?? ""
}

func agentExecute(task: String, sessionId: String, model: String = "llama3") async throws -> String {
    let resp = try await sendRequest(cmd: "agent_execute", args: ["task": task, "session_id": sessionId, "model": model])
    guard resp["ok"] as? Bool == true else { throw AgentBridgeError.serverError(...) }
    return (resp["data"] as? [String: Any])?["result"] as? String ?? ""
}

func agentStream(task: String, sessionId: String, model: String = "llama3") -> AsyncThrowingStream<[String: Any], Error> {
    AsyncThrowingStream { cont in
        Task {
            // 实现流式读取：发送 _agent_stream 命令，逐行解析 JSON
        }
    }
}
```

---

## 依赖安装

```bash
pip install sentence-transformers>=3.0.0 tiktoken>=0.7.0
```

---

## 提交规范

1. 每个 Task 完成后，Builder 在 `TASKS.md` 更新状态
2. 每个 Task 完成后，必须提交 PR（main → phase2）
3. 每个 Task 必须有对应的测试文件（`Core/test_*.py`）
4. 所有测试通过后才能提交 QA 审核

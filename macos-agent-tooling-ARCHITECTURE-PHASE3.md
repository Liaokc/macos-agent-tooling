# macOS 本地Agent工具链 — Phase 3 架构设计
> CTO 输出 | 版本: v0.1 | 日期: 2026-03-26

---

## Status: 已完成

## 关联文档
- Phase 1 架构：`intelligence/macos-agent-tooling-ARCHITECTURE.md`
- Phase 2 架构：`macos-agent-tooling-ARCHITECTURE-PHASE2.md`
- Phase 3 Builder 任务包：`macos-agent-tooling-BUILDER-TASKS-PHASE3.md`

---

## 1. Phase 3 目标与边界

### 目标
让 Agent **真正可用 + 可交互**：用户能看见 Agent 在做什么、能配置 Agent 行为、能管理记忆。

### Phase 1+2 已完成
| 模块 | 状态 | 文件 |
|------|------|------|
| SwiftUI Chat UI | ✅ | `App/Views/ChatView.swift` |
| Ollama Bridge | ✅ | `Core/ollama_bridge.py` |
| Session Manager | ✅ | `Core/session_manager.py` |
| Tool Executor | ✅ | `Core/tool_executor.py` |
| Memory Manager | ✅ | `Core/memory_manager.py` |
| Context Window Manager | ✅ | `Core/context_window.py` |
| Agent Executor（ReAct loop） | ✅ | `Core/agent_executor.py` |
| IPC Layer | ✅ | `Core/ipc.py` |
| Swift AgentBridge | ✅ | `App/IPC/AgentBridge.swift` |

### Phase 3 新增模块
```
┌─────────────────────────────────────────────────────────────────────┐
│                  SwiftUI App (Phase 3 扩展)                         │
│                                                                      │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │ AgentModeView │  │  SettingsView   │  │  MemoryManagerView  │   │
│  │ (工具调用可视化)│  │  (配置面板)     │  │  (记忆管理面板)      │   │
│  └───────┬───────┘  └────────┬─────────┘  └──────────┬───────────┘   │
│          │                   │                        │              │
│          └───────────────────┼────────────────────────┘              │
│                              │ SwiftUI 绑定                          │
└──────────────────────────────┼──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                   Core/Python (Phase 3 新增)                         │
│                                                                      │
│  ┌───────────────────┐  ┌──────────────────────┐  ┌──────────────┐   │
│  │  ToolTemplates    │  │  ConfigManager       │  │ AgentMode    │   │
│  │  (扩展工具集)     │  │  (配置持久化)        │  │  Orchestrator│   │
│  │                   │  │                      │  │  (工具调用追踪)│   │
│  └───────┬───────────┘  └──────────────────────┘  └──────┬───────┘   │
│          │                                                │           │
│          │        ┌───────────────────────────────────────┘           │
│          │        │  (复用 Phase 2 所有模块)                           │
│          │        │                                                     │
└──────────┼────────┼─────────────────────────────────────────────────────┘
           │        │
           ▼        ▼
      (Ollama / SQLite / 文件系统)
```

---

## 2. Agent Mode UI（核心新功能）

### 2.1 设计目标
用户能看到 Agent 执行过程中的每一步：正在思考什么、调用了什么工具、工具返回了什么。

### 2.2 AgentEvent 扩展

Phase 2 的 `AgentEventType` 已有 5 种类型，Phase 3 扩展为 7 种：

```python
class AgentEventType(Enum):
    # Phase 2 已有
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TEXT = "text"
    DONE = "done"
    ERROR = "error"
    # Phase 3 新增
    THINKING = "thinking"       # Agent 正在推理（LLM 输出非 tool_calls 文本）
    ITERATION = "iteration"     # 新一轮 ReAct iteration 开始
```

### 2.3 SwiftUI Agent Mode 数据模型

```swift
// App/Models/AgentMode.swift

enum AgentActivityItem: Identifiable {
    case thinking(id: UUID, text: String)
    case iterationStart(id: UUID, number: Int)
    case toolCall(id: UUID, tool: String, args: [String: AnyCodable])
    case toolResult(id: UUID, tool: String, output: String, success: Bool, duration: TimeInterval)
    case textChunk(id: UUID, text: String)
    case done(id: UUID, finalText: String)
    case error(id: UUID, message: String)

    var id: UUID { /* 从各 case 提取 */ }
}

struct AgentModeState {
    var isActive: Bool = false
    var activities: [AgentActivityItem] = []
    var currentIteration: Int = 0
    var totalDuration: TimeInterval = 0
}
```

### 2.4 Agent Thinking 检测逻辑

在 `AgentExecutor.execute()` 中，区分"思考文本"和"工具调用"：

```python
# AgentExecutor.execute() 内部修改
# 解析 LLM 输出时：
# 1. 先检查是否包含 <tool_calls> 块
# 2. 如果不包含 → yield THINKING 事件（用户可见的推理过程）
# 3. 如果包含 → 从块中提取 tool_calls，块外文本也 yield THINKING

def _parse_response(self, text: str) -> tuple[list[dict], str]:
    """
    返回 (tool_calls, thinking_text)
    thinking_text = 块外的所有文本（用户可见的推理）
    """
    start = text.find("<tool_calls>")
    end = text.find("</tool_calls>")
    if start == -1 or end == -1:
        return [], text  # 无 tool_calls，全部是 thinking
    thinking = text[:start] + text[end + len("</tool_calls>"):]
    tool_calls = self._parse_tool_calls_from_block(text[start:end + len("</tool_calls>")])
    return tool_calls, thinking.strip()
```

### 2.5 流式 UI 更新协议

Swift 端通过 `_agent_stream` 接收 JSON lines，每行一个 AgentEvent，格式：

```json
{"event": "thinking", "data": {"text": "Let me think about this..."}}
{"event": "iteration", "data": {"number": 1}}
{"event": "tool_call", "data": {"tool": "bash", "args": {"command": "ls"}, "call_id": "abc123"}}
{"event": "tool_result", "data": {"tool": "bash", "output": "...", "success": true, "duration_ms": 234}}
{"event": "text", "data": {"token": "The result is"}}
{"event": "done", "data": {"response": "..."}}
```

### 2.6 SwiftUI ChatView 改造

**现有 ChatView** 只显示 `MessageItem`（user/assistant）。
**Phase 3 改造**：在 assistant 消息气泡下方，追加 Agent Mode 展开面板。

```
┌──────────────────────────────────────────────────────────────┐
│  Assistant                                                   │
├──────────────────────────────────────────────────────────────┤
│ Thinking: Let me search for that information...              │ ← AgentThinkingView
│                                                              │
│ 🔧 bash {"command": "ls -la"}              [running...]     │ ← ToolCallCardView
│    ↳ Output: 12 items listed                        234ms   │
│                                                              │
│ 🧠 Thinking: I found the files, now let me read...            │ ← AgentThinkingView
│                                                              │
│ 🔧 read_file {"path": "notes.md"}             [done]        │ ← ToolCallCardView
│    ↗ Output: ... (preview)                            89ms  │
│                                                              │
│ Done: Here are the results...                                │ ← Final response
└──────────────────────────────────────────────────────────────┘
```

### 2.7 关键 SwiftUI 组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `AgentModeOverlay` | `App/Views/AgentModeOverlay.swift` | 全屏/浮动 agent 活动面板 |
| `ToolCallCard` | `App/Views/ToolCallCard.swift` | 单个工具调用的卡片 |
| `AgentThinkingBubble` | `App/Views/AgentThinkingBubble.swift` | thinking 文本气泡 |
| `IterationProgressBar` | `App/Views/IterationProgressBar.swift` | ReAct 迭代进度条 |
| `AgentModeViewModel` | `App/ViewModels/AgentModeViewModel.swift` | Agent Mode 状态管理 |

---

## 3. Tool Templates（扩展工具集）

### 3.1 现有工具（Phase 2）
`bash`, `read_file`, `write_file`, `list_dir`, `done`

### 3.2 Phase 3 新增工具

| 工具名 | 描述 | 安全性 |
|--------|------|--------|
| `web_search` | 使用 `curl` 调用 DuckDuckGo/Google（无 API key） | 沙箱内 |
| `read_multiple_files` | 批量读取多个文件 | workspace 路径验证 |
| `task_completion` | 标记任务完成（带摘要输出） | 内置 |
| `osascript` | 执行 AppleScript（需用户确认） | 需显式确认 |
| `http_request` | 发起 HTTP GET/POST | 沙箱内，只能 localhost 或用户指定域名 |

### 3.3 WebSearch 工具实现

```python
async def _web_search(self, query: str, max_results: int = 5) -> ToolResult:
    """
    使用 DuckDuckGo HTML 搜索（无需 API key）
    解析 HTML 获取 snippet
    """
    import urllib.parse
    encoded = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-L", "--max-time", "10", "--user-agent",
            "Mozilla/5.0", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        html = stdout.decode("utf-8", errors="replace")
        # 简单解析：提取 <a class="result__a"> 链接和 snippet
        results = self._parse_ddg_html(html, max_results)
        return ToolResult("web_search", {"query": query}, results, success=True)
    except Exception as e:
        return ToolResult("web_search", {"query": query}, "", str(e), success=False)

def _parse_ddg_html(self, html: str, max_results: int) -> str:
    # 从 HTML 中提取标题 + URL + snippet
    # 简单正则实现，不依赖外部解析库
    results = []
    # ...（正则提取 result__a, result__snippet）
    return "\n".join(results)
```

### 3.4 工具注册机制（用户自定义工具）

```python
# tool_registry.py

from dataclasses import dataclass, field
from typing import Callable, Awaitable

@dataclass
class ToolTemplate:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[ToolResult]]
    requires_confirmation: bool = False  # 危险操作需要用户确认
    enabled: bool = True

class ToolRegistry:
    """
    全局工具注册表。
    Phase 2 内置工具 + Phase 3 模板工具 + 用户自定义工具。
    """

    def __init__(self):
        self._tools: dict[str, ToolTemplate] = {}
        self._register_builtin()

    def _register_builtin(self):
        # 注册 Phase 2 内置工具（bash, read_file, etc.）
        ...

    def register(self, template: ToolTemplate):
        """用户可调用此方法注册自定义工具"""
        self._tools[template.name] = template

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def get_schemas(self) -> list[dict]:
        return [t.input_schema for t in self._tools.values() if t.enabled]

    def get_tool(self, name: str) -> ToolTemplate | None:
        return self._tools.get(name)
```

### 3.5 用户自定义工具配置格式

JSON 文件位于 `~/.macos-agent-tooling/custom_tools.json`：

```json
{
  "custom_tools": [
    {
      "name": "send_email",
      "description": "Send an email via mailgun API",
      "input_schema": {
        "type": "object",
        "properties": {
          "to": {"type": "string", "description": "Recipient email"},
          "subject": {"type": "string"},
          "body": {"type": "string"}
        },
        "required": ["to", "subject", "body"]
      },
      "handler": "python_module.function",
      "requires_confirmation": true
    }
  ]
}
```

---

## 4. Settings/Configuration UI

### 4.1 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `model` | string | "llama3" | 当前使用模型 |
| `available_models` | list[string] | [...] | 可用模型列表 |
| `max_iterations` | int | 10 | Agent 最大迭代次数 |
| `temperature` | float | 0.7 | LLM temperature |
| `memory_semantic_enabled` | bool | true | 是否启用语义记忆 |
| `memory_episodic_enabled` | bool | true | 是否启用情景记忆 |
| `memory_prune_days` | int | 30 | 记忆保留天数 |
| `system_prompt` | string | "..." | 自定义 system prompt |
| `show_thinking` | bool | true | 是否显示 Agent thinking |
| `tool_confirmation` | bool | true | 危险工具是否需确认 |
| `sandbox_workspace` | string | "~/.macos-agent-workspace" | 沙箱工作目录 |

### 4.2 ConfigManager（Python 侧）

```python
# config_manager.py

import json
import os
from dataclasses import dataclass, field
from typing import Any

CONFIG_PATH = os.path.expanduser("~/.macos-agent-tooling/config.json")

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

    def to_dict(self) -> dict: ...

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig": ...

class ConfigManager:
    """
    配置管理：持久化到 JSON，支持热重载。
    """

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._config: AgentConfig = AgentConfig()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                data = json.load(f)
                self._config = AgentConfig.from_dict(data)

    def save(self, config: AgentConfig | None = None):
        if config:
            self._config = config
        with open(self.path, "w") as f:
            json.dump(self._config.to_dict(), f, indent=2)

    def get(self) -> AgentConfig:
        return self._config

    def update(self, **kwargs):
        """原子更新部分配置"""
        d = self._config.to_dict()
        d.update(kwargs)
        self._config = AgentConfig.from_dict(d)
        self.save()
```

### 4.3 SwiftUI SettingsView

```
┌─────────────────────────────────────────────────────────────────┐
│  Settings                                               [Done]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Model                                                          │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ llama3                                              ▼   │    │
│  └────────────────────────────────────────────────────────┘    │
│  [Refresh Models]                                               │
│                                                                  │
│  Agent Behavior                                                 │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ Max Iterations          [────●─────────] 10            │    │
│  │ Temperature             [──────●───────] 0.7           │    │
│  │ Show Agent Thinking     [●]                            │    │
│  │ Confirm Dangerous Tools  [●]                            │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Memory                                                         │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ Enable Semantic Memory   [●]                            │    │
│  │ Enable Episodic Memory   [●]                            │    │
│  │ Prune After (days)       [──────●───────] 30           │    │
│  │ [View/Manage Memories]                                 │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                  │
│  System Prompt                                                  │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ You are a helpful macOS AI assistant...                 │    │
│  │                                                        │    │
│  └────────────────────────────────────────────────────────┘    │
│  [Reset to Default]                                             │
│                                                                  │
│  Sandbox                                                       │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ Workspace Path: ~/.macos-agent-workspace              │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 IPC 扩展命令（Settings）

```python
elif cmd == "get_config":
    config = config_manager.get()
    resp = {"ok": True, "data": {"config": config.to_dict()}}

elif cmd == "update_config":
    # args: {"key": "value", ...}
    config_manager.update(**args)
    resp = {"ok": True, "data": {"config": config_manager.get().to_dict()}}
```

---

## 5. Memory Management UI

### 5.1 功能需求

1. **查看记忆列表**：semantic + episodic 分tab展示
2. **搜索记忆**：全文搜索 + 语义搜索
3. **手动删除**：选中删除 / 全清
4. **记忆使用量**：条目数、数据库大小

### 5.2 IPC 扩展命令

```python
elif cmd == "memory_list":
    memory_type = args.get("type", None)  # "semantic" | "episodic" | None(all)
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)
    entries = await memory_mgr.list_memories(
        memory_type=memory_type, limit=limit, offset=offset
    )
    total = await memory_mgr.count_memories(memory_type=memory_type)
    db_size = os.path.getsize(memory_mgr.db_path)
    resp = {
        "ok": True,
        "data": {
            "entries": [e.to_dict() for e in entries],
            "total": total,
            "db_size_bytes": db_size
        }
    }

elif cmd == "memory_delete":
    memory_id = args.get("id", "")
    success = await memory_mgr.delete(memory_id)
    resp = {"ok": True, "data": {"deleted": success}}

elif cmd == "memory_clear":
    memory_type = args.get("type", None)  # None = all
    count = await memory_mgr.clear(memory_type=memory_type)
    resp = {"ok": True, "data": {"cleared": count}}
```

### 5.3 MemoryManager 新增方法

```python
# memory_manager.py 新增

async def list_memories(
    self,
    memory_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MemoryEntry]:
    """分页列出记忆（按时间倒序）"""

async def count_memories(self, memory_type: str | None = None) -> int:
    """统计记忆数量"""

async def delete(self, memory_id: str) -> bool:
    """按 ID 删除记忆"""

async def clear(self, memory_type: str | None = None) -> int:
    """清空指定类型记忆或全部记忆"""
```

### 5.4 SwiftUI MemoryManagerView

```
┌─────────────────────────────────────────────────────────────────┐
│  Memory Manager                                         [Done]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Usage                                                           │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Semantic Memories:  42 entries                          │   │
│  │ Episodic Memories:   128 entries                         │   │
│  │ Total:              170 entries                         │   │
│  │ Database Size:       2.4 MB                             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Search                                                          │
│  ┌──────────────────────────────────────────┐ [Search]         │
│  │                                          │                   │
│  └──────────────────────────────────────────┘                   │
│                                                                  │
│  [All] [Semantic] [Episodic]                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ ●  User prefers dark mode                    semantic   │   │
│  │   Created: 2026-03-25 14:30                  ⭐⭐⭐       │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ ●  Session s1 summary: Used git to commit...  episodic  │   │
│  │   Session: s1  |  Created: 2026-03-24 10:15   ⭐⭐        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  [Delete Selected]  [Clear All Semantic]  [Clear All]          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 文件结构（Phase 3 新增/修改）

```
macos-agent-tooling/
├── Core/
│   ├── tool_executor.py      ✅ Phase 2（扩展 Phase 3 工具）
│   ├── tool_registry.py      🆕 Phase 3（工具注册表）
│   ├── agent_executor.py     ✅ Phase 2（扩展 AgentEvent）
│   ├── memory_manager.py     ✅ Phase 2（扩展 list/delete/clear）
│   ├── config_manager.py     🆕 Phase 3（配置持久化）
│   ├── ipc.py                ✅ Phase 2（扩展 Phase 3 命令）
│   └── requirements.txt       （无新增依赖）
│
├── App/
│   ├── Models/
│   │   └── AgentMode.swift   🆕 Phase 3
│   ├── ViewModels/
│   │   ├── ChatViewModel.swift     ✅ Phase 2（扩展 agent mode 绑定）
│   │   └── AgentModeViewModel.swift 🆕 Phase 3
│   ├── Views/
│   │   ├── ChatView.swift          ✅ Phase 2（改造：Agent Mode 面板）
│   │   ├── AgentModeOverlay.swift  🆕 Phase 3
│   │   ├── ToolCallCard.swift      🆕 Phase 3
│   │   ├── AgentThinkingBubble.swift 🆕 Phase 3
│   │   ├── IterationProgressBar.swift 🆕 Phase 3
│   │   ├── SettingsView.swift      🆕 Phase 3
│   │   └── MemoryManagerView.swift  🆕 Phase 3
│   └── IPC/
│       └── AgentBridge.swift  ✅ Phase 2（扩展 Phase 3 IPC 方法）
│
└── intelligence/
    └── macos-agent-tooling-BUILDER-TASKS-PHASE3.md  🆕 Phase 3
```

---

## 7. 模块接口设计

### 7.1 IPC 命令总表

| 命令 | 方向 | 用途 |
|------|------|------|
| `chat` | Swift → Core | 聊天（非流式） |
| `chat_stream` | Swift → Core | 聊天流式 |
| `agent_execute` | Swift → Core | Agent 执行（非流式） |
| `_agent_stream` | Swift → Core | Agent 流式执行（JSON lines） |
| `memory_search` | Swift → Core | 记忆搜索 |
| `memory_add` | Swift → Core | 添加记忆 |
| `get_tools` | Swift → Core | 获取工具列表 |
| `get_config` | Swift → Core | 获取配置 🆕 |
| `update_config` | Swift → Core | 更新配置 🆕 |
| `memory_list` | Swift → Core | 分页列出记忆 🆕 |
| `memory_delete` | Swift → Core | 删除单条记忆 🆕 |
| `memory_clear` | Swift → Core | 清空记忆 🆕 |

### 7.2 AgentBridge.swift 新增方法

```swift
// App/IPC/AgentBridge.swift

// ── Configuration ────────────────────────────────────────────────

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
}

func getConfig() async throws -> AgentConfig
func updateConfig(_ config: AgentConfig) async throws -> AgentConfig

// ── Memory Management ─────────────────────────────────────────────

struct MemoryEntry: Codable, Identifiable {
    let id: String
    let content: String
    let memoryType: String
    let importance: Float
    let createdAt: Double
    let metadata: [String: String]
}

struct MemoryListResponse: Codable {
    let entries: [MemoryEntry]
    let total: Int
    let dbSizeBytes: Int
}

func memoryList(type: String? = nil, limit: Int = 50, offset: Int = 0) async throws -> MemoryListResponse
func memoryDelete(id: String) async throws -> Bool
func memoryClear(type: String? = nil) async throws -> Int

// ── Agent Stream（扩展 Phase 2）────────────────────────────────────

enum AgentStreamEvent {
    case thinking(text: String)
    case iterationStart(number: Int)
    case toolCall(tool: String, args: [String: AnyCodable], callId: String)
    case toolResult(tool: String, output: String, success: Bool, durationMs: Int)
    case textChunk(text: String)
    case done(response: String)
    case error(message: String)
}

func agentStream(task: String, sessionId: String, model: String = "llama3") -> AsyncThrowingStream<AgentStreamEvent, Error>
```

---

## 8. 给 Builder 的具体任务派单

### Task-3A: Agent Executor 扩展（THINKING + ITERATION 事件）
**文件**: `Core/agent_executor.py`
**依赖**: Phase 2 AgentExecutor
**验收人**: QA-3A

**规格**:
1. 新增 `THINKING` 和 `ITERATION` 事件类型
2. `_parse_response()` 返回 `(tool_calls, thinking_text)`
3. thinking_text 通过 `THINKING` 事件 yield
4. 每个 ReAct loop 开始时 yield `ITERATION` 事件
5. 单元测试覆盖 thinking 解析逻辑

---

### Task-3B: Tool Templates（扩展工具集）
**文件**: `Core/tool_registry.py`（新建）+ `Core/tool_executor.py`（扩展）
**依赖**: 无
**验收人**: QA-3B

**规格**:
1. 实现 `ToolRegistry` 类（工具注册表）
2. 注册 Phase 3 新工具：`web_search`, `read_multiple_files`, `http_request`
3. `osascript` 标记 `requires_confirmation=True`
4. 用户自定义工具从 `~/.macos-agent-tooling/custom_tools.json` 加载
5. 所有工具 schema 可通过 `registry.get_schemas()` 获取
6. 单元测试覆盖新工具

---

### Task-3C: ConfigManager（配置持久化）
**文件**: `Core/config_manager.py`（新建）
**依赖**: 无
**验收人**: QA-3C

**规格**:
1. `AgentConfig` dataclass（12 个配置项）
2. `ConfigManager` 类：加载/保存/热更新
3. IPC 新增 `get_config` + `update_config` 命令
4. 默认配置写入 `~/.macos-agent-tooling/config.json`

---

### Task-3D: MemoryManager 扩展（list/delete/clear）
**文件**: `Core/memory_manager.py`（扩展）
**依赖**: Phase 2 MemoryManager
**验收人**: QA-3D

**规格**:
1. 新增 `list_memories(memory_type, limit, offset)`
2. 新增 `count_memories(memory_type)`
3. 新增 `delete(memory_id)`
4. 新增 `clear(memory_type)`
5. IPC 新增 `memory_list` + `memory_delete` + `memory_clear`
6. 返回结果包含 `db_size_bytes`

---

### Task-3E: Swift AgentBridge 扩展
**文件**: `App/IPC/AgentBridge.swift`
**依赖**: Task-3A, Task-3C, Task-3D
**验收人**: QA-3E

**规格**:
1. 新增 `AgentConfig` struct（Swift 侧配置模型）
2. 新增 `MemoryEntry` + `MemoryListResponse`
3. 新增 `AgentStreamEvent` 枚举
4. 实现 `getConfig()`, `updateConfig()`
5. 实现 `memoryList()`, `memoryDelete()`, `memoryClear()`
6. 扩展 `agentStream()` 支持 THINKING/ITERATION 事件

---

### Task-3F: Agent Mode UI（SwiftUI）
**文件**: `App/Views/AgentModeOverlay.swift`, `ToolCallCard.swift`, `AgentThinkingBubble.swift`, `IterationProgressBar.swift`
**依赖**: Task-3E
**验收人**: QA-3F

**规格**:
1. `AgentModeOverlay`：浮动面板，展开/折叠动画
2. `ToolCallCard`：显示工具名、参数、状态（running/done/error）、耗时
3. `AgentThinkingBubble`：显示 thinking 文本，渐变透明度
4. `IterationProgressBar`：进度条显示当前 iteration / max_iterations
5. ChatView 中 assistant 消息气泡下方嵌入 Agent Mode 展开区域

---

### Task-3G: SettingsView（SwiftUI）
**文件**: `App/Views/SettingsView.swift`
**依赖**: Task-3E
**验收人**: QA-3G

**规格**:
1. 模型选择下拉框 + Refresh Models 按钮
2. Slider：max_iterations, temperature
3. Toggle：show_thinking, tool_confirmation
4. Toggle：memory_semantic_enabled, memory_episodic_enabled
5. Stepper：memory_prune_days
6. TextEditor：system_prompt（带 Reset to Default）
7. TextField：sandbox_workspace
8. 保存时调用 `updateConfig()`

---

### Task-3H: MemoryManagerView（SwiftUI）
**文件**: `App/Views/MemoryManagerView.swift`
**依赖**: Task-3E
**验收人**: QA-3H

**规格**:
1. Usage 卡片：条目数 + DB 大小
2. SearchBar：调用 `memorySearch()`
3. SegmentedControl：[All] [Semantic] [Episodic]
4. List：展示 MemoryEntry，点击展开内容
5. Delete Selected / Clear All Semantic / Clear All 按钮
6. 滑动删除支持

---

## 9. QA 审核节点

| QA 节点 | 触发条件 | 审核重点 |
|---------|---------|---------|
| **QA-3A** | Task-3A 完成 | THINKING/ITERATION 事件正确性、thinking 解析边界 case |
| **QA-3B** | Task-3B 完成 | web_search HTML 解析、路径穿越防护、osascript 确认流程 |
| **QA-3C** | Task-3C 完成 | 配置持久化原子性、JSON 损坏恢复、默认值正确 |
| **QA-3D** | Task-3D 完成 | 删除/清空正确性、db_size 准确性、分页正确 |
| **QA-3E** | Task-3E 完成 | Swift 类型安全、IPC 协议兼容性、AsyncThrowingStream |
| **QA-3F** | Task-3F 完成 | UI 流畅性（动画 60fps）、折叠/展开状态一致性 |
| **QA-3G** | Task-3G 完成 | 所有控件绑定正确、保存/重置逻辑 |
| **QA-3H** | Task-3H 完成 | 搜索实时性、删除确认弹窗、分页加载 |
| **QA-3I** | 全部完成 | 全链路集成测试：Settings → Agent → Memory → UI |

---

## 10. 风险与缓解

| 风险 | 缓解策略 |
|------|---------|
| thinking 文本过多导致 UI 卡顿 | 滚动区域固定高度，thinking 文本截断到 500 字符 |
| web_search HTML 解析脆弱 | 优先用 `curl` + `grep` 提取关键字段，不依赖完整 HTML 解析 |
| 用户配置 JSON 损坏 | ConfigManager 加载失败时回退到默认配置 |
| osascript 安全风险 | 标记 `requires_confirmation=True`，Swift 侧弹出确认 Alert |
| 记忆数据库太大 | `prune_old_memories()` 后台定期执行，UI 显示 DB 大小 |

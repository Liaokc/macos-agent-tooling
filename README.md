# macOS Agent Tooling

> 本地 AI Agent 工具链，面向 M 系列芯片优化。

## 架构

参考 `intelligence/macos-agent-tooling-ARCHITECTURE.md`（Phase 1~3 完整架构）

## 项目结构

```
macos-agent-tooling/
├── App/
│   ├── macOSAgentTooling.swift     # App entry point
│   ├── ContentView.swift          # Main view
│   ├── Views/
│   │   ├── ChatView.swift        # Chat UI
│   │   ├── AgentModeOverlay.swift # Agent 工具调用可视化 + thinking 气泡
│   │   ├── SettingsView.swift     # 模型/记忆/System prompt 配置
│   │   └── MemoryManagerView.swift # 记忆列表/搜索/删除
│   ├── ViewModels/
│   │   └── ChatViewModel.swift    # Chat state management
│   └── IPC/
│       └── AgentBridge.swift      # Swift ↔ Python IPC
├── Core/
│   ├── ollama_bridge.py          # Ollama API client (list/pull/generate/chat/stats)
│   ├── session_manager.py        # SQLite session storage (WAL mode)
│   ├── ipc.py                    # Python IPC server (subprocess JSON 行协议)
│   ├── tool_executor.py          # Bash 白名单沙箱 + 文件读写工具
│   ├── memory_manager.py         # 3层记忆 (FTS5 + embedding + episodic)
│   ├── context_window.py         # tiktoken token 计数 + middle truncation
│   ├── agent_executor.py          # ReAct loop + THINKING/ITERATION 事件
│   ├── tool_registry.py          # 工具注册表 (web_search/http_request 等)
│   └── config_manager.py         # 12项配置持久化 (JSON)
├── intelligence/                  # 架构文档
├── project.yml                    # XcodeGen config
└── requirements.txt
```

## 开发依赖

| 依赖 | 版本 | 说明 |
|------|------|------|
| httpx | ≥0.28.0 | HTTP 客户端，已安装 |
| sentence-transformers | ≥3.0.0 | Embedding 生成，已安装 |
| tiktoken | ≥0.7.0 | Token 计数，已安装 |
| XcodeGen | 最新 | macOS 项目生成，**需有网络环境** |
| Xcode | 15+ | macOS 14+ |
| Ollama | 最新 | 本地模型推理 |

## 本地运行

```bash
cd ~/.openclaw/workspace/macos-agent-tooling

# 激活虚拟环境
source .venv/bin/activate

# 启动 Ollama（另开终端）
ollama serve
ollama pull llama3

# 生成 Xcode 项目（需有网络）
xcodegen generate

# 用 Xcode 打开
open macOSAgentTooling.xcodeproj
# → Cmd+R 运行
```

## Phase 完成状态

### ✅ Phase 1 — Chat UI + 核心通信层

| Task | 状态 | 说明 |
|------|------|------|
| Xcode 项目初始化 | ✅ | project.yml + Swift App 入口 |
| Ollama Bridge | ✅ | list/pull/generate/chat/stats + Metal GPU 监控 |
| Session Manager | ✅ | SQLite WAL + CRUD + messages + JSON 导出 |
| IPC Layer | ✅ | Subprocess JSON 行协议 + AsyncThrowingStream |
| Chat UI | ✅ | SwiftUI 气泡界面 + stream + session 切换 |

### ✅ Phase 2 — Agent 执行层 + 记忆管理层

| Task | 状态 | 说明 |
|------|------|------|
| Tool Executor | ✅ | Bash 白名单沙箱（20命令）+ 路径穿越防护 |
| Memory Manager | ✅ | FTS5 全文搜索 + all-MiniLM-L6-v2 embedding |
| Context Window | ✅ | tiktoken 计数 + middle truncation |
| Agent Executor | ✅ | ReAct loop + THINKING/ITERATION 事件 |
| IPC 扩展 | ✅ | 5个新命令 + _agent_stream |

### ✅ Phase 3 — Agent UI + 配置管理 + 工具扩展

| Task | 状态 | 说明 |
|------|------|------|
| Agent Mode UI | ✅ | ToolCallCard + ThinkingBubble + IterationProgressBar |
| Tool Templates | ✅ | web_search + http_request + read_multiple_files |
| ConfigManager | ✅ | 12项配置持久化 + JSON 原子写入 |
| Memory Extensions | ✅ | list_memories + delete + clear |
| Swift AgentBridge | ✅ | AgentConfig + MemoryEntry + AgentStreamEvent |
| SettingsView | ✅ | 6配置区块 + updateConfig 绑定 |
| MemoryManagerView | ✅ | Usage 统计 + SearchBar + 分页 + 删除/清空 |

## 已知约束

1. **XcodeGen 网络问题**：本环境无法访问 GitHub，Xcode 项目需在有网络环境生成
2. **GitHub push**：SSH/HTTPS 被网络拦截，需在代理正常环境执行 `git push origin main`
3. **httpx**：纯 Python 安装，无网络依赖

## 技术亮点

- **零外部服务**：所有推理本地运行，隐私友好
- **Metal 原生优化**：M 系列芯片 GPU 加速
- **3层记忆架构**：working + semantic（FTS5+embedding）+ episodic
- **安全沙箱**：白名单命令 + workspace 路径隔离
- **原子配置写入**：JSON tmp 文件 + POSIX rename，不损坏配置

## 后续计划

- **Phase 4**：完整 App 集成 + 内测
- 工具模板扩展（代码搜索、文件对比等）
- MCP 协议支持

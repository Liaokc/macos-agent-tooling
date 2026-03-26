# macOS Agent Tooling

> 本地 AI Agent 工具链，面向 M 系列芯片优化。

## 架构

参考 `intelligence/macos-agent-tooling-ARCHITECTURE.md`

## Phase 1 文件结构

```
macos-agent-tooling/
├── App/
│   ├── macOSAgentTooling.swift      # App entry point
│   ├── ContentView.swift           # Placeholder
│   ├── Views/
│   │   └── ChatView.swift          # Chat UI
│   ├── ViewModels/
│   │   └── ChatViewModel.swift     # Chat state management
│   ├── IPC/
│   │   └── AgentBridge.swift       # Swift ↔ Python IPC
│   └── Assets.xcassets/
├── Core/
│   ├── shared_types.py             # Shared data models
│   ├── ollama_bridge.py            # Ollama API client
│   ├── session_manager.py          # SQLite session storage
│   └── ipc.py                      # Python IPC server
├── Resources/
├── project.yml                     # XcodeGen config
└── requirements.txt
```

## 开发依赖

- **XcodeGen**: `brew install xcodegen`
- **Python**: 3.11+ (推荐 3.11 或 3.12, 3.14 尚不兼容 aiosqlite)
- **Ollama**: [官方安装](https://ollama.ai)

## 本地运行

### Python Core (独立测试)

```bash
cd ~/.openclaw/workspace/macos-agent-tooling
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 测试 Ollama Bridge
python3 Core/ollama_bridge.py

# 测试 Session Manager
python3 Core/session_manager.py
```

### Xcode 项目

```bash
cd ~/.openclaw/workspace/macos-agent-tooling

# 初始化 Xcode 项目（需要 XcodeGen）
xcodegen generate

# 打开项目
open macOSAgentTooling.xcodeproj
```

### Ollama 服务

```bash
# 确保 ollama 在运行
ollama serve

# 拉取默认模型
ollama pull llama3
```

## Phase 1 完成状态

| Task | 状态 | 说明 |
|------|------|------|
| Task-MAT-001 | ✅ | XcodeGen project.yml, Swift App 入口 |
| Task-MAT-002 | ✅ | Ollama Bridge (HTTP API, 进度回调, 异常处理) |
| Task-MAT-003 | ✅ | Session Manager (SQLite, async 封装, 导出) |
| Task-MAT-004 | ✅ | Swift ↔ Python IPC (subprocess JSON, AsyncSequence) |
| Task-MAT-005 | ✅ | SwiftUI Chat (stream, session 切换, model 切换) |

## 已知约束

1. **XcodeGen 网络问题**: 本环境无法访问 GitHub/brew，Xcode 项目需手动在有网络环境生成
2. **Python 3.14 兼容性**: aiosqlite 不兼容，使用 sqlite3 + asyncio.to_thread 替代
3. **httpx**: 通过 pip 安装纯 Python 包，无网络依赖问题

## 后续计划

- **Phase 2**: Memory Manager, Agent Executor, Context Window Manager
- **Phase 3**: Tool Registry, Model 自动推荐, 产品化

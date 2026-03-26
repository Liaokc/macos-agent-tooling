# macOS Agent Tooling — Phase 1 部署指南

> 本机 XcodeGen 无法访问 GitHub（brew install 失败），提供绕过方案。

---

## 状态：✅ Python Core 可用，⚠️ Xcode 项目需在有网络机器上生成

---

## 产出物清单

| # | 文件 | 状态 |
|---|------|------|
| 1 | `project.yml` | ✅ |
| 2 | `App/macOSAgentTooling.swift` | ✅ |
| 3 | `App/ContentView.swift` | ✅ |
| 4 | `App/Views/ChatView.swift` | ✅ |
| 5 | `App/ViewModels/ChatViewModel.swift` | ✅ |
| 6 | `App/IPC/AgentBridge.swift` | ✅ |
| 7 | `Core/ollama_bridge.py` | ✅ |
| 8 | `Core/session_manager.py` | ✅ |
| 9 | `Core/ipc.py` | ✅ |
| 10 | `Assets.xcassets/` | ✅ |

---

## 依赖项

### Python 依赖
| 依赖 | 版本 | 状态 | 来源 |
|------|------|------|------|
| httpx | ≥0.28.0 | ✅ 已安装 | pip（PyPI，无网络问题） |
| sqlite3 | 内置 | ✅ | Python 标准库 |

**验证：**
```bash
cd ~/.openclaw/workspace/macos-agent-tooling
source .venv/bin/activate
python3 -c "import httpx; print('httpx OK')"
python3 Core/session_manager.py  # 无报错即通过
```

### Swift 依赖
无外部依赖。纯 Apple 框架：
- `AppKit`
- `SwiftUI`
- `Foundation`

### 系统要求
- macOS 14.0+（ Sonoma）
- Xcode 15.0+
- Ollama（运行 AI 模型）

---

## 问题：XcodeGen 网络不通

**症状：** `brew install xcodegen` / `xcodegen generate` 失败，无法访问 GitHub。

**原因：** 本机 DNS/防火墙阻断 GitHub。

### 解决方案

#### 方案 A（推荐）：手动下载 XcodeGen 二进制

在**任意有网络的 Mac/Linux 机器**上执行：

```bash
# 下载 XcodeGen（GitHub releases 直链）
curl -L https://github.com/TonyGermaneri/xcode-gen/releases/download/v2.41.0/xcode-gen-mac-arm64.tar.gz -o xcode-gen.tar.gz
# 或 x86_64：https://github.com/TonyGermaneri/xcode-gen/releases/download/v2.41.0/xcode-gen-mac-x86_64.tar.gz

tar -xzf xcode-gen.tar.gz
chmod +x xcode-gen
./xcodegen --version   # 验证
```

然后把 `xcodegen` 二进制文件复制到本机（U盘/AirDrop/scp）：

```bash
# 复制到本机任意目录，例如 ~/bin/
cp ~/Downloads/xcode-gen ~/bin/xcodegen
chmod +x ~/bin/xcodegen
export PATH="$HOME/bin:$PATH"   # 或加到 .zshrc

# 验证
xcodegen --version
```

#### 方案 B：在有网络的机器上生成 .xcodeproj

在有网络的 Mac 上安装 XcodeGen 后：

```bash
git clone <your-repo> macos-agent-tooling
cd macos-agent-tooling
xcodegen generate
# 生成 macOSAgentTooling.xcodeproj
```

把生成的 `.xcodeproj` 文件夹通过 U盘/AirDrop 复制回本机，**直接用 Xcode 打开**即可（无需再次运行 xcodegen）。

> ⚠️ 注意：`project.yml` 里写死了源文件路径为 `App/`、`Core/`、`Resources/`，移动 `.xcodeproj` 位置会破坏引用。

#### 方案 C：跳过 XcodeGen，直接用 Xcode（手动创建项目）

1. 打开 Xcode → File → New → Project → macOS → App
2. Bundle ID 填：`com.cheng-agent.macosagenttooling`
3. 取消勾选 "Create Git repository"
4. 删除自动生成的源文件，**把 `App/`、`Core/`、`Resources/` 三个目录拖入项目**
5. Build

---

## 完整部署步骤（在目标机器上）

### Step 1：复制项目文件
```bash
# 把整个 ~/.openclaw/workspace/macos-agent-tooling/ 目录复制到目标机器
# 保持目录结构不变
```

### Step 2：安装 XcodeGen（如果目标机器网络正常）
```bash
brew install xcodegen
```

### Step 3：生成 Xcode 项目
```bash
cd ~/.openclaw/workspace/macos-agent-tooling
xcodegen generate
# 生成 macOSAgentTooling.xcodeproj
```

### Step 4：用 Xcode 打开并运行
```bash
open macOSAgentTooling.xcodeproj
# Xcode 打开后，点击 ▶ Run (Cmd+R)
```

### Step 5：启动 Ollama 后台服务
```bash
# 终端运行
ollama serve

# 拉取默认模型（首次）
ollama pull llama3
```

### Step 6：验证 Python Core（可选，不影响 Swift App）
```bash
cd ~/.openclaw/workspace/macos-agent-tooling
source .venv/bin/activate
python3 Core/session_manager.py   # 无报错即通过
```

---

## 运行前提条件

1. **macOS 14.0+**（Sonoma），Apple Silicon 或 Intel
2. **Xcode 15.0+** 已安装
3. **XcodeGen** 已安装（或从其他机器复制了 .xcodeproj）
4. **Ollama** 已安装并运行在 `http://localhost:11434`
5. **至少一个模型**已下载（`ollama pull llama3` 或其他）

---

## 已知问题

| 问题 | 说明 | 解决方案 |
|------|------|---------|
| XcodeGen 无法从 GitHub 下载 | 本机网络限制 | 用方案 A/B/C 绕过 |
| Python 3.14 + aiosqlite | 不兼容 | 项目使用内置 sqlite3 + asyncio.to_thread |
| httpx | 纯 Python | pip install 即可，无 C 扩展依赖 |

---

## 文件路径

```
~/.openclaw/workspace/macos-agent-tooling/
├── App/
│   ├── macOSAgentTooling.swift     # App 入口
│   ├── ContentView.swift
│   ├── Views/ChatView.swift
│   ├── ViewModels/ChatViewModel.swift
│   ├── IPC/AgentBridge.swift
│   └── Assets.xcassets/
├── Core/
│   ├── __init__.py
│   ├── ipc.py                      # Python IPC Server（Swift 子进程）
│   ├── ollama_bridge.py
│   ├── session_manager.py
│   └── shared_types.py
├── Resources/
├── project.yml                      # XcodeGen 配置
├── requirements.txt
└── .venv/                           # Python 虚拟环境（含 httpx）
```

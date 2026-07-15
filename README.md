# Crew · the workshop

> 一个 11 人 + 1 老板 + 1 工头的**多 Agent 桌面协作应用**。
> 打开一个原生窗口，Foreman 帮你调度、干活、复盘。

![screenshot](static/crew.png)

## ✨ 特性

- 🖥️ **真正的桌面应用**（pywebview + WebView2）——不是浏览器 tab、没有黑色终端
- 👥 **11 位常驻同事** + 1 位 Foreman 工头 + 1 位 Chief 老板 → 讨论 / 布置 / 执行 / 复盘
- 🔧 **Foreman 真调用 Hermes CLI**（`hermes -z --yolo`）—— 让 agent 真正落地干活，不是画大饼
- 🧠 **10 家 LLM 兼容 + 自动识别** —— OpenAI / Anthropic / DeepSeek / Kimi / 智谱 / 火山方舟 / OpenRouter / Groq / 通义 / 硅基流动
- 🎨 **Raft 手作牛皮纸风 UI** —— LXGW WenKai 中文 + 硬派英文按钮 + 手绘描边 + 便签堆叠
- ⚙️ **随时切换 provider/model** —— 右上角设置面板、无需重启
- 🔒 **权限档位** —— Strict（每步审批）/ Balanced / Auto（Foreman 全权）
- 💾 **数据本地**（`%APPDATA%\Crew\`）—— 卸载不删数据、聊天历史留档

## 🚀 快速开始

### 直接下载安装（推荐）

到 [Releases](../../releases) 下载 `Crew-Setup-v1.3.0.exe`，双击 → 走向导 → 完成。

### 或者从源码跑

```bash
# 1. clone
git clone <this-repo-url>
cd crew-multi-agent

# 2. 装依赖（要 Python 3.11+，推荐 3.13）
pip install fastapi uvicorn httpx websockets pywebview pillow

# 3. 跑桌面版
python launcher.py

# 4. 或者只跑 server（用浏览器访问）
python server.py
# → 打开 http://127.0.0.1:8765/
```

## 🏗️ 自己打包

需要装 [Inno Setup 6](https://jrsoftware.org/isdl.php) 才能出 setup.exe。

```bash
# 装打包工具
pip install pyinstaller

# 1. 生成图标（可选，static/crew.ico 已在仓库里）
python gen_icon.py

# 2. PyInstaller 出 dist/Crew/crew.exe
pyinstaller crew.spec --noconfirm --clean

# 3. Inno Setup 出 dist/Crew-Setup-v1.3.0.exe
"C:/Users/你/AppData/Local/Programs/Inno Setup 6/ISCC.exe" crew.iss
```

## 🧭 架构

```
┌─────────────────────────────────────────────┐
│ launcher.py                                 │
│   ↓ 主线程                                    │
│   pywebview 原生窗口 (WebView2)              │
│       ↓ HTTP 127.0.0.1:8765                  │
│                                             │
│ ┌─ server.py (FastAPI + WebSocket) ────┐   │
│ │  ↓                                    │   │
│ │  supervisor.py (Foreman + Hermes)    │   │
│ │  providers.py (10 家 LLM)            │   │
│ │  static/index.html (前端)             │   │
│ └───────────────────────────────────────┘   │
│                                             │
│ 数据：%APPDATA%\Crew\                       │
│   ├─ config.json                            │
│   ├─ .env                                   │
│   ├─ team.db (SQLite)                       │
│   ├─ dynamic_agents.json                    │
│   └─ *.log                                  │
└─────────────────────────────────────────────┘
```

## 👥 团队

**产品线：** Pine（产品）/ Ash（开发）/ Wren（设计）/ Owl（测试）
**决策层：** Chief（老板）
**运营线：** Rune（数据）/ Poppy（客服）/ Judge（法务）/ Rally（运营）/ Ivy（HR）/ Ledger（财务）
**工头：** Foreman —— 分发任务、调用 Hermes CLI、跟你复盘

Foreman 拿到需求会：
1. `discuss` 讨论方案（可选）
2. `hire` 招新同事（如需）
3. `execute` → 调 `hermes -z --yolo` 真跑

## 🔑 支持的 LLM

| Provider | 默认模型 | Key 自动识别 |
|---|---|---|
| 火山方舟 (ARK) | `ark-code-latest` | 手选 |
| OpenAI | `gpt-4o-mini` | `sk-` 共用 |
| **Anthropic Claude** | `claude-3-5-sonnet-latest` | ✅ `sk-ant-` 唯一 |
| DeepSeek | `deepseek-chat` | `sk-` 共用 |
| Kimi (月之暗面) | `kimi-latest` | `sk-` 共用 |
| **智谱 GLM** | `glm-4-flash` | ✅ `hex.hex` 唯一 |
| **OpenRouter** | `anthropic/claude-3.5-sonnet` | ✅ `sk-or-` 唯一 |
| **Groq** | `llama-3.3-70b-versatile` | ✅ `gsk_` 唯一 |
| 通义千问 (阿里) | `qwen-plus` | `sk-` 共用 |
| SiliconFlow (硅基流动) | `Qwen/Qwen2.5-7B-Instruct` | `sk-` 共用 |

无法唯一识别（都用 `sk-` 前缀）时，向导会弹下拉让你手动选。

## 🛠️ 前置依赖

- **Windows 10/11**（WebView2 内置；Win10 老版本可能需要下 [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)）
- **Hermes CLI**（`hermes.exe` 在 PATH）—— 让 Foreman 真正落地执行；没装也能跑，只是执行类命令空转

## 📄 License

MIT

## 🙏 致谢

- 中文字体：[LXGW WenKai](https://github.com/lxgw/LxgwWenKai)
- 头像：[DiceBear](https://www.dicebear.com/) pixel-art
- 底层：FastAPI、pywebview、pydantic v2、火山方舟、Anthropic、OpenAI …

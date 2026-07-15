# Crew · Desktop Multi-Agent Runtime

[简体中文](./README.md) · **English**

> **An open-source desktop runtime that unifies multi-agent orchestration, multi-LLM abstraction, and multi-CLI execution.**
> pywebview + WebView2 native window in front, FastAPI + WebSocket duplex channel behind,
> 12 built-in domain agents (unlimited user-extensible), 14 LLM providers (10 cloud + 4 local/self-hosted),
> and an executor layer that transparently bridges **Hermes / Claude Code / OpenAI Codex / OpenCode / Aider / Gemini CLI**
> plus any user-defined CLI.

<p align="left">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2B-0078D6?logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/pywebview-6.2-4B8BBE" alt="pywebview">
  <img src="https://img.shields.io/badge/WebView2-Edge-0078D4?logo=microsoftedge&logoColor=white" alt="WebView2">
  <img src="https://img.shields.io/badge/protocols-OpenAI%20%7C%20Anthropic%20%7C%20Ollama%20%7C%20vLLM-blueviolet" alt="protocols">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
  <a href="https://github.com/V-lVl/crew-multi-agent/releases/latest"><img src="https://img.shields.io/github/v/release/V-lVl/crew-multi-agent" alt="Release"></a>
</p>

---

## 📖 Table of Contents

- [Positioning](#-positioning)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Team Roster](#-team-roster)
- [Workflow: How Foreman Dispatches](#-workflow-how-foreman-dispatches)
- [Supported LLM Providers](#-supported-llm-providers)
- [Supported Local Agent CLIs](#-supported-local-agent-clis)
- [User Extension Points (v1.5+)](#-user-extension-points-v15)
- [Permission Model](#-permission-model)
- [Repository Layout](#-repository-layout)
- [Where Data Lives](#-where-data-lives)
- [Building From Source](#-building-from-source)
- [Development Guide](#-development-guide)
- [FAQ](#-faq)
- [Roadmap](#-roadmap)
- [License](#-license)

---

## 🎯 Positioning

**Crew is positioned as an "agent-native desktop workstation"** — not a chatbot, but a programmable, extensible, distributable multi-agent runtime.

It addresses three concrete engineering gaps:

1. **A single agent cannot cover cross-domain collaboration.** Real work spans product, engineering, QA, legal, finance; one prompt cannot carry all that context. Crew orchestrates **multiple agents each with a domain system prompt**, giving each role identity, function, and voice.
2. **Existing multi-agent frameworks have high engineering friction.** LangGraph / CrewAI / AutoGen are SDK libraries — the deliverable is "write your own runner". Crew ships a **compiled desktop binary** + first-run wizard + graphical config to cover zero-code users.
3. **Execution and reasoning stay tightly coupled.** Users want different agents to invoke different local tools (Claude Code for coding, Codex for tests, custom CLI for internal data). Crew abstracts executors as `AgentSpec` in `agents_cli.py`: runtime detection + manual switching + user-defined CLIs are all first-class.

### Three angles on Crew

| Angle | Description |
|---|---|
| **Product** | Windows `.exe` double-click; 20 MB installer; no terminal window; own icon, taskbar entry, Start menu, uninstall applet |
| **Engineering** | Python 3.11+ + FastAPI + WebSocket + SQLite + pywebview + WebView2; vanilla HTML/JS front-end; full-stack hackable |
| **Runtime** | Multi-LLM abstraction (OpenAI-compat / Anthropic Messages / local OpenAI-compat) + multi-CLI executor abstraction (subprocess + args template) + 3-tier approval + persistent sessions |

---

## ✨ Features

### Native desktop integration

- **No terminal window:** `crew.exe` has PE Subsystem = 2 (Windows GUI); launch enters the WebView2 window with no CMD box
- **Native shell:** pywebview 6.2 main process + Edge WebView2 kernel; cold start < 2 s
- **Full desktop contract:** own process icon (RT_ICON 7 sizes) + taskbar entry + Start menu shortcut + Programs & Features uninstall
- **Distribution size:** ≈ 20 MB (Setup EXE) / ≈ 22 MB (portable ZIP) — vs. comparable Electron apps at 150 MB+

### Multi-agent orchestration

- **12 built-in agents:** Foreman (task dispatcher) + Chief (decision-maker) + 10 domain teammates (product, engineering, design, QA, data, support, legal, ops, HR, finance)
- **Every agent independently configurable:** `name / role / emoji / color / system prompt / default_on`
- **v1.6+: users fully self-serve agent creation** ⭐ — fill 6 fields in the GUI panel and a new teammate is registered, no need to have Foreman hire them at runtime
- Foreman has three verbs: `discuss` (moderate multi-agent debate) · `hire` (Foreman-initiated onboarding) · `execute` (invoke real local CLI)
- **Runtime-hired + user-created agents** both persist in `dynamic_agents.json` and appear on next boot

### LLM provider abstraction layer

- **14 providers:** OpenAI · Anthropic · DeepSeek · Kimi · Zhipu GLM · VolcEngine ARK · OpenRouter · Groq · Qwen · SiliconFlow · **Ollama** · **LM Studio** · **vLLM/TGI/llama.cpp server** · **Custom OpenAI-compatible endpoint**
- **Protocols:** OpenAI Chat Completions (12 providers) + Anthropic Messages (1) + local self-hosted OpenAI-compat (4 share the same protocol)
- **API key prefix detection:** `sk-ant-*` → Anthropic; `sk-or-*` → OpenRouter; `gsk_*` → Groq; ambiguous `sk-*` triggers a dropdown
- **Key optional on local providers** (`key_optional=True`) — Ollama / LM Studio / vLLM typically don't validate keys; the UI proactively hints "leave blank"
- **Endpoint fully overridable:** default to factory URL, or drop in your own (LAN IP / reverse proxy / corporate gateway)
- **v1.6+: connectivity self-test button** ⭐ — one click fires a real request against unsaved config, returns `latency_ms` + model reply. Errors show full HTTP status + body

### Local Agent CLI abstraction ⭐

- **Executor decoupled:** Foreman hard-codes no single CLI — everything flows through `agents_cli.AgentSpec`
- **6 built-ins:** Hermes · Claude Code · OpenAI Codex · OpenCode · Aider · Gemini CLI
- **Startup auto-detection:** `shutil.which()` + npm global fallback + venv locations
- **v1.6+: user-defined CLI + inline test-run button** ⭐ — 5-field form (id / name / command / args_template / homepage) registers any local agent; per-row "试跑 (Try)" button fires `Say hello in one line.` and shows stdout + latency in real time
- **Args template system:** `{prompt}` placeholder supported; if omitted, the prompt is appended at the end
- **Absolute path or PATH command** both accepted — `C:\Tools\myagent.exe` and `myagent` (via PATH) are equivalent

### Grounded execution + 3-tier permissions

- Foreman `execute` uses `asyncio.create_subprocess_exec` to spawn the selected CLI
- stdout / stderr are streamed line-by-line back to the front-end message log
- Three tiers: **Strict** (approve every step) · **Balanced** (default, approve sensitive ops) · **Auto** (Foreman decides)
- Full audit chain: Foreman intent → UI approval card → user approve/reject → logged to `team.db`

### Local-first + data sovereignty

- Everything lives under `%APPDATA%\Crew\`: `config.json` (config) · `.env` (API keys) · `team.db` (SQLite messages + approvals) · `dynamic_agents.json` (custom agents)
- **Zero external listener:** uvicorn binds only `127.0.0.1:8765`; LAN access requires an explicit code change
- **Uninstall preserves data**; reinstall picks up where you left off (Inno Setup `UsePreviousData=yes`)

---

## 🧰 Tech Stack

| Layer | Technology | Role |
|---|---|---|
| **Packaging** | PyInstaller 6.21 (onedir) | Python + deps + static assets → standalone `crew.exe` |
| **Installer** | Inno Setup 6 | Produces `Crew-Setup-*.exe` (standard Windows installer + uninstall entry) |
| **Desktop shell** | pywebview 6.2 + Edge WebView2 | Native window, main-thread GUI |
| **Backend** | FastAPI 0.115 + Uvicorn | HTTP + WebSocket dual channels |
| **Model calls** | httpx (async) | OpenAI-compatible + Anthropic Messages, both protocols |
| **Storage** | SQLite (stdlib `sqlite3`) | Topics / messages / approvals |
| **Frontend** | Vanilla HTML + CSS + JS, no framework | Lightweight, fast startup, easy to audit |
| **Chinese font** | LXGW WenKai (web-loaded) | Sans-serif Kai style |
| **CLI bridge** | subprocess → any detected local Agent CLI | Unified abstraction in `agents_cli.py` |

**Why not Electron?** Electron ships Chromium on every install (~150 MB). WebView2 reuses the system Edge runtime — 20 MB installer, <2s cold start.

**Why no frontend framework?** The whole UI is one window with a message stream. React/Vue's build pipeline, state library, and bundle size would be negative value here. Plain HTML served by FastAPI's `StaticFiles` — save file, hit reload, done.

---

## 🏗 Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                Double-click crew.exe (Windows GUI subsystem)     │
│                              │                                  │
│                launcher.py (main process, single PID)            │
│              ┌─────────────┼─────────────┐                     │
│              │             │             │                     │
│         [main thread]  [daemon thread]                          │
│              │             │                                    │
│      pywebview.create   uvicorn.run                             │
│         Edge WebView2   FastAPI @ 127.0.0.1:8765                │
│         native window     │                                     │
│              ↕            │                                     │
│         HTTP + WS ────────┤                                     │
│                           │                                     │
│                    ┌──────┴──────┐                              │
│                    │  server.py  │                              │
│                    │  (API tier) │                              │
│                    └──────┬──────┘                              │
│                           │                                     │
│           ┌───────────────┼──────────────┐                      │
│           ↓               ↓              ↓                      │
│    supervisor.py    providers.py    static/index.html          │
│  (Foreman logic)   (10 LLM plumbing)  (frontend SPA)            │
│           │                                                     │
│           ↓                                                     │
│    agents_cli.py (local CLI abstraction + auto-detect)          │
│           │                                                     │
│           └─→ subprocess → hermes / claude / codex /            │
│                            opencode / aider / gemini            │
│                                                                 │
│  Data on disk:  %APPDATA%\Crew\                                 │
│    ├─ config.json          # prefs + provider + selected CLI    │
│    ├─ .env                 # API keys (plaintext, local only)    │
│    ├─ team.db              # SQLite: topics / msgs / approvals   │
│    ├─ dynamic_agents.json  # runtime-hired teammates             │
│    └─ launcher.log         # boot log                            │
└────────────────────────────────────────────────────────────────┘
```

### Key design constraints

- **pywebview must run on the main thread** (Windows COM requirement). uvicorn goes into a daemon thread, so exiting the process is total.
- **`console=False` (Windows GUI subsystem)** — no CMD window flashes on launch. Side effect: `sys.stdout = None`, so every `print` goes through `_redirect_std_streams` and gets swallowed into a log file.
- **ASSETS_DIR vs. DATA_DIR:** read-only assets live inside the exe (PyInstaller `_MEIPASS`); writable data lives under `%APPDATA%`. Uninstall/reinstall keeps history.
- **Port 8765 is hardcoded.** Multi-instance would require dynamic port allocation.

---

## 🚀 Quick Start

### Option 1 — Installer (recommended)

1. Grab the latest **`Crew-Setup-vX.X.X.exe`** (~20 MB) from the [Releases page](https://github.com/V-lVl/crew-multi-agent/releases/latest)
2. Double-click it, walk through the wizard (optional desktop / Start-menu shortcuts)
3. Launch from the desktop or Start menu
4. First run pops the onboarding wizard — paste any provider's API key. Crew auto-detects the provider from the prefix.

**Uninstall:** Control Panel → Programs and Features → Crew → Uninstall. Your data stays at `%APPDATA%\Crew\`; delete it manually if you want a clean wipe.

### Option 2 — Portable

1. Grab **`Crew-vX.X-win64.zip`** (~22 MB)
2. Extract anywhere
3. Double-click `crew.exe`

No admin rights, no registry writes.

### Option 3 — From source

For development, debugging, or hacking on internals.

```bash
# 1. Clone
git clone https://github.com/V-lVl/crew-multi-agent.git
cd crew-multi-agent

# 2. Virtual env (Python 3.11+, 3.13 recommended)
python -m venv .venv
.venv\Scripts\activate

# 3. Install deps
pip install fastapi "uvicorn[standard]" httpx websockets pywebview pillow

# 4. Desktop mode (native window)
python launcher.py

# Or web-only mode (open http://127.0.0.1:8765/ in your browser)
python server.py
```

**Requires Windows 10 / 11.** Older Windows 10 builds may lack the WebView2 Runtime — grab the Evergreen build from [Microsoft](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).

---

## 👥 Team Roster

| Role | Name | Purpose | When they appear |
|---|---|---|---|
| Chief | **Chief** | Decision-making, sign-off, retrospection | Strategy calls, final approval |
| Foreman | **Foreman** | Dispatch, hiring, execution | **Always present** — receives every incoming task |
| Product | **Pine** | PRDs, requirements, prioritization | Fuzzy needs, scope creep |
| Engineering | **Ash** | Architecture, code, tech choices | Implementation, debugging, perf |
| Design | **Wren** | UI/UX, visuals, interaction | UI, ergonomics, usability |
| QA | **Owl** | Test cases, regression, verification | Pre-release, bug repro |
| Data | **Rune** | Metrics, tracking, analysis | Data-driven decisions, A/B outcomes |
| Support | **Poppy** | User feedback, FAQs | User complaints, post-sales |
| Legal | **Judge** | Compliance, terms, privacy | User data, contracts |
| Ops / Growth | **Rally** | Growth, campaigns, content | Acquisition, community, conversion |
| HR | **Ivy** | Hiring, team, collaboration | Personnel, team growth |
| Finance | **Ledger** | Budget, cost, reporting | Budget approval, cost analysis |

**Each teammate's system prompt** lives in the `AGENTS` dict in `supervisor.py` — personality, tone, capability boundaries, tool preferences.

**Dynamic hiring:** if a task needs "a game designer" or "a molecular biology expert", Foreman writes a fresh system prompt and appends to `dynamic_agents.json` for future sessions.

---

## 🔄 Workflow: How Foreman Dispatches

**User sends a message → Foreman decides → one of three verbs:**

```mermaid
flowchart TB
    U["User message"] --> F{"Foreman analysis"}
    F -->|Unclear plan / diverging opinions| D["discuss<br/>call a meeting"]
    F -->|Needs a role not on roster| H["hire<br/>write system prompt & onboard"]
    F -->|Plan is clear / ready to ship| E["execute<br/>invoke local CLI"]

    D --> Q["Pull relevant teammates into a topic"]
    Q --> F

    H --> N["Persist to dynamic_agents.json"]
    N --> F

    E --> A{"Permission tier"}
    A -->|Strict| P["Approval card per step"]
    A -->|Balanced| P2["Approval on key steps"]
    A -->|Auto| X["Run immediately"]
    P --> R["hermes / claude / codex / …"]
    P2 --> R
    X --> R
    R --> L["Stream stdout via WebSocket"]
    L --> C["Chief reviews & archives"]
```

**Approval card in the UI:**

```
┌────────────────────────────────────────────────┐
│ Foreman wants to execute (Claude Code):         │
│   Command: claude -p "Write a Python script..." │
│   Estimated: 30s                                 │
│                                                 │
│   [Approve]   [Reject]   [Details]              │
└────────────────────────────────────────────────┘
```

---

## 🔌 Supported LLM Providers

### Cloud APIs (10 providers)

| Provider | Default model | Key prefix detection | Protocol |
|---|---|---|---|
| **VolcEngine ARK** | `ark-code-latest` | Manual only | OpenAI-compatible |
| **OpenAI** | `gpt-4o-mini` | `sk-` (shared) | OpenAI native |
| **Anthropic Claude** | `claude-3-5-sonnet-latest` | ✅ `sk-ant-` unique | Anthropic Messages |
| **DeepSeek** | `deepseek-chat` | `sk-` (shared) | OpenAI-compatible |
| **Kimi** (Moonshot) | `kimi-latest` | `sk-` (shared) | OpenAI-compatible |
| **Zhipu GLM** | `glm-4-flash` | ✅ `hex.hex` unique | OpenAI-compatible |
| **OpenRouter** | `anthropic/claude-3.5-sonnet` | ✅ `sk-or-` unique | OpenAI-compatible |
| **Groq** | `llama-3.3-70b-versatile` | ✅ `gsk_` unique | OpenAI-compatible |
| **Qwen** (Alibaba) | `qwen-plus` | `sk-` (shared) | OpenAI-compatible |
| **SiliconFlow** | `Qwen/Qwen2.5-7B-Instruct` | `sk-` (shared) | OpenAI-compatible |

### Local / self-hosted deployments (v1.5+)

| Provider | Default port | Default endpoint | API key required |
|---|---|---|---|
| **Ollama** | `11434` | `http://127.0.0.1:11434/v1/chat/completions` | ✗ optional |
| **LM Studio** | `1234` | `http://127.0.0.1:1234/v1/chat/completions` | ✗ optional |
| **vLLM / TGI / llama.cpp server** | `8000` | `http://127.0.0.1:8000/v1/chat/completions` | ✗ optional |
| **Custom OpenAI-compatible endpoint** | user-defined | `http://127.0.0.1:8000/v1/chat/completions` | ✗ optional |

**Typical local-deployment recipes:**

- **Ollama**: `ollama pull llama3.2` → `ollama serve` → in Crew pick Ollama, set model to `llama3.2`.
- **LM Studio**: download a model in the GUI → Server tab → Start Server → pick LM Studio, use the identifier shown in the panel.
- **vLLM**: `python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct` → pick vLLM.
- **Corporate LLM gateway**: pick "Custom OpenAI-compatible endpoint" and drop in your own URL + model name.

**Detection strategy:** prefer prefix uniqueness. When a prefix is ambiguous (the four `sk-` sharing providers), the wizard drops a picker for the user. Local deployments bypass key detection entirely.

**Adding your own provider:** append one entry to the `PROVIDERS` dict in `providers.py` — five lines.

---

## 🤖 Supported Local Agent CLIs

Crew is **not tied to any single executor**. On startup, `agents_cli.py` probes the system PATH (and a few well-known install locations) for these:

### Built-in

| Agent | Command | Invocation | Notes |
|---|---|---|---|
| **Hermes Agent** | `hermes` | `hermes -z "<prompt>" --yolo` | Nous Research's general-purpose agent framework |
| **Claude Code** | `claude` | `claude -p "<prompt>"` | Anthropic's official CLI |
| **OpenAI Codex** | `codex` | `codex exec "<prompt>"` | OpenAI's official coding agent |
| **OpenCode** | `opencode` | `opencode run "<prompt>"` | Open-source community fork |
| **Aider** | `aider` | `aider --message "<prompt>" --yes --no-git` | Long-running git-aware pair programmer |
| **Gemini CLI** | `gemini` | `gemini -p "<prompt>"` | Google's official CLI |

### Auto-detection

At startup, `agents_cli.py` does the following for each spec (`BUILTIN_SPECS` + user-defined `custom_agents` from config):

1. If `command` is an absolute path, test `Path.exists()` directly
2. Otherwise, calls `shutil.which()` to look up the command on PATH
3. Falls back to a few well-known "installed to a fixed spot" locations (e.g. `%APPDATA%\npm\` for npm-installed CLIs, Hermes' venv path)
4. Returns a list of `{id, name, path, installed, custom, homepage, install_hint, args_template}`

**Default pick:** Hermes if installed, otherwise the first installed agent in order. Once the user picks explicitly in the settings panel, the choice is persisted to `config.json` under `local_agent`.

### Manual switching

Top-right settings panel → **本地执行 Agent · Local Executor** dropdown. Uninstalled agents are greyed out, custom agents show a `(自定义)` tag. Install or add one, restart the app, and it'll show up.

### Custom agents (v1.5+)

Beyond the six built-ins, Crew lets you register **arbitrary local agents** — your in-house tools, forks of open-source projects, home-grown agents, whatever.

**How to add:** ⚙ settings → Local Executor → **+ 添加 (Add)**

Fill five fields:

| Field | Description | Example |
|---|---|---|
| **ID** | Unique id (must not collide with built-in ids) | `my-agent` |
| **Display name** | Text shown in the dropdown | `My Custom Agent` |
| **Command** | Absolute path or PATH command name | `C:\Tools\myagent.exe` or `myagent` |
| **Args template** | Use `{prompt}` as placeholder; without it, the prompt is auto-appended | `run --input {prompt} --yolo` |
| **Homepage** | Optional | — |

**Storage:** written to `config.json` under `custom_agents`:

```json
{
  "custom_agents": [
    {
      "id": "my-agent",
      "name": "My Custom Agent",
      "command": "C:\\Tools\\myagent.exe",
      "args_template": "run --input {prompt} --yolo",
      "homepage": ""
    }
  ]
}
```

**REST API** for programmatic management:

- `GET  /api/custom-agents` — list all custom agents
- `POST /api/custom-agents` — add or update (upsert by id)
- `DELETE /api/custom-agents/<id>` — remove one

### Adding a new built-in CLI

Append one entry to `BUILTIN_SPECS` in `agents_cli.py`:

```python
AgentSpec(
    id="your-agent",
    name="Your Agent",
    command="your-agent",           # command name (no extension)
    build_args=lambda prompt: ["--prompt", prompt, "--non-interactive"],
    homepage="https://your-agent.example",
    install_hint="npm install -g @your-org/your-agent",
    extra_probe_paths=[             # optional: non-PATH install locations
        str(Path.home() / "AppData" / "Local" / "your-agent" / "bin" / "your-agent.exe"),
    ],
),
```

Restart, done.

### API endpoints

- `GET /api/local-agents` → detection results + currently selected
- `POST /api/local-agents/select` `{"agent_id": "claude"}` → switch default executor
- `GET  /api/custom-agents` → user-defined agents
- `POST /api/custom-agents` → upsert one
- `DELETE /api/custom-agents/<id>` → remove
- `GET /api/config` also carries `local_agents` / `selected_local_agent` / `local_agent_ready`

---

## 🧩 User Extension Points (v1.5+)

Starting from v1.5, Crew opens three fully user-controllable extension channels — no code changes required.

### 1. Custom LLM endpoint

Any OpenAI Chat Completions-compatible service plugs in: Ollama, LM Studio, vLLM, TGI, llama.cpp server, corporate LLM gateway, reverse proxy, Cloudflare AI Gateway, etc.

**UI path:** top-right ⚙ → Provider dropdown → pick **Ollama / LM Studio / vLLM / Custom OpenAI-compatible endpoint** → override the Endpoint field → fill Model name → leave API Key blank (or enter your internal token) → Save.

**Connectivity self-test (v1.6+):** below the Endpoint field, a **"Test connection"** button fires a real `{role: user, content: ping}` request against the unsaved config and returns latency + model reply. Errors surface the full HTTP status + body — no log-diving.

**API:**

```bash
# Test the values currently in the UI (does not persist)
curl -X POST http://127.0.0.1:8765/api/providers/test \
  -H "Content-Type: application/json" \
  -d '{"provider":"ollama","endpoint":"http://127.0.0.1:11434/v1/chat/completions","model":"llama3.2"}'
# {"ok":true,"latency_ms":432,"model_reply":"pong","provider":"ollama",...}
```

### 2. Custom local Agent CLI

Beyond the 6 built-ins, register arbitrary CLI executors.

**UI path:** ⚙ Settings → under Local Executor Agent, click **"+ Add"** → 5 fields:

| Field | Meaning | Example |
|---|---|---|
| `id` | Unique key (must not collide with built-ins) | `my-agent` |
| `name` | Display name | `My Custom Agent` |
| `command` | Absolute path or PATH command | `C:\Tools\myagent.exe` |
| `args_template` | Args template, `{prompt}` = placeholder | `run --input {prompt} --yolo` |
| `homepage` | Optional | — |

**Test-run (v1.6+):** every custom agent row has a **"Try"** button that fires `Say hello in one line.` at the CLI with a 45-second timeout, then shows latency + first 120 chars of stdout inline. Verify immediately, don't open a real topic just to test.

**Storage:** written to `config.json.custom_agents`. At runtime `agents_cli.detect_installed()` merges built-in + custom.

**REST API:**

```
GET    /api/custom-agents            list all
POST   /api/custom-agents            upsert one (by id)
DELETE /api/custom-agents/<id>       delete one
POST   /api/local-agents/test        test-run
POST   /api/local-agents/select      set as default
```

### 3. Custom teammates (v1.6+) ⭐

Previously only Foreman could "hire" new teammates (`hire` action, only at runtime). **v1.6 opens this to end users via a GUI panel** — the user is HR.

**UI path:** top-right **👥 Team Roster** button → **+ Create new teammate** → 6 fields:

| Field | Meaning |
|---|---|
| `name` | English name, unique, cannot collide with built-ins (e.g. Vera / Nova / Milo) |
| `role` | Function label (e.g. "Architect", "DBA", "Security Engineer") |
| `emoji` | Avatar glyph (e.g. ⚙ ✦ ▲ ♢) |
| `color` | Avatar background (native color picker) |
| `system` | Full System Prompt (markdown-friendly; scope, style, boundaries) |
| `default_on` | Auto-join new topics? (if off, must be @-mentioned or Foreman-hired) |

**Storage:** written to `dynamic_agents.json`, synced into the in-memory `AGENTS` dict → immediately visible to the router (`router_system`), multi-agent discuss (`discuss`), and hiring (`hire`).

**Edit & delete:** the same panel supports editing role/emoji/color/system of an existing custom teammate, or deleting them. The 12 built-in teammates cannot be deleted.

**REST API:**

```
GET    /api/agents/custom            list {custom: [...], builtin: [...]}
POST   /api/agents/custom            upsert (by name)
DELETE /api/agents/custom/<name>     delete
```

**Built-in name protection:** POST with `name ∈ built-in 12` (Foreman/Pine/Ash/Wren/Owl/Chief/Rune/Poppy/Judge/Rally/Ivy/Ledger) returns 400.

---

## 🛡️ Runtime Hardening (v1.7+)

Starting v1.7, Crew moves from "it runs" to "production-ready":

### Context & cost

- **Automatic context trimming**: sliding window with provider-aware `max_context` metadata
- **Token & cost tracking**: every LLM call logs `prompt_tokens / completion_tokens / cost_usd`, broken down by agent. The 💰 badge in the header refreshes today's spend every 30s
- **Pricing table**: input/output USD per 1M tokens for all 14 providers built into `pricing.py`. Local models count as $0
- **Retry with backoff**: 429 / 5xx / network errors → exponential backoff (1s → 2s → 4s, max 3 attempts)

### Interaction control

- **⏹ Stop**: cancel all running agent replies
- **🔄 Regenerate**: hover any agent message header — the last message can be regenerated
- **📎 Attachments**: images (multimodal for GPT-4o / Claude 3.5+ / Doubao Vision / Qwen-VL) + text files inlined. Click, drag-drop, or `Ctrl+V` paste
- **WS auto-reconnect**: exponential backoff (1s → 30s)

### Permissions

- **Per-agent tiers**: global vs agent — **the stricter wins**. Set Owl to `strict` alone and its executions need approval even when global is `autonomous`

### Data

- **Attachments**: `%APPDATA%\Crew\attachments\{sha1}.{ext}`, deduplicated
- **Usage log**: `llm_usage` table in `team.db`

---


## 🔐 Permission Model

Three graduated tiers:

| Tier | Foreman behavior | Fits when |
|---|---|---|
| **Strict** | Approval card on every `discuss` / `hire` / `execute` | First-time use, production data, unfamiliar CLI behavior |
| **Balanced** (default) | `discuss` auto-passes; `hire` / `execute` need approval | Day-to-day |
| **Auto** | Everything auto-passes, Foreman is fully autonomous | Long batch runs, AFK operation |

Stored in `%APPDATA%\Crew\config.json` under `permission_level`. Change from the dropdown at the top of the main UI.

---

## 📁 Repository Layout

```
crew-multi-agent/
├─ launcher.py              # Entry: pywebview window + uvicorn thread
├─ server.py                # FastAPI app + routes + WebSocket broadcast
├─ supervisor.py            # Foreman brain + 11 agent definitions
├─ providers.py             # 10-provider LLM abstraction + auto-detect
├─ agents_cli.py            # Local CLI abstraction + probe (Hermes/Claude/Codex/…)
├─ gen_icon.py              # Emits static/crew.ico (7-size multi-frame)
├─ gen_avatars.py           # Emits static/avatars/*.svg (DiceBear frozen)
│
├─ crew.spec                # PyInstaller spec
├─ crew.iss                 # Inno Setup script
├─ install.py / install.bat # First-time source install wizard (optional)
│
├─ static/                  # Frontend assets (baked into exe, read-only)
│  ├─ index.html            # Single-file SPA (HTML + CSS + JS)
│  ├─ crew.ico              # App icon (7 sizes)
│  ├─ crew.png              # 256×256 preview
│  └─ avatars/*.svg         # 12 teammate avatars
│
├─ dist_extras/             # Redistribution extras
│  └─ README.md             # User-facing README bundled in packages
│
├─ 打开作战室.bat            # Source-mode launcher (Windows)
├─ 停止作战室.bat            # Kill-all crew.exe helper
├─ start_hidden.vbs         # Windowless start (for startup folder)
│
├─ .env.example             # API key template
├─ .gitignore
├─ README.md                # Chinese
├─ README.en.md             # You are here
└─ LICENSE
```

---

## 💾 Where Data Lives

**All writable data lives under `%APPDATA%\Crew\`** (i.e. `C:\Users\<you>\AppData\Roaming\Crew\`):

| File | Purpose | Kept after uninstall? |
|---|---|---|
| `config.json` | Preferences (provider, model, permission tier, selected local agent, onboarding state) | Yes |
| `.env` | API keys (plaintext) | Yes |
| `team.db` | SQLite: topics, messages, approval log | Yes |
| `dynamic_agents.json` | System prompts of Foreman-hired teammates | Yes |
| `launcher.log` | Boot log | Yes |
| `server.log` | Backend log | Yes |
| `launcher_crash.log` | Crash traceback (if any) | Yes |

**Why keep them?** Uninstall + reinstall preserves history. To wipe: `rmdir /s "%APPDATA%\Crew"`.

---

## 📦 Building From Source

### Toolchain

```bash
pip install pyinstaller
```

Plus [Inno Setup 6](https://jrsoftware.org/isdl.php) (free, default location is fine).

### Three steps

```bash
# 1. Generate the icon (optional — repo already ships static/crew.ico)
python gen_icon.py

# 2. PyInstaller → crew.exe + dependency directory
pyinstaller crew.spec --noconfirm --clean
# → dist/Crew/crew.exe (~10 MB)
# → dist/Crew/_internal/ (~25 MB — Python runtime + all .pyd)

# 3. Inno Setup → installer
"C:/Users/<you>/AppData/Local/Programs/Inno Setup 6/ISCC.exe" crew.iss
# → dist/Crew-Setup-vX.X.X.exe (~20 MB)
```

### Build gotchas

- **Use `onedir`, not `onefile`** — 3–5× faster startup, plays nicely with installer diffs.
- **`console=False`** — Windows GUI subsystem; no CMD flash.
- **Must `collect_all('pydantic_core')`** — pydantic v2's C extension `.pyd` can't be found via hidden import alone.
- **Must `collect_all('webview')` / `clr_loader` / `pythonnet`** — pywebview leans on the .NET bridge on Windows.
- **Icon embed:** `exe = EXE(..., icon="static/crew.ico")` in `crew.spec`. Verify with `pefile`: the built `crew.exe` should carry 7 `RT_ICON` entries.

---

## 🛠 Development Guide

### Adding a teammate

Add an entry to `AGENTS` in `supervisor.py`:

```python
AGENTS = {
    ...
    "Sage": {
        "role_zh": "顾问",
        "avatar": "static/avatars/Sage.svg",
        "system_prompt": (
            "You are Sage, a seasoned strategy advisor. Calm tone, tight logic, "
            "always structure your point in three bullets. Keep replies under 200 words."
        ),
        "temperature": 0.6,
    },
}
```

Drop a `static/avatars/Sage.svg` (pull from [DiceBear](https://www.dicebear.com/9.x/pixel-art/svg?seed=Sage)), restart.

### Adding an LLM provider

Add an entry to `PROVIDERS` in `providers.py` — see the [Supported LLM Providers](#-supported-llm-providers) section for the schema.

### Adding a local Agent CLI

Add an entry to `AGENT_SPECS` in `agents_cli.py` — see the [Supported Local Agent CLIs](#-supported-local-agent-clis) section.

### Modifying the UI

`static/index.html` is a **single-file SPA** — HTML, CSS, and JS all in one file. No build step.

Save, `Ctrl+R` in the app window, done. No backend restart needed.

### Debug mode

In `launcher.py`, flip `webview.start(gui=None, debug=False)` to `debug=True`. Right-click in the window → "Inspect" opens DevTools.

Backend logs: source mode goes to stdout; packaged builds go to `%APPDATA%\Crew\launcher.log`.

---

## ❓ FAQ

**Q: Windows Defender says "unknown publisher".**
A: Expected. The installer isn't code-signed (individual EV cert ~$300/yr, deferred). Click "More info → Run anyway". A proper EV cert is the only real fix.

**Q: Double-clicking the icon does nothing.**
A: Check `%APPDATA%\Crew\launcher.log` and `launcher_crash.log`. Most common cause: missing WebView2 Runtime on older Windows 10 — install the Evergreen build from Microsoft.

**Q: Can I access this from the LAN?**
A: Not by default — `launcher.py` binds to `127.0.0.1`. You can flip it to `0.0.0.0` and add your own auth layer, but exposing this to the internet is a **bad idea**.

**Q: macOS / Linux?**
A: **Windows only today.** pywebview supports WKWebView (macOS) and GTK WebKit (Linux) in principle, but the icon, installer, and data-path code all need adapting. PRs welcome.

**Q: What if I have zero local Agent CLIs installed?**
A: Fine. `discuss` and `hire` don't touch any local CLI. Only `execute` needs one — and if none is present, you'll get an "no executor installed" message with install commands for each supported option.

**Q: 20 MB installer, but 35 MB after unpacking. Why?**
A: Inno Setup uses LZMA2 compression. The unpacked size includes Python 3.13 runtime (~15 MB) + pydantic_core `.pyd` (~2 MB) + WebView2 bridge DLLs (~5 MB) + FastAPI/uvicorn/httpx (~3 MB).

**Q: Can I use just one LLM provider?**
A: Yes. Paste one key in the wizard, ignore the rest. Change / add later from the settings panel.

---

## 🗺 Roadmap

- [x] v1.0 — Web prototype (local browser)
- [x] v1.1 — 10-provider LLM abstraction + auto-detect
- [x] v1.2 — pywebview desktop window, no CMD flash
- [x] v1.3 — Custom icon + Inno Setup installer
- [x] v1.4 — **Multi-CLI support** (Hermes / Claude Code / Codex / OpenCode / Aider / Gemini auto-detect + switch)
- [ ] v1.5 — Tray icon + background residence (close-to-tray)
- [ ] v1.6 — Multi-session / multi-project workspaces
- [ ] v1.7 — Export & share (dump a `discuss` to Markdown / PDF)
- [ ] v1.8 — macOS build (pywebview + WKWebView)
- [ ] v2.0 — Plugin system (third-parties can register new agents / providers / executors)

---

## 📄 License

[MIT](./LICENSE)

---

## 🙏 Acknowledgements

- Desktop shell: [pywebview](https://pywebview.flowrl.com/)
- Backend: [FastAPI](https://fastapi.tiangolo.com/) · [Uvicorn](https://www.uvicorn.org/)
- Packaging: [PyInstaller](https://pyinstaller.org/) · [Inno Setup](https://jrsoftware.org/isinfo.php)
- Chinese font: [LXGW WenKai](https://github.com/lxgw/LxgwWenKai)
- Avatars: [DiceBear](https://www.dicebear.com/) pixel-art style
- Local CLI ecosystem: Hermes · Claude Code · OpenAI Codex · OpenCode · Aider · Gemini CLI
- LLM APIs: VolcEngine ARK · OpenAI · Anthropic · DeepSeek · Moonshot · Zhipu · OpenRouter · Groq · Qwen · SiliconFlow

---

<sub>Built by <a href="https://github.com/V-lVl">V-lVl</a> · <a href="./README.md">简体中文</a></sub>

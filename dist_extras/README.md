# Crew · Desktop v1.2 使用说明

## 一分钟上手

1. **装**：双击 `Crew-Setup-v1.2.0.exe` 走安装向导（下一步下一步），会自动装到 `%LOCALAPPDATA%\Programs\Crew\` + 建开始菜单/桌面快捷方式（可选）
   - 或者直接双击 zip 里的 `crew.exe`（便携版）
2. **开**：从开始菜单或桌面点 Crew 图标 → **直接是原生桌面窗口**（没有黑色 CMD、没有浏览器 tab）
3. **首次进入**看到向导：粘任一家 LLM 的 API key（自动识别 provider），点"好的，开始"
4. 开始聊天/开会/交给 Foreman 干活

## 桌面版特性 (v1.2 新)

- ✨ **真正的桌面窗口**：pywebview + WebView2，独立标题栏、任务栏图标、干净的窗口
- ✨ **无黑色 CMD 窗口**：双击直接弹应用窗口，跟 Slack/VSCode/Codex 一样
- ✨ **应用图标**：Raft 风黑底黄 C，任务栏可辨识
- ✨ **关窗即退**：关掉窗口 = 完全退出，不留后台进程

## 支持 10 家 LLM

| Provider | 默认模型 | Key 自动识别 |
|---|---|---|
| 火山方舟 (ARK) | ark-code-latest | 手选 |
| OpenAI | gpt-4o-mini | sk- (共用) |
| Anthropic Claude | claude-3-5-sonnet-latest | ✓ sk-ant- |
| DeepSeek | deepseek-chat | sk- (共用) |
| Kimi (月之暗面) | kimi-latest | sk- (共用) |
| 智谱 GLM | glm-4-flash | ✓ hex.hex |
| OpenRouter | anthropic/claude-3.5-sonnet | ✓ sk-or- |
| Groq | llama-3.3-70b-versatile | ✓ gsk_ |
| 通义千问 (阿里) | qwen-plus | sk- (共用) |
| SiliconFlow (硅基流动) | Qwen/Qwen2.5-7B-Instruct | sk- (共用) |

**随时换 provider**：主界面右上角 **⚙** 按钮 → 设置面板。

## 关闭方式

- 直接关掉应用窗口（右上角 ×）即可完全退出

## 数据保存位置

`%APPDATA%\Crew\` （即 `C:\Users\你\AppData\Roaming\Crew\`）：

- `config.json` — 偏好（provider / model / 权限档 / onboard 状态）
- `.env` — API key
- `team.db` — 聊天历史 / 话题 / 消息
- `dynamic_agents.json` — Foreman 新招进来的同事
- `server.log` / `launcher.log` — 后端 & 启动日志

**卸载**：控制面板 → 程序和功能 → Crew → 卸载。想连数据一起删就手动 `rmdir /s %APPDATA%\Crew`。

## 前置依赖

- **Windows 10/11**（WebView2 内置，无需安装）
- **Hermes CLI**（`hermes.exe` 在 PATH）—— 用来让 Foreman 真正执行任务；没装也能跑，只是执行类命令不生效

## 端口 / 网络

- 应用内部用 `127.0.0.1:8765` 与后端通信（对外不监听，其他机器访问不了）
- 想局域网访问？改 `launcher.py` 里 `host="127.0.0.1"` → `"0.0.0.0"` 后重打包

## 出问题

1. 双击图标无反应？看 `%APPDATA%\Crew\launcher.log` / `launcher_crash.log`
2. 窗口起不来但托盘有痕迹？任务管理器杀 `crew.exe` 和 `msedgewebview2.exe`
3. WebView2 缺失？Win 10 早期版本可能没预装，去 Microsoft 官网下 "WebView2 Runtime Evergreen"

"""本地 Agent CLI 抽象层。

自动探测系统里安装了哪些"本地 coding agent"命令行工具，
统一封装成一个 run(prompt, ...) 接口。

目前支持：
  · Hermes (nousresearch/hermes-agent)
  · Claude Code (anthropic 官方)
  · OpenAI Codex (openai 官方)
  · OpenCode (开源社区)
  · Aider (aider-chat)
  · Gemini CLI (google)

Foreman 在 execute 时会用当前选中的 agent。用户也可以在 UI 里手动切换。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class AgentSpec:
    """一个本地 Agent CLI 的元信息。"""
    id: str                          # 唯一 id，如 "hermes"
    name: str                        # 显示名，如 "Hermes Agent"
    command: str                     # 可执行文件名（不含扩展）
    build_args: Callable[[str], list[str]]  # (prompt) → cmd_args (不含可执行文件本身)
    homepage: str = ""               # 官网/仓库
    install_hint: str = ""           # 未安装时的提示
    extra_probe_paths: list[str] = field(default_factory=list)  # 额外的固定路径兜底


def _hermes_args(prompt: str) -> list[str]:
    # hermes -z "<prompt>" --yolo
    return ["-z", prompt, "--yolo"]


def _claude_args(prompt: str) -> list[str]:
    # claude "<prompt>"  (Claude Code CLI 一次性模式)
    # 官方推荐用 `claude -p "<prompt>"` 做 non-interactive
    return ["-p", prompt]


def _codex_args(prompt: str) -> list[str]:
    # codex exec "<prompt>"  (OpenAI Codex CLI 非交互模式)
    return ["exec", prompt]


def _opencode_args(prompt: str) -> list[str]:
    # opencode run "<prompt>"
    return ["run", prompt]


def _aider_args(prompt: str) -> list[str]:
    # aider --message "<prompt>" --yes --no-git（--yes 跳过确认，最接近 yolo）
    return ["--message", prompt, "--yes", "--no-git"]


def _gemini_args(prompt: str) -> list[str]:
    # gemini -p "<prompt>"  (非交互模式)
    return ["-p", prompt]


# 已知 CLI 列表 —— 按"生态活跃度 + 用户量"顺序排
AGENT_SPECS: list[AgentSpec] = [
    AgentSpec(
        id="hermes",
        name="Hermes Agent",
        command="hermes",
        build_args=_hermes_args,
        homepage="https://github.com/NousResearch/hermes-agent",
        install_hint="pip install hermes-agent",
        extra_probe_paths=[
            str(Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"),
        ],
    ),
    AgentSpec(
        id="claude",
        name="Claude Code",
        command="claude",
        build_args=_claude_args,
        homepage="https://docs.claude.com/claude-code",
        install_hint="npm install -g @anthropic-ai/claude-code",
    ),
    AgentSpec(
        id="codex",
        name="OpenAI Codex",
        command="codex",
        build_args=_codex_args,
        homepage="https://github.com/openai/codex",
        install_hint="npm install -g @openai/codex",
    ),
    AgentSpec(
        id="opencode",
        name="OpenCode",
        command="opencode",
        build_args=_opencode_args,
        homepage="https://opencode.ai",
        install_hint="curl -fsSL https://opencode.ai/install | bash",
    ),
    AgentSpec(
        id="aider",
        name="Aider",
        command="aider",
        build_args=_aider_args,
        homepage="https://aider.chat",
        install_hint="pip install aider-install && aider-install",
    ),
    AgentSpec(
        id="gemini",
        name="Gemini CLI",
        command="gemini",
        build_args=_gemini_args,
        homepage="https://github.com/google-gemini/gemini-cli",
        install_hint="npm install -g @google/gemini-cli",
    ),
]


# ─── 探测 ────────────────────────────────────────────────
def _resolve(spec: AgentSpec) -> Optional[str]:
    """返回可执行文件绝对路径，找不到就 None。"""
    # Windows 下命令名可能带 .exe / .cmd / .bat
    candidates = [spec.command, f"{spec.command}.exe", f"{spec.command}.cmd", f"{spec.command}.bat"]
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    for guess in spec.extra_probe_paths:
        if Path(guess).exists():
            return guess
    return None


def detect_installed() -> list[dict]:
    """探测系统里所有已安装的本地 Agent CLI，返回一个 list of dict：

    [
      {"id": "hermes", "name": "Hermes Agent", "path": "C:\\...", "installed": True, ...},
      {"id": "claude", "name": "Claude Code", "path": None, "installed": False, ...},
      ...
    ]
    """
    results = []
    for spec in AGENT_SPECS:
        path = _resolve(spec)
        results.append({
            "id": spec.id,
            "name": spec.name,
            "command": spec.command,
            "path": path,
            "installed": path is not None,
            "homepage": spec.homepage,
            "install_hint": spec.install_hint,
        })
    return results


def get_default() -> Optional[str]:
    """返回默认选中的 agent id：优先 hermes，其次按 AGENT_SPECS 顺序找第一个装了的。"""
    installed = [a for a in detect_installed() if a["installed"]]
    if not installed:
        return None
    # hermes 优先
    for a in installed:
        if a["id"] == "hermes":
            return a["id"]
    return installed[0]["id"]


def get_spec(agent_id: str) -> Optional[AgentSpec]:
    for spec in AGENT_SPECS:
        if spec.id == agent_id:
            return spec
    return None


# ─── 执行 ────────────────────────────────────────────────
async def run_agent(
    agent_id: str,
    prompt: str,
    on_line: Optional[Callable[[str], None]] = None,
    timeout: float = 300,
    cwd: Optional[str] = None,
) -> str:
    """跑指定的本地 agent CLI，边跑边把 stdout 一行一行喂给 on_line 回调。

    参数：
      agent_id  - "hermes" / "claude" / "codex" / "opencode" / "aider" / "gemini"
      prompt    - 要交给 agent 的 prompt / task
      on_line   - 每读到一行 stdout 就回调一次
      timeout   - 秒。超时会 kill
      cwd       - 工作目录（None = 继承）

    返回：完整 stdout（含超时/异常标记）。
    """
    spec = get_spec(agent_id)
    if spec is None:
        return f"[未知 agent: {agent_id}]"

    exe = _resolve(spec)
    if not exe:
        return (
            f"[{spec.name} 未安装]\n"
            f"安装方式：{spec.install_hint}\n"
            f"官网：{spec.homepage}"
        )

    args = [exe, *spec.build_args(prompt)]

    def _sync() -> str:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
            cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        buf: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                buf.append(line)
                if on_line:
                    try:
                        on_line(line.rstrip("\n"))
                    except Exception:
                        pass
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            buf.append("\n[执行超时被终止]")
        except Exception as e:
            buf.append(f"\n[执行出错: {e}]")
        return "".join(buf)

    return await asyncio.to_thread(_sync)


if __name__ == "__main__":
    # 自测：打印当前系统探测结果
    import json
    detected = detect_installed()
    print(json.dumps(detected, indent=2, ensure_ascii=False))
    print(f"\n默认选择：{get_default()}")

"""本地 Agent CLI 抽象层。

自动探测系统里安装了哪些"本地 coding agent"命令行工具，
统一封装成一个 run(prompt, ...) 接口。

内置支持：
  · Hermes (nousresearch/hermes-agent)
  · Claude Code (anthropic 官方)
  · OpenAI Codex (openai 官方)
  · OpenCode (开源社区)
  · Aider (aider-chat)
  · Gemini CLI (google)

用户自定义：
  用户可以在 UI 里添加任意本地 agent（自建 agent、公司内部工具、fork 的开源项目等），
  只需给出：显示名、可执行文件路径、参数模板（用 {prompt} 占位）。
  自定义条目存在 config.json 的 custom_agents 数组里，运行时合并进 detect 结果。

Foreman 在 execute 时会用当前选中的 agent。用户也可以在 UI 里手动切换。
"""
from __future__ import annotations

import asyncio
import shlex
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
    command: str                     # 可执行文件名（不含扩展）或绝对路径
    build_args: Callable[[str], list[str]]  # (prompt) → cmd_args (不含可执行文件本身)
    homepage: str = ""               # 官网/仓库
    install_hint: str = ""           # 未安装时的提示
    extra_probe_paths: list[str] = field(default_factory=list)  # 额外的固定路径兜底
    custom: bool = False             # 是否用户自定义
    # 自定义 agent 用：完整的 args 模板字符串，含 {prompt} 占位
    args_template: str = ""


def _hermes_args(prompt: str) -> list[str]:
    return ["-z", prompt, "--yolo"]


def _claude_args(prompt: str) -> list[str]:
    return ["-p", prompt]


def _codex_args(prompt: str) -> list[str]:
    return ["exec", prompt]


def _opencode_args(prompt: str) -> list[str]:
    return ["run", prompt]


def _aider_args(prompt: str) -> list[str]:
    # aider --message "<prompt>" --yes --no-git（--yes 跳过确认，最接近 yolo）
    return ["--message", prompt, "--yes", "--no-git"]


def _gemini_args(prompt: str) -> list[str]:
    return ["-p", prompt]


# 内置已知 CLI —— 按"生态活跃度 + 用户量"顺序排
BUILTIN_SPECS: list[AgentSpec] = [
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


# 兼容旧代码：保留 AGENT_SPECS 名字指向内置列表
AGENT_SPECS = BUILTIN_SPECS


# ─── 自定义 agent 支持 ────────────────────────────────────
def _build_args_from_template(template: str) -> Callable[[str], list[str]]:
    """把模板字符串（含 {prompt} 占位）编译成 build_args 函数。

    例：
      "run --input {prompt} --yolo"  → ["run", "--input", "<prompt>", "--yolo"]
      如果模板里没有 {prompt}，就把 prompt 追加到末尾。
    """
    def builder(prompt: str) -> list[str]:
        # 用 shlex 把模板切成 argv-style tokens，然后逐个替换 {prompt}
        try:
            tokens = shlex.split(template, posix=False)
        except Exception:
            tokens = template.split()
        if any("{prompt}" in t for t in tokens):
            return [t.replace("{prompt}", prompt) for t in tokens]
        # 没有 {prompt} 占位 → 直接把 prompt 拼在最后
        return [*tokens, prompt]

    return builder


def _custom_spec_from_dict(d: dict) -> Optional[AgentSpec]:
    """把 config.json 里的一条 custom_agent 记录变成 AgentSpec。

    期望格式：
      {
        "id": "my-agent",           # 唯一 id
        "name": "My Agent",         # 显示名
        "command": "C:/path/to/agent.exe",  # 绝对路径或 PATH 上的命令名
        "args_template": "run --input {prompt} --yolo",
        "homepage": "",             # 可选
      }
    """
    cid = str(d.get("id", "")).strip()
    name = str(d.get("name", "")).strip()
    command = str(d.get("command", "")).strip()
    args_template = str(d.get("args_template", "")).strip()
    if not cid or not name or not command:
        return None
    return AgentSpec(
        id=cid,
        name=name,
        command=command,
        build_args=_build_args_from_template(args_template),
        homepage=str(d.get("homepage", "")).strip(),
        install_hint="用户自定义 agent",
        custom=True,
        args_template=args_template,
    )


# ─── 探测 ────────────────────────────────────────────────
def _resolve(spec: AgentSpec) -> Optional[str]:
    """返回可执行文件绝对路径，找不到就 None。"""
    # 1. 如果 command 已经是一个存在的路径，直接用
    p = Path(spec.command)
    if p.is_absolute() and p.exists():
        return str(p)

    # 2. 系统 PATH 上找（Windows 下可能带 .exe / .cmd / .bat）
    candidates = [spec.command, f"{spec.command}.exe", f"{spec.command}.cmd", f"{spec.command}.bat"]
    for c in candidates:
        found = shutil.which(c)
        if found:
            return found

    # 3. 额外兜底路径
    for guess in spec.extra_probe_paths:
        if Path(guess).exists():
            return guess
    return None


def _all_specs(custom_agents: Optional[list[dict]] = None) -> list[AgentSpec]:
    """内置 + 自定义合并后的全量列表。id 冲突时自定义优先（覆盖内置）。"""
    if not custom_agents:
        return list(BUILTIN_SPECS)
    custom_specs = [s for s in (_custom_spec_from_dict(d) for d in custom_agents) if s is not None]
    custom_ids = {s.id for s in custom_specs}
    # 内置里 id 冲突的先剔掉
    keep_builtins = [s for s in BUILTIN_SPECS if s.id not in custom_ids]
    return keep_builtins + custom_specs


def detect_installed(custom_agents: Optional[list[dict]] = None) -> list[dict]:
    """探测系统里所有已安装的本地 Agent CLI + 用户自定义 agent。

    返回一个 list of dict：
    [
      {"id": "hermes", "name": "Hermes Agent", "path": "C:\\...",
       "installed": True, "custom": False, "homepage": "...",
       "install_hint": "...", "args_template": ""},
      ...
    ]
    """
    results = []
    for spec in _all_specs(custom_agents):
        path = _resolve(spec)
        results.append({
            "id": spec.id,
            "name": spec.name,
            "command": spec.command,
            "path": path,
            "installed": path is not None,
            "custom": spec.custom,
            "homepage": spec.homepage,
            "install_hint": spec.install_hint,
            "args_template": spec.args_template,
        })
    return results


def get_default(custom_agents: Optional[list[dict]] = None) -> Optional[str]:
    """默认选中的 agent id：优先 hermes，其次按顺序找第一个装了的。"""
    installed = [a for a in detect_installed(custom_agents) if a["installed"]]
    if not installed:
        return None
    for a in installed:
        if a["id"] == "hermes":
            return a["id"]
    return installed[0]["id"]


def get_spec(agent_id: str, custom_agents: Optional[list[dict]] = None) -> Optional[AgentSpec]:
    for spec in _all_specs(custom_agents):
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
    custom_agents: Optional[list[dict]] = None,
) -> str:
    """跑指定的本地 agent CLI，边跑边把 stdout 一行一行喂给 on_line 回调。

    参数：
      agent_id       - 内置 id 或用户自定义 id
      prompt         - 要交给 agent 的 prompt / task
      on_line        - 每读到一行 stdout 就回调一次
      timeout        - 秒。超时会 kill
      cwd            - 工作目录（None = 继承）
      custom_agents  - 从 config.json 读来的自定义 agent 列表

    返回：完整 stdout（含超时/异常标记）。
    """
    spec = get_spec(agent_id, custom_agents)
    if spec is None:
        return f"[未知 agent: {agent_id}]"

    exe = _resolve(spec)
    if not exe:
        return (
            f"[{spec.name} 未找到]\n"
            f"命令：{spec.command}\n"
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
    import json
    # 自测 1：只探内置
    print("=== 内置探测 ===")
    print(json.dumps(detect_installed(), indent=2, ensure_ascii=False))
    print(f"默认：{get_default()}\n")

    # 自测 2：叠加一个假的自定义 agent
    fake = [{
        "id": "myagent",
        "name": "My Custom Agent",
        "command": "notepad.exe",  # 系统一定有的东西，方便探测 True
        "args_template": "--input {prompt} --run",
    }]
    print("=== 加自定义后 ===")
    print(json.dumps(detect_installed(fake), indent=2, ensure_ascii=False))
    print(f"默认：{get_default(fake)}")

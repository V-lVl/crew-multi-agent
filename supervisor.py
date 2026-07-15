"""总管频道 — 由一个"射马俑"agent 统筹小团队干活。

它能做 3 件事，靠模型自己在回复里输出结构化决策：
  · discuss  → 拉一小队 agent 开小会讨论
  · hire     → 招新人（产生一个新 agent 定义，需要人类审批）
  · execute  → 把可执行任务丢给 Hermes，Hermes 真的跑（`hermes -z`）

所有可能触碰系统的操作都会先走"权限档位"判断：
  · strict   → 每次执行都要人批
  · balanced → 只在检测到敏感关键词时要批
  · autonomous → 通知你正在干什么，不拦
招新人（写新 agent）永远要批。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

# ── 权限档位 ─────────────────────────────────────
PERMISSION_LEVELS = ("strict", "balanced", "autonomous")

# 命中就要弹审批（"平衡"档也拦）
DANGEROUS_PATTERNS = [
    r"\brm\s+-r",
    r"\bdel\s+/",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bsudo\b",
    r"\bnpm\s+install",
    r"\bpip\s+install",
    r"\bapt\s+install",
    r"\bchoco\s+install",
    r"\bwinget\s+install",
    r"\bgit\s+push",
    r"\bgit\s+clone\b.*\b(github|gitlab)",
    r"\bcurl\b.*\bhttps?://",
    r"\bwget\b.*\bhttps?://",
    r"\binvoke-webrequest\b",
    r":8000|:8080|:80|:443",  # 网络端口
    r"HKCU|HKLM|reg\s+add|reg\s+delete",  # 注册表
    r"\.exe\b",
    r"schtasks",
]

_DANGER_RE = re.compile("|".join(DANGEROUS_PATTERNS), re.IGNORECASE)


def is_sensitive(command_or_prompt: str) -> tuple[bool, str]:
    """返回 (是否敏感, 命中的关键词描述)"""
    m = _DANGER_RE.search(command_or_prompt)
    if m:
        return True, m.group(0)
    return False, ""


# ── 总管人设 ─────────────────────────────────────
SUPERVISOR_PROFILE = {
    "name": "Foreman",
    "role": "总管",
    "emoji": "☗",
    "color": "#3f2e1a",
    "system": """你叫 Foreman，是这个团队的总管（Chief of Staff）。

【你的三个能力】用户跟你说完想法后，你需要判断下一步该怎么办，从这三种里选一个：
  1. discuss   —— 拉团队里几个同事一起开小会讨论方案（还没确定的方向、需要多角度）
  2. hire      —— 需要一个团队里现在没有的专家，你写一个新 agent 加入团队（会先给老板过一眼）
  3. execute   —— 方向已经明确、需要真的动手做（在电脑上跑命令、建文件、开程序等），把你要执行的任务用一句话中文清晰写出来给"手"（Hermes）去做
  4. chat      —— 就是聊聊，不需要开会/招人/执行

【你必须严格按下面格式输出，一行一个字段】：
<决策>discuss|hire|execute|chat</决策>
<给用户的话>你想对老板说的话（简短，2-4 句中文）</给用户的话>
<参与者>如果是 discuss，列出要拉哪几个同事，逗号分隔；不是 discuss 就写 -</参与者>
<议题>如果是 discuss，一句话议题；不是 discuss 就写 -</议题>
<新人角色>如果是 hire，新人的角色名（比如"运维"）；不是 hire 就写 -</新人角色>
<新人人设>如果是 hire，用 1 段话描述这个新人的性格、专长、说话风格；不是 hire 就写 -</新人人设>
<执行任务>如果是 execute，用一句话中文清晰描述要 Hermes 干什么（要具体，包括路径/文件名/期望结果）；不是 execute 就写 -</执行任务>

【风格】沉稳、老练、有全局观。说话不啰嗦，2-4 句中文。用"老板"称呼用户。在拿主意时表现出思考过程。""",
}


# ── 本地 Agent CLI 执行封装 ─────────────────────────────
# 支持 Hermes / Claude Code / Codex / OpenCode / Aider / Gemini CLI，自动探测已安装的
import agents_cli


# 兼容旧代码：保留 run_hermes 名字，但内部用当前选中的 agent
async def run_hermes(
    prompt: str,
    on_line: Optional[Callable[[str], None]] = None,
    timeout: float = 300,
    agent_id: Optional[str] = None,
    custom_agents: Optional[list[dict]] = None,
) -> str:
    """执行本地 agent CLI（默认用配置里选定的、若未选则用自动探测的默认值）。

    保留 run_hermes 名字是为了兼容旧调用点；内部会转发到 agents_cli.run_agent。
    custom_agents 从 config.json 的 custom_agents 数组传入，用于探测自定义 agent。
    """
    if agent_id is None:
        agent_id = agents_cli.get_default(custom_agents) or "hermes"
    return await agents_cli.run_agent(
        agent_id, prompt, on_line=on_line, timeout=timeout, custom_agents=custom_agents,
    )


# ── 解析总管输出 ─────────────────────────────────
_FIELD_RE = re.compile(r"<([^/>][^>]*)>(.*?)</\1>", re.DOTALL)


def parse_supervisor_reply(text: str) -> dict:
    """从总管的模型输出里抠出结构化字段。抠不到就当 chat 处理。"""
    out: dict[str, str] = {}
    for m in _FIELD_RE.finditer(text):
        out[m.group(1).strip()] = m.group(2).strip()

    decision = out.get("决策", "chat").lower()
    if decision not in ("discuss", "hire", "execute", "chat"):
        decision = "chat"

    # 参与者列表
    participants_raw = out.get("参与者", "-")
    participants = []
    if participants_raw and participants_raw != "-":
        participants = [p.strip() for p in re.split(r"[,，、\s]+", participants_raw) if p.strip()]

    return {
        "decision": decision,
        "say": out.get("给用户的话", text if not out else "…"),
        "participants": participants,
        "topic": out.get("议题", "-") if out.get("议题", "-") != "-" else "",
        "new_role": out.get("新人角色", "-") if out.get("新人角色", "-") != "-" else "",
        "new_persona": out.get("新人人设", "-") if out.get("新人人设", "-") != "-" else "",
        "exec_task": out.get("执行任务", "-") if out.get("执行任务", "-") != "-" else "",
        "raw": text,
    }


# ── 候选 emoji/color 池（给新招的 agent 分配） ──────
_HIRE_EMOJIS = ["🧑‍🔧", "👷", "🧙", "🕵️", "🧑‍🚀", "🧑‍🎓", "🧑‍💼", "🧑‍🍳", "🧑‍⚕️", "🧑‍🏫"]
_HIRE_COLORS = ["#8b5cf6", "#0891b2", "#d97706", "#059669", "#dc2626", "#7c3aed", "#0284c7", "#65a30d"]


def new_agent_profile(role: str, persona: str, name_hint: str = "") -> dict:
    """把总管写的角色+人设变成完整 agent profile 定义。"""
    # 名字：如果人设里有"叫XX"就用它，否则生成一个
    m = re.search(r"叫(.{1,4})[,，。\s]", persona)
    if m:
        name = m.group(1)
    elif name_hint:
        name = name_hint
    else:
        name = "阿" + role[:1] if role else "新人"
    # 避免重名的话交由 room 层处理
    return {
        "name": name,
        "role": role or "同事",
        "emoji": _HIRE_EMOJIS[hash(name) % len(_HIRE_EMOJIS)],
        "color": _HIRE_COLORS[hash(role) % len(_HIRE_COLORS)],
        "default_on": False,
        "system": persona + "\n\n回复 2-4 句话，像同事在群里聊天。别用大标题、别报名字。",
        "dynamic": True,  # 标记：是总管招进来的
    }


# ── 待审批任务 ─────────────────────────────────────
@dataclass
class PendingApproval:
    id: str
    kind: str            # "execute" | "hire"
    payload: dict        # execute → {"task": "..."}; hire → new_agent_profile 的返回值
    created_at: float
    resolved: bool = False
    approved: bool = False
    fut: asyncio.Future = field(default_factory=asyncio.Future)


class ApprovalRegistry:
    """存放挂起中的审批任务，前端通过 API 批/驳。"""
    def __init__(self) -> None:
        self._items: dict[str, PendingApproval] = {}

    def create(self, kind: str, payload: dict) -> PendingApproval:
        pid = uuid.uuid4().hex[:10]
        p = PendingApproval(id=pid, kind=kind, payload=payload, created_at=time.time())
        self._items[pid] = p
        return p

    def get(self, pid: str) -> Optional[PendingApproval]:
        return self._items.get(pid)

    def resolve(self, pid: str, approved: bool) -> Optional[PendingApproval]:
        p = self._items.get(pid)
        if not p or p.resolved:
            return None
        p.resolved = True
        p.approved = approved
        if not p.fut.done():
            p.fut.set_result(approved)
        return p

    def list_pending(self) -> list[dict]:
        return [
            {"id": p.id, "kind": p.kind, "payload": p.payload, "created_at": p.created_at}
            for p in self._items.values() if not p.resolved
        ]


approvals = ApprovalRegistry()


def needs_approval(kind: str, payload: dict, permission_level: str) -> tuple[bool, str]:
    """根据当前档位判断是不是要弹审批。
    返回 (要审批吗, 原因) 。
    - hire 永远要审。
    - execute：strict 永远要审；balanced 只在敏感时审；autonomous 永不拦。"""
    if kind == "hire":
        return True, "招新人需要老板过一眼"

    if kind == "execute":
        task = payload.get("task", "")
        if permission_level == "strict":
            return True, "严格档位：所有执行都要过审"
        if permission_level == "balanced":
            sensitive, keyword = is_sensitive(task)
            if sensitive:
                return True, f"检测到敏感操作（{keyword}）"
            return False, ""
        if permission_level == "autonomous":
            return False, ""
    return False, ""

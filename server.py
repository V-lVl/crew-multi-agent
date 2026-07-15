"""多 Agent 团队聊天室 - 后端 v2

新增：
- 11 个预设角色 + 用户可勾选谁在场
- SQLite 话题记忆：每次 /discuss 存档、自动摘要、可回看、可继续
- 智能路由从"当前在场成员"里挑人
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

import supervisor as sv  # 总管、招人、Hermes 执行、审批

ARK_API = "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"
MODEL = "ark-code-latest"

BASE = Path(__file__).parent

# 数据目录（可写）：优先环境变量 CREW_DATA_DIR，否则用源码同级
# 打包后 launcher 会设 %APPDATA%\Crew；源码模式落到项目目录（保持向后兼容）
DATA_DIR = Path(os.environ.get("CREW_DATA_DIR", str(BASE)))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 静态资源目录（只读）：优先 PyInstaller 的 _MEIPASS，否则源码同级
_meipass = getattr(sys, "_MEIPASS", None)
ASSETS_DIR = Path(_meipass) if _meipass else BASE

DB_PATH = DATA_DIR / "team.db"
CONFIG_PATH = DATA_DIR / "config.json"
DYNAMIC_AGENTS_PATH = DATA_DIR / "dynamic_agents.json"


def load_config() -> dict:
    """读取用户配置（向导写、后端读）。缺项走默认。"""
    default = {"permission_level": "balanced", "onboarded": False}
    if CONFIG_PATH.exists():
        try:
            return default | json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_dynamic_agents() -> dict:
    """总管招进来的人存这里，程序重启不丢。"""
    if DYNAMIC_AGENTS_PATH.exists():
        try:
            return json.loads(DYNAMIC_AGENTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_dynamic_agents(d: dict) -> None:
    DYNAMIC_AGENTS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


CONFIG = load_config()


def _load_env_from_file() -> None:
    """从 BASE/.env 加载 KEY=VALUE 到 os.environ（如果 .env 存在），
    但不覆盖已存在的环境变量。这样 VBS/计划任务启动时也能拿到 API key。"""
    envfile = DATA_DIR / ".env"
    if not envfile.exists():
        return
    for line in envfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env_from_file()

# ─────────────────────────── LLM Provider（多家兼容） ───────────────────────────
import providers as _providers

# 优先从 CONFIG 里读，其次从 .env 兼容旧 ARK_API_KEY，最后为空
def _get_current_llm() -> tuple[str, str, str, str]:
    """返回 (provider_id, api_key, endpoint, model)"""
    cfg = load_config()
    pid = cfg.get("provider") or "ark"
    key = os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY") or cfg.get("api_key", "")
    p = _providers.get_provider(pid) or _providers.get_provider("ark") or {}
    endpoint = cfg.get("endpoint") or p.get("endpoint", "")
    model = cfg.get("model") or p.get("default_model", "")
    return pid, key, endpoint, model


# 向后兼容：ARK_KEY 保留（有代码引用），指向当前 key
ARK_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY", "")

# ─────────────────────────── 角色定义（11 人） ───────────────────────────
# 头像用像素风 SVG，前端 renderAgents 时按 name 查 static/avatars/{name}.svg
AGENTS: dict[str, dict[str, str]] = {
    "Pine": {
        "role": "产品经理", "emoji": "🌲", "color": "#3f6b4a", "default_on": True,
        "system": "你叫 Pine，团队的产品经理。风格：条理清晰、爱问『用户是谁』『解决什么痛点』。关注：用户价值、需求优先级、MVP。回复 2-4 句话中文，像同事在群里聊天，别用大标题、别报名字。",
    },
    "Ash": {
        "role": "开发", "emoji": "⌨", "color": "#3b82f6", "default_on": True,
        "system": "你叫 Ash，团队的开发工程师。风格：直接实在、偶尔吐槽、爱说『这个能做但…』。关注：技术可行性、工作量、坑、性能。回复 2-4 句话中文，别用大标题、别报名字。",
    },
    "Wren": {
        "role": "设计师", "emoji": "✎", "color": "#a855f7", "default_on": True,
        "system": "你叫 Wren，团队的设计师。风格：感性专业、爱说『用户第一眼看到的应该是…』。关注：视觉层级、体验、一致性。回复 2-4 句话中文，别用大标题、别报名字。",
    },
    "Owl": {
        "role": "测试", "emoji": "◉", "color": "#10b981", "default_on": True,
        "system": "你叫 Owl，团队的测试工程师，QA 老兵。风格：怀疑一切、爱说『那如果…呢』『用户乱点怎么办』。关注：异常路径、边界、兼容性。回复 2-4 句话中文，别用大标题、别报名字。",
    },
    "Chief": {
        "role": "老板", "emoji": "♛", "color": "#f59e0b", "default_on": True,
        "system": "你是 Chief，最终决策者。风格：果断、抓重点、爱说『三个问题：一是…二是…三是…』『这样，就按…做』。关注：ROI、时间、拍板。回复 2-5 句话中文，能拍板就拍板，别用大标题。",
    },
    "Rune": {
        "role": "数据分析", "emoji": "▦", "color": "#06b6d4", "default_on": False,
        "system": "你叫 Rune，团队的数据分析师。风格：一切用数据说话、爱说『我看下数据』『转化率』『AB 测试』。关注：埋点、指标定义、样本量、显著性。回复 2-4 句话中文，具体数字用示意值，别用大标题、别报名字。",
    },
    "Poppy": {
        "role": "客服", "emoji": "☏", "color": "#f97316", "default_on": False,
        "system": "你叫 Poppy，团队的客服主管。风格：接地气、爱说『用户已经在骂了』『昨天有 20 单投诉』。关注：用户真实吐槽、常见问题、售后成本。回复 2-4 句话中文，多用一线用户视角，别用大标题、别报名字。",
    },
    "Judge": {
        "role": "法务", "emoji": "§", "color": "#64748b", "default_on": False,
        "system": "你叫 Judge，团队法务。风格：谨慎、爱说『合规上』『风险点在于』『先看下相关法规』。关注：隐私、合规、条款、知识产权、监管风险。回复 2-4 句话中文，别用大标题、别报名字。",
    },
    "Rally": {
        "role": "运营", "emoji": "❋", "color": "#ef4444", "default_on": False,
        "system": "你叫 Rally，团队的运营。风格：热情有活力、爱说『这个可以搞个活动』『拉新点在哪』。关注：增长、留存、活动、内容、社区。回复 2-4 句话中文，别用大标题、别报名字。",
    },
    "Ivy": {
        "role": "HR", "emoji": "❦", "color": "#14b8a6", "default_on": False,
        "system": "你叫 Ivy，团队的 HR。风格：温和但坚持原则、爱说『从团队角度』『招人成本』『大家的感受』。关注：人力配置、招聘、团队氛围、加班合理性。回复 2-4 句话中文，别用大标题、别报名字。",
    },
    "Ledger": {
        "role": "财务", "emoji": "¥", "color": "#eab308", "default_on": False,
        "system": "你叫 Ledger，团队的财务。风格：精打细算、爱说『这个预算够吗』『ROI 算过没』『现金流』。关注：成本、预算、回本周期、税务。回复 2-4 句话中文，别用大标题、别报名字。",
    },
}
AGENT_NAMES = list(AGENTS.keys())
DEFAULT_ROSTER = [n for n, a in AGENTS.items() if a["default_on"]]

# 合并"总管招进来的人"（持久化在 dynamic_agents.json）
_dyn = load_dynamic_agents()
for _name, _prof in _dyn.items():
    if _name not in AGENTS:
        AGENTS[_name] = _prof
        AGENT_NAMES.append(_name)


def router_system(active: list[str]) -> str:
    return (
        "你是一个团队群消息路由器。用户在群里发了一条消息，当前在场成员：\n"
        + "\n".join(f"- {n}（{AGENTS[n]['role']}）" for n in active)
        + f"\n请判断这条消息最应该由谁来回复。只输出一个名字，从 [{'、'.join(active)}] 里选一个，不要输出其他任何内容。"
    )


# ─────────────────────────── 模型调用（多 provider 兼容） ───────────────────────────
async def call_ark(messages: list[dict], max_tokens: int = 400, temperature: float = 0.85) -> str:
    """兼容旧名。真正走 call_llm。"""
    return await call_llm(messages, max_tokens, temperature)


async def call_llm(messages: list[dict], max_tokens: int = 400, temperature: float = 0.85) -> str:
    pid, key, endpoint, model = _get_current_llm()
    p = _providers.get_provider(pid) or {}
    protocol = p.get("protocol", "openai")
    key_optional = p.get("key_optional", False)

    def _sync() -> str:
        # key 允许空的 provider（本地部署）不要求 key
        if not key and not key_optional:
            return "[未配置 API key。点右上『?』或首次向导里填一个。]"
        try:
            if protocol == "anthropic":
                # Anthropic Messages API 结构不同
                sys_msg = ""
                msgs = []
                for m in messages:
                    if m["role"] == "system":
                        sys_msg = m["content"]
                    else:
                        msgs.append({"role": m["role"], "content": m["content"]})
                body = json.dumps(
                    {
                        "model": model,
                        "system": sys_msg,
                        "messages": msgs,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                req = urllib.request.Request(
                    endpoint,
                    data=body,
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8"))
                    return data["content"][0]["text"].strip()
            else:
                # OpenAI 兼容格式（大部分 provider + 本地 Ollama/LM Studio/vLLM）
                body = json.dumps(
                    {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
                    ensure_ascii=False,
                ).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                # 有 key 就带上；本地部署可以没 key
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                req = urllib.request.Request(endpoint, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            return f"[{pid} {e.code}: {e.read().decode('utf-8', 'ignore')[:180]}]"
        except urllib.error.URLError as e:
            # 本地 endpoint 连不上时给个更友好的提示
            if p.get("local"):
                return f"[连不上 {pid} ({endpoint})。检查本地服务是否启动：{e.reason}]"
            return f"[{pid} 连接失败: {e.reason}]"
        except Exception as e:
            return f"[{pid} 调用出错: {e}]"

    return await asyncio.to_thread(_sync)


# ─────────────────────────── 数据库：话题记忆 ───────────────────────────
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        summary TEXT DEFAULT '',
        roster TEXT DEFAULT '[]',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        kind TEXT DEFAULT 'msg',
        ts REAL NOT NULL,
        FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_topic ON messages(topic_id, id)")
    conn.commit()
    return conn


def db_create_topic(title: str, roster: list[str]) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO topics(title,roster,created_at,updated_at) VALUES(?,?,?,?)",
            (title[:120], json.dumps(roster, ensure_ascii=False), time.time(), time.time()),
        )
        return cur.lastrowid


def db_add_message(topic_id: int, role: str, name: str, content: str, kind: str, ts: float) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO messages(topic_id,role,name,content,kind,ts) VALUES(?,?,?,?,?,?)",
            (topic_id, role, name, content, kind, ts),
        )
        c.execute("UPDATE topics SET updated_at=? WHERE id=?", (ts, topic_id))


def db_set_summary(topic_id: int, summary: str) -> None:
    with db() as c:
        c.execute("UPDATE topics SET summary=? WHERE id=?", (summary, topic_id))


def db_list_topics(limit: int = 50) -> list[dict]:
    with db() as c:
        rows = c.execute(
            "SELECT id,title,summary,roster,created_at,updated_at,"
            "(SELECT COUNT(*) FROM messages WHERE topic_id=topics.id) AS msg_count "
            "FROM topics ORDER BY updated_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) | {"roster": json.loads(r["roster"])} for r in rows]


def db_load_topic(topic_id: int) -> tuple[dict, list[dict]] | None:
    with db() as c:
        t = c.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not t:
            return None
        msgs = c.execute(
            "SELECT role,name,content,kind,ts FROM messages WHERE topic_id=? ORDER BY id",
            (topic_id,),
        ).fetchall()
    topic = dict(t)
    topic["roster"] = json.loads(topic["roster"])
    return topic, [dict(m) for m in msgs]


def db_delete_topic(topic_id: int) -> None:
    with db() as c:
        c.execute("DELETE FROM messages WHERE topic_id=?", (topic_id,))
        c.execute("DELETE FROM topics WHERE id=?", (topic_id,))


# ─────────────────────────── 房间状态（内存） ───────────────────────────
class Room:
    def __init__(self) -> None:
        self.topic_id: int | None = None
        self.history: list[dict[str, Any]] = []
        self.roster: list[str] = list(DEFAULT_ROSTER)
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    def switch_to(self, topic_id: int | None, history: list[dict], roster: list[str]) -> None:
        self.topic_id = topic_id
        self.history = history
        self.roster = roster or list(DEFAULT_ROSTER)

    def build_context(self, target_agent: str, limit: int = 24) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": AGENTS[target_agent]["system"]}]
        for m in self.history[-limit:]:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": f"{m['name']}: {m['content']}"})
            elif m["role"] == "agent":
                if m["name"] == target_agent:
                    msgs.append({"role": "assistant", "content": m["content"]})
                else:
                    msgs.append({"role": "user", "content": f"[{m['name']}]: {m['content']}"})
        return msgs

    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def push_message(self, role: str, name: str, content: str, kind: str = "msg") -> None:
        ts = time.time()
        item = {"role": role, "name": name, "content": content, "ts": ts, "kind": kind}
        self.history.append(item)
        if self.topic_id is None:
            # 用户发第一条消息 / 触发 discuss 时懒创建 topic
            self.topic_id = db_create_topic(content[:60] if role == "user" else "临时话题", self.roster)
        db_add_message(self.topic_id, role, name, content, kind, ts)
        payload = {"type": "message", **item, "topic_id": self.topic_id}
        if role == "agent" and name in AGENTS:
            payload["emoji"] = AGENTS[name]["emoji"]
            payload["color"] = AGENTS[name]["color"]
            payload["title"] = AGENTS[name]["role"]
        await self.broadcast(payload)

    async def push_message_as(self, role: str, name: str, content: str,
                              emoji: str = "", color: str = "", title: str = "",
                              kind: str = "msg") -> None:
        """像 push_message，但用外部指定的 emoji/color/title（用于总管等不在 AGENTS 字典里的角色）。"""
        ts = time.time()
        item = {"role": role, "name": name, "content": content, "ts": ts, "kind": kind}
        self.history.append(item)
        if self.topic_id is None:
            self.topic_id = db_create_topic(content[:60] if role == "user" else "临时话题", self.roster)
        db_add_message(self.topic_id, role, name, content, kind, ts)
        payload = {"type": "message", **item, "topic_id": self.topic_id,
                   "emoji": emoji, "color": color, "title": title}
        await self.broadcast(payload)

    async def push_typing(self, name: str, on: bool) -> None:
        payload = {"type": "typing", "name": name, "on": on}
        if name in AGENTS:
            payload["emoji"] = AGENTS[name]["emoji"]
            payload["color"] = AGENTS[name]["color"]
            payload["title"] = AGENTS[name]["role"]
        elif name == sv.SUPERVISOR_PROFILE["name"]:
            payload["emoji"] = sv.SUPERVISOR_PROFILE["emoji"]
            payload["color"] = sv.SUPERVISOR_PROFILE["color"]
            payload["title"] = sv.SUPERVISOR_PROFILE["role"]
        await self.broadcast(payload)


room = Room()


# ─────────────────────────── 路由 & 讨论 ───────────────────────────
def detect_mention(text: str) -> str | None:
    for name in AGENT_NAMES:
        if f"@{name}" in text:
            return name
    return None


async def route_message(text: str) -> str:
    active = room.roster or DEFAULT_ROSTER
    reply = await call_ark(
        [{"role": "system", "content": router_system(active)},
         {"role": "user", "content": text}],
        max_tokens=20, temperature=0.1,
    )
    for name in active:
        if name in reply:
            return name
    return active[0]


async def agent_reply(agent: str) -> None:
    async with room.lock:
        await room.push_typing(agent, True)
        try:
            reply = await call_ark(room.build_context(agent))
        finally:
            await room.push_typing(agent, False)
        await room.push_message("agent", agent, reply)


async def summarize_topic(topic_id: int | None = None, history: list[dict] | None = None) -> None:
    """让 ark 给某个话题写一句话摘要，存回 DB。默认用当前 room，也可传入快照。"""
    tid = topic_id if topic_id is not None else room.topic_id
    hist = history if history is not None else list(room.history)
    if tid is None or not hist:
        return
    convo = "\n".join(
        f"{m['name']}({m.get('kind','msg')}): {m['content']}"
        for m in hist if m.get("kind") != "notice"
    )[:3000]
    summary = await call_ark(
        [{"role": "system", "content": "用一句中文总结这次团队讨论，30 字以内，聚焦结论或决定，别加引号别加句号。"},
         {"role": "user", "content": convo}],
        max_tokens=80, temperature=0.4,
    )
    summary = summary.strip().strip("。「」\"'")[:80]
    db_set_summary(tid, summary)
    await room.broadcast({"type": "topic_updated", "topic_id": tid, "summary": summary})


async def run_discuss(topic: str) -> None:
    active = room.roster or DEFAULT_ROSTER
    # Chief 放最后拍板
    order = [n for n in active if n != "Chief"] + (["Chief"] if "Chief" in active else [])
    await room.push_message("system", "系统", f"🎬 开始讨论：{topic}（{len(order)} 人）", kind="notice")
    await room.push_message("user", "议题", topic)
    for name in order:
        await agent_reply(name)
        await asyncio.sleep(0.2)
    await room.push_message("system", "系统", "✅ 讨论结束", kind="notice")
    await summarize_topic()


# ─────────────────────────── 总管流程 ───────────────────────────

def _push_supervisor_msg(content: str, kind: str = "msg") -> asyncio.Task:
    """把 Foreman（总管）的话推到当前话题。"""
    return asyncio.create_task(room.push_message_as(
        role="agent", name=sv.SUPERVISOR_PROFILE["name"],
        content=content, kind=kind,
        emoji=sv.SUPERVISOR_PROFILE["emoji"],
        color=sv.SUPERVISOR_PROFILE["color"],
        title=sv.SUPERVISOR_PROFILE["role"],
    ))


async def supervisor_reply(user_text: str) -> None:
    """总管主流程：接一句话 → 让模型输出结构化决策 → 分派。"""
    await room.push_typing(sv.SUPERVISOR_PROFILE["name"], True)
    try:
        # 组一个上下文：sup 的 system + 团队当前有谁 + 最近对话
        team_info = "当前团队在场：" + "、".join(
            f"{n}（{AGENTS[n]['role']}）" for n in room.roster
        )
        # 也把不在场但能拉进来的人告诉总管
        other = [f"{n}（{AGENTS[n]['role']}）" for n in AGENTS if n not in room.roster]
        if other:
            team_info += "\n可以叫进来的：" + "、".join(other)
        messages = [
            {"role": "system", "content": sv.SUPERVISOR_PROFILE["system"] + "\n\n" + team_info},
        ]
        # 只带最近 20 条历史
        for m in room.history[-20:]:
            if m["role"] == "user":
                messages.append({"role": "user", "content": f"{m['name']}: {m['content']}"})
            elif m["role"] == "agent":
                if m["name"] == sv.SUPERVISOR_PROFILE["name"]:
                    messages.append({"role": "assistant", "content": m["content"]})
                else:
                    messages.append({"role": "user", "content": f"[{m['name']}]: {m['content']}"})
        messages.append({"role": "user", "content": user_text})

        raw = await call_ark(messages, max_tokens=600, temperature=0.7)
    finally:
        await room.push_typing(sv.SUPERVISOR_PROFILE["name"], False)

    decision = sv.parse_supervisor_reply(raw)

    # 先把总管想说的话发出来
    await room.push_message_as(
        role="agent", name=sv.SUPERVISOR_PROFILE["name"],
        content=decision["say"] or raw,
        emoji=sv.SUPERVISOR_PROFILE["emoji"],
        color=sv.SUPERVISOR_PROFILE["color"],
        title=sv.SUPERVISOR_PROFILE["role"],
    )

    d = decision["decision"]
    if d == "chat":
        return

    if d == "discuss":
        # 拉人开小会
        parts = [p for p in decision["participants"] if p in AGENTS]
        if not parts:
            parts = list(room.roster)
        # 临时把人拉进 roster（只加不减）
        for p in parts:
            if p not in room.roster:
                room.roster.append(p)
        await room.broadcast({"type": "state", "topic_id": room.topic_id, "roster": room.roster})
        topic_text = decision["topic"] or user_text
        await room.push_message("system", "系统",
            f"◆ Foreman 召集小会：{topic_text}（{'、'.join(parts)}）", kind="notice")
        for p in parts:
            # 用讨论上下文，但只回 2-3 句
            await agent_reply(p)
            await asyncio.sleep(0.15)
        await room.push_message("system", "系统", "✅ 讨论结束，Foreman 会做总结", kind="notice")
        # 让总管收尾
        summary_msgs = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "刚才这轮讨论，你作为总管做一个 2-3 句的收尾和下一步建议。"},
        ]
        wrapup = await call_ark(summary_msgs, max_tokens=300, temperature=0.6)
        await room.push_message_as(
            role="agent", name=sv.SUPERVISOR_PROFILE["name"],
            content=wrapup,
            emoji=sv.SUPERVISOR_PROFILE["emoji"],
            color=sv.SUPERVISOR_PROFILE["color"],
            title=sv.SUPERVISOR_PROFILE["role"],
        )
        return

    if d == "hire":
        role_name = decision["new_role"]
        persona = decision["new_persona"]
        if not role_name or not persona:
            await room.push_message("system", "系统", "⚠️ Foreman 想招人但没写清楚，跳过", kind="notice")
            return
        profile = sv.new_agent_profile(role_name, persona)
        # 避免重名
        base_name = profile["name"]
        idx = 2
        while profile["name"] in AGENTS:
            profile["name"] = f"{base_name}{idx}"
            idx += 1
        # 招人永远要审批
        pending = sv.approvals.create("hire", profile)
        await room.broadcast({
            "type": "approval_needed",
            "id": pending.id, "kind": "hire", "payload": profile,
        })
        await room.push_message("system", "系统",
            f"◇ Foreman 想招一位【{role_name}】，等 Chief 批准…", kind="notice")
        try:
            approved = await asyncio.wait_for(pending.fut, timeout=180)
        except asyncio.TimeoutError:
            approved = False
        await room.broadcast({"type": "approval_resolved", "id": pending.id, "approved": approved})
        if not approved:
            await room.push_message("system", "系统",
                f"❌ 招人被驳回：{role_name}", kind="notice")
            return
        # 落盘 + 加入 AGENTS + 拉进 roster
        AGENTS[profile["name"]] = profile
        if profile["name"] not in AGENT_NAMES:
            AGENT_NAMES.append(profile["name"])
        dyn = load_dynamic_agents()
        dyn[profile["name"]] = profile
        save_dynamic_agents(dyn)
        if profile["name"] not in room.roster:
            room.roster.append(profile["name"])
        await room.broadcast({"type": "agent_added", "profile": {
            "name": profile["name"], "role": profile["role"],
            "emoji": profile["emoji"], "color": profile["color"], "default_on": False,
        }})
        await room.broadcast({"type": "state", "topic_id": room.topic_id, "roster": room.roster})
        await room.push_message("system", "系统",
            f"✅ 新同事【{profile['name']} · {profile['role']}】已入职", kind="notice")
        # 让新人打个招呼
        await agent_reply(profile["name"])
        return

    if d == "execute":
        task = decision["exec_task"]
        if not task:
            await room.push_message("system", "系统", "⚠️ Foreman 想让 Hermes 做事但没写清楚", kind="notice")
            return
        need_approve, reason = sv.needs_approval("execute", {"task": task}, CONFIG["permission_level"])
        if need_approve:
            pending = sv.approvals.create("execute", {"task": task, "reason": reason})
            await room.broadcast({
                "type": "approval_needed",
                "id": pending.id, "kind": "execute", "payload": {"task": task, "reason": reason},
            })
            await room.push_message("system", "系统",
                f"🔒 需要老板批准执行：{reason}", kind="notice")
            try:
                approved = await asyncio.wait_for(pending.fut, timeout=180)
            except asyncio.TimeoutError:
                approved = False
            await room.broadcast({"type": "approval_resolved", "id": pending.id, "approved": approved})
            if not approved:
                await room.push_message("system", "系统",
                    "❌ 执行被驳回", kind="notice")
                return
        else:
            level_txt = {"strict":"严格", "balanced":"平衡", "autonomous":"自主"}.get(CONFIG["permission_level"], CONFIG["permission_level"])
            await room.push_message("system", "系统",
                f"▶ {level_txt}档位下这条不算敏感 · Foreman 直接推给本地 Agent 执行", kind="notice")

        # 真的推给本地 agent
        custom_agents_cfg = CONFIG.get("custom_agents", [])
        selected_agent_id = CONFIG.get("local_agent") or sv.agents_cli.get_default(custom_agents_cfg) or "hermes"
        spec = sv.agents_cli.get_spec(selected_agent_id, custom_agents_cfg)
        agent_display = spec.name if spec else selected_agent_id
        await room.push_message("system", "系统", f"▶ {agent_display} 执行中：{task}", kind="notice")
        # 用 room.broadcast 流式喂回执行日志（简化：只把最终 stdout 一次性推）
        loop = asyncio.get_event_loop()

        def _on_line(line: str) -> None:
            if not line.strip():
                return
            # 简化：把每行当作系统备注推
            asyncio.run_coroutine_threadsafe(
                room.push_message("system", agent_display, line[:500], kind="tool"),
                loop,
            )
        result = await sv.run_hermes(
            task, on_line=_on_line, timeout=300,
            agent_id=selected_agent_id, custom_agents=custom_agents_cfg,
        )
        # 收尾
        tail = result.strip().splitlines()[-1] if result.strip() else "(无输出)"
        await room.push_message("system", "系统", f"✅ 执行完成，最后一行：{tail[:200]}", kind="notice")
        # 让 Foreman 点评
        followup = await call_ark(messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"Hermes 已执行完毕，输出末尾是：{tail[:400]}。用 2-3 句话跟老板汇报结果、下一步建议。"},
        ], max_tokens=250, temperature=0.6)
        await room.push_message_as(
            role="agent", name=sv.SUPERVISOR_PROFILE["name"],
            content=followup,
            emoji=sv.SUPERVISOR_PROFILE["emoji"],
            color=sv.SUPERVISOR_PROFILE["color"],
            title=sv.SUPERVISOR_PROFILE["role"],
        )
        return


# ─────────────────────────── HTTP / WS ───────────────────────────
app = FastAPI()
static_dir = ASSETS_DIR / "static"

from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/agents")
async def get_agents() -> dict:
    return {
        "agents": [
            {"name": n, "role": a["role"], "emoji": a["emoji"], "color": a["color"], "default_on": a["default_on"]}
            for n, a in AGENTS.items()
        ],
        "default_roster": DEFAULT_ROSTER,
    }


@app.get("/api/topics")
async def get_topics() -> dict:
    return {"topics": db_list_topics(), "current": room.topic_id}


@app.get("/api/topics/{topic_id}")
async def get_topic(topic_id: int) -> dict:
    r = db_load_topic(topic_id)
    if not r:
        raise HTTPException(404)
    topic, msgs = r
    return {"topic": topic, "messages": msgs}


@app.delete("/api/topics/{topic_id}")
async def delete_topic(topic_id: int) -> dict:
    db_delete_topic(topic_id)
    if room.topic_id == topic_id:
        room.switch_to(None, [], list(DEFAULT_ROSTER))
        await room.broadcast({"type": "switched", "topic_id": None, "roster": room.roster})
    return {"ok": True}


# ─── 审批 / 权限档 / 配置 ───

@app.get("/api/config")
async def get_config() -> dict:
    """给前端读：当前权限档位、是否已完成首次安装引导、本地 agent 探测、LLM 配置。"""
    # 探测本地 Agent CLI
    local_agents = sv.agents_cli.detect_installed(CONFIG.get("custom_agents", []))
    selected_agent = CONFIG.get("local_agent") or sv.agents_cli.get_default(CONFIG.get("custom_agents", []))
    hermes_ready = any(a["installed"] for a in local_agents)  # 只要有任一本地 agent 装了就 ready
    pid, key, endpoint, model = _get_current_llm()
    p_cfg = _providers.get_provider(pid) or {}
    # LLM 已就绪的判定：有 key，或 provider 是本地部署（key 可选）
    llm_ready = bool(key) or p_cfg.get("key_optional", False)
    return {
        "permission_level": CONFIG.get("permission_level", "balanced"),
        "onboarded": CONFIG.get("onboarded", False),
        "hermes_ready": hermes_ready,  # 兼容旧字段名
        "local_agent_ready": hermes_ready,
        "local_agents": local_agents,
        "selected_local_agent": selected_agent,
        "custom_agents": CONFIG.get("custom_agents", []),
        "ark_key_set": llm_ready,  # 兼容旧字段名
        "llm_key_set": llm_ready,
        "llm": {
            "provider": pid,
            "provider_name": (_providers.get_provider(pid) or {}).get("name", pid),
            "model": model,
            "endpoint": endpoint,
        },
        "supervisor": {
            "name": sv.SUPERVISOR_PROFILE["name"],
            "role": sv.SUPERVISOR_PROFILE["role"],
            "emoji": sv.SUPERVISOR_PROFILE["emoji"],
            "color": sv.SUPERVISOR_PROFILE["color"],
        },
    }


@app.get("/api/local-agents")
async def get_local_agents() -> dict:
    """探测本地已装的 Agent CLI + 用户自定义 agent。"""
    custom_agents = CONFIG.get("custom_agents", [])
    agents = sv.agents_cli.detect_installed(custom_agents)
    selected = CONFIG.get("local_agent") or sv.agents_cli.get_default(custom_agents)
    return {"agents": agents, "selected": selected}


@app.post("/api/local-agents/select")
async def select_local_agent(payload: dict) -> dict:
    """让用户手动切换默认本地 Agent。"""
    agent_id = payload.get("agent_id", "").strip()
    custom_agents = CONFIG.get("custom_agents", [])
    all_agents = sv.agents_cli.detect_installed(custom_agents)
    valid_ids = {a["id"] for a in all_agents}
    if agent_id not in valid_ids:
        raise HTTPException(400, f"unknown agent_id: {agent_id}")
    installed_map = {a["id"]: a["installed"] for a in all_agents}
    if not installed_map.get(agent_id):
        raise HTTPException(400, f"{agent_id} 尚未安装到本机")
    CONFIG["local_agent"] = agent_id
    save_config(CONFIG)
    await room.broadcast({"type": "config_updated", "config": CONFIG})
    return {"ok": True, "selected": agent_id}


# ─── 用户自定义 Agent CRUD ─────────────────────────
@app.get("/api/custom-agents")
async def list_custom_agents() -> dict:
    """列出用户自定义的所有 agent。"""
    return {"custom_agents": CONFIG.get("custom_agents", [])}


@app.post("/api/custom-agents")
async def add_custom_agent(payload: dict) -> dict:
    """新增或更新一个自定义 agent。

    body: {id, name, command, args_template, homepage?}
    id 必填 + 全局唯一（含内置）。command 必须是绝对路径或 PATH 上的命令名。
    args_template 可含 {prompt} 占位；不含则 prompt 自动拼末尾。
    """
    cid = str(payload.get("id", "")).strip()
    name = str(payload.get("name", "")).strip()
    command = str(payload.get("command", "")).strip()
    args_template = str(payload.get("args_template", "")).strip()
    homepage = str(payload.get("homepage", "")).strip()

    if not cid or not name or not command:
        raise HTTPException(400, "id / name / command 都是必填")
    # id 不能撞内置
    builtin_ids = {s.id for s in sv.agents_cli.BUILTIN_SPECS}
    if cid in builtin_ids:
        raise HTTPException(400, f"id={cid} 与内置 agent 冲突，请换一个")

    # upsert
    entry = {"id": cid, "name": name, "command": command,
             "args_template": args_template, "homepage": homepage}
    lst = list(CONFIG.get("custom_agents", []))
    for i, item in enumerate(lst):
        if item.get("id") == cid:
            lst[i] = entry
            break
    else:
        lst.append(entry)
    CONFIG["custom_agents"] = lst
    save_config(CONFIG)
    # 报告探测结果
    detected = next((a for a in sv.agents_cli.detect_installed(lst) if a["id"] == cid), None)
    await room.broadcast({"type": "config_updated", "config": CONFIG})
    return {"ok": True, "agent": entry, "detected": detected}


@app.delete("/api/custom-agents/{agent_id}")
async def delete_custom_agent(agent_id: str) -> dict:
    """删除一个自定义 agent。"""
    lst = [a for a in CONFIG.get("custom_agents", []) if a.get("id") != agent_id]
    CONFIG["custom_agents"] = lst
    # 如果当前选中的正是被删的，回退到默认
    if CONFIG.get("local_agent") == agent_id:
        CONFIG["local_agent"] = sv.agents_cli.get_default(lst)
    save_config(CONFIG)
    await room.broadcast({"type": "config_updated", "config": CONFIG})
    return {"ok": True}


@app.get("/api/providers")
async def get_providers() -> dict:
    """列出所有支持的 LLM provider（下拉用）。"""
    return {"providers": _providers.list_providers()}


@app.post("/api/providers/guess")
async def guess_api_provider(payload: dict) -> dict:
    """根据用户填的 key 尝试自动识别 provider。"""
    key = payload.get("api_key", "")
    best, candidates = _providers.guess_provider(key)
    # 返回每个候选的完整信息（含名字）
    plist = _providers.list_providers()
    all_by_id = {p["id"]: p for p in plist}
    return {
        "best": best,
        "best_name": all_by_id.get(best, {}).get("name") if best else None,
        "candidates": [all_by_id[c] for c in candidates if c in all_by_id],
        "confident": best is not None and len(candidates) == 1,
    }


@app.post("/api/config")
async def set_config(payload: dict) -> dict:
    """向导 / 权限档下拉都写这里。"""
    if "permission_level" in payload:
        lvl = payload["permission_level"]
        if lvl in sv.PERMISSION_LEVELS:
            CONFIG["permission_level"] = lvl
    if "onboarded" in payload:
        CONFIG["onboarded"] = bool(payload["onboarded"])
    # 新：provider / model / endpoint（可分别设置）
    if "provider" in payload and payload["provider"]:
        pid = payload["provider"]
        if _providers.get_provider(pid):
            CONFIG["provider"] = pid
    if "model" in payload:
        val = payload["model"].strip()
        if val:
            CONFIG["model"] = val
        else:
            # 空字符串 = 清除，回退到 provider 默认
            CONFIG.pop("model", None)
    if "endpoint" in payload:
        val = payload["endpoint"].strip()
        if val:
            CONFIG["endpoint"] = val
        else:
            # 空字符串 = 清除，回退到 provider 默认
            CONFIG.pop("endpoint", None)
    # 兼容：ark_api_key 或新的 api_key 都接受
    new_key = payload.get("api_key") or payload.get("ark_api_key")
    if new_key:
        env_path = DATA_DIR / ".env"
        env_path.write_text(f"LLM_API_KEY={new_key}\nARK_API_KEY={new_key}\n", encoding="utf-8")
        global ARK_KEY
        ARK_KEY = new_key
        os.environ["LLM_API_KEY"] = new_key
        os.environ["ARK_API_KEY"] = new_key
    save_config(CONFIG)
    await room.broadcast({"type": "config_updated", "config": CONFIG})
    return {"ok": True, "config": CONFIG}


@app.get("/api/approvals")
async def get_approvals() -> dict:
    return {"pending": sv.approvals.list_pending()}


@app.post("/api/approvals/{pid}")
async def resolve_approval(pid: str, payload: dict) -> dict:
    p = sv.approvals.resolve(pid, bool(payload.get("approved")))
    if not p:
        raise HTTPException(404, "approval not found or already resolved")
    return {"ok": True}


@app.get("/api/dynamic-agents")
async def get_dynamic_agents() -> dict:
    """让向导/调试查看已招进来的动态成员。"""
    return {"agents": load_dynamic_agents()}


@app.delete("/api/dynamic-agents/{name}")
async def delete_dynamic_agent(name: str) -> dict:
    dyn = load_dynamic_agents()
    if name in dyn:
        del dyn[name]
        save_dynamic_agents(dyn)
    if name in AGENTS and AGENTS[name].get("dynamic"):
        del AGENTS[name]
        if name in AGENT_NAMES:
            AGENT_NAMES.remove(name)
        if name in room.roster:
            room.roster.remove(name)
        await room.broadcast({"type": "agent_removed", "name": name})
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    room.clients.add(ws)
    # 一上来推当前状态
    await ws.send_json({"type": "state", "topic_id": room.topic_id, "roster": room.roster})
    for m in room.history:
        payload = {"type": "message", **m, "topic_id": room.topic_id}
        if m["role"] == "agent" and m["name"] in AGENTS:
            payload["emoji"] = AGENTS[m["name"]]["emoji"]
            payload["color"] = AGENTS[m["name"]]["color"]
            payload["title"] = AGENTS[m["name"]]["role"]
        await ws.send_json(payload)
    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action") or "send"

            if action == "set_roster":
                new_roster = [n for n in data.get("roster", []) if n in AGENTS]
                if new_roster:
                    room.roster = new_roster
                    await room.broadcast({"type": "state", "topic_id": room.topic_id, "roster": room.roster})
                continue

            if action == "new_topic":
                # 结束并总结当前 topic（拍快照传进去，防止被后续切换清空）
                if room.topic_id and room.history:
                    asyncio.create_task(summarize_topic(room.topic_id, list(room.history)))
                room.switch_to(None, [], room.roster)
                await room.broadcast({"type": "switched", "topic_id": None, "roster": room.roster, "messages": []})
                continue

            if action == "load_topic":
                tid = int(data.get("topic_id"))
                r = db_load_topic(tid)
                if not r:
                    continue
                topic, msgs = r
                room.switch_to(tid, [dict(m) for m in msgs], topic["roster"] or list(DEFAULT_ROSTER))
                # 推给所有客户端
                payload = {"type": "switched", "topic_id": tid, "roster": room.roster, "messages": []}
                await room.broadcast(payload)
                for m in room.history:
                    p = {"type": "message", **m, "topic_id": tid}
                    if m["role"] == "agent" and m["name"] in AGENTS:
                        p["emoji"] = AGENTS[m["name"]]["emoji"]
                        p["color"] = AGENTS[m["name"]]["color"]
                        p["title"] = AGENTS[m["name"]]["role"]
                    await room.broadcast(p)
                continue

            # 默认：send
            text = (data.get("content") or "").strip()
            if not text:
                continue
            user_name = data.get("user") or "我"
            channel = data.get("channel") or "team"  # "team" | "supervisor"

            if text == "/clear":
                if room.topic_id and room.history:
                    asyncio.create_task(summarize_topic(room.topic_id, list(room.history)))
                room.switch_to(None, [], room.roster)
                await room.broadcast({"type": "switched", "topic_id": None, "roster": room.roster, "messages": []})
                continue

            # 总管频道：用户发言直接进 supervisor_reply
            if channel == "supervisor":
                await room.push_message("user", user_name, text)
                asyncio.create_task(supervisor_reply(text))
                continue

            if text.startswith("/discuss"):
                topic = text[len("/discuss"):].strip() or "随便聊聊"
                asyncio.create_task(run_discuss(topic))
                continue

            await room.push_message("user", user_name, text)
            mention = detect_mention(text)
            if mention and mention in room.roster:
                asyncio.create_task(agent_reply(mention))
            elif mention:
                # @ 了不在场的人：拉他进场
                if mention not in room.roster:
                    room.roster = room.roster + [mention]
                    await room.broadcast({"type": "state", "topic_id": room.topic_id, "roster": room.roster})
                asyncio.create_task(agent_reply(mention))
            else:
                async def _auto():
                    target = await route_message(text)
                    await agent_reply(target)
                asyncio.create_task(_auto())
    except WebSocketDisconnect:
        pass
    finally:
        room.clients.discard(ws)


if __name__ == "__main__":
    # 因为可能被 pythonw.exe (无窗口) 启动，任何异常/print 都要落到日志里
    import sys
    log_path = DATA_DIR / "server.log"
    try:
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass
    import uvicorn
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] → http://127.0.0.1:8765  (key set: {bool(ARK_KEY)})")
    try:
        uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
    except Exception as e:
        import traceback
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] fatal: {e}")
        traceback.print_exc()

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
from fastapi.responses import FileResponse, Response

import supervisor as sv  # 任务调度、招人、Hermes 执行、审批

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
ATTACHMENTS_DIR = DATA_DIR / "attachments"


def load_config() -> dict:
    """读取用户配置（向导写、后端读）。缺项走默认。"""
    default = {"permission_level": "balanced", "onboarded": False, "redact_sensitive": True}
    if CONFIG_PATH.exists():
        try:
            return default | json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_dynamic_agents() -> dict:
    """任务调度招进来的人存这里，程序重启不丢。"""
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
import attachments as _attachments
_attachments.set_attachments_dir(ATTACHMENTS_DIR)

# ─── MCP registry（v2.0） ───────────────────────────────────
from mcp_registry import MCPRegistry
from mcp_client import MCPError as _MCPError
MCP_CONFIG_PATH = DATA_DIR / "mcp_servers.json"
mcp_registry = MCPRegistry(MCP_CONFIG_PATH)
mcp_registry.load_config()

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

# 合并"任务调度招进来的人"（持久化在 dynamic_agents.json）
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
async def call_ark(messages: list[dict], max_tokens: int = 400, temperature: float = 0.85,
                   agent_name: str = "") -> str:
    """兼容旧名。真正走 call_llm。"""
    return await call_llm(messages, max_tokens, temperature, agent_name=agent_name)


async def call_llm(
    messages: list[dict],
    max_tokens: int = 400,
    temperature: float = 0.85,
    agent_name: str = "",
) -> str:
    """调用当前配置的 LLM。

    自动处理：
      · 上下文过长时裁剪（保 system + 最近消息）
      · 429/5xx 指数退避重试 3 次
      · 记录 usage 到 team.db 的 llm_usage 表
    """
    import pricing as _pricing
    import redact as _redact

    pid, key, endpoint, model = _get_current_llm()
    p = _providers.get_provider(pid) or {}
    protocol = p.get("protocol", "openai")
    key_optional = p.get("key_optional", False)

    # ─── 敏感字段脱敏（默认开，可关）───
    if CONFIG.get("redact_sensitive", True):
        redact_counts = {"phone": 0, "id": 0, "email": 0, "key": 0, "bank": 0}
        redacted_msgs = []
        for m in messages:
            new_content, c = _redact.redact(m.get("content", ""))
            for k, v in c.items():
                redact_counts[k] += v
            redacted_msgs.append({**m, "content": new_content})
        messages = redacted_msgs
        total = _redact.total(redact_counts)
        if total > 0:
            hits = ", ".join(f"{k}={v}" for k, v in redact_counts.items() if v > 0)
            print(f"[redact] {agent_name or 'llm'}: 脱敏 {total} 处 ({hits})")

    # ─── 上下文裁剪 ───
    max_ctx = _pricing.get_max_context(pid)
    messages, trim_meta = _pricing.trim_messages(messages, max_ctx, reserve_for_response=max_tokens + 500)
    if trim_meta["dropped"] > 0:
        print(f"[ctx] {agent_name or 'llm'}: 裁剪掉 {trim_meta['dropped']} 条消息 "
              f"(tokens {trim_meta['tokens_before']} → {trim_meta['tokens_after']})")

    def _sync() -> tuple[str, dict]:
        """返回 (text, usage_dict)。usage_dict = {prompt, completion, cost, retries, ok}"""
        # key 允许空的 provider（本地部署）不要求 key
        if not key and not key_optional:
            return f"[{pid} 还没配 API key。点右上『⚙』或首次向导里填一个]", {"ok": False}

        last_error = ""
        for attempt in range(3):  # 最多重试 3 次
            try:
                if protocol == "anthropic":
                    sys_msg = ""
                    msgs = []
                    for m in messages:
                        if m["role"] == "system":
                            sys_msg = m["content"]
                        else:
                            msgs.append({"role": m["role"], "content": m["content"]})
                    body = json.dumps(
                        {"model": model, "system": sys_msg, "messages": msgs,
                         "max_tokens": max_tokens, "temperature": temperature},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    req = urllib.request.Request(endpoint, data=body, headers={
                        "x-api-key": key, "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    })
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = json.loads(r.read().decode("utf-8"))
                        text = data["content"][0]["text"].strip()
                        u = data.get("usage", {})
                        prompt_tok = u.get("input_tokens", 0)
                        comp_tok = u.get("output_tokens", 0)
                else:
                    # OpenAI 兼容
                    body = json.dumps(
                        {"model": model, "messages": messages,
                         "max_tokens": max_tokens, "temperature": temperature},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    headers = {"Content-Type": "application/json"}
                    if key:
                        headers["Authorization"] = f"Bearer {key}"
                    req = urllib.request.Request(endpoint, data=body, headers=headers)
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = json.loads(r.read().decode("utf-8"))
                        text = data["choices"][0]["message"]["content"].strip()
                        u = data.get("usage", {})
                        prompt_tok = u.get("prompt_tokens", 0)
                        comp_tok = u.get("completion_tokens", 0)

                # 有些 provider 不返回 usage — 用估算
                if not prompt_tok:
                    prompt_tok = _pricing.estimate_messages_tokens(messages)
                if not comp_tok:
                    comp_tok = _pricing.estimate_tokens(text)

                cost = _pricing.estimate_cost_usd(pid, prompt_tok, comp_tok)
                return text, {"ok": True, "prompt": prompt_tok, "completion": comp_tok,
                              "cost": cost, "retries": attempt, "provider": pid, "model": model}

            except urllib.error.HTTPError as e:
                code = e.code
                body_text = e.read().decode("utf-8", "ignore")[:200] if hasattr(e, 'read') else ""
                last_error = f"HTTP {code}: {body_text}"
                # 429 / 5xx 指数退避重试；4xx（除 429）立即失败
                if code == 429 or 500 <= code < 600:
                    if attempt < 2:
                        import time as _t
                        _t.sleep(2 ** attempt)  # 1s, 2s, 4s
                        continue
                # 友好化文案
                if code == 429:
                    friendly = f"[{pid} 请求太密，重试 3 次仍限流。等一分钟或换 provider]"
                elif code == 401 or code == 403:
                    friendly = f"[{pid} API key 无效或权限不足（HTTP {code}）。到 ⚙ 设置里更新 key]"
                elif code == 404:
                    friendly = f"[{pid} 端点或 model 不存在（HTTP 404）。检查 model 名，或该 model 是否已被 provider 下线]"
                elif code == 400:
                    friendly = f"[{pid} 请求格式错（HTTP 400）：{body_text[:100]}]"
                elif 500 <= code < 600:
                    friendly = f"[{pid} 服务端故障（HTTP {code}），已重试 3 次仍失败。稍后再试或换 provider]"
                else:
                    friendly = f"[{pid} HTTP {code}: {body_text[:100]}]"
                return friendly, {"ok": False, "retries": attempt, "error": last_error}
            except urllib.error.URLError as e:
                last_error = f"{e.reason}"
                if attempt < 2:
                    import time as _t
                    _t.sleep(2 ** attempt)
                    continue
                if p.get("local"):
                    return f"[连不上本地 {pid} ({endpoint})。检查服务是否启动：{e.reason}]", {"ok": False, "error": last_error}
                return f"[{pid} 连接失败：{e.reason}。检查网络或代理]", {"ok": False, "error": last_error}
            except TimeoutError as e:
                last_error = "timeout"
                if attempt < 2:
                    import time as _t
                    _t.sleep(2 ** attempt)
                    continue
                return f"[{pid} 请求超时（60 秒无响应）。模型可能太慢或网络卡]", {"ok": False, "error": last_error}
            except Exception as e:
                return f"[{pid} 调用出错：{e}]", {"ok": False, "error": str(e)}

        return f"[{pid} 重试 3 次后仍失败: {last_error}]", {"ok": False, "error": last_error}

    text, usage = await asyncio.to_thread(_sync)

    # ─── 写 usage 到 db ───
    if usage.get("ok"):
        try:
            db_log_usage(
                agent=agent_name or "unknown",
                provider=usage["provider"],
                model=usage["model"],
                prompt_tokens=usage["prompt"],
                completion_tokens=usage["completion"],
                cost_usd=usage["cost"],
                retries=usage["retries"],
            )
        except Exception as e:
            print(f"[usage log 写入失败] {e}")

    return text


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
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        agent TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_tokens INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL,
        cost_usd REAL NOT NULL,
        retries INTEGER DEFAULT 0
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(ts)")
    conn.commit()
    return conn


def db_log_usage(agent: str, provider: str, model: str,
                 prompt_tokens: int, completion_tokens: int, cost_usd: float,
                 retries: int = 0) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO llm_usage(ts,agent,provider,model,prompt_tokens,completion_tokens,cost_usd,retries) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), agent, provider, model, prompt_tokens, completion_tokens, cost_usd, retries),
        )


def db_usage_today() -> dict:
    """返回今天（本地时区 0 点起）的 usage 汇总。"""
    import datetime as _dt
    today_start = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with db() as c:
        row = c.execute(
            "SELECT COUNT(*) as calls, "
            "COALESCE(SUM(prompt_tokens),0) as prompt, "
            "COALESCE(SUM(completion_tokens),0) as completion, "
            "COALESCE(SUM(cost_usd),0) as cost "
            "FROM llm_usage WHERE ts >= ?",
            (today_start,),
        ).fetchone()
        by_agent = c.execute(
            "SELECT agent, COUNT(*) as calls, "
            "SUM(prompt_tokens+completion_tokens) as tokens, "
            "SUM(cost_usd) as cost "
            "FROM llm_usage WHERE ts >= ? GROUP BY agent ORDER BY cost DESC LIMIT 10",
            (today_start,),
        ).fetchall()
        return {
            "calls": row["calls"] or 0,
            "prompt_tokens": row["prompt"] or 0,
            "completion_tokens": row["completion"] or 0,
            "total_tokens": (row["prompt"] or 0) + (row["completion"] or 0),
            "cost_usd": round(row["cost"] or 0, 4),
            "by_agent": [
                {"agent": r["agent"], "calls": r["calls"],
                 "tokens": r["tokens"], "cost": round(r["cost"] or 0, 4)}
                for r in by_agent
            ],
        }


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
        # 追踪所有正在跑的 agent/supervisor 任务，用于用户"停止"操作
        self.pending_tasks: set[asyncio.Task] = set()

    def track_task(self, task: asyncio.Task) -> asyncio.Task:
        """把一个任务纳入 pending_tasks，完成时自动清理。"""
        self.pending_tasks.add(task)
        task.add_done_callback(self.pending_tasks.discard)
        return task

    def stop_all(self) -> int:
        """取消所有正在跑的任务，返回被 cancel 的数量。"""
        n = 0
        for t in list(self.pending_tasks):
            if not t.done():
                t.cancel()
                n += 1
        return n

    def switch_to(self, topic_id: int | None, history: list[dict], roster: list[str]) -> None:
        self.topic_id = topic_id
        self.history = history
        self.roster = roster or list(DEFAULT_ROSTER)

    def build_context(self, target_agent: str, limit: int = 24) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": AGENTS[target_agent]["system"]}]
        # 检查当前模型是否支持视觉
        try:
            pid, _, _, model = _get_current_llm()
            protocol = (_providers.get_provider(pid) or {}).get("protocol", "openai")
            vision_ok = _attachments.is_vision_capable(pid, model)
        except Exception:
            protocol, vision_ok = "openai", False

        recent = self.history[-limit:]
        for i, m in enumerate(recent):
            atts = m.get("attachments", []) or []
            has_images = any(a.get("kind") == "image" for a in atts)
            has_text_files = any(a.get("kind") == "text" for a in atts)

            if m["role"] == "user":
                # 只对**最后一条** user 消息带图时启用多模态（否则老图会重复占 token）
                is_last_user = (i == len(recent) - 1)
                if is_last_user and (has_images or has_text_files) and (vision_ok or has_text_files):
                    text = f"{m['name']}: {m['content']}"
                    content = _attachments.build_multimodal_content(text, atts, protocol)
                    msgs.append({"role": "user", "content": content})
                elif is_last_user and has_images and not vision_ok:
                    # 提示模型看不到图
                    n = sum(1 for a in atts if a.get("kind") == "image")
                    msgs.append({"role": "user",
                                 "content": f"{m['name']}: {m['content']}\n[附件：{n} 张图片，但当前模型不支持视觉，未附上]"})
                else:
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

    async def push_message(self, role: str, name: str, content: str, kind: str = "msg",
                           attachments: list[dict] | None = None) -> None:
        ts = time.time()
        item = {"role": role, "name": name, "content": content, "ts": ts, "kind": kind}
        if attachments:
            item["attachments"] = attachments
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
        """像 push_message，但用外部指定的 emoji/color/title（用于任务调度等不在 AGENTS 字典里的角色）。"""
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
        agent_name="[router]",
        max_tokens=20, temperature=0.1,
    )
    for name in active:
        if name in reply:
            return name
    return active[0]


async def agent_reply(agent: str, _chain_depth: int = 0) -> None:
    async with room.lock:
        await room.push_typing(agent, True)
        try:
            reply = await agent_reply_with_tools(agent)
        finally:
            await room.push_typing(agent, False)
        await room.push_message("agent", agent, reply)
    # ─── Agent → Agent 定向 @ 转派 ────────────────────────────
    # 深度 <3 时检查 reply 里有没有 @ 另一个在场 agent
    if _chain_depth < 3:
        mentioned = detect_mention(reply)
        if mentioned and mentioned != agent and mentioned in room.roster:
            await asyncio.sleep(0.2)
            room.track_task(asyncio.create_task(agent_reply(mentioned, _chain_depth + 1)))


# ─── MCP tool call 循环 ─────────────────────────────────────
import re as _re

TOOL_CALL_PATTERN = _re.compile(
    r"```tool_call\s*\n(\{.*?\})\s*\n```", _re.DOTALL
)

MAX_TOOL_ITERATIONS = 5


def _build_tools_prompt(agent: str) -> str:
    """给 agent 生成 tool 使用说明。如果它没有任何工具，返回空串。"""
    tools = mcp_registry.tools_for_agent(agent)
    if not tools:
        return ""

    lines = [
        "",
        "# 你有以下工具可用（MCP）",
        "",
        "想调用工具时，输出一个 fenced code block，语言标 `tool_call`，内容是一行 JSON：",
        "",
        "```tool_call",
        '{"tool": "<qualified_name>", "args": {...}}',
        "```",
        "",
        "然后**立即停止输出**，等系统返回工具结果。你会在下一轮收到结果，再决定继续调用工具还是给最终回复。",
        "调用工具时不要输出其他解释文字，只输出这一个 code block。",
        "**不需要工具时**：直接正常回复。不要滥用工具。",
        "",
        "## 工具列表",
        "",
    ]
    for t in tools:
        lines.append(f"### `{t['qualified_name']}`")
        if t.get("description"):
            lines.append(t["description"])
        schema = t.get("inputSchema") or {}
        props = schema.get("properties") or {}
        if props:
            lines.append("参数：")
            for p_name, p_spec in props.items():
                p_type = p_spec.get("type", "any")
                p_desc = p_spec.get("description", "")
                required = p_name in (schema.get("required") or [])
                mark = "（必填）" if required else ""
                lines.append(f"  · `{p_name}` ({p_type}){mark}：{p_desc}")
        lines.append("")

    return "\n".join(lines)


def _extract_tool_call(text: str) -> dict | None:
    """从 LLM 文本里抠出 tool_call JSON。找不到返回 None。"""
    m = TOOL_CALL_PATTERN.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict) or "tool" not in data:
        return None
    return data


def _format_tool_result(qualified_name: str, result: dict) -> str:
    """把 MCP tool 结果转成给 LLM 看的下一轮 user 消息文本。"""
    is_error = bool(result.get("isError"))
    parts = []
    for block in result.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    text = "\n".join(parts) or "(工具没返回内容)"
    if is_error:
        return f"[tool_result] {qualified_name} ❌ 出错：\n{text}"
    return f"[tool_result] {qualified_name} ✓ 结果：\n{text}"


async def agent_reply_with_tools(agent: str) -> str:
    """让某 agent 回复；如果它调用工具，就跑循环直到给出终稿。

    返回：最终给用户看的文本。
    """
    tools_prompt = _build_tools_prompt(agent)
    base_context = room.build_context(agent)

    # 把 tools_prompt 拼进 system message（第一条）
    if tools_prompt and base_context and base_context[0]["role"] == "system":
        # 处理 system message 可能是 str 或 list（多模态）
        sys_content = base_context[0]["content"]
        if isinstance(sys_content, str):
            base_context[0]["content"] = sys_content + "\n" + tools_prompt
        elif isinstance(sys_content, list):
            base_context[0]["content"].append({"type": "text", "text": tools_prompt})

    # tool call 循环
    conversation = list(base_context)  # working copy
    for iteration in range(MAX_TOOL_ITERATIONS):
        reply = await call_ark(conversation, agent_name=agent)
        tool_call = _extract_tool_call(reply)

        if not tool_call:
            # 无工具调用 —— 就是终稿
            return reply

        qname = tool_call.get("tool", "")
        args = tool_call.get("args") or {}

        # 广播 tool_call 事件给前端（展示卡片）
        await room.broadcast({
            "type": "tool_call",
            "agent": agent,
            "tool": qname,
            "args": args,
            "iteration": iteration + 1,
            "ts": time.time(),
        })

        # 调用工具
        try:
            result = await mcp_registry.call_tool(qname, args, agent_name=agent)
            result_text = _format_tool_result(qname, result)
            is_error = bool(result.get("isError"))
        except _MCPError as e:
            result_text = f"[tool_result] {qname} ❌ 出错：{e}"
            is_error = True

        # 广播 tool_result 事件
        await room.broadcast({
            "type": "tool_result",
            "agent": agent,
            "tool": qname,
            "is_error": is_error,
            "text_preview": (result_text[:300] + "…") if len(result_text) > 300 else result_text,
            "ts": time.time(),
        })

        # 把这轮 assistant + user (工具结果) 塞进 conversation，继续下一轮
        conversation.append({"role": "assistant", "content": reply})
        conversation.append({"role": "user", "content": result_text})

    # 循环上限：强制收尾
    conversation.append({
        "role": "user",
        "content": "（工具调用次数达上限，请基于已有信息给出最终回复，别再调工具了）",
    })
    return await call_ark(conversation, agent_name=agent)


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
        agent_name="[summary]",
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


# ─────────────────────────── 任务调度流程 ───────────────────────────

def _push_supervisor_msg(content: str, kind: str = "msg") -> asyncio.Task:
    """把 Foreman（任务调度）的话推到当前话题。"""
    return asyncio.create_task(room.push_message_as(
        role="agent", name=sv.SUPERVISOR_PROFILE["name"],
        content=content, kind=kind,
        emoji=sv.SUPERVISOR_PROFILE["emoji"],
        color=sv.SUPERVISOR_PROFILE["color"],
        title=sv.SUPERVISOR_PROFILE["role"],
    ))


async def supervisor_reply(user_text: str) -> None:
    """任务调度主流程：接一句话 → 让模型输出结构化决策 → 分派。"""
    await room.push_typing(sv.SUPERVISOR_PROFILE["name"], True)
    try:
        # 组一个上下文：sup 的 system + 团队当前有谁 + 最近对话
        team_info = "当前团队在场：" + "、".join(
            f"{n}（{AGENTS[n]['role']}）" for n in room.roster
        )
        # 也把不在场但能拉进来的人告诉任务调度
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

        raw = await call_ark(messages, max_tokens=600, temperature=0.7, agent_name=sv.SUPERVISOR_PROFILE["name"])
    finally:
        await room.push_typing(sv.SUPERVISOR_PROFILE["name"], False)

    decision = sv.parse_supervisor_reply(raw)

    # 先把任务调度想说的话发出来
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
        # 让任务调度收尾
        summary_msgs = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "刚才这轮讨论，你作为任务调度做一个 2-3 句的收尾和下一步建议。"},
        ]
        wrapup = await call_ark(summary_msgs, max_tokens=300, temperature=0.6, agent_name=sv.SUPERVISOR_PROFILE["name"])
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
        # per-agent 权限：Foreman 是执行发起者，从 CONFIG.agent_permissions 里查它的单独档位
        foreman_name = sv.SUPERVISOR_PROFILE["name"]
        agent_perms = CONFIG.get("agent_permissions", {})
        foreman_level = agent_perms.get(foreman_name)  # None 表示用全局
        need_approve, reason = sv.needs_approval("execute", {"task": task}, CONFIG["permission_level"], agent_level=foreman_level)
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
            level_txt = {"strict":"逐条审批", "balanced":"敏感审批", "autonomous":"自主执行"}.get(CONFIG["permission_level"], CONFIG["permission_level"])
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
        ], max_tokens=250, temperature=0.6, agent_name=sv.SUPERVISOR_PROFILE["name"])
        await room.push_message_as(
            role="agent", name=sv.SUPERVISOR_PROFILE["name"],
            content=followup,
            emoji=sv.SUPERVISOR_PROFILE["emoji"],
            color=sv.SUPERVISOR_PROFILE["color"],
            title=sv.SUPERVISOR_PROFILE["role"],
        )
        return


# ─────────────────────────── HTTP / WS ───────────────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_app):
    # 启动：把 enabled MCP server 都拉起来
    try:
        await mcp_registry.start_all_enabled()
    except Exception as e:
        print(f"[mcp] start_all_enabled 出错但不阻塞：{e}")
    yield
    # 关闭：优雅关掉所有 MCP subprocess
    try:
        await mcp_registry.stop_all()
    except Exception:
        pass

app = FastAPI(lifespan=lifespan)
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


@app.get("/api/topics/{topic_id}/export")
async def export_topic(topic_id: int, fmt: str = "md") -> Response:
    """导出会话为 markdown / json。"""
    r = db_load_topic(topic_id)
    if not r:
        raise HTTPException(404)
    topic, msgs = r
    title = topic.get("title", f"topic-{topic_id}")
    if fmt == "json":
        body = json.dumps({"topic": topic, "messages": msgs}, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{topic_id}.json"'},
        )
    # 默认 markdown
    lines = [f"# {title}", ""]
    if topic.get("summary"):
        lines.append(f"> {topic['summary']}")
        lines.append("")
    for m in msgs:
        role = m.get("role", "")
        name = m.get("name", "")
        content = m.get("content", "")
        ts = m.get("ts", 0)
        import datetime as _dt
        ts_str = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        prefix = {"user": "🧑", "agent": "🤖", "system": "⚙"}.get(role, "·")
        lines.append(f"### {prefix} {name}  <sub>{ts_str}</sub>")
        lines.append("")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")
    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{topic_id}.md"'},
    )


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


@app.get("/api/agents/custom")
async def list_custom_agents_roster() -> dict:
    """列出用户自定义/动态招募的所有同事（区分内置 vs 用户创建）。"""
    dyn = load_dynamic_agents()
    # 内置 AGENTS 里首次加载时定义的（server 启动时已合并 dyn 进 AGENTS 了，
    # 所以要用 dyn 单独区分）
    return {
        "custom": [{"name": n, **prof} for n, prof in dyn.items()],
        "builtin": [n for n in AGENTS if n not in dyn],
    }


@app.post("/api/agents/custom")
async def upsert_custom_agent_roster(payload: dict) -> dict:
    """新增或更新一个自定义同事。

    body: {name, role, emoji?, color?, system, default_on?}
    """
    name = str(payload.get("name", "")).strip()
    role = str(payload.get("role", "")).strip()
    system = str(payload.get("system", "")).strip()
    if not name or not role or not system:
        raise HTTPException(400, "name / role / system 都是必填")
    if len(name) > 30:
        raise HTTPException(400, "name 太长（限 30 字符）")
    # 简单校验：name 不能含空白/引号
    if any(c in name for c in ' \t\n"\'`'):
        raise HTTPException(400, "name 不能含空白或引号")

    emoji = str(payload.get("emoji", "")).strip() or "●"
    color = str(payload.get("color", "")).strip() or "#6b7280"
    default_on = bool(payload.get("default_on", False))
    profile = {"role": role, "emoji": emoji, "color": color,
               "default_on": default_on, "system": system}

    # 内置角色不允许被覆盖
    dyn = load_dynamic_agents()
    if name in AGENTS and name not in dyn:
        raise HTTPException(400, f"'{name}' 与内置角色冲突，请换一个名字")

    dyn[name] = profile
    save_dynamic_agents(dyn)
    # 同步到 in-memory AGENTS，让 router / hire 立即可见
    AGENTS[name] = profile
    if name not in AGENT_NAMES:
        AGENT_NAMES.append(name)
    await room.broadcast({"type": "roster_updated"})
    return {"ok": True, "agent": {"name": name, **profile}}


@app.delete("/api/agents/custom/{name}")
async def delete_custom_agent_roster(name: str) -> dict:
    """删除一个用户自定义同事。内置角色不能删。"""
    dyn = load_dynamic_agents()
    if name not in dyn:
        raise HTTPException(404, f"'{name}' 不是用户自定义角色（或不存在）")
    del dyn[name]
    save_dynamic_agents(dyn)
    # 从 in-memory 表里移除
    AGENTS.pop(name, None)
    if name in AGENT_NAMES:
        AGENT_NAMES.remove(name)
    # 从当前话题的 roster 里也拿掉
    if name in room.roster:
        room.roster.remove(name)
    await room.broadcast({"type": "roster_updated"})
    return {"ok": True}


@app.post("/api/local-agents/test")
async def test_local_agent(payload: dict) -> dict:
    """试跑一个本地 agent CLI：拿一个小 prompt 跑一下，返回 stdout。

    body: {agent_id, prompt?, timeout?}
      agent_id  必填
      prompt    默认: "Say hello in one line."
      timeout   默认 30 秒
    """
    agent_id = str(payload.get("agent_id", "")).strip()
    if not agent_id:
        raise HTTPException(400, "agent_id 必填")
    prompt = str(payload.get("prompt", "")).strip() or "Say hello in one line."
    timeout = float(payload.get("timeout", 30))

    custom_agents = CONFIG.get("custom_agents", [])
    spec = sv.agents_cli.get_spec(agent_id, custom_agents)
    if spec is None:
        raise HTTPException(404, f"unknown agent_id: {agent_id}")
    exe = sv.agents_cli._resolve(spec)
    if not exe:
        return {"ok": False, "reason": f"命令未找到: {spec.command}"}

    import time as _t
    t0 = _t.time()
    output = await sv.agents_cli.run_agent(
        agent_id, prompt, timeout=timeout, custom_agents=custom_agents,
    )
    elapsed = round((_t.time() - t0) * 1000)
    stripped = output.strip()
    return {
        "ok": True,
        "agent_id": agent_id,
        "name": spec.name,
        "path": exe,
        "elapsed_ms": elapsed,
        "prompt": prompt,
        "output": stripped[:2000],
        "output_truncated": len(stripped) > 2000,
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


@app.post("/api/attachments/upload")
async def upload_attachment(payload: dict) -> dict:
    """接收 base64 data URL，落盘 → 返回附件 id + 元数据。
    body: {data_url: 'data:image/png;base64,...', filename?: str}
    """
    data_url = payload.get("data_url", "")
    filename = payload.get("filename", "")
    if not data_url:
        return {"ok": False, "error": "data_url 必填"}
    try:
        att = _attachments.save_attachment(data_url, filename=filename)
        # 检测当前模型是否支持视觉
        pid, _, _, model = _get_current_llm()
        vision_ok = _attachments.is_vision_capable(pid, model)
        return {
            "ok": True,
            "id": att["id"],
            "filename": att["filename"],
            "mime": att["mime"],
            "size": att["size"],
            "kind": att["kind"],
            "vision_supported": vision_ok or att["kind"] != "image",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/ollama/models")
async def ollama_models() -> dict:
    """探测本地 Ollama 有没有起 + 拉已装 model 列表。"""
    def _probe():
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
                data = json.loads(r.read().decode("utf-8"))
                names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                return {"ok": True, "models": names, "endpoint": "http://127.0.0.1:11434"}
        except Exception as e:
            return {"ok": False, "error": str(e), "models": []}
    return await asyncio.to_thread(_probe)


# ─── MCP API（v2.0） ────────────────────────────────────────
@app.get("/api/mcp/servers")
async def mcp_list_servers() -> dict:
    """列出所有配置的 MCP server + 状态 + tools。"""
    return {"servers": mcp_registry.list_servers(),
            "agent_tools": dict(mcp_registry.agent_tools)}


@app.post("/api/mcp/servers/{name}/enable")
async def mcp_enable_server(name: str, payload: dict = None) -> dict:
    payload = payload or {}
    enabled = bool(payload.get("enabled", True))
    ok, msg = await mcp_registry.set_enabled(name, enabled)
    return {"ok": ok, "msg": msg,
            "server": mcp_registry.servers[name].to_public() if name in mcp_registry.servers else None}


@app.post("/api/mcp/servers/{name}/restart")
async def mcp_restart_server(name: str) -> dict:
    ok, msg = await mcp_registry.restart(name)
    return {"ok": ok, "msg": msg,
            "server": mcp_registry.servers[name].to_public() if name in mcp_registry.servers else None}


@app.get("/api/mcp/tools")
async def mcp_all_tools() -> dict:
    """所有 running server 的完整工具列表。"""
    return {"tools": mcp_registry.all_tools()}


@app.get("/api/mcp/tools/{agent}")
async def mcp_tools_for_agent(agent: str) -> dict:
    """某个 agent 能看到的工具（权限过滤后）。"""
    return {"agent": agent, "tools": mcp_registry.tools_for_agent(agent)}


@app.get("/api/mcp/resources/{server_name}")
async def mcp_list_resources(server_name: str) -> dict:
    """列一个 server 的 resources。"""
    return {"server": server_name, "resources": await mcp_registry.list_resources(server_name)}


@app.get("/api/mcp/resources/{server_name}/read")
async def mcp_read_resource(server_name: str, uri: str) -> dict:
    """读 resource。uri 作为 query param 传。"""
    try:
        return await mcp_registry.read_resource(server_name, uri)
    except _MCPError as e:
        raise HTTPException(400, f"读 resource 失败：{e}")


@app.get("/api/mcp/prompts/{server_name}")
async def mcp_list_prompts(server_name: str) -> dict:
    return {"server": server_name, "prompts": await mcp_registry.list_prompts(server_name)}


@app.post("/api/mcp/prompts/{server_name}/get")
async def mcp_get_prompt(server_name: str, payload: dict) -> dict:
    name = payload.get("name", "")
    args = payload.get("arguments", {}) or {}
    try:
        return await mcp_registry.get_prompt(server_name, name, args)
    except _MCPError as e:
        raise HTTPException(400, f"取 prompt 失败：{e}")


@app.post("/api/mcp/agent_tools")
async def mcp_set_agent_tools(payload: dict) -> dict:
    """设置 agent → tool patterns 映射。
    body: {agent: str, patterns: list[str]}   patterns 为空 = 移除该 agent 的权限
    """
    agent = payload.get("agent", "")
    patterns = list(payload.get("patterns") or [])
    if not agent:
        return {"ok": False, "msg": "agent 必填"}
    mcp_registry.set_agent_tools(agent, patterns)
    return {"ok": True, "agent_tools": dict(mcp_registry.agent_tools)}


@app.post("/api/mcp/call")
async def mcp_call_tool(payload: dict) -> dict:
    """手动调用一个工具（用于前端"试运行"按钮）。
    body: {tool: 'server.tool_name', args: {...}, agent?: str}
    """
    tool = payload.get("tool", "")
    args = payload.get("args") or {}
    agent = payload.get("agent", "")   # 空 = 跳过权限校验
    if not tool:
        return {"ok": False, "msg": "tool 必填"}
    try:
        result = await mcp_registry.call_tool(tool, args, agent_name=agent)
        return {"ok": True, "result": result}
    except _MCPError as e:
        return {"ok": False, "msg": str(e)}


@app.get("/api/agents/permissions")
async def get_agent_permissions() -> dict:
    """返回 {global: str, per_agent: {name: level}}"""
    return {
        "global": CONFIG.get("permission_level", "balanced"),
        "per_agent": CONFIG.get("agent_permissions", {}),
    }


@app.post("/api/agents/permissions")
async def set_agent_permission(payload: dict) -> dict:
    """设置某 agent 的独立档位。body: {name, level|null}
    level=null 表示清除（回退到全局档位）。"""
    name = str(payload.get("name", "")).strip()
    level = payload.get("level")
    if not name:
        return {"ok": False, "error": "name 必填"}
    perms = CONFIG.get("agent_permissions", {})
    if level is None or level == "":
        perms.pop(name, None)
    elif level in sv.PERMISSION_LEVELS:
        perms[name] = level
    else:
        return {"ok": False, "error": f"档位必须是 {sv.PERMISSION_LEVELS}"}
    CONFIG["agent_permissions"] = perms
    save_config(CONFIG)
    return {"ok": True, "per_agent": perms}


@app.get("/api/marketplace")
async def marketplace_list() -> dict:
    """从 GitHub raw 拉最新 templates.json。失败时用本地打包版兜底。"""
    urls = [
        "https://raw.githubusercontent.com/V-lVl/crew-multi-agent/main/marketplace/templates.json",
    ]
    def _fetch():
        for u in urls:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Crew/3.0"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    return json.loads(r.read().decode("utf-8", "ignore"))
            except Exception:
                continue
        return None
    templates = await asyncio.to_thread(_fetch)
    if templates is None:
        # 兜底：本地打包版里的 templates.json
        local = ASSETS_DIR / "marketplace" / "templates.json"
        if local.exists():
            templates = json.loads(local.read_text(encoding="utf-8"))
        else:
            templates = []
    return {"templates": templates, "source": "remote" if templates else "empty"}


@app.post("/api/marketplace/install")
async def marketplace_install(payload: dict) -> dict:
    """安装一个模板：把 agents 加到 dynamic_agents + tool patterns 合并到 mcp_registry。"""
    template = payload.get("template") or {}
    agents = template.get("agents", []) or []
    tool_map = template.get("mcp_agent_tools", {}) or {}

    if not agents:
        raise HTTPException(400, "模板里没有 agents")

    # 加 dynamic agents（沿用现有加同事流程）
    added = []
    dyn = load_dynamic_agents()
    for a in agents:
        try:
            name = str(a.get("name", "")).strip()
            role = str(a.get("role", "")).strip()
            system = str(a.get("system", "")).strip()
            if not name or not role or not system:
                continue
            if any(c in name for c in ' \t\n"\'`'):
                continue
            profile = {
                "role": role,
                "emoji": str(a.get("emoji", "")).strip() or "●",
                "color": str(a.get("color", "")).strip() or "#6b7280",
                "default_on": bool(a.get("default_on", False)),
                "system": system,
            }
            if name in AGENTS and name not in dyn:
                # 与内置角色冲突，跳过
                continue
            dyn[name] = profile
            AGENTS[name] = profile
            added.append(name)
        except Exception as e:
            logger.error(f"加 agent {a.get('name')} 失败：{e}")
    save_dynamic_agents(dyn)

    # 合并 mcp agent_tools
    for agent, patterns in tool_map.items():
        existing = list(mcp_registry.agent_tools.get(agent, []))
        for p in patterns:
            if p not in existing:
                existing.append(p)
        mcp_registry.agent_tools[agent] = existing
    mcp_registry.save_config()

    return {"ok": True, "added_agents": added}


@app.get("/api/network")
async def network_info() -> dict:
    """返回当前监听 host + 本机 LAN IP，用户判断能不能被其他机器连。"""
    import socket as _s
    lan_ip = ""
    try:
        s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    # server 目前的 host 由 launcher 决定；只报告当前实际 IP
    return {
        "lan_ip": lan_ip,
        "url_local": "http://127.0.0.1:8765/",
        "url_lan": f"http://{lan_ip}:8765/" if lan_ip else "",
    }


@app.get("/api/workspace")
async def workspace_current() -> dict:
    """当前 workspace + 所有可用 workspace 列表。"""
    root = os.environ.get("CREW_DATA_ROOT", "")
    active = os.environ.get("CREW_WORKSPACE", "default")
    workspaces = []
    if root:
        ws_dir = Path(root) / "workspaces"
        if ws_dir.exists():
            workspaces = sorted(d.name for d in ws_dir.iterdir() if d.is_dir())
    if "default" not in workspaces:
        workspaces = ["default"] + workspaces
    return {"active": active, "workspaces": workspaces, "root": root}


@app.post("/api/workspace/switch")
async def workspace_switch(payload: dict) -> dict:
    """切换 workspace。只改标记文件，需要重启 crew.exe 生效。"""
    import re as _re
    name = str(payload.get("name", "")).strip()
    if not _re.match(r"^[A-Za-z0-9_-]{1,32}$", name):
        raise HTTPException(400, "workspace 名只允许字母/数字/-/_，1-32 字符")
    root = os.environ.get("CREW_DATA_ROOT", "")
    if not root:
        raise HTTPException(500, "CREW_DATA_ROOT 环境变量未设置（源码模式暂不支持切换）")
    # 建目录
    (Path(root) / "workspaces" / name).mkdir(parents=True, exist_ok=True)
    # 写标记
    (Path(root) / "active_workspace").write_text(name, encoding="utf-8")
    return {"ok": True, "active": name, "need_restart": True}


@app.get("/api/usage/today")
async def get_usage_today() -> dict:
    return db_usage_today()


@app.get("/api/usage/timeseries")
async def get_usage_timeseries(days: int = 7) -> dict:
    """按日聚合最近 N 天的 tokens / cost。"""
    import time as _t
    days = max(1, min(days, 90))
    since = _t.time() - days * 86400
    with db() as c:
        rows = c.execute(
            "SELECT CAST(strftime('%s', datetime(ts, 'unixepoch', 'localtime', 'start of day'), 'utc') AS INTEGER) as day, "
            "COALESCE(SUM(prompt_tokens+completion_tokens),0) as tokens, "
            "COALESCE(SUM(cost_usd),0) as cost, "
            "COUNT(*) as calls "
            "FROM llm_usage WHERE ts >= ? GROUP BY day ORDER BY day",
            (since,),
        ).fetchall()
    return {"days": days, "series": [dict(r) for r in rows]}


@app.get("/api/usage/by_provider")
async def get_usage_by_provider(days: int = 7) -> dict:
    import time as _t
    since = _t.time() - days * 86400
    with db() as c:
        rows = c.execute(
            "SELECT provider, model, "
            "SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion, "
            "SUM(cost_usd) as cost, COUNT(*) as calls "
            "FROM llm_usage WHERE ts >= ? GROUP BY provider, model ORDER BY cost DESC",
            (since,),
        ).fetchall()
    return {"days": days, "rows": [dict(r) for r in rows]}


@app.get("/api/usage/mcp_tools")
async def get_usage_mcp_tools(days: int = 7) -> dict:
    """MCP tool 调用统计——从 llm_usage 无法拿，改用 registry 的调用计数（若有）。"""
    stats = getattr(mcp_registry, "tool_call_stats", lambda: {})()
    return {"days": days, "tools": stats}


@app.post("/api/providers/test")
async def test_provider(payload: dict) -> dict:
    """连通性自测：拿 UI 里填的 provider/endpoint/model/key 组一个最小的对话请求，
    快速验证能不能连通、能不能拿到模型响应。

    body: {provider, endpoint?, model?, api_key?}
    endpoint/model/api_key 都可选——不填就用 provider 默认 + 当前保存的 key。
    """
    import time as _t
    pid = str(payload.get("provider", "")).strip()
    p = _providers.get_provider(pid)
    if not p:
        raise HTTPException(400, f"unknown provider: {pid}")

    # 组装临时 endpoint/model/key（不写盘、不改 CONFIG）
    endpoint = str(payload.get("endpoint", "")).strip() or p["endpoint"]
    model = str(payload.get("model", "")).strip() or p["default_model"]
    # 优先 payload 里的 key；否则用当前保存的
    key = str(payload.get("api_key", "")).strip()
    if not key:
        key = os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY") or ""
    key_optional = p.get("key_optional", False)
    if not key and not key_optional:
        return {"ok": False, "reason": "缺少 API key（该 provider 需要 key）"}

    # 用一个最小的 ping prompt
    messages = [{"role": "user", "content": "ping"}]
    protocol = p.get("protocol", "openai")
    t0 = _t.time()

    def _sync() -> dict:
        try:
            if protocol == "anthropic":
                body = json.dumps({
                    "model": model,
                    "system": "Reply with a single word: pong",
                    "messages": messages,
                    "max_tokens": 10,
                }, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    endpoint, data=body,
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                )
            else:
                body = json.dumps({
                    "model": model, "messages": messages, "max_tokens": 10, "temperature": 0.1,
                }, ensure_ascii=False).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                req = urllib.request.Request(endpoint, data=body, headers=headers)

            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
                elapsed = round((_t.time() - t0) * 1000)
                if protocol == "anthropic":
                    reply = data.get("content", [{}])[0].get("text", "")
                else:
                    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {"ok": True, "latency_ms": elapsed,
                        "model_reply": (reply or "").strip()[:200],
                        "provider": pid, "endpoint": endpoint, "model": model}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:400]
            return {"ok": False, "reason": f"HTTP {e.code}: {body}"}
        except urllib.error.URLError as e:
            return {"ok": False, "reason": f"连接失败: {e.reason}"}
        except Exception as e:
            return {"ok": False, "reason": f"错误: {e}"}

    return await asyncio.to_thread(_sync)


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
    # 多人协作：从 query 拿 user_name
    user_name = ws.query_params.get("user", "").strip() or "我"
    if len(user_name) > 24:
        user_name = user_name[:24]
    ws.state.user_name = user_name  # type: ignore
    room.clients.add(ws)
    # 广播新用户加入
    await room.broadcast({"type": "presence", "event": "join", "user": user_name,
                          "online": [getattr(c.state, "user_name", "我") for c in room.clients]})
    # 一上来推当前状态
    await ws.send_json({"type": "state", "topic_id": room.topic_id, "roster": room.roster,
                        "self_user": user_name,
                        "online": [getattr(c.state, "user_name", "我") for c in room.clients]})
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

            if action == "stop":
                # 中断所有正在跑的 agent / supervisor / discuss 任务
                n = room.stop_all()
                # 清除所有 typing 提示
                async with room.lock:
                    for agent in list(room.roster):
                        await room.push_typing(agent, False)
                await room.push_message("system", "系统", f"⏹ 已停止 {n} 个正在进行的任务", kind="notice")
                continue

            if action == "regenerate":
                # 删除最后一条 agent 消息，让 router 重新决定
                if not room.history:
                    continue
                # 找最后一条 agent 消息
                last_agent_idx = None
                for i in range(len(room.history) - 1, -1, -1):
                    if room.history[i].get("role") == "agent":
                        last_agent_idx = i
                        break
                if last_agent_idx is None:
                    continue
                removed = room.history[last_agent_idx]
                # 从 db 删掉
                if room.topic_id:
                    try:
                        with db() as c:
                            c.execute("DELETE FROM messages WHERE topic_id=? AND role='agent' AND name=? AND ts=?",
                                     (room.topic_id, removed.get("name"), removed.get("ts")))
                    except Exception:
                        pass
                room.history.pop(last_agent_idx)
                await room.broadcast({"type": "message_removed", "ts": removed.get("ts")})
                # 让原 agent 重说一次
                agent_name = removed.get("name")
                if agent_name and agent_name in room.roster:
                    room.track_task(asyncio.create_task(agent_reply(agent_name)))
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
            attachments_data = data.get("attachments") or []  # 前端上传后带过来的元数据数组
            if not text and not attachments_data:
                continue
            # 用户名优先用 WS 连接时协商的（多人协作），fallback 消息里的
            user_name = getattr(ws.state, "user_name", None) or data.get("user") or "我"
            channel = data.get("channel") or "team"  # "team" | "supervisor"

            if text == "/clear":
                if room.topic_id and room.history:
                    asyncio.create_task(summarize_topic(room.topic_id, list(room.history)))
                room.switch_to(None, [], room.roster)
                await room.broadcast({"type": "switched", "topic_id": None, "roster": room.roster, "messages": []})
                continue

            # 附件：从磁盘还原 base64 + preview（前端上传后只带 id 过来）
            attachments_full = []
            for meta in attachments_data:
                aid = meta.get("id")
                if not aid:
                    continue
                # 找文件
                found = list(ATTACHMENTS_DIR.glob(f"{aid}.*"))
                if not found:
                    continue
                fpath = found[0]
                raw = fpath.read_bytes()
                kind = meta.get("kind", "other")
                att = {
                    "id": aid,
                    "filename": meta.get("filename", fpath.name),
                    "mime": meta.get("mime", "application/octet-stream"),
                    "size": len(raw),
                    "kind": kind,
                }
                if kind == "image":
                    import base64 as _b64
                    att["base64"] = _b64.b64encode(raw).decode("ascii")
                elif kind == "text":
                    att["preview"] = raw.decode("utf-8", "ignore")[:5000]
                attachments_full.append(att)

            # 任务调度频道：用户发言直接进 supervisor_reply
            if channel == "supervisor":
                await room.push_message("user", user_name, text, attachments=attachments_full)
                room.track_task(asyncio.create_task(supervisor_reply(text)))
                continue

            if text.startswith("/discuss"):
                topic = text[len("/discuss"):].strip() or "随便聊聊"
                room.track_task(asyncio.create_task(run_discuss(topic)))
                continue

            await room.push_message("user", user_name, text, attachments=attachments_full)
            mention = detect_mention(text)
            if mention and mention in room.roster:
                room.track_task(asyncio.create_task(agent_reply(mention)))
            elif mention:
                # @ 了不在场的人：拉他进场
                if mention not in room.roster:
                    room.roster = room.roster + [mention]
                    await room.broadcast({"type": "state", "topic_id": room.topic_id, "roster": room.roster})
                room.track_task(asyncio.create_task(agent_reply(mention)))
            else:
                async def _auto():
                    target = await route_message(text)
                    await agent_reply(target)
                room.track_task(asyncio.create_task(_auto()))
    except WebSocketDisconnect:
        pass
    finally:
        room.clients.discard(ws)
        try:
            await room.broadcast({"type": "presence", "event": "leave",
                                  "user": getattr(ws.state, "user_name", "?"),
                                  "online": [getattr(c.state, "user_name", "我") for c in room.clients]})
        except Exception:
            pass


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

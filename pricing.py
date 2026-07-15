"""Provider 定价 + 上下文窗口元数据。

单独抽出来是因为价格会经常变（provider 会调价 / 出新模型），
而 providers.py 的字段是稳定的（endpoint / protocol / key format）。

单价单位：**USD per 1M tokens**（美元每百万 token）。
数据源：各 provider 官方定价页，2026-07 前后。
不追求完全精确——用户看到的是"这一天大概花了多少"的量级感，不是财务对账。
"""
from __future__ import annotations

# ─────────────────────────── 单价表 ───────────────────────────
# 每个 provider 的**默认模型**的单价。用户切换到别的模型时会用兜底值。
# 找不到时用 DEFAULT_PRICING（用最贵的 GPT-4 级别，宁高勿低）。
PRICING: dict[str, dict] = {
    # ─── 云端 ───
    "openai":       {"input": 0.15,  "output": 0.60,  "max_context": 128000},  # gpt-4o-mini
    "anthropic":    {"input": 3.00,  "output": 15.00, "max_context": 200000},  # claude-3-5-sonnet
    "deepseek":     {"input": 0.14,  "output": 0.28,  "max_context": 64000},   # deepseek-chat
    "kimi":         {"input": 0.14,  "output": 0.14,  "max_context": 128000},  # moonshot-v1-8k 起步价
    "zhipu":        {"input": 0.10,  "output": 0.10,  "max_context": 128000},  # glm-4-flash
    "ark":          {"input": 0.60,  "output": 2.00,  "max_context": 256000},  # doubao-seed-1-6 兜底
    "openrouter":   {"input": 0.50,  "output": 1.50,  "max_context": 128000},  # 按目标模型，此处兜底
    "groq":         {"input": 0.05,  "output": 0.08,  "max_context": 128000},  # llama-3.1-8b
    "qwen":         {"input": 0.28,  "output": 0.83,  "max_context": 32000},   # qwen-max
    "siliconflow":  {"input": 0.05,  "output": 0.05,  "max_context": 32000},   # Qwen2.5-7B
    # ─── 本地/自托管：$0，用户自己算电费 ───
    "ollama":       {"input": 0.00,  "output": 0.00,  "max_context": 32000},
    "lmstudio":     {"input": 0.00,  "output": 0.00,  "max_context": 32000},
    "vllm":         {"input": 0.00,  "output": 0.00,  "max_context": 32000},
    "custom_openai":{"input": 0.00,  "output": 0.00,  "max_context": 32000},
}

# 找不到 provider 时的兜底（保守估计，倾向高价避免用户"惊喜"）
DEFAULT_PRICING = {"input": 1.00, "output": 3.00, "max_context": 32000}


def get_pricing(provider_id: str) -> dict:
    """返回 {input, output, max_context}。永远返回可用值。"""
    return PRICING.get(provider_id, DEFAULT_PRICING)


def estimate_cost_usd(provider_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """按单价表估算这次调用的美元花费。"""
    p = get_pricing(provider_id)
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000


def get_max_context(provider_id: str) -> int:
    return get_pricing(provider_id).get("max_context", 32000)


# ─────────────────────────── Token 估算 ───────────────────────────
# 不引入 tiktoken 依赖（会让打包体积+50MB）。用近似公式：
#   英文：1 token ≈ 4 字符
#   中文：1 token ≈ 1.5 字符（比英文更 token-heavy）
# 混合文本按 unicode 分类逐字符加权。误差 ±15%，对上下文管理够用。
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cn = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - cn
    return int(cn / 1.5 + other / 4) + 1


def estimate_messages_tokens(messages: list[dict]) -> int:
    """整个 messages 数组的 token 估算，含 role/name overhead。"""
    total = 0
    for m in messages:
        # 每条消息约有 4 token 的固定开销（role + separators）
        total += 4 + estimate_tokens(m.get("content", ""))
        if m.get("name"):
            total += estimate_tokens(m["name"])
    return total + 2  # 结尾 primer


# ─────────────────────────── 上下文裁剪 ───────────────────────────
def trim_messages(
    messages: list[dict],
    max_context: int,
    reserve_for_response: int = 1500,
) -> tuple[list[dict], dict]:
    """把 messages 裁剪到 max_context - reserve_for_response 以内。

    策略：
      1. 保留所有 system message（都放前面）
      2. 保留最近的 user/assistant message
      3. 从中间开始丢弃老消息
      4. 如果丢了消息，插一条 "[已省略 N 条早期消息]" 的 system 提示

    返回 (裁剪后的 messages, meta) 其中 meta = {kept: N, dropped: M, tokens_before, tokens_after}
    """
    budget = max_context - reserve_for_response
    if budget < 500:
        budget = 500  # 最少留一些

    before = estimate_messages_tokens(messages)
    if before <= budget:
        return messages, {"kept": len(messages), "dropped": 0,
                          "tokens_before": before, "tokens_after": before}

    # 分开 system 和 chat
    systems = [m for m in messages if m.get("role") == "system"]
    chats = [m for m in messages if m.get("role") != "system"]

    sys_tokens = estimate_messages_tokens(systems)
    chat_budget = budget - sys_tokens
    if chat_budget < 200:
        # system 都太长了，硬截断
        return systems, {"kept": len(systems), "dropped": len(chats),
                         "tokens_before": before, "tokens_after": sys_tokens}

    # 从后往前保留 chat，直到超预算
    kept_chats: list[dict] = []
    used = 0
    for m in reversed(chats):
        cost = 4 + estimate_tokens(m.get("content", ""))
        if used + cost > chat_budget:
            break
        kept_chats.insert(0, m)
        used += cost

    dropped = len(chats) - len(kept_chats)
    result = list(systems)
    if dropped > 0:
        result.append({
            "role": "system",
            "content": f"[上下文过长，此处省略了 {dropped} 条早期消息。如需完整历史，可点新话题重开。]"
        })
    result.extend(kept_chats)
    after = estimate_messages_tokens(result)
    return result, {"kept": len(result), "dropped": dropped,
                    "tokens_before": before, "tokens_after": after}

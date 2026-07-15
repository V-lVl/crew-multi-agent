"""LLM Provider 配置 + 自动识别

用户填一个 API key，系统尝试自动识别。识别不了就让用户手动选。
调用统一走 OpenAI 兼容格式（chat/completions），个别不兼容的（Anthropic Messages API）单独处理。
"""
from __future__ import annotations

import re
from typing import Optional


# ─────────────────────────── Provider 配置 ───────────────────────────
# 每个 provider：
#   name          显示名
#   endpoint      chat completions URL
#   default_model 默认模型（用户可覆盖）
#   key_hint      key 长啥样，用于自动识别（正则或前缀）
#   uniquely_identified  True 表示这个前缀独占（识别不会冲突）
#   protocol      "openai" | "anthropic"（Anthropic 消息 API 结构不同）

PROVIDERS: dict[str, dict] = {
    "ark": {
        "name": "火山方舟 (ARK)",
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "default_model": "ark-code-latest",
        "key_hint": None,  # ARK 的 key 没有稳定前缀
        "uniquely_identified": False,
        "protocol": "openai",
    },
    "openai": {
        "name": "OpenAI",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "key_hint": r"^sk-[A-Za-z0-9_\-]{20,}$",
        "uniquely_identified": False,  # 跟 DeepSeek/Kimi/Qwen 前缀冲突
        "protocol": "openai",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-3-5-sonnet-latest",
        "key_hint": r"^sk-ant-[A-Za-z0-9_\-]{20,}$",
        "uniquely_identified": True,
        "protocol": "anthropic",
    },
    "deepseek": {
        "name": "DeepSeek",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "default_model": "deepseek-chat",
        "key_hint": r"^sk-[A-Za-z0-9]{32,}$",  # DeepSeek 的 sk- 后是纯字母数字
        "uniquely_identified": False,
        "protocol": "openai",
    },
    "moonshot": {
        "name": "Kimi (月之暗面)",
        "endpoint": "https://api.moonshot.cn/v1/chat/completions",
        "default_model": "kimi-latest",
        "key_hint": r"^sk-[A-Za-z0-9]{40,}$",
        "uniquely_identified": False,
        "protocol": "openai",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-4-flash",
        "key_hint": r"^[a-f0-9]{32}\.[A-Za-z0-9]{16,}$",  # <hex32>.<...> 格式
        "uniquely_identified": True,
        "protocol": "openai",
    },
    "openrouter": {
        "name": "OpenRouter",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "anthropic/claude-3.5-sonnet",
        "key_hint": r"^sk-or-[A-Za-z0-9_\-]{20,}$",
        "uniquely_identified": True,
        "protocol": "openai",
    },
    "groq": {
        "name": "Groq",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "default_model": "llama-3.3-70b-versatile",
        "key_hint": r"^gsk_[A-Za-z0-9]{40,}$",
        "uniquely_identified": True,
        "protocol": "openai",
    },
    "qwen": {
        "name": "通义千问 (阿里)",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen-plus",
        "key_hint": r"^sk-[A-Za-z0-9]{28,}$",  # 阿里 sk- 后也是字母数字
        "uniquely_identified": False,
        "protocol": "openai",
    },
    "siliconflow": {
        "name": "SiliconFlow (硅基流动)",
        "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "key_hint": r"^sk-[a-z]{20,}$",  # sk- 后小写字母
        "uniquely_identified": False,
        "protocol": "openai",
    },
}


def guess_provider(api_key: str) -> tuple[Optional[str], list[str]]:
    """根据 key 猜 provider。
    返回 (best_guess, candidates)：
      best_guess: 唯一能确定时返回名字，不能就返回 None
      candidates: 匹配的所有 provider（供 UI 展示）
    """
    key = api_key.strip()
    if not key:
        return None, []

    candidates = []
    unique = None

    for pid, cfg in PROVIDERS.items():
        pat = cfg.get("key_hint")
        if not pat:
            continue
        if re.match(pat, key):
            candidates.append(pid)
            if cfg["uniquely_identified"]:
                unique = pid

    if unique:
        return unique, [unique]
    if len(candidates) == 1:
        return candidates[0], candidates
    if len(candidates) > 1:
        return None, candidates
    return None, []


def list_providers() -> list[dict]:
    """给前端下拉用。"""
    return [
        {
            "id": pid,
            "name": cfg["name"],
            "default_model": cfg["default_model"],
        }
        for pid, cfg in PROVIDERS.items()
    ]


def get_provider(pid: str) -> Optional[dict]:
    """按 id 拿 provider 配置。"""
    return PROVIDERS.get(pid)

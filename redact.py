"""敏感字段脱敏：在把消息送 LLM 前替换掉手机号/身份证/邮箱/API key 等。

设计原则：
- 只脱敏 outbound（发给 LLM 的内容），不改 UI 显示、不改 db 存储
- 用户可通过 config 关闭
- 保留原文长度尽量接近，不破坏语义（用占位符 [PHONE] [ID] [EMAIL] [KEY]）
"""
import re

# 正则：宽松匹配国内手机、18位身份证、邮箱、常见 API key 前缀
_PHONE = re.compile(r"1[3-9]\d{9}")
_ID = re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")
_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_API_KEY_PATTERNS = [
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),  # OpenAI-style incl. project keys
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),  # Anthropic
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),    # Google
    re.compile(r"ghp_[A-Za-z0-9]{36}"),      # GitHub personal
    re.compile(r"gho_[A-Za-z0-9]{36}"),      # GitHub OAuth
    re.compile(r"xox[bpoas]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),  # bearer tokens
]

# 银行卡（16-19 位数字，宽松）
_BANK = re.compile(r"\b(?:62|43|48|51|52|53|54|55|56)\d{14,17}\b")


def redact(text: str) -> tuple[str, dict]:
    """返回 (脱敏后文本, 统计 dict)。"""
    if not text:
        return text, {"phone": 0, "id": 0, "email": 0, "key": 0, "bank": 0}
    counts = {"phone": 0, "id": 0, "email": 0, "key": 0, "bank": 0}

    def _sub_phone(m):
        counts["phone"] += 1
        p = m.group(0)
        return p[:3] + "****" + p[-4:]

    def _sub_id(m):
        counts["id"] += 1
        s = m.group(0)
        return s[:6] + "********" + s[-4:]

    def _sub_email(m):
        counts["email"] += 1
        e = m.group(0)
        at = e.index("@")
        prefix = e[:at]
        if len(prefix) <= 2:
            masked = prefix[0] + "*"
        else:
            masked = prefix[0] + "***" + prefix[-1]
        return masked + e[at:]

    def _sub_key(m):
        counts["key"] += 1
        return "[REDACTED_KEY]"

    def _sub_bank(m):
        counts["bank"] += 1
        s = m.group(0)
        return s[:4] + "*" * (len(s) - 8) + s[-4:]

    # 顺序：先 key（避免手机号误伤），再 id / bank / phone / email
    for pat in _API_KEY_PATTERNS:
        text = pat.sub(_sub_key, text)
    text = _ID.sub(_sub_id, text)
    text = _BANK.sub(_sub_bank, text)
    text = _PHONE.sub(_sub_phone, text)
    text = _EMAIL.sub(_sub_email, text)
    return text, counts


def total(counts: dict) -> int:
    return sum(counts.values())

"""附件处理：图片 + 文本文件 → LLM 可用的 content。

保存策略：
  · 图片：base64 编码进内存 + 存到 %APPDATA%/Crew/attachments/{sha1}.{ext}
  · 文本：抽取内容拼进 prompt

对话时如何呈现给 LLM：
  · 视觉 provider（OpenAI-compatible with vision + Anthropic）：
      构造多模态 content = [{type:text}, {type:image_url|image}]
  · 非视觉：退化为 "[用户上传了 N 张图片，但当前模型不支持视觉]" 提示
  · 文本文件：直接拼进 user 消息的 text 部分（限制 5000 字符）
"""
from __future__ import annotations
import base64
import hashlib
import os
from pathlib import Path
from typing import Optional


# 视觉能力清单：知道哪些 provider/model 家族支持看图
VISION_CAPABLE_MODELS = {
    # OpenAI
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4-vision", "gpt-5",
    # Anthropic
    "claude-3", "claude-3-5", "claude-3-7", "claude-sonnet", "claude-opus", "claude-haiku",
    # ARK
    "doubao-vision", "doubao-1.5-vision",
    # DeepSeek
    "deepseek-vl",
    # 通义
    "qwen-vl", "qwen2-vl", "qwen2.5-vl",
    # 智谱
    "glm-4v",
    # Kimi (Moonshot)
    "moonshot-v1-vision",
    # 开源
    "llava", "internvl", "minicpm-v",
}


def is_vision_capable(provider_id: str, model: str) -> bool:
    m = model.lower()
    for pattern in VISION_CAPABLE_MODELS:
        if pattern in m:
            return True
    return False


ATTACHMENTS_DIR: Optional[Path] = None


def set_attachments_dir(path: Path) -> None:
    global ATTACHMENTS_DIR
    ATTACHMENTS_DIR = path
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def save_attachment(data_url_or_bytes, filename: str = "") -> dict:
    """接收前端传来的 base64 data URL 或原始 bytes，落盘并返回元数据。

    返回：{id, path, mime, size, kind: 'image'|'text'|'other', preview: str}
    """
    if ATTACHMENTS_DIR is None:
        raise RuntimeError("attachments dir not initialized")

    if isinstance(data_url_or_bytes, str):
        # data:image/png;base64,xxxx
        if data_url_or_bytes.startswith("data:"):
            head, b64 = data_url_or_bytes.split(",", 1)
            mime = head[5:].split(";")[0] or "application/octet-stream"
            raw = base64.b64decode(b64)
        else:
            # 纯 base64
            mime = "application/octet-stream"
            raw = base64.b64decode(data_url_or_bytes)
    else:
        mime = "application/octet-stream"
        raw = bytes(data_url_or_bytes)

    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("附件太大（>20MB）")

    sha = hashlib.sha1(raw).hexdigest()[:16]
    ext = mime.split("/")[-1] if "/" in mime else "bin"
    fname = f"{sha}.{ext}"
    (ATTACHMENTS_DIR / fname).write_bytes(raw)

    kind = "image" if mime.startswith("image/") else \
           "text" if mime.startswith("text/") else "other"

    preview = ""
    if kind == "text":
        try:
            preview = raw.decode("utf-8", "ignore")[:5000]
        except Exception:
            preview = ""

    return {
        "id": sha,
        "path": str(ATTACHMENTS_DIR / fname),
        "filename": filename or fname,
        "mime": mime,
        "size": len(raw),
        "kind": kind,
        "preview": preview,
        "base64": base64.b64encode(raw).decode("ascii") if kind == "image" else "",
    }


def build_multimodal_content(text: str, attachments: list[dict], protocol: str) -> object:
    """把 text + 附件组装成 LLM 需要的 content 格式。

    protocol="openai"    → OpenAI 兼容多模态：list of {type:text|image_url}
    protocol="anthropic" → Anthropic：list of {type:text|image}
    """
    # 拼进 text 的：文本附件预览
    text_parts = [text] if text else []
    for att in attachments:
        if att.get("kind") == "text" and att.get("preview"):
            text_parts.append(f"\n\n[附件 {att.get('filename','file.txt')}]\n{att['preview']}")

    full_text = "\n".join(text_parts).strip() or " "  # 空字符串某些模型会报错

    images = [a for a in attachments if a.get("kind") == "image" and a.get("base64")]
    if not images:
        return full_text  # 无图 → 直接返回字符串

    if protocol == "anthropic":
        content = [{"type": "text", "text": full_text}]
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["mime"],
                    "data": img["base64"],
                }
            })
        return content
    else:  # openai-compatible
        content = [{"type": "text", "text": full_text}]
        for img in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img['mime']};base64,{img['base64']}"}
            })
        return content

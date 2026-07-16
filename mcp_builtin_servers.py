"""内置 Python MCP server 示例：filesystem + time。

作用：
  1. 让 Crew 开箱即用有 MCP 能力，不强制用户装 Node.js / npm
  2. 作为 mcp_client 的集成测试对象
  3. 演示怎么写自己的 MCP server

启动：python mcp_builtin_servers.py filesystem <root_dir>
     python mcp_builtin_servers.py time

协议：JSON-RPC 2.0 over stdio，每条消息一行 UTF-8 JSON。
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


PROTOCOL_VERSION = "2024-11-05"
SERVER_VERSION = "0.1.0"


def send(msg: dict) -> None:
    """写一行 JSON 到 stdout，flush。"""
    line = json.dumps(msg, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def error_response(rid, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def result_response(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


# ═══════════════════════════════════════════════════
# filesystem server —— 沙箱在指定根目录
# ═══════════════════════════════════════════════════

class FilesystemServer:
    NAME = "filesystem-builtin"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, p: str) -> Path:
        """把用户传入的相对路径解析到 root 内。禁止逃逸。"""
        candidate = (self.root / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        # 必须在 root 内
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ValueError(f"路径越界，只允许访问 {self.root} 及其子目录")
        return candidate

    def tools(self) -> list[dict]:
        return [
            {
                "name": "read_file",
                "description": f"读取 {self.root} 下的文本文件内容。返回 UTF-8 文本。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对或绝对路径"}
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": f"把文本写入 {self.root} 下的文件。会覆盖已有内容。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "list_dir",
                "description": f"列出 {self.root} 下某个目录的内容。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "default": "."}},
                },
            },
        ]

    def call(self, name: str, args: dict) -> dict:
        if name == "read_file":
            p = self._safe_path(args.get("path", ""))
            if not p.exists():
                return {"content": [{"type": "text", "text": f"文件不存在：{p}"}], "isError": True}
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > 100_000:
                text = text[:100_000] + f"\n\n[...文件过大，已截断至 100KB]"
            return {"content": [{"type": "text", "text": text}]}

        if name == "write_file":
            p = self._safe_path(args.get("path", ""))
            content = args.get("content", "")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"content": [{"type": "text", "text": f"已写入 {len(content)} 字符到 {p}"}]}

        if name == "list_dir":
            p = self._safe_path(args.get("path", "."))
            if not p.exists() or not p.is_dir():
                return {"content": [{"type": "text", "text": f"不是目录：{p}"}], "isError": True}
            items = []
            for child in sorted(p.iterdir()):
                kind = "dir" if child.is_dir() else "file"
                size = child.stat().st_size if child.is_file() else 0
                items.append(f"{kind:4}  {size:>10}  {child.name}")
            listing = "\n".join(items) if items else "(空目录)"
            return {"content": [{"type": "text", "text": listing}]}

        return {"content": [{"type": "text", "text": f"未知工具：{name}"}], "isError": True}


# ═══════════════════════════════════════════════════
# time server —— 提供当前时间 / 时区转换
# ═══════════════════════════════════════════════════

class TimeServer:
    NAME = "time-builtin"

    def tools(self) -> list[dict]:
        return [
            {
                "name": "now",
                "description": "返回当前时间。可指定时区（IANA 时区名，如 Asia/Shanghai）。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tz": {"type": "string", "description": "IANA 时区名，默认为本地"}
                    },
                },
            },
        ]

    def call(self, name: str, args: dict) -> dict:
        if name == "now":
            tz_name = args.get("tz", "")
            if tz_name:
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(tz_name)
                    now = datetime.now(tz)
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"未知时区 {tz_name}：{e}"}], "isError": True}
            else:
                now = datetime.now()
            return {"content": [{"type": "text",
                    "text": now.strftime("%Y-%m-%d %H:%M:%S %Z").strip()}]}
        return {"content": [{"type": "text", "text": f"未知工具：{name}"}], "isError": True}


# ═══════════════════════════════════════════════════
# fetch server —— HTTP 抓取
# ═══════════════════════════════════════════════════

class FetchServer:
    NAME = "fetch-builtin"

    def tools(self) -> list[dict]:
        return [
            {
                "name": "fetch_url",
                "description": "抓取一个 HTTP/HTTPS URL 的文本内容。用于查网页、API。GET only.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "http:// 或 https:// 开头"},
                        "max_length": {"type": "integer", "default": 50000,
                                       "description": "返回内容最大字节数"},
                        "headers": {"type": "object", "description": "可选 HTTP headers"},
                    },
                    "required": ["url"],
                },
            },
        ]

    def call(self, name: str, args: dict) -> dict:
        if name == "fetch_url":
            url = args.get("url", "").strip()
            if not url.startswith(("http://", "https://")):
                return {"content": [{"type": "text", "text": "URL 必须以 http:// 或 https:// 开头"}], "isError": True}
            max_len = int(args.get("max_length", 50000))
            headers = args.get("headers") or {}
            headers.setdefault("User-Agent", "Crew-MCP/0.1")
            try:
                import urllib.request
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read(max_len + 1)
                    truncated = len(raw) > max_len
                    text = raw[:max_len].decode("utf-8", errors="replace")
                    status = resp.status
                    ctype = resp.headers.get("Content-Type", "")
                out = f"[HTTP {status}] Content-Type: {ctype}\n\n{text}"
                if truncated:
                    out += f"\n\n[截断，原内容 > {max_len} 字节]"
                return {"content": [{"type": "text", "text": out}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"抓取失败：{e}"}], "isError": True}
        return {"content": [{"type": "text", "text": f"未知工具：{name}"}], "isError": True}


# ═══════════════════════════════════════════════════
# shell server —— 受限 shell 执行（白名单命令）
# ═══════════════════════════════════════════════════

class ShellServer:
    """只执行白名单里的只读命令（默认：ls/cat/pwd/echo/date/git status 之类）。"""
    NAME = "shell-builtin"

    # 白名单：只允许这些命令的前缀
    ALLOWED_COMMANDS = {
        "ls", "dir", "pwd", "echo", "date", "hostname", "whoami",
        "cat", "head", "tail", "wc", "grep", "find",
        "git", "python", "node", "npm", "curl", "wget",
        "ps", "df", "free", "uname", "which", "where",
    }

    def tools(self) -> list[dict]:
        return [
            {
                "name": "run_command",
                "description": ("执行一个 shell 命令并返回 stdout/stderr。"
                                f"只允许这些命令：{', '.join(sorted(self.ALLOWED_COMMANDS))}。"
                                "命令通过 subprocess 直接 exec，不走 shell，不支持管道/重定向。"),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "第一个词必须在白名单里"},
                        "cwd": {"type": "string", "description": "工作目录，默认当前"},
                        "timeout": {"type": "integer", "default": 30},
                    },
                    "required": ["command"],
                },
            },
        ]

    def call(self, name: str, args: dict) -> dict:
        if name != "run_command":
            return {"content": [{"type": "text", "text": f"未知工具：{name}"}], "isError": True}

        cmd_str = args.get("command", "").strip()
        if not cmd_str:
            return {"content": [{"type": "text", "text": "命令为空"}], "isError": True}

        import shlex
        try:
            argv = shlex.split(cmd_str, posix=False)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"命令解析失败：{e}"}], "isError": True}

        first = os.path.basename(argv[0]).lower().rsplit(".", 1)[0]
        if first not in self.ALLOWED_COMMANDS:
            return {"content": [{"type": "text",
                    "text": f"命令 {first!r} 不在白名单内。允许的：{', '.join(sorted(self.ALLOWED_COMMANDS))}"}],
                    "isError": True}

        import subprocess
        try:
            proc = subprocess.run(argv,
                cwd=args.get("cwd") or None,
                timeout=int(args.get("timeout", 30)),
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = f"[exit={proc.returncode}]\n"
            if proc.stdout:
                out += f"─ stdout ─\n{proc.stdout[:20000]}\n"
            if proc.stderr:
                out += f"─ stderr ─\n{proc.stderr[:5000]}\n"
            return {"content": [{"type": "text", "text": out}]}
        except subprocess.TimeoutExpired:
            return {"content": [{"type": "text", "text": "命令超时"}], "isError": True}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"执行失败：{e}"}], "isError": True}


# ═══════════════════════════════════════════════════
# JSON-RPC 主循环
# ═══════════════════════════════════════════════════

def run(server) -> None:
    """从 stdin 逐行读 JSON-RPC，回应到 stdout。"""
    initialized = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue

        method = msg.get("method")
        rid = msg.get("id")  # notification 没 id
        params = msg.get("params") or {}

        # notification：仅仅记录 initialized，不回
        if rid is None:
            if method == "notifications/initialized":
                initialized = True
            continue

        # request
        try:
            if method == "initialize":
                send(result_response(rid, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": server.NAME, "version": SERVER_VERSION},
                }))
            elif method == "tools/list":
                send(result_response(rid, {"tools": server.tools()}))
            elif method == "tools/call":
                name = params.get("name", "")
                args = params.get("arguments") or {}
                res = server.call(name, args)
                send(result_response(rid, res))
            elif method == "ping":
                send(result_response(rid, {}))
            else:
                send(error_response(rid, -32601, f"Method not found: {method}"))
        except Exception as e:
            send(error_response(rid, -32000, str(e)))


def main():
    if len(sys.argv) < 2:
        print("用法：python mcp_builtin_servers.py <filesystem <root> | time | fetch | shell>", file=sys.stderr)
        sys.exit(1)
    kind = sys.argv[1]
    if kind == "filesystem":
        root = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
        run(FilesystemServer(root))
    elif kind == "time":
        run(TimeServer())
    elif kind == "fetch":
        run(FetchServer())
    elif kind == "shell":
        run(ShellServer())
    elif kind == "memory":
        # memory 走独立文件的 JSON-RPC 循环
        db_path = sys.argv[2] if len(sys.argv) > 2 else "memory.db"
        # 复用它自己的 main：把 argv 重置成 [prog, db_path]
        sys.argv = [sys.argv[0], db_path]
        from mcp_memory_server import main as _mem_main
        _mem_main()
        return
    else:
        print(f"未知 server 类型：{kind}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

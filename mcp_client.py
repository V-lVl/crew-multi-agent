"""MCP (Model Context Protocol) 客户端 - 单 server 连接器。

自实现的 JSON-RPC 2.0 over stdio 客户端，不依赖官方 mcp SDK。

MCP 核心方法（我们实现 6 个）：
  · initialize            握手，交换 protocol version + capabilities
  · initialized (notify)  通知 server 客户端就绪
  · tools/list            列出可用工具
  · tools/call            调用工具
  · resources/list        列出资源
  · resources/read        读取资源

传输：stdio，每条消息一行 UTF-8 JSON。

用法：
    client = MCPClient(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    await client.start()
    tools = await client.list_tools()
    result = await client.call_tool("read_file", {"path": "/tmp/foo.txt"})
    await client.stop()
"""
from __future__ import annotations
import asyncio
import json
import os
import shutil
import sys
from typing import Any, Optional


PROTOCOL_VERSION = "2024-11-05"  # 当前 MCP 协议版本
CLIENT_INFO = {"name": "crew", "version": "2.0.0"}
CLIENT_CAPABILITIES: dict = {"roots": {"listChanged": False}}


class MCPError(Exception):
    """MCP 协议或 server 侧返回的错误。"""
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClient:
    """一条 stdio pipe 上的 MCP client。

    生命周期：
        __init__ → start() → 多次调用方法 → stop()
    """

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, cwd: str | None = None,
                 request_timeout: float = 30.0):
        self.name = name  # 便于日志区分（filesystem / github / ...）
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.request_timeout = request_timeout

        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._server_info: dict = {}
        self._server_capabilities: dict = {}
        self._started = False
        self._stderr_buffer: list[str] = []  # 最近 100 行 stderr，调试用
        self._closed_event = asyncio.Event()

    # ─── lifecycle ──────────────────────────────────

    async def start(self) -> None:
        """启动 subprocess + 完成 initialize 握手。"""
        if self._started:
            return

        # 解析可执行文件路径（Windows 下 npx.cmd 之类需要 which）
        exe = shutil.which(self.command) or self.command
        merged_env = os.environ.copy()
        merged_env.update(self.env)

        try:
            self.proc = await asyncio.create_subprocess_exec(
                exe, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
                cwd=self.cwd,
            )
        except FileNotFoundError as e:
            raise MCPError(-32000, f"启动失败：找不到命令 {self.command!r}。{e}")

        # 后台读 stdout / stderr
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # 发 initialize
        try:
            result = await self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": CLIENT_CAPABILITIES,
                "clientInfo": CLIENT_INFO,
            }, timeout=10.0)
        except Exception as e:
            # 启动就失败——把 stderr 附上便于排查
            await self.stop()
            hint = "\n".join(self._stderr_buffer[-10:])
            raise MCPError(-32000, f"initialize 失败：{e}。stderr 尾部：{hint}")

        self._server_info = result.get("serverInfo", {})
        self._server_capabilities = result.get("capabilities", {})

        # 按协议发 initialized notification
        await self._notify("notifications/initialized", {})
        self._started = True

    async def stop(self) -> None:
        """优雅关闭 subprocess。"""
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
            except ProcessLookupError:
                pass
        # 取消 pending
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(MCPError(-32000, "客户端已关闭"))
        self._pending.clear()

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
        self._started = False
        self._closed_event.set()

    def is_alive(self) -> bool:
        return bool(self.proc and self.proc.returncode is None and self._started)

    # ─── stdio 读写 ─────────────────────────────────

    async def _read_stdout(self) -> None:
        """持续从 stdout 读一行 JSON，分发到对应的 Future。"""
        assert self.proc and self.proc.stdout
        while True:
            try:
                line = await self.proc.stdout.readline()
            except Exception:
                break
            if not line:
                break  # EOF
            try:
                msg = json.loads(line.decode("utf-8").strip())
            except Exception:
                # 非 JSON 行忽略（有的 server 会打调试日志到 stdout —— 违反协议但兼容一下）
                continue
            await self._dispatch(msg)
        # stdout 关了 → 通知所有 pending 请求
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(MCPError(-32000, "server 关闭了 stdout"))
        self._pending.clear()

    async def _read_stderr(self) -> None:
        """吃掉 stderr，保留最近 100 行方便报错时展示。"""
        assert self.proc and self.proc.stderr
        while True:
            try:
                line = await self.proc.stderr.readline()
            except Exception:
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            self._stderr_buffer.append(text)
            if len(self._stderr_buffer) > 100:
                self._stderr_buffer.pop(0)

    async def _dispatch(self, msg: dict) -> None:
        """收到一条消息：可能是 response、notification、或 server 主动 request。"""
        if "id" in msg and ("result" in msg or "error" in msg):
            # 是 response
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(MCPError(
                        err.get("code", -32000),
                        err.get("message", "unknown"),
                        err.get("data"),
                    ))
                else:
                    fut.set_result(msg.get("result", {}))
        # server 发来的 notification / request 目前忽略（未来可实现 sampling）

    async def _write(self, obj: dict) -> None:
        assert self.proc and self.proc.stdin
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def _request(self, method: str, params: dict | None = None,
                       timeout: float | None = None) -> Any:
        """发一个 request，等 response。"""
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut

        await self._write({
            "jsonrpc": "2.0", "id": rid, "method": method,
            "params": params or {},
        })

        try:
            return await asyncio.wait_for(fut, timeout=timeout or self.request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise MCPError(-32001, f"请求 {method} 超时（{timeout or self.request_timeout}s）")

    async def _notify(self, method: str, params: dict | None = None) -> None:
        """发一个 notification（不等 response）。"""
        await self._write({
            "jsonrpc": "2.0", "method": method, "params": params or {},
        })

    # ─── 高层 API ──────────────────────────────────

    async def list_tools(self) -> list[dict]:
        """返回 [{name, description, inputSchema}]。"""
        if "tools" not in self._server_capabilities:
            return []
        result = await self._request("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """调用一个工具。返回 {content: [{type,text|...}], isError: bool}"""
        return await self._request("tools/call", {
            "name": name, "arguments": arguments or {},
        }, timeout=self.request_timeout)

    async def list_resources(self) -> list[dict]:
        if "resources" not in self._server_capabilities:
            return []
        result = await self._request("resources/list")
        return result.get("resources", [])

    async def read_resource(self, uri: str) -> dict:
        return await self._request("resources/read", {"uri": uri})

    @property
    def server_info(self) -> dict:
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> dict:
        return dict(self._server_capabilities)

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_buffer[-20:])

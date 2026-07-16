"""MCP HTTP transport client。

对接远程 MCP server（HTTP + Streamable HTTP，符合 MCP 2025-03 spec 的最简子集）：
  - initialize / tools/list / tools/call / resources/list / resources/read
    / prompts/list / prompts/get 全部走 POST JSON-RPC 到 <base_url>/rpc
  - 不实现 SSE 双向通知（server → client 事件推送）；只做请求-响应

用法：
    client = HTTPMCPClient("time", "https://mcp.example.com", auth_bearer="xxx")
    await client.start()
    tools = await client.list_tools()

若要支持 SSE，可以后续扩展；MVP 版够对接 90% 的公开 server。
"""
from __future__ import annotations
import asyncio
import json
import urllib.request
import urllib.error
from typing import Any


class MCPError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class HTTPMCPClient:
    """通过 HTTP POST 与远程 MCP server 通信。"""

    def __init__(self, name: str, url: str,
                 auth_bearer: str = "", headers: dict | None = None,
                 request_timeout: float = 30.0):
        self.name = name
        # base URL（末尾去掉 /）
        self.url = url.rstrip("/")
        self.auth_bearer = auth_bearer
        self.headers = dict(headers or {})
        self.request_timeout = request_timeout
        self._id_counter = 0
        self._server_info: dict = {}
        self._server_capabilities: dict = {}
        self._started = False
        self._alive = False

    async def start(self) -> None:
        if self._started:
            return
        # initialize 握手
        result = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "crew-multi-agent", "version": "3.0"},
        })
        self._server_info = result.get("serverInfo", {})
        self._server_capabilities = result.get("capabilities", {})
        self._started = True
        self._alive = True

    async def stop(self) -> None:
        # HTTP 无连接可关，标记死透即可
        self._alive = False
        self._started = False

    def is_alive(self) -> bool:
        return self._alive

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    async def _request(self, method: str, params: dict | None = None,
                       timeout: float | None = None) -> dict:
        """发起一次 JSON-RPC 请求（阻塞 urllib，跑在 to_thread 里）。"""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_bearer:
            headers["Authorization"] = f"Bearer {self.auth_bearer}"
        headers.update(self.headers)

        def _do_post() -> dict:
            req = urllib.request.Request(
                self.url + "/rpc" if not self.url.endswith("/rpc") else self.url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.request_timeout) as r:
                    body = r.read().decode("utf-8")
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:500] if hasattr(e, 'read') else ""
                raise MCPError(-32000, f"HTTP {e.code}: {body}")
            except urllib.error.URLError as e:
                self._alive = False
                raise MCPError(-32001, f"网络错误：{e.reason}")

        resp = await asyncio.to_thread(_do_post)
        if "error" in resp:
            err = resp["error"]
            raise MCPError(err.get("code", -32000), err.get("message", "unknown"), err.get("data"))
        return resp.get("result", {})

    # ─── 高层 API（与 stdio MCPClient 同名 API）───

    async def list_tools(self) -> list[dict]:
        if "tools" not in self._server_capabilities:
            return []
        result = await self._request("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return await self._request("tools/call", {
            "name": name, "arguments": arguments or {},
        })

    async def list_resources(self) -> list[dict]:
        if "resources" not in self._server_capabilities:
            return []
        result = await self._request("resources/list")
        return result.get("resources", [])

    async def read_resource(self, uri: str) -> dict:
        return await self._request("resources/read", {"uri": uri})

    async def list_prompts(self) -> list[dict]:
        if "prompts" not in self._server_capabilities:
            return []
        result = await self._request("prompts/list")
        return result.get("prompts", [])

    async def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        return await self._request("prompts/get", {
            "name": name, "arguments": arguments or {},
        })

    @property
    def server_info(self) -> dict:
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> dict:
        return dict(self._server_capabilities)

    @property
    def stderr_tail(self) -> list[str]:
        return []  # HTTP transport 没有 stderr

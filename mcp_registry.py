"""MCP Registry —— 多 server 的集中管理。

职责：
  · 从配置加载 servers（哪些启用、哪些禁用）
  · 生命周期：start_all / stop_all / restart(name)
  · Tool 汇总：所有启用 server 的工具合并成一张"全局工具表"
  · 名字冲突：`server_name.tool_name` 命名空间避免撞名
  · 健康检查：定期 ping，挂了标记 dead，下次调用前重启
  · 权限：每个 agent 允许调哪些 tool

配置格式（mcp_servers.json）：

    {
      "servers": [
        {
          "name": "filesystem",
          "enabled": true,
          "command": "python",
          "args": ["mcp_builtin_servers.py", "filesystem", "%APPDATA%\\Crew\\workspace"],
          "env": {},
          "description": "本地文件读写（沙箱在 Crew workspace）"
        },
        ...
      ],
      "agent_tools": {
        "Ash":  ["filesystem.*", "time.*"],     // 通配符：整个 server 都开
        "Owl":  ["filesystem.read_file"],       // 具体某个 tool
        "*":    ["time.now"]                     // 通配 agent（默认权限）
      }
    }
"""
from __future__ import annotations
import asyncio
import fnmatch
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from mcp_client import MCPClient, MCPError


logger = logging.getLogger("mcp_registry")


class MCPServerEntry:
    """一个 server 的运行时状态。"""
    def __init__(self, config: dict):
        self.name: str = config["name"]
        self.enabled: bool = config.get("enabled", True)
        self.command: str = config["command"]
        self.args: list[str] = list(config.get("args", []))
        self.env: dict = dict(config.get("env", {}))
        self.description: str = config.get("description", "")
        self.request_timeout: float = float(config.get("request_timeout", 30.0))

        self.client: Optional[MCPClient] = None
        self.tools: list[dict] = []         # 缓存 tools/list 结果
        self.last_error: str = ""
        self.status: str = "stopped"        # stopped | starting | running | dead

    def to_public(self) -> dict:
        """返回可通过 API 暴露的信息（不含敏感 env）。"""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "command": self.command,
            "args": self.args,
            "description": self.description,
            "status": self.status,
            "last_error": self.last_error,
            "tools": [{"name": t["name"], "description": t.get("description", "")}
                      for t in self.tools],
        }


class MCPRegistry:
    """全局 registry：单例，被 server.py 持有。"""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.servers: dict[str, MCPServerEntry] = {}   # name → entry
        self.agent_tools: dict[str, list[str]] = {}    # agent → glob patterns
        self._lock = asyncio.Lock()
        self._loaded = False

    # ─── 配置加载 ─────────────────────────────────

    def load_config(self) -> None:
        """从磁盘读配置。文件缺失 → 使用兜底默认。"""
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载 mcp_servers.json 失败：{e}，使用默认配置")
                data = self._default_config()
        else:
            data = self._default_config()
            # 写盘作为模板
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(f"已写入默认 mcp_servers.json 到 {self.config_path}")

        # 展开环境变量
        for s in data.get("servers", []):
            s["args"] = [os.path.expandvars(a) for a in s.get("args", [])]

        # 重建 servers dict（保留旧的 client 若名字不变）
        old = self.servers
        self.servers = {}
        for cfg in data.get("servers", []):
            name = cfg["name"]
            if name in old:
                # 保留运行中的 client
                entry = MCPServerEntry(cfg)
                entry.client = old[name].client
                entry.tools = old[name].tools
                entry.status = old[name].status
                self.servers[name] = entry
            else:
                self.servers[name] = MCPServerEntry(cfg)

        self.agent_tools = dict(data.get("agent_tools", {}))
        self._loaded = True

    def save_config(self) -> None:
        """把当前 servers 状态写回配置文件。"""
        data = {
            "servers": [
                {
                    "name": s.name,
                    "enabled": s.enabled,
                    "command": s.command,
                    "args": s.args,
                    "env": s.env,
                    "description": s.description,
                    "request_timeout": s.request_timeout,
                }
                for s in self.servers.values()
            ],
            "agent_tools": self.agent_tools,
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _default_config(self) -> dict:
        """开箱默认：内置 4 个 Python MCP server。

        - time: 默认开（无副作用）
        - fetch: 默认开（只读联网）
        - filesystem: 默认关（安全）
        - shell: 默认关（安全）

        权限默认：所有 agent 都能查时间和抓 URL；文件和 shell 只给 Ash + Foreman。
        """
        appdata = os.environ.get("APPDATA", "")
        workspace = str(Path(appdata) / "Crew" / "workspace") if appdata else "./workspace"
        script_dir = Path(__file__).parent.resolve()

        return {
            "servers": [
                {
                    "name": "time",
                    "enabled": True,
                    "command": "python",
                    "args": [str(script_dir / "mcp_builtin_servers.py"), "time"],
                    "env": {},
                    "description": "当前时间 + 时区转换（内置，零依赖）",
                },
                {
                    "name": "fetch",
                    "enabled": True,
                    "command": "python",
                    "args": [str(script_dir / "mcp_builtin_servers.py"), "fetch"],
                    "env": {},
                    "description": "抓取 HTTP/HTTPS URL 的文本内容（GET only）",
                },
                {
                    "name": "filesystem",
                    "enabled": False,
                    "command": "python",
                    "args": [str(script_dir / "mcp_builtin_servers.py"),
                             "filesystem", workspace],
                    "env": {},
                    "description": f"本地文件读写（沙箱在 {workspace}）",
                },
                {
                    "name": "shell",
                    "enabled": False,
                    "command": "python",
                    "args": [str(script_dir / "mcp_builtin_servers.py"), "shell"],
                    "env": {},
                    "description": "受限 shell 命令白名单执行（只读命令，如 ls/git/curl）",
                },
            ],
            "agent_tools": {
                "*": ["time.*", "fetch.*"],           # 所有 agent 默认能查时间 + 抓网页
                "Ash": ["*"],                          # 开发能用所有工具
                "Foreman": ["*"],                      # 调度员也全权
                "Owl": ["filesystem.read_file", "shell.run_command"],  # 测试只读
            },
        }

    # ─── 生命周期 ─────────────────────────────────

    async def start(self, name: str) -> tuple[bool, str]:
        """启动一个 server。返回 (成功, 消息)。"""
        async with self._lock:
            entry = self.servers.get(name)
            if not entry:
                return False, f"未知 server：{name}"

            if entry.client and entry.client.is_alive():
                return True, "已在运行"

            entry.status = "starting"
            entry.last_error = ""

            # 命令解析（关键）：
            # · "python" → 走 sys.executable。开发时 = python.exe；打包版 = crew.exe
            # · 打包版里，command 若是 "python" 且 args[0] 指向内置脚本 mcp_builtin_servers.py，
            #   改用 "crew.exe --mcp-server <kind> [rest]" 方案，让 crew.exe 走 launcher 的 MCP 分支
            import sys as _sys
            command = entry.command
            args = list(entry.args)
            is_frozen = getattr(_sys, "frozen", False)

            if command == "python":
                if is_frozen and args and "mcp_builtin_servers" in args[0]:
                    # 打包版 + 内置 server：走 crew.exe --mcp-server
                    command = _sys.executable
                    # args[0] 是脚本路径，args[1] 是 kind（time/fetch/...），args[2:] 是 kind 的参数
                    kind = args[1] if len(args) > 1 else "time"
                    rest = args[2:] if len(args) > 2 else []
                    args = ["--mcp-server", kind] + rest
                else:
                    command = _sys.executable

            client = MCPClient(
                name=entry.name,
                command=command,
                args=args,
                env=entry.env,
                request_timeout=entry.request_timeout,
            )
            try:
                await client.start()
                tools = await client.list_tools()
                entry.client = client
                entry.tools = tools
                entry.status = "running"
                logger.info(f"[mcp] {name} 启动成功，{len(tools)} 个工具")
                return True, f"启动成功，{len(tools)} 个工具"
            except Exception as e:
                await client.stop()
                entry.status = "dead"
                entry.last_error = str(e)
                logger.error(f"[mcp] {name} 启动失败：{e}")
                return False, str(e)

    async def stop(self, name: str) -> tuple[bool, str]:
        async with self._lock:
            entry = self.servers.get(name)
            if not entry:
                return False, f"未知 server：{name}"
            if entry.client:
                await entry.client.stop()
                entry.client = None
            entry.tools = []
            entry.status = "stopped"
            return True, "已停止"

    async def restart(self, name: str) -> tuple[bool, str]:
        await self.stop(name)
        return await self.start(name)

    async def start_all_enabled(self) -> None:
        """启动所有 enabled=true 的 server。失败不阻塞其他。"""
        tasks = []
        for entry in self.servers.values():
            if entry.enabled and not (entry.client and entry.client.is_alive()):
                tasks.append(self.start(entry.name))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        for entry in list(self.servers.values()):
            if entry.client:
                try:
                    await entry.client.stop()
                except Exception:
                    pass
                entry.client = None
                entry.status = "stopped"

    async def set_enabled(self, name: str, enabled: bool) -> tuple[bool, str]:
        """开关某个 server（会自动 start/stop + 写盘）。"""
        entry = self.servers.get(name)
        if not entry:
            return False, f"未知 server：{name}"
        entry.enabled = enabled
        self.save_config()
        if enabled:
            return await self.start(name)
        else:
            return await self.stop(name)

    # ─── 工具查询/调用 ────────────────────────────

    def all_tools(self) -> list[dict]:
        """所有 running server 的工具的扁平列表。
        每项：{qualified_name, server, name, description, inputSchema}"""
        out = []
        for entry in self.servers.values():
            if entry.status != "running":
                continue
            for t in entry.tools:
                out.append({
                    "qualified_name": f"{entry.name}.{t['name']}",
                    "server": entry.name,
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema", {}),
                })
        return out

    def tools_for_agent(self, agent_name: str) -> list[dict]:
        """返回某个 agent 允许使用的工具列表（按 agent_tools 匹配 glob）。"""
        patterns = list(self.agent_tools.get(agent_name, []))
        # 加上通配 agent 的权限
        patterns.extend(self.agent_tools.get("*", []))
        if not patterns:
            return []

        allowed = []
        for tool in self.all_tools():
            qn = tool["qualified_name"]
            if any(fnmatch.fnmatchcase(qn, p) for p in patterns):
                allowed.append(tool)
        return allowed

    def set_agent_tools(self, agent_name: str, patterns: list[str]) -> None:
        """设置某个 agent 允许的工具 glob 模式列表。空列表 = 移除权限。"""
        if patterns:
            self.agent_tools[agent_name] = list(patterns)
        else:
            self.agent_tools.pop(agent_name, None)
        self.save_config()

    async def call_tool(self, qualified_name: str, arguments: dict,
                        agent_name: str = "") -> dict:
        """按 `server.tool` 调用工具。

        若指定 agent_name：先做权限校验；不通过则抛 MCPError。
        """
        if "." not in qualified_name:
            raise MCPError(-32602, f"工具名必须形如 server.tool_name，收到 {qualified_name!r}")

        server_name, tool_name = qualified_name.split(".", 1)

        # 权限校验（按配置的 glob 模式，不依赖 server 是否运行 —— 挂了就自愈重启）
        if agent_name:
            patterns = list(self.agent_tools.get(agent_name, []))
            patterns.extend(self.agent_tools.get("*", []))
            import fnmatch as _fn
            if not any(_fn.fnmatchcase(qualified_name, p) for p in patterns):
                raise MCPError(-32001,
                    f"Agent {agent_name!r} 没有 {qualified_name!r} 的权限")

        entry = self.servers.get(server_name)
        if not entry:
            raise MCPError(-32601, f"未知 server：{server_name}")

        # 若已挂，自动尝试重启一次
        if entry.status != "running" or not (entry.client and entry.client.is_alive()):
            ok, msg = await self.start(server_name)
            if not ok:
                raise MCPError(-32000, f"server {server_name} 未运行且重启失败：{msg}")

        assert entry.client is not None
        try:
            result = await entry.client.call_tool(tool_name, arguments)
        except MCPError:
            raise
        except Exception as e:
            # 底层出问题，把 client 标死，下次重启
            entry.status = "dead"
            entry.last_error = str(e)
            raise MCPError(-32000, f"调用 {qualified_name} 失败：{e}")

        return result

    # ─── 状态导出 ─────────────────────────────────

    def list_servers(self) -> list[dict]:
        return [s.to_public() for s in self.servers.values()]

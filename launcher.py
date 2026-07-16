"""Crew launcher —— PyInstaller 打包后的入口（桌面版）。

关键：打包时 console=False，双击不弹黑窗。
因此必须防护 stdout/stderr（在 console=False 下 sys.stdout=None，print 会崩）。

职责：
  1. 找/建用户数据目录 %APPDATA%\\Crew\\
  2. 把 stdout/stderr 重定向到日志文件（避免 print 崩）
  3. 设 CREW_DATA_DIR 环境变量给 server 读
  4. 后台线程跑 uvicorn（server.app）
  5. 主线程用 pywebview 开一个原生窗口
  6. 关窗即退出

特殊模式：crew.exe --mcp-server <kind> [args...]
  作为 MCP subprocess server 启动。用于打包版 mcp_registry 拉起内置 server。
  这条分支在 pywebview / uvicorn / 数据目录初始化之前执行——尽量轻。

pywebview 必须在主线程运行（Windows COM 要求）。
"""
from __future__ import annotations

import os
import sys
import time
import threading
import socket
from pathlib import Path


# ── MCP subprocess 模式：早于任何 GUI/网络初始化 ─────────
# 命令行：crew.exe --mcp-server <time|fetch|filesystem|shell> [args...]
if len(sys.argv) >= 3 and sys.argv[1] == "--mcp-server":
    # 打包 onedir 版：mcp_builtin_servers.py 会被 PyInstaller 收进 pyz，
    # 直接 import 即可。console=False 打包版里 sys.stdout 可能是 None，
    # 需要用 os 级 FD（打包时 console=True 才有 stdout；MCP subprocess 必须能写 stdout）。
    # 由于 crew.exe 是 console=False，我们直接绕开：MCP subprocess 需要自己的 stdout/stdin。
    # PyInstaller onedir 中，子进程会继承父进程 stdio。父进程用 subprocess 拉起时会 pipe stdout。
    # 但 console=False 的 exe 没有真正的 stdout FD —— 关键！
    # 解决：subprocess.PIPE 会为 stdout 分配匿名管道，此时 sys.stdout 可正常 write。
    # 保险起见：确保 sys.stdout / sys.stdin 二进制模式可用。
    try:
        # 让 stdout 使用 UTF-8（Windows 默认 GBK，MCP JSON 需要 UTF-8）
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # 剥离 --mcp-server 参数，让 mcp_builtin_servers.main 看到正常的 argv
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    from mcp_builtin_servers import main as _mcp_main
    _mcp_main()
    sys.exit(0)


APP_NAME = "Crew"
APP_TITLE = "Crew · the workshop"
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"


def get_data_dir() -> Path:
    """用户数据目录：%APPDATA%\\Crew\\（Windows）/ ~/.crew/（其他）+ workspace 子目录。

    workspace 名从 <root>/active_workspace 文件读取，默认 'default'。
    最终目录：<root>/workspaces/<name>/
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        root = Path(base) / APP_NAME
    else:
        root = Path.home() / f".{APP_NAME.lower()}"
    root.mkdir(parents=True, exist_ok=True)

    # 读当前 workspace 名
    ws_marker = root / "active_workspace"
    ws_name = "default"
    if ws_marker.exists():
        try:
            ws_name = ws_marker.read_text(encoding="utf-8").strip() or "default"
        except Exception:
            pass
    # 只允许字母/数字/连字符/下划线
    import re as _re
    if not _re.match(r"^[A-Za-z0-9_-]{1,32}$", ws_name):
        ws_name = "default"

    d = root / "workspaces" / ws_name
    d.mkdir(parents=True, exist_ok=True)

    # ─── 老数据迁移（v3.0 升级路径）───
    # v2.x 的 team.db / config.json / mcp_servers.json / attachments/ / dynamic_agents.json
    # 都在 root/ 根目录。首次进 workspaces/default 时把它们平移过来。
    if ws_name == "default":
        legacy_files = ["team.db", "config.json", "mcp_servers.json", "dynamic_agents.json"]
        for fn in legacy_files:
            src = root / fn
            dst = d / fn
            if src.exists() and not dst.exists():
                try:
                    import shutil as _sh
                    _sh.move(str(src), str(dst))
                except Exception:
                    pass
        legacy_att = root / "attachments"
        new_att = d / "attachments"
        if legacy_att.exists() and not new_att.exists():
            try:
                import shutil as _sh
                _sh.move(str(legacy_att), str(new_att))
            except Exception:
                pass

    # 让 server 知道 root（切换 workspace 用）
    os.environ["CREW_DATA_ROOT"] = str(root)
    os.environ["CREW_WORKSPACE"] = ws_name
    return d


# ─── 关键：console=False 下 stdout/stderr 是 None，任何 print 会崩 ───
def _redirect_std_streams(data_dir: Path) -> None:
    """把 stdout/stderr 重定向到日志文件（避免 print/logger 崩溃）"""
    log_path = data_dir / "launcher.log"
    try:
        # 打包（console=False）后 sys.stdout 可能是 None
        if sys.stdout is None or getattr(sys.stdout, "fileno", None) is None:
            f = open(log_path, "a", encoding="utf-8", buffering=1)
            sys.stdout = f
            sys.stderr = f
        else:
            # 源码模式：保留控制台，但也 tee 到日志（可选，先不 tee）
            pass
        # Windows 控制台 UTF-8（源码模式有效）
        if sys.platform == "win32" and sys.stdout is not None:
            try:
                sys.stdout.reconfigure(encoding="utf-8")
                sys.stderr.reconfigure(encoding="utf-8")
            except Exception:
                pass
    except Exception:
        # 兜底：给 sys.stdout 一个 no-op writer
        class _NullWriter:
            def write(self, *a, **kw): pass
            def flush(self, *a, **kw): pass
        if sys.stdout is None: sys.stdout = _NullWriter()
        if sys.stderr is None: sys.stderr = _NullWriter()


def _log(msg: str) -> None:
    """安全 log（不依赖 print，直接写文件；同时尝试 print）"""
    try:
        print(f"[{APP_NAME}] {msg}", flush=True)
    except Exception:
        pass


def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    """轮询端口，直到 open 或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        try:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
        finally:
            try:
                s.close()
            except Exception:
                pass
    return False


def run_uvicorn() -> None:
    """后台线程里跑 uvicorn。"""
    try:
        import uvicorn
        import server  # noqa: F401
        # 多人协作：读 config.allow_lan，true 时监听 0.0.0.0
        host = "127.0.0.1"
        try:
            data_dir = Path(os.environ.get("CREW_DATA_DIR", "."))
            cfg_path = data_dir / "config.json"
            if cfg_path.exists():
                import json as _j
                cfg = _j.loads(cfg_path.read_text(encoding="utf-8"))
                if cfg.get("allow_lan"):
                    host = "0.0.0.0"
        except Exception:
            pass
        uvicorn.run(
            "server:app",
            host=host,
            port=PORT,
            log_level="warning",
            access_log=False,
        )
    except Exception as e:
        _log(f"uvicorn 崩溃: {type(e).__name__}: {e}")
        try:
            (get_data_dir() / "uvicorn_crash.log").write_text(
                f"{type(e).__name__}: {e}\n",
                encoding="utf-8",
            )
        except Exception:
            pass


def main() -> None:
    # 1. 准备数据目录 + 重定向流
    data_dir = get_data_dir()
    _redirect_std_streams(data_dir)
    os.environ["CREW_DATA_DIR"] = str(data_dir)

    _log(f"data dir: {data_dir}")
    _log(f"starting server on {URL}")

    # 2. 起 uvicorn（守护线程）
    server_thread = threading.Thread(target=run_uvicorn, daemon=True)
    server_thread.start()

    # 3. 等 server ready
    if not wait_for_port(PORT, timeout=30):
        _log("ERROR: server 30 秒内没起来，退出")
        # 在窗口未起时尝试用 tkinter 弹错误框（避免用户看不到任何东西）
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Crew 启动失败：server 30 秒未响应。\n请查看 {data_dir}\\launcher.log",
                "Crew · 启动失败",
                0x10,  # MB_ICONERROR
            )
        except Exception:
            pass
        sys.exit(1)
    time.sleep(0.5)
    _log("server ready, opening desktop window...")

    # 4. 主线程开原生窗口
    try:
        import webview
        webview.create_window(
            title=APP_TITLE,
            url=URL,
            width=1280,
            height=820,
            min_size=(900, 600),
            resizable=True,
            confirm_close=False,
            background_color="#f6efe1",
        )
        webview.start(gui=None, debug=False)
    except Exception as e:
        _log(f"pywebview 失败: {e}，退回浏览器")
        try:
            # webbrowser.open 有时静默失败；用 cmd start 更可靠
            opened = False
            try:
                import webbrowser
                opened = webbrowser.open(URL)
            except Exception as be:
                _log(f"webbrowser.open 失败: {be}")
            if not opened:
                try:
                    import subprocess
                    subprocess.Popen(["cmd", "/c", "start", "", URL], shell=False)
                    opened = True
                    _log("用 cmd start 打开浏览器")
                except Exception as se:
                    _log(f"cmd start 也失败: {se}")
            if not opened:
                # 最后一招：os.startfile 直接开 URL
                try:
                    import os as _os
                    _os.startfile(URL)  # type: ignore
                    opened = True
                    _log("用 os.startfile 打开浏览器")
                except Exception as oe:
                    _log(f"os.startfile 也失败: {oe}")

            import ctypes
            hint = f"已用浏览器打开 {URL}" if opened else f"请手动在浏览器打开：\n{URL}"
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Crew 桌面窗口启动失败（缺 .NET Runtime）\n{hint}",
                "Crew · 窗口降级",
                0x30,  # MB_ICONWARNING
            )
            # 让用户浏览器用着，主线程 hang 住让 daemon 存活
            while True:
                time.sleep(3600)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        try:
            (get_data_dir() / "launcher_crash.log").write_text(
                f"{type(e).__name__}: {e}\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        # 打包版没控制台看不到 traceback，弹个对话框
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Crew 崩溃：{type(e).__name__}: {e}",
                "Crew · 崩溃",
                0x10,
            )
        except Exception:
            pass
        raise

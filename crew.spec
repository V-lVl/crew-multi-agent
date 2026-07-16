# -*- mode: python ; coding: utf-8 -*-
"""Crew PyInstaller spec —— 打包 launcher.py 成 crew.exe

用法：
  pyinstaller crew.spec --noconfirm

产物：
  dist/Crew/         onedir 模式，含 crew.exe 和一堆 dll/pyd
  dist/Crew/crew.exe 双击启动
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

# static/ 目录一起打进去，运行时通过 sys._MEIPASS 访问
datas = [
    ("static", "static"),  # (源, 目标) 目标是相对 _MEIPASS
    ("marketplace", "marketplace"),  # 兜底模板
    # pydantic_core 的 native .pyd（PyInstaller 有时漏收）
    ("D:/Program Files/Python313/Lib/site-packages/pydantic_core/_pydantic_core.cp313-win_amd64.pyd",
     "pydantic_core"),
]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "websockets.legacy",
    "websockets.legacy.server",
    # 项目内部模块（保险，防 PyInstaller 静态分析漏抓）
    "agents_cli",
    "pricing",
    "attachments",
    "providers",
    "supervisor",
    "server",
    "mcp_client",
    "mcp_http_client",
    "mcp_registry",
    "mcp_builtin_servers",
    "mcp_memory_server",
    "redact",
    # pywebview 桌面版
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr_loader",
    "clr_loader.wrappers",
    "clr_loader.util",
    "clr_loader.util.find",
    "clr_loader.util.runtime_spec",
    "pythonnet",
]

# pywebview + pythonnet：把库整个 collect（含 .NET dll、winforms 资源）
from PyInstaller.utils.hooks import collect_all as _collect_all
for _mod in ("webview", "clr_loader", "pythonnet"):
    _d, _b, _h = _collect_all(_mod)
    datas += _d
    binaries += _b
    hiddenimports += _h

# pydantic v2 的 C 扩展必须显式收集（否则 ModuleNotFoundError: pydantic_core._pydantic_core）
for pkg in ("pydantic", "pydantic_core", "cffi", "pythonnet", "clr_loader"):
    _d, _b, _h = collect_all(pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="crew",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 桌面版：不弹 CMD 黑窗口，只显示 pywebview 原生窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="static/crew.ico",  # 桌面/任务栏图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Crew",
)

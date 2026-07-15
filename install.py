"""团队作战室 · 安装向导

一次跑通：
  1. 检查 Python 3.10+（当前 python 就能跑就 OK）
  2. 装依赖：hermes-agent, fastapi, uvicorn, httpx, python-multipart
  3. 建 .env（如果没有）
  4. 注册 Windows 登录时自启（可选）
  5. 打印下一步（打开浏览器）

用法：
  双击 install.bat  或
  python install.py         # 交互式
  python install.py --yes   # 全自动，全部选 yes
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import shutil
from pathlib import Path

HERE = Path(__file__).parent.resolve()
IS_WIN = os.name == "nt"
ARGS_YES = "--yes" in sys.argv or "-y" in sys.argv or "--autostart-only" in sys.argv

REQUIRED_PKGS = [
    "hermes-agent",
    "fastapi>=0.100",
    "uvicorn[standard]>=0.23",
    "httpx>=0.24",
    "python-multipart",
    "websockets",
]

BANNER = r"""
  ╔══════════════════════════════════════════╗
  ║   🎩 团队作战室 · 安装向导                ║
  ║   TeamRoom · Setup                       ║
  ╚══════════════════════════════════════════╝
"""


def _c(s: str, code: int = 33) -> str:
    """Colorize terminal text; fall back to plain if pipe."""
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def ok(msg: str) -> None:
    print(f"  {_c('✓', 32)} {msg}")


def warn(msg: str) -> None:
    print(f"  {_c('!', 33)} {msg}")


def bad(msg: str) -> None:
    print(f"  {_c('✗', 31)} {msg}")


def step(msg: str) -> None:
    print(f"\n{_c('▸', 36)} {msg}")


def ask(prompt: str, default: str = "y") -> bool:
    if ARGS_YES:
        return True
    r = input(f"    {prompt} [{default}]: ").strip().lower()
    return (r or default) in ("y", "yes")


def check_python() -> bool:
    step("Python 版本")
    v = sys.version_info
    if v < (3, 10):
        bad(f"Python 版本太低：{v.major}.{v.minor}，需要 3.10 及以上")
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro}")
    return True


def check_pip() -> bool:
    step("pip")
    try:
        subprocess.check_output(
            [sys.executable, "-m", "pip", "--version"],
            stderr=subprocess.STDOUT,
        )
        ok("pip 可用")
        return True
    except Exception as e:
        bad(f"pip 不可用：{e}")
        return False


def install_deps() -> bool:
    step("安装依赖")
    print(f"    将安装：{', '.join(REQUIRED_PKGS)}")
    if not ask("继续？", "y"):
        warn("跳过依赖安装")
        return False
    args = [sys.executable, "-m", "pip", "install", "--upgrade", *REQUIRED_PKGS]
    print(f"    → {' '.join(args)}")
    r = subprocess.call(args)
    if r == 0:
        ok("依赖安装完成")
        return True
    bad(f"pip 返回 {r}")
    return False


def check_hermes() -> tuple[bool, str | None]:
    step("Hermes CLI")
    # 先看 PATH
    exe = shutil.which("hermes")
    if exe:
        ok(f"找到 hermes：{exe}")
        return True, exe
    # venv 里
    if IS_WIN:
        cand = Path(sys.executable).parent / "hermes.exe"
    else:
        cand = Path(sys.executable).parent / "hermes"
    if cand.exists():
        ok(f"找到 hermes：{cand}")
        return True, str(cand)
    bad("hermes 命令找不到")
    warn("依赖安装后应该自动到位；如果这一步失败，手动 `pip install hermes-agent`")
    return False, None


def setup_env_file() -> bool:
    step("配置 API Key")
    env_path = HERE / ".env"
    cfg_path = HERE / "config.json"
    existing_key = None

    # 已有 .env
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ARK_API_KEY="):
                existing_key = line.split("=", 1)[1].strip()
                break

    # 已有 config.json 里的 key
    if not existing_key and cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            existing_key = cfg.get("ark_api_key")
        except Exception:
            pass

    if existing_key and existing_key.startswith("ark-"):
        ok(f"已有 ARK_API_KEY（{existing_key[:8]}…）")
        return True

    print("    需要一个 ARK_API_KEY（火山方舟 · Volcengine ARK · 用于团队讨论 LLM）")
    print("    获取地址：https://console.volcengine.com/ark")
    if ARGS_YES:
        warn("--yes 模式跳过 key 输入；稍后到 http://127.0.0.1:8765/ 首次向导填")
        return True
    k = input("    粘贴 ARK_API_KEY（ark- 开头，可跳过）: ").strip()
    if not k:
        warn("跳过；稍后到 http://127.0.0.1:8765/ 首次向导填")
        return True
    env_path.write_text(f"ARK_API_KEY={k}\n", encoding="utf-8")
    ok(f".env 写好（{k[:8]}…）")
    return True


def setup_autostart() -> bool:
    if not IS_WIN:
        step("开机自启")
        warn("非 Windows，跳过（macOS/Linux 请手动配置）")
        return True
    step("登录自启动")
    if not ask("现在注册？", "y"):
        warn("跳过；随时可运行 `install.py --autostart-only` 补上")
        return True
    vbs = HERE / "start_hidden.vbs"
    if not vbs.exists():
        bad(f"找不到 {vbs}")
        return False

    # 优先：用户级"启动"文件夹（不需要管理员权限，最兼容）
    startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
    if startup_dir.exists():
        shortcut_target = startup_dir / "TeamWarRoom.vbs"
        try:
            # 直接放一个转发 vbs（保持原 start_hidden.vbs 位置不变，用它作为主入口）
            forwarder = (
                f'Set WshShell = CreateObject("WScript.Shell")\r\n'
                f'WshShell.Run """" & "{vbs}" & """", 0, False\r\n'
            )
            shortcut_target.write_text(forwarder, encoding="utf-8")
            ok(f"已放入登录启动文件夹：{shortcut_target.name}")
            print(f"    位置：{startup_dir}")
            return True
        except Exception as e:
            warn(f"写启动文件夹失败：{e}")

    # 兜底：schtasks（若有权限）
    warn("尝试 schtasks 注册计划任务…")
    task_name = "TeamWarRoom"
    tr = f'wscript.exe "{vbs}"'
    r = subprocess.call([
        "schtasks.exe", "/Create",
        "/TN", task_name,
        "/TR", tr,
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/F",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r == 0:
        ok(f"计划任务 {task_name} 注册成功")
        return True
    bad(f"注册失败，schtasks 返回 {r}")
    warn("跳过；仍可通过双击 打开作战室.bat 手动启动")
    return False


def print_next_steps() -> None:
    step("完成 · 下一步")
    print(f"""
    启动：   双击 {_c('打开作战室.bat', 33)}       （或运行 python server.py）
    地址：   {_c('http://127.0.0.1:8765/', 36)}
    停止：   双击 {_c('停止作战室.bat', 33)}
    卸载：   删除本目录 + `schtasks /Delete /TN TeamWarRoom /F`

    如果这是第一次运行，浏览器打开后会弹出"环境检查"向导；
    没填 ARK_API_KEY 也能进去，向导里可以再填。
""")


def main() -> int:
    print(BANNER)
    print(f"    安装目录：{HERE}")
    print(f"    Python:  {sys.executable}")
    if ARGS_YES:
        print(f"    模式：   全自动（--yes）")

    if not check_python():
        return 1
    if not check_pip():
        return 1

    # 只补装 autostart
    if "--autostart-only" in sys.argv:
        setup_autostart()
        return 0

    if not install_deps():
        return 1
    ok_hermes, _ = check_hermes()
    if not ok_hermes:
        warn("hermes 未装好，'找老张 · 执行' 功能会不可用；团队讨论仍然能用")

    setup_env_file()
    setup_autostart()
    print_next_steps()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n  中断。")
        sys.exit(130)

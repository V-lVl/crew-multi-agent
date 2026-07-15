@echo off
REM ─────────────────────────────────────────────────────
REM 团队作战室 — 手动启动（双击这个文件也行）
REM ─────────────────────────────────────────────────────
cd /d "%~dp0"
wscript.exe start_hidden.vbs
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/"
exit

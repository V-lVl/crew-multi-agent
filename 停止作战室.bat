@echo off
REM 停止团队作战室后台服务
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq server.py" >nul 2>&1
REM 兜底：按端口找进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do (
  taskkill /F /PID %%a >nul 2>&1
)
echo 已停止。
timeout /t 2 /nobreak >nul

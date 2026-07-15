@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 优先用 py -3；其次找 python 3
where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 install.py %*
    goto :done
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python install.py %*
    goto :done
)

echo.
echo   [!] 没找到 Python 3。请先从 https://www.python.org/downloads/ 下载安装
echo       （安装时勾选 "Add Python to PATH"）
echo.
pause
exit /b 1

:done
echo.
pause

@echo off
rem Daniya one-click launcher
rem Uses the bundled .venv virtual environment.
chcp 65001 >nul
cd /d "%~dp0"

rem If already running, just notify and exit.
powershell -NoProfile -ExecutionPolicy Bypass -File "check_running.ps1" >nul 2>&1
if %errorlevel%==1 (
    echo Daniya is already running. No need to start again.
    ping 127.0.0.1 -n 4 >nul
    exit /b
)

rem Launch main program without a console window (reliable, non-blocking).
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0.venv\Scripts\pythonw.exe' -ArgumentList '%~dp0main.py' -WorkingDirectory '%~dp0'"
echo Daniya launched. Please look for her window.
ping 127.0.0.1 -n 4 >nul

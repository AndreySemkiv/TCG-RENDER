@echo off
setlocal
chcp 65001 >nul
title TCG RENDER - Environment setup
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv "%~dp0.venv"
) else (
    python -m venv "%~dp0.venv"
)
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
echo.
echo Environment ready. Open start.cmd to launch TCG RENDER.
pause
exit /b 0
:failed
echo.
echo Could not prepare the environment. Install Python 3.9 or newer and check your internet connection.
pause
exit /b 1

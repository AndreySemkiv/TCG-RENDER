@echo off
setlocal
chcp 65001 >nul
title TCG RENDER
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0launch.py" %*
    goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0launch.py" %*
    goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0launch.py" %*
    goto finished
)
echo Python not found. Install Python and the dependencies listed in README.md.
pause
exit /b 2
:finished
set "TCG_EXIT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %TCG_EXIT%

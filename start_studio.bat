@echo off
title Qwen-Image-2.1 AI Generation Studio
cd /d "%~dp0"

echo ======================================================================
echo   Qwen-Image-2.1 AI Generation Studio - All-in-One Launcher
echo ======================================================================
echo   [1/2] Local ComfyUI Backend (http://127.0.0.1:8188)
echo   [2/2] FastAPI Studio Web Interface (http://127.0.0.1:7860)
echo.
echo   This script starts everything automatically and opens your browser.
echo ======================================================================
echo.

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" launch_studio.py
) else (
    python launch_studio.py
)
pause

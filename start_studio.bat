@echo off
title Qwen-Image-2.1 AI Generation Studio
cd /d "%~dp0"

echo ======================================================================
echo   Qwen-Image-2.1 AI Generation Studio - Windows Desktop Application
echo ======================================================================
echo   [1/3] Local ComfyUI Backend (http://127.0.0.1:8188)
echo   [2/3] Local Dolphin-Llama3 Prompt Enhancer (http://127.0.0.1:11434)
echo   [3/3] Standalone Windows Desktop App Window (Edge WebView2)
echo.
echo   Starting desktop application window...
echo ======================================================================
echo.

if "%1"=="--browser" (
    if exist "%~dp0.venv\Scripts\python.exe" (
        "%~dp0.venv\Scripts\python.exe" launch_studio.py
    ) else (
        python launch_studio.py
    )
    exit /b 0
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" desktop_app.py %*
) else (
    python desktop_app.py %*
)
pause


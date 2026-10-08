@echo off
title Qwen-Image-2.1 AI Generation Studio
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" launch_studio.py
) else (
    python launch_studio.py
)
pause

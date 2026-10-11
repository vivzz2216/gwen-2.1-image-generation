@echo off
title Qwen-Image-2.1 ComfyUI
cd /d "%~dp0ComfyUI_windows_portable"
set "QWEN_VAE_FLAGS="
if "%QWEN_CPU_VAE%"=="1" set "QWEN_VAE_FLAGS=--cpu-vae"
python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --listen 127.0.0.1 --port 8188 --lowvram %QWEN_VAE_FLAGS% --disable-metadata --preview-method none --reserve-vram 1.0
pause

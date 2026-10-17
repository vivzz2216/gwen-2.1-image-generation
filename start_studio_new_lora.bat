@echo off
rem Launches the Studio with the f23gg "NSFW Qwen Lora" instead of the default LoRA.
rem Revert: just use start_studio.bat (default LoRA), or delete this file.
set "QWEN_LORA_NAME=NSFW Qwen Lora.safetensors"
call "%~dp0start_studio.bat" %*

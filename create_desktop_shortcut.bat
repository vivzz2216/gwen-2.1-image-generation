@echo off
title Install Qwen Image 2.1 Studio Shortcuts
cd /d "%~dp0"

echo ======================================================================
echo   Installing Qwen Image 2.1 Studio Desktop and Start Menu Shortcuts...
echo ======================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create_shortcuts.ps1"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ======================================================================
    echo   [SUCCESS] Shortcut created on your Desktop!
    echo   You can now launch the app directly from your Desktop or Start Menu.
    echo ======================================================================
) else (
    echo.
    echo   [ERROR] Could not create shortcuts automatically.
)

echo.
pause

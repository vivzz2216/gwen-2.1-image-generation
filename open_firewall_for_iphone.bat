@echo off
title Allow iPhone Access to Qwen Studio (Port 7860)
cd /d "%~dp0"

:: Check for Administrator permissions
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process cmd.exe -Verb RunAs -ArgumentList '/k \"\"%~f0\"\" :admin'"
    exit /b
)

echo ======================================================================
echo   Configuring Windows Firewall for iPhone / Remote Access
echo ======================================================================
echo.
echo Adding inbound firewall rule for TCP port 7860...
netsh advfirewall firewall add rule name="Qwen Studio (Port 7860)" dir=in action=allow protocol=TCP localport=7860
echo.
echo ======================================================================
echo   Firewall rule added successfully!
echo   Your iPhone can now connect over Wi-Fi and Tailscale.
echo ======================================================================
echo.
echo Press any key to close this window.
pause >nul

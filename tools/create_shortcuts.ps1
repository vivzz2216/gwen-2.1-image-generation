# Create Windows Desktop and Start Menu Shortcuts for Qwen Image 2.1 Studio
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

$PythonwPath = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$DesktopAppPy = Join-Path $ProjectRoot "desktop_app.py"
$IconPath = Join-Path $ProjectRoot "assets\qwen_studio.ico"

if (!(Test-Path $IconPath)) {
    $IconPath = Join-Path $ProjectRoot "assets\app_icon.ico"
}

if (Test-Path $PythonwPath) {
    $TargetExe = $PythonwPath
    $TargetArgs = "`"$DesktopAppPy`""
} elseif (Test-Path $ExePath) {
    $TargetExe = $ExePath
    $TargetArgs = ""
} else {
    Write-Error "Target executable not found: $ExePath"
    exit 1
}

$WshShell = New-Object -ComObject WScript.Shell

# 1. Collect all desktop paths (standard Desktop, OneDrive Desktop, etc.)
$DesktopCandidates = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path $env:USERPROFILE "Desktop"),
    (Join-Path $env:USERPROFILE "OneDrive\Desktop")
) | Select-Object -Unique | Where-Object { Test-Path $_ }

foreach ($Desk in $DesktopCandidates) {
    $ShortcutPath = Join-Path $Desk "Qwen Image 2.1 Studio.lnk"
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force -ErrorAction SilentlyContinue
    }
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetExe
    $Shortcut.Arguments = $TargetArgs
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.IconLocation = "$IconPath, 0"
    $Shortcut.Description = "Qwen Image 2.1 AI Generation Studio"
    $Shortcut.Save()
    Write-Host "Created Desktop Shortcut: $ShortcutPath" -ForegroundColor Green
}

# 2. Start Menu Shortcut
$StartMenuDir = [Environment]::GetFolderPath("Programs")
$AppMenuDir = Join-Path $StartMenuDir "Qwen Image Studio"
if (!(Test-Path $AppMenuDir)) {
    New-Item -ItemType Directory -Path $AppMenuDir -Force | Out-Null
}
$StartShortcut = Join-Path $AppMenuDir "Qwen Image 2.1 Studio.lnk"
if (Test-Path $StartShortcut) {
    Remove-Item $StartShortcut -Force -ErrorAction SilentlyContinue
}
$MenuShortcut = $WshShell.CreateShortcut($StartShortcut)
$MenuShortcut.TargetPath = $TargetExe
$MenuShortcut.Arguments = $TargetArgs
$MenuShortcut.WorkingDirectory = $ProjectRoot
$MenuShortcut.IconLocation = "$IconPath, 0"
$MenuShortcut.Description = "Qwen Image 2.1 AI Generation Studio"
$MenuShortcut.Save()
Write-Host "Created Start Menu Shortcut: $StartShortcut" -ForegroundColor Green

# 3. Notify Windows Shell to refresh desktop and icon cache immediately
try {
    Add-Type -TypeDefinition @"
    using System;
    using System.Runtime.InteropServices;
    public class ShellNotifier {
        [DllImport("shell32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        public static extern void SHChangeNotify(int wEventId, int uFlags, IntPtr dwItem1, IntPtr dwItem2);
    }
"@
    # SHCNE_ASSOCCHANGED = 0x08000000, SHCNF_FLUSH = 0x1000
    [ShellNotifier]::SHChangeNotify(0x08000000, 0x1000, [IntPtr]::Zero, [IntPtr]::Zero)
    Write-Host "Windows Shell notified of icon update." -ForegroundColor Cyan
} catch {
    # Non-critical if ShellNotifier type was already added
}

Write-Host "`nSuccessfully updated Qwen Image 2.1 Studio shortcuts with official Qwen logo!" -ForegroundColor Green

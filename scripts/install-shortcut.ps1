param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot ".."),
    [string]$DesktopPath = [Environment]::GetFolderPath("Desktop")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$Launcher = Join-Path $ProjectRoot "scripts\start-dashboard.vbs"
$WScript = Join-Path $env:WINDIR "System32\wscript.exe"
$Icon = Join-Path $ProjectRoot "assets\icons\autody.ico"
$ShortcutPath = Join-Path $DesktopPath "AutoDy Management.lnk"

if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "AutoDy launcher not found: $Launcher"
}
if (-not (Test-Path -LiteralPath $WScript -PathType Leaf)) {
    throw "Windows Script Host not found: $WScript"
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $WScript
$Shortcut.Arguments = ('"{0}"' -f $Launcher)
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "AutoDy Management"

if (Test-Path -LiteralPath $Icon -PathType Leaf) {
    $Shortcut.IconLocation = ('{0},0' -f $Icon)
}

$Shortcut.Save()

if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
    throw "Shortcut creation failed: $ShortcutPath"
}

Write-Output $ShortcutPath

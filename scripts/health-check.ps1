param(
    [string]$ProgramRoot,
    [string]$DataRoot
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$Root = if ($ProgramRoot) { [IO.Path]::GetFullPath($ProgramRoot) } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$DataRoot = if ($DataRoot) { [IO.Path]::GetFullPath($DataRoot) } elseif ($env:AUTODY_HOME) { [IO.Path]::GetFullPath($env:AUTODY_HOME) } else { $Root }
$env:AUTODY_HOME = $DataRoot
$env:AUTODY_PROGRAM_ROOT = $Root
$BrowserRoot = if (Test-Path -LiteralPath (Join-Path $Root "runtime\ms-playwright")) { Join-Path $Root "runtime\ms-playwright" } else { Join-Path $DataRoot "data\ms-playwright" }
$env:AUTODY_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
$Python = if (Test-Path -LiteralPath (Join-Path $Root "runtime\python\python.exe")) { Join-Path $Root "runtime\python\python.exe" } else { Join-Path $Root ".venv\Scripts\python.exe" }
$Config = Join-Path $DataRoot "config.yaml"
$LogDir = Join-Path $DataRoot "data\logs"
$Log = Join-Path $LogDir "scheduler.log"
$NotificationDir = Join-Path $DataRoot "data\notifications"
$Alert = Join-Path $NotificationDir "need-attention.txt"
$NotificationsEnabled = -not ((Get-Content -Raw $Config -ErrorAction SilentlyContinue) -match '(?m)^completion_notifications_enabled:\s*false\s*$')

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $NotificationDir | Out-Null

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 开始登录健康检查" |
    Add-Content -Encoding UTF8 $Log
$stdout = Join-Path $env:TEMP "autody-health-stdout-$PID.log"
$stderr = Join-Path $env:TEMP "autody-health-stderr-$PID.log"
$process = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-m", "autody.cli", "health-check", "--config", "`"$Config`"") `
    -WorkingDirectory $DataRoot `
    -Wait `
    -PassThru `
    -NoNewWindow `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr
foreach ($path in @($stdout, $stderr)) {
    if (Test-Path $path) {
        Get-Content -Raw -Encoding UTF8 $path | Add-Content -Encoding UTF8 $Log
        Remove-Item -LiteralPath $path -Force
    }
}
$exitCode = $process.ExitCode

if ($exitCode -ne 0) {
    $message = @"
AutoDy 登录或聊天页面检查失败。
时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

请打开桌面的 AutoDy 管理台，在“需要处理”中完成扫码或修复。
详细日志：$Log
"@
    $message | Set-Content -Encoding UTF8 $Alert
    if ($NotificationsEnabled) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $message,
            "AutoDy 需要重新登录",
            "OK",
            "Warning"
        ) | Out-Null
    }
} elseif (Test-Path $Alert) {
    Remove-Item -LiteralPath $Alert -Force
}

exit $exitCode

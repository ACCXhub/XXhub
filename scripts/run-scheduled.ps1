$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:AUTODY_HOME = $Root
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root "data\ms-playwright"
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Config = Join-Path $Root "config.yaml"
$LogDir = Join-Path $Root "data\logs"
$Log = Join-Path $LogDir "scheduler.log"
$NotificationDir = Join-Path $Root "data\notifications"
$Alert = Join-Path $NotificationDir "need-attention.txt"
$NotificationsEnabled = -not ((Get-Content -Raw $Config -ErrorAction SilentlyContinue) -match '(?m)^completion_notifications_enabled:\s*false\s*$')
$RetryPendingExitCode = 10
$RetryDelaysSeconds = @(120, 300, 600)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $NotificationDir | Out-Null
$started = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$started] 开始每日发送任务" | Add-Content -Encoding UTF8 $Log
function Invoke-AutoDyRun([string]$Source) {
    $stdout = Join-Path $env:TEMP "autody-run-stdout-$PID-$Source.log"
    $stderr = Join-Path $env:TEMP "autody-run-stderr-$PID-$Source.log"
    $process = Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "autody.cli", "run", "--config", "`"$Config`"", "--source", $Source) `
        -WorkingDirectory $Root `
        -Wait `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr
    $output = ""
    foreach ($path in @($stdout, $stderr)) {
        if (Test-Path $path) {
            $text = Get-Content -Raw -Encoding UTF8 $path
            $text | Add-Content -Encoding UTF8 $Log
            $output += $text
            Remove-Item -LiteralPath $path -Force
        }
    }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $output }
}

$run = Invoke-AutoDyRun "scheduled"
$exitCode = $run.ExitCode
$combinedOutput = $run.Output
foreach ($delay in $RetryDelaysSeconds) {
    if ($exitCode -ne $RetryPendingExitCode) { break }
    Start-Sleep -Seconds $delay
    $run = Invoke-AutoDyRun "retry"
    $exitCode = $run.ExitCode
    $combinedOutput += $run.Output
}

if ($exitCode -ne 0 -and $combinedOutput -match "AUTODY_FINAL_NOTIFICATION=1") {
    $message = @"
AutoDy 每日发送任务失败。
时间：$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
退出码：$exitCode

请查看：
$Log

如果提示登录失效，请打开桌面的 AutoDy 管理台查看“需要处理”。
"@
    $message | Set-Content -Encoding UTF8 $Alert
    if ($NotificationsEnabled) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $message,
            "AutoDy 需要处理",
            "OK",
            "Warning"
        ) | Out-Null
    }
} elseif (Test-Path $Alert) {
    Remove-Item -LiteralPath $Alert -Force
}

exit $exitCode

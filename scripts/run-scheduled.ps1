param(
    [string]$ProgramRoot,
    [string]$DataRoot,
    [switch]$DevelopmentMode
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
. (Join-Path $PSScriptRoot "resolve-runtime-roots.ps1")
$RuntimeRoots = Resolve-AutoDyRuntimeRoots `
    -ProgramRoot $ProgramRoot `
    -DataRoot $DataRoot `
    -DevelopmentMode:$DevelopmentMode `
    -ScriptRoot $PSScriptRoot
$Root = $RuntimeRoots.ProgramRoot
$DataRoot = $RuntimeRoots.DataRoot
$RuntimeContext = Resolve-AutoDyLaunchContext -ProgramRoot $Root -DataRoot $DataRoot
$env:AUTODY_HOME = $DataRoot
$env:AUTODY_PROGRAM_ROOT = $Root
$BrowserRoot = $RuntimeContext.BrowserRoot
$env:AUTODY_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
$Python = $RuntimeContext.Python
$Config = Join-Path $DataRoot "config.yaml"
$LogDir = Join-Path $DataRoot "data\logs"
$Log = Join-Path $LogDir ("scheduler-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
$NotificationDir = Join-Path $DataRoot "data\notifications"
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
        -WorkingDirectory $DataRoot `
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
    if (Test-Path -LiteralPath $Alert) {
        $message = (Get-Content -Raw -Encoding UTF8 -LiteralPath $Alert).Trim()
    } else {
        $message = "AutoDy 每日发送任务未完成，请打开管理台查看中文原因和处理建议。"
        $message | Set-Content -Encoding UTF8 $Alert
    }
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

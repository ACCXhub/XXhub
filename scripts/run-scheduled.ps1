param(
    [string]$ProgramRoot,
    [string]$DataRoot,
    [switch]$DevelopmentMode
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$Registration = Get-ItemProperty -LiteralPath "HKCU:\Software\AutoDy" -ErrorAction SilentlyContinue
$RegisteredProgramRoot = if ($Registration) { [string]$Registration.InstallFolder } else { "" }
$RegisteredDataRoot = if ($Registration) { [string]$Registration.DataRoot } else { "" }
$RegisteredInstalledMode = (
    -not $DevelopmentMode -and
    -not [string]::IsNullOrWhiteSpace($RegisteredProgramRoot) -and
    -not [string]::IsNullOrWhiteSpace($RegisteredDataRoot)
)
if ($RegisteredInstalledMode -and (-not $ProgramRoot -or -not $DataRoot)) {
    throw "Registered installed AutoDy requires explicit ProgramRoot and DataRoot. Repair the scheduled task from AutoDy."
}
$Root = if ($ProgramRoot) { [IO.Path]::GetFullPath($ProgramRoot) } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$DataRoot = if ($DataRoot) { [IO.Path]::GetFullPath($DataRoot) } elseif ($env:AUTODY_HOME) { [IO.Path]::GetFullPath($env:AUTODY_HOME) } else { $Root }
if ($RegisteredInstalledMode) {
    $ExpectedProgramRoot = [IO.Path]::GetFullPath($RegisteredProgramRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $ExpectedDataRoot = [IO.Path]::GetFullPath($RegisteredDataRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (
        $Root.TrimEnd([IO.Path]::DirectorySeparatorChar) -ine $ExpectedProgramRoot -or
        $DataRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) -ine $ExpectedDataRoot
    ) {
        throw "Registered installed AutoDy runtime roots do not match. Repair the scheduled task from AutoDy."
    }
}
$env:AUTODY_HOME = $DataRoot
$env:AUTODY_PROGRAM_ROOT = $Root
$BrowserRoot = if (Test-Path -LiteralPath (Join-Path $Root "runtime\ms-playwright")) { Join-Path $Root "runtime\ms-playwright" } else { Join-Path $DataRoot "data\ms-playwright" }
$env:AUTODY_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
$Python = if (Test-Path -LiteralPath (Join-Path $Root "runtime\python\python.exe")) { Join-Path $Root "runtime\python\python.exe" } else { Join-Path $Root ".venv\Scripts\python.exe" }
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

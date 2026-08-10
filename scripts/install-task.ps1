param(
    [string]$ProgramRoot,
    [string]$DataRoot,
    [string]$TaskUserId,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyHealthCheckTime = "07:20",
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailySendTime = "07:30",
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$RecoveryDeadline = "23:59",
    [ValidateSet(0, 1)]
    [int]$WeeklyHealthCheckEnabled = 1,
    [ValidateSet('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')]
    [string]$WeeklyHealthCheckWeekday = "Sunday",
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$WeeklyHealthCheckTime = "20:00"
)

$ErrorActionPreference = "Stop"
$TaskName = "AutoDy-DailySpark"
$Root = if ($ProgramRoot) { [IO.Path]::GetFullPath($ProgramRoot) } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$DataRoot = if ($DataRoot) { [IO.Path]::GetFullPath($DataRoot) } elseif ($env:AUTODY_HOME) { [IO.Path]::GetFullPath($env:AUTODY_HOME) } else { $Root }
$Python = if (Test-Path -LiteralPath (Join-Path $Root "runtime\python\python.exe")) {
    Join-Path $Root "runtime\python\python.exe"
} else {
    Join-Path $Root ".venv\Scripts\python.exe"
}

if (-not (Test-Path $Python)) { throw "Missing $Python. Create .venv and install the project first." }
if (-not (Test-Path (Join-Path $DataRoot "config.yaml"))) { throw "Missing config.yaml. Start AutoDy once before installing scheduled tasks." }

$PowerShell = (Get-Command powershell.exe).Source
$RunScript = Join-Path $Root "scripts\run-scheduled.ps1"
$HealthScript = Join-Path $Root "scripts\health-check.ps1"
$RunSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$HealthSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$PrincipalUserId = if ($TaskUserId) {
    (New-Object System.Security.Principal.SecurityIdentifier($TaskUserId)).Value
} else {
    [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}
$Principal = New-ScheduledTaskPrincipal -UserId $PrincipalUserId -LogonType Interactive -RunLevel Limited
$TaskArguments = "-ProgramRoot `"$Root`" -DataRoot `"$DataRoot`""
$RunAction = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" $TaskArguments" -WorkingDirectory $Root
$HealthAction = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$HealthScript`" $TaskArguments" -WorkingDirectory $Root
$DailyStart = [datetime]::ParseExact($DailySendTime, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$RecoveryEnd = [datetime]::ParseExact($RecoveryDeadline, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$RecoveryDuration = $RecoveryEnd - $DailyStart
if ($RecoveryDuration.TotalMinutes -lt 0) {
    throw "RecoveryDeadline must not be earlier than DailySendTime."
}
$NextDailyStart = [datetime]::Today.Add($DailyStart.TimeOfDay)
if ($NextDailyStart -le (Get-Date)) {
    $NextDailyStart = $NextDailyStart.AddDays(1)
}
$RunTrigger = New-ScheduledTaskTrigger -Daily -At $NextDailyStart
if ($RecoveryDuration.TotalMinutes -ge 1) {
    $RecoveryInterval = New-TimeSpan -Minutes ([Math]::Min(30, [Math]::Floor($RecoveryDuration.TotalMinutes)))
    $Repetition = New-ScheduledTaskTrigger `
        -Once `
        -At $NextDailyStart `
        -RepetitionInterval $RecoveryInterval `
        -RepetitionDuration $RecoveryDuration
    $RunTrigger.Repetition = $Repetition.Repetition
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $RunAction `
    -Trigger $RunTrigger `
    -Settings $RunSettings `
    -Principal $Principal `
    -Description "Daily Douyin spark message" `
    -Force `
    -ErrorAction Stop | Out-Null

Register-ScheduledTask `
    -TaskName "AutoDy-Health-Daily" `
    -Action $HealthAction `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $DailyHealthCheckTime) `
    -Settings $HealthSettings `
    -Principal $Principal `
    -Description "Check Douyin login before daily AutoDy send" `
    -Force `
    -ErrorAction Stop | Out-Null

if ($WeeklyHealthCheckEnabled) {
    Register-ScheduledTask `
        -TaskName "AutoDy-Health-Weekly" `
        -Action $HealthAction `
        -Trigger (New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $WeeklyHealthCheckWeekday -At $WeeklyHealthCheckTime) `
        -Settings $HealthSettings `
        -Principal $Principal `
        -Description "Weekly Douyin login health reminder" `
        -Force `
        -ErrorAction Stop | Out-Null
} elseif (Get-ScheduledTask -TaskName "AutoDy-Health-Weekly" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "AutoDy-Health-Weekly" -Confirm:$false
}

$InstalledTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if (-not $InstalledTask.Settings.StartWhenAvailable) { throw "Scheduled task verification failed: StartWhenAvailable is disabled." }
if ($RecoveryDuration.TotalMinutes -ge 1 -and -not $InstalledTask.Triggers[0].Repetition.Interval) {
    throw "Scheduled task verification failed: same-day recovery repetition is disabled."
}
Write-Host "Scheduled task $TaskName installed for $DailySendTime local time."

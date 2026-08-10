import json
from pathlib import Path
import subprocess


def test_install_script_has_required_scheduler_contract():
    text = Path("scripts/install-task.ps1").read_text(encoding="utf-8-sig")
    for token in [
        "07:30",
        "StartWhenAvailable",
        "IgnoreNew",
        ".venv\\Scripts\\python.exe",
        "Register-ScheduledTask",
        "-ErrorAction Stop",
        "Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop",
    ]:
        assert token in text


def test_remove_script_uses_same_task_name():
    install = Path("scripts/install-task.ps1").read_text(encoding="utf-8-sig")
    remove = Path("scripts/remove-task.ps1").read_text(encoding="utf-8-sig")
    assert '$TaskName = "AutoDy-DailySpark"' in install
    assert '"AutoDy-DailySpark"' in remove
    assert '"AutoDy-Health-Daily"' in remove
    assert '"AutoDy-Health-Weekly"' in remove


def test_scheduler_wrappers_log_and_notify():
    run = Path("scripts/run-scheduled.ps1").read_text(encoding="utf-8-sig")
    health = Path("scripts/health-check.ps1").read_text(encoding="utf-8-sig")
    install = Path("scripts/install-task.ps1").read_text(encoding="utf-8-sig")
    for token in ["scheduler-{0}.log", "yyyy-MM-dd", "data\\notifications", "MessageBox"]:
        assert token in run
    for token in ["health-check", "data\\notifications", "MessageBox"]:
        assert token in health
    assert "Desktop" not in run
    assert "Desktop" not in health
    assert "RedirectStandardOutput" in run
    assert "RedirectStandardOutput" in health
    for token in ["AutoDy-Health-Daily", "07:20", "AutoDy-Health-Weekly", "20:00"]:
        assert token in install


def test_scheduled_send_waits_for_retry_pending_and_only_notifies_for_final_outcomes():
    run = Path("scripts/run-scheduled.ps1").read_text(encoding="utf-8-sig")

    for token in ["$RetryPendingExitCode = 10", "Start-Sleep", "AUTODY_FINAL_NOTIFICATION=1"]:
        assert token in run


def test_scheduler_wrappers_set_portable_playwright_environment():
    for path in [Path("scripts/run-scheduled.ps1"), Path("scripts/health-check.ps1")]:
        text = path.read_text(encoding="utf-8-sig")
        for token in ["AUTODY_HOME", "AUTODY_PROGRAM_ROOT", "AUTODY_BROWSERS_PATH", "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_SKIP_BROWSER_GC"]:
            assert token in text


def test_every_scheduled_task_uses_ignore_new_policy():
    text = Path("scripts/install-task.ps1").read_text(encoding="utf-8-sig")
    assert "-MultipleInstances IgnoreNew" in text
    assert text.count("-Settings $RunSettings") == 1
    assert text.count("-Settings $HealthSettings") == 2


def test_daily_send_task_repeats_under_scheduler_until_recovery_deadline():
    text = Path("scripts/install-task.ps1").read_text(encoding="utf-8-sig")

    for token in [
        '[string]$RecoveryDeadline = "23:59"',
        "$RecoveryDuration = $RecoveryEnd - $DailyStart",
        "-RepetitionInterval $RecoveryInterval",
        "-RepetitionDuration $RecoveryDuration",
        "$RunTrigger.Repetition = $Repetition.Repetition",
        "same-day recovery repetition is disabled",
    ]:
        assert token in text
    assert "[string]$TaskUserId" in text
    assert "New-ScheduledTaskPrincipal -UserId $PrincipalUserId" in text
    assert "-RunLevel Limited" in text
    assert "WindowsIdentity]::GetCurrent().Name" not in text


def test_scheduler_repair_defines_the_daily_send_start_in_the_future(
    tmp_path: Path,
):
    program_root = tmp_path / "program"
    data_root = tmp_path / "data-root"
    python = program_root / "runtime" / "python" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    data_root.mkdir()
    (data_root / "config.yaml").write_text("targets: []\n", encoding="utf-8")
    script = Path("scripts/install-task.ps1").resolve()
    command = rf'''
$global:Registered = @{{}}
function New-ScheduledTaskSettingsSet {{
    param([switch]$StartWhenAvailable, $MultipleInstances, $ExecutionTimeLimit)
    [pscustomobject]@{{ StartWhenAvailable = [bool]$StartWhenAvailable }}
}}
function New-ScheduledTaskPrincipal {{
    param($UserId, $LogonType, $RunLevel)
    [pscustomobject]@{{ UserId = $UserId }}
}}
function New-ScheduledTaskAction {{
    param($Execute, $Argument, $WorkingDirectory)
    [pscustomobject]@{{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }}
}}
function New-ScheduledTaskTrigger {{
    param(
        [switch]$Daily, [switch]$Once, [switch]$Weekly,
        [datetime]$At, [timespan]$RepetitionInterval,
        [timespan]$RepetitionDuration, $WeeksInterval, $DaysOfWeek
    )
    $repetition = if ($Once) {{
        [pscustomobject]@{{ Interval = $RepetitionInterval; Duration = $RepetitionDuration }}
    }} else {{
        [pscustomobject]@{{ Interval = $null; Duration = $null }}
    }}
    [pscustomobject]@{{ StartBoundary = $At; Repetition = $repetition }}
}}
function Register-ScheduledTask {{
    param($TaskName, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
    $global:Registered[$TaskName] = [pscustomobject]@{{
        Triggers = @($Trigger); Settings = $Settings
    }}
}}
function Get-ScheduledTask {{
    param($TaskName)
    if ($global:Registered.ContainsKey($TaskName)) {{ $global:Registered[$TaskName] }}
}}
& '{script}' `
    -ProgramRoot '{program_root}' `
    -DataRoot '{data_root}' `
    -DailyHealthCheckTime '00:01' `
    -DailySendTime '00:01' `
    -RecoveryDeadline '23:59' `
    -WeeklyHealthCheckEnabled 0
$trigger = $global:Registered['AutoDy-DailySpark'].Triggers[0]
$start = [datetime]$trigger.StartBoundary
$result = [pscustomobject]@{{
    start_is_future = $start -gt (Get-Date)
    start_time = $start.ToString('HH:mm')
    hours_until_start = ($start - (Get-Date)).TotalHours
}}
Write-Output ('__RESULT__' + ($result | ConvertTo-Json -Compress))
'''

    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(
        next(
            line.removeprefix("__RESULT__")
            for line in completed.stdout.splitlines()
            if line.startswith("__RESULT__")
        )
    )
    assert payload["start_is_future"] is True
    assert payload["start_time"] == "00:01"
    assert 0 < payload["hours_until_start"] <= 24


def test_source_launchers_use_project_local_python_not_console_entrypoint():
    startup = Path("scripts/start-dashboard.ps1").read_text(encoding="utf-8-sig")
    tray = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")
    assert "autody-tray.ps1" in startup
    assert ".venv\\Scripts\\python.exe" in tray
    assert "autody.cli" in tray
    for path in [Path("scripts/run-scheduled.ps1"), Path("scripts/health-check.ps1")]:
        text = path.read_text(encoding="utf-8-sig")
        assert ".venv\\Scripts\\python.exe" in text
        assert "runtime\\python\\python.exe" in text
        assert "[string]$DataRoot" in text
        assert "autody.cli" in text
    installer = Path("scripts/install-task.ps1").read_text(encoding="utf-8-sig")
    assert ".venv\\Scripts\\python.exe" in installer
    assert "runtime\\python\\python.exe" in installer
    assert '-DataRoot `"$DataRoot`"' in installer


def test_scheduled_wrappers_fail_closed_only_for_registered_installed_mode():
    resolver = Path("scripts/resolve-runtime-roots.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "HKCU:\\Software\\AutoDy" in resolver
    assert "Registered installed AutoDy requires explicit ProgramRoot and DataRoot" in resolver
    assert "Registered installed AutoDy runtime roots do not match" in resolver
    assert "[switch]$DevelopmentMode" in resolver

    for path in [Path("scripts/run-scheduled.ps1"), Path("scripts/health-check.ps1")]:
        text = path.read_text(encoding="utf-8-sig")
        assert 'Join-Path $PSScriptRoot "resolve-runtime-roots.ps1"' in text
        assert "Resolve-AutoDyRuntimeRoots" in text
        assert "[switch]$DevelopmentMode" in text
        assert text.index("Resolve-AutoDyRuntimeRoots") < text.index("$Python =")

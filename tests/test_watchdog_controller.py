from pathlib import Path


TRAY = Path("scripts/autody-tray.ps1")


def tray_text() -> str:
    return TRAY.read_text(encoding="utf-8-sig")


def block(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end, text.index(start))]


def test_watchdog_waits_for_sustained_health_failure_and_rate_limits_recovery():
    text = tray_text()

    assert "$WatchdogHealthGraceSeconds = 20" in text
    assert "$WatchdogRecoveryWindowMinutes = 10" in text
    assert "$WatchdogRecoveryLimit = 3" in text
    watchdog = block(text, "function Invoke-WatchdogTick", "function Invoke-OptionalDashboardActivation")
    assert ".TotalSeconds -lt $WatchdogHealthGraceSeconds" in watchdog
    assert "Invoke-WatchdogRecovery" in watchdog
    assert "$script:WatchdogNeedsAttention = $true" in text


def test_watchdog_gracefully_stops_then_force_stops_only_the_verified_pid():
    text = tray_text()
    stop = block(text, "function Stop-ManagedService", "function Stop-VerifiedInstalledAutoDyServices")

    graceful = 'Invoke-RestMethod -Uri "$script:Url/api/service-shutdown" -Method Post'
    forced = "Stop-Process -Id $ManagedPid -Force"
    assert graceful in stop
    assert '"X-AutoDy-Control-Token" = $script:ServiceControlToken' in stop
    assert "$snapshot.Pid -ne $ManagedPid" in stop
    assert "Test-OwnedAutoDyIdentity" in stop
    assert "Test-ManagedProcessStillCurrent" in stop
    assert stop.index(graceful) < stop.index(forced)
    assert "Get-Process -Name python" not in text
    assert "taskkill" not in text.casefold()
    assert "Stop-Process -Name python" not in text


def test_manual_stop_is_user_registry_state_and_restart_is_not_manual_stop():
    text = tray_text()

    assert '$ManualStopRegistryPath = "HKCU:\\Software\\AutoDy"' in text
    assert "function Set-ManualStopToday" in text
    assert "function Clear-ManualStopDate" in text
    assert "function Test-ManualStopToday" in text
    assert "Clear-ManualStopDate" in text[text.index("# A normal user launch explicitly resumes AutoDy for today.") :]

    restart = block(text, "$restart.add_Click", "$repair.add_Click")
    full_exit = block(text, "$exitStop.add_Click", "$timer =")
    assert "Set-ManualStopToday" not in restart
    assert "Set-ManualStopToday" in full_exit
    assert "$script:WatchdogSuppressed = $true" in restart
    assert "$script:WatchdogSuppressed = $true" in full_exit


def test_controlled_stop_kills_tray_first_then_only_verified_autody_services():
    text = tray_text()
    controlled = block(text, "if ($StopExisting)", "if ($DefineOnly)")

    assert controlled.index("Stop-ExistingAutoDyTrayHosts") < controlled.index(
        "Stop-VerifiedInstalledAutoDyServices"
    )
    installed = block(
        text,
        "function Stop-VerifiedInstalledAutoDyServices",
        "function New-StartupWaitPage",
    )
    assert "Test-OwnedAutoDyIdentity" in installed
    assert "Stop-ManagedService" in installed


def test_watchdog_recovery_does_not_call_send_retry_friend_repair_or_data_writes():
    text = tray_text()
    watchdog = block(text, "function Invoke-WatchdogRecovery", "function Invoke-WatchdogTick")

    assert "Start-Or-ReuseService -SkipPortPersistence -Silent" in watchdog
    assert "Stop-ManagedService -Silent" in watchdog
    for forbidden in [
        "/api/repair",
        "run-target",
        "scan-friends",
        "refresh-friend",
        "retry_pending",
        "Save-ServicePort",
        "Write-TrayLog",
    ]:
        assert forbidden not in watchdog

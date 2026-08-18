import base64
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from autody.config import AppConfig, Target
from autody import scheduler
from autody.scheduler import (
    ScheduleSettings,
    SchedulerService,
    scheduler_status_rows,
    validate_schedule_settings,
)


@pytest.mark.skipif(scheduler.platform.system() != "Windows", reason="Windows token")
def test_current_process_user_sid_is_read_from_the_process_token():
    task_user_id = scheduler.current_process_user_sid()

    assert task_user_id is not None
    assert task_user_id.startswith("S-1-")


def expected_task_rows(
    program_root: Path,
    data_root: Path,
    config: AppConfig,
    task_user_id: str | None = None,
) -> list[dict]:
    launcher = program_root / "scripts" / "scheduled-task-launcher.vbs"
    health_arguments = (
        f'"{launcher}" "{program_root / "scripts" / "health-check.ps1"}" '
        f'"{program_root}" "{data_root}"'
    )
    send_arguments = (
        f'"{launcher}" "{program_root / "scripts" / "run-scheduled.ps1"}" '
        f'"{program_root}" "{data_root}"'
    )
    recovery_minutes = (
        int(config.recovery_deadline[:2]) * 60
        + int(config.recovery_deadline[3:])
        - int(config.daily_send_time[:2]) * 60
        - int(config.daily_send_time[3:])
    )
    rows = [
        {
            "name": "AutoDy-Health-Daily",
            "state": "Ready",
            "start_boundary": f"2026-08-10T{config.daily_health_check_time}:00",
            "execute": "wscript.exe",
            "arguments": health_arguments,
            "working_directory": str(program_root),
            "principal_user_id": task_user_id or "",
        },
        {
            "name": "AutoDy-DailySpark",
            "state": "Ready",
            "start_boundary": f"2026-08-10T{config.daily_send_time}:00",
            "repetition_interval": f"PT{min(30, recovery_minutes)}M" if recovery_minutes else "",
            "repetition_duration": f"PT{recovery_minutes}M" if recovery_minutes else "",
            "execute": "wscript.exe",
            "arguments": send_arguments,
            "working_directory": str(program_root),
            "principal_user_id": task_user_id or "",
        },
    ]
    if config.weekly_health_check_enabled:
        rows.append(
            {
                "name": "AutoDy-Health-Weekly",
                "state": "Ready",
                "start_boundary": f"2026-08-10T{config.weekly_health_check_time}:00",
                "execute": "wscript.exe",
                "arguments": health_arguments,
                "working_directory": str(program_root),
                "principal_user_id": task_user_id or "",
            }
        )
    return rows


def test_windows_task_rows_returns_empty_only_after_successful_snapshot(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    def successful_empty_snapshot(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")
    monkeypatch.setattr(scheduler.subprocess, "run", successful_empty_snapshot)

    assert scheduler.windows_task_rows() == []
    assert captured["kwargs"]["timeout"] >= 20
    script = captured["command"][-1]
    assert "$ErrorActionPreference='Stop'" in script
    assert "Get-ScheduledTask -ErrorAction Stop" in script
    assert "-TaskPath '\\'" in script
    assert "-ErrorAction SilentlyContinue" not in script
    assert "ConvertTo-Json -InputObject @($rows)" in script


def test_windows_task_rows_raises_explicit_error_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("powershell.exe", 20)

    monkeypatch.setattr(scheduler.subprocess, "run", timed_out)

    with pytest.raises(RuntimeError, match="定时任务快照读取超时"):
        scheduler.windows_task_rows()


def test_windows_task_rows_raises_explicit_error_on_powershell_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Task Scheduler service is unavailable",
        ),
    )

    with pytest.raises(RuntimeError, match="定时任务快照读取失败"):
        scheduler.windows_task_rows()


def test_windows_task_rows_raises_explicit_error_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="not-json",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="定时任务快照返回了无效数据"):
        scheduler.windows_task_rows()


def test_schedule_settings_validate_times_and_recovery_window():
    settings = ScheduleSettings(
        daily_health_check_time="07:20",
        daily_send_time="07:30",
        weekly_health_check_enabled=True,
        weekly_health_check_weekday="Sunday",
        weekly_health_check_time="20:00",
        recovery_deadline="23:59",
    )

    assert validate_schedule_settings(settings) == settings
    with pytest.raises(ValueError, match="不得早于"):
        validate_schedule_settings(settings.model_copy(update={"recovery_deadline": "07:00"}))


def test_scheduler_status_marks_missing_same_day_recovery_repetition_as_drift():
    config = AppConfig(targets=[Target(name="fixture")])
    rows = scheduler_status_rows(
        config,
        [
            {
                "name": "AutoDy-DailySpark",
                "state": "Ready",
                "start_boundary": "2026-08-10T07:30:00",
                "repetition_interval": "",
                "repetition_duration": "",
            }
        ],
        1,
    )

    send = next(row for row in rows if row["name"] == "AutoDy-DailySpark")
    assert send["drift"] is True
    assert send["drift_reason"] == "schedule_mismatch"


def test_scheduler_status_accepts_expected_same_day_recovery_repetition():
    config = AppConfig(targets=[Target(name="fixture")])
    rows = scheduler_status_rows(
        config,
        [
            {
                "name": "AutoDy-DailySpark",
                "state": "Ready",
                "start_boundary": "2026-08-10T07:30:00",
                "repetition_interval": "PT30M",
                "repetition_duration": "PT16H29M",
            }
        ],
        1,
    )

    send = next(row for row in rows if row["name"] == "AutoDy-DailySpark")
    assert send["drift"] is False
    assert send["drift_reason"] is None


def test_scheduler_status_accepts_no_repetition_for_zero_recovery_window():
    config = AppConfig(
        targets=[Target(name="fixture")],
        recovery_deadline="07:30",
    )
    rows = scheduler_status_rows(
        config,
        [
            {
                "name": "AutoDy-DailySpark",
                "state": "Ready",
                "start_boundary": "2026-08-10T07:30:00",
                "repetition_interval": "",
                "repetition_duration": "",
            }
        ],
        1,
    )

    send = next(row for row in rows if row["name"] == "AutoDy-DailySpark")
    assert send["drift"] is False
    assert send["drift_reason"] is None


def test_scheduler_status_accepts_account_name_equivalent_to_expected_sid(
    monkeypatch: pytest.MonkeyPatch,
):
    config = AppConfig(targets=[Target(name="fixture")])
    task_user_id = "S-1-5-21-1000"
    rows = expected_task_rows(
        Path("C:/AutoDy"), Path("C:/Users/fixture/AppData/Local/AutoDy"), config
    )
    rows[1]["principal_user_id"] = "AUTODY-TEST\\fixture"
    monkeypatch.setattr(
        scheduler,
        "_windows_user_sid",
        lambda value: task_user_id if value == "AUTODY-TEST\\fixture" else value,
        raising=False,
    )

    statuses = scheduler_status_rows(
        config,
        rows,
        1,
        task_user_id=task_user_id,
    )

    send = next(row for row in statuses if row["name"] == "AutoDy-DailySpark")
    assert send["drift_reason"] is None


def test_scheduler_apply_rolls_windows_tasks_back_when_update_fails(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("targets:\n  - name: 小明\nmessages_file: messages.txt\n", encoding="utf-8")
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    previous = AppConfig(targets=[Target(name="小明")])
    next_config = previous.model_copy(update={"daily_send_time": "08:00"})
    calls: list[str] = []

    def install(config: AppConfig):
        calls.append(config.daily_send_time)
        if config.daily_send_time == "08:00":
            raise RuntimeError("task scheduler rejected update")

    service = SchedulerService(tmp_path, install=install)
    with pytest.raises(RuntimeError, match="task scheduler"):
        service.apply(config_path, previous, next_config)

    assert calls == ["08:00", "07:30"]
    assert "07:30" in config_path.read_text(encoding="utf-8") or "daily_send_time" not in config_path.read_text(encoding="utf-8")


def test_schedule_preview_lists_affected_windows_tasks(tmp_path: Path):
    config = AppConfig(targets=[Target(name="小明")])
    preview = SchedulerService(tmp_path).preview(
        config,
        config.model_copy(update={"daily_send_time": "08:00", "weekly_health_check_enabled": False}),
    )

    assert preview["old"]["daily_send_time"] == "07:30"
    assert preview["new"]["daily_send_time"] == "08:00"
    assert {item["name"] for item in preview["affected_tasks"]} == {
        "AutoDy-Health-Daily", "AutoDy-DailySpark", "AutoDy-Health-Weekly"
    }


def test_packaged_scheduler_repair_passes_program_and_data_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    program_root = tmp_path / "program"
    data_root = tmp_path / "user-data"
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler, "_is_process_elevated", lambda: True)

    config = AppConfig(targets=[Target(name="fixture")])
    SchedulerService(
        program_root,
        data_root=data_root,
        task_rows=lambda: expected_task_rows(
            program_root.resolve(), data_root.resolve(), config
        ),
    ).repair(config)

    assert captured[captured.index("-ProgramRoot") + 1] == str(program_root.resolve())
    assert captured[captured.index("-DataRoot") + 1] == str(data_root.resolve())
    assert captured[captured.index("-RecoveryDeadline") + 1] == "23:59"


def test_scheduler_repair_builds_elevated_payload_with_original_identity_and_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "user-data").resolve()
    task_user_id = "S-1-5-21-1000"
    config = AppConfig(
        targets=[Target(name="fixture")],
        daily_health_check_time="06:20",
        daily_send_time="06:30",
        recovery_deadline="22:45",
        weekly_health_check_enabled=False,
        weekly_health_check_weekday="Tuesday",
        weekly_health_check_time="19:10",
    )
    captured: dict = {}

    monkeypatch.setattr(
        scheduler, "_is_process_elevated", lambda: False, raising=False
    )

    def fake_elevated_operation(payload: dict, operation_data_root: Path) -> None:
        captured.update(payload)
        captured["operation_data_root"] = operation_data_root

    monkeypatch.setattr(
        scheduler,
        "_run_elevated_scheduler_operation",
        fake_elevated_operation,
        raising=False,
    )

    SchedulerService(
        program_root,
        data_root=data_root,
        task_user_id=task_user_id,
        task_rows=lambda: expected_task_rows(
            program_root, data_root, config, task_user_id
        ),
    ).repair(config)

    assert captured == {
        "operation": "install",
        "program_root": str(program_root),
        "data_root": str(data_root),
        "task_user_id": task_user_id,
        "daily_health_check_time": "06:20",
        "daily_send_time": "06:30",
        "recovery_deadline": "22:45",
        "weekly_health_check_enabled": False,
        "weekly_health_check_weekday": "Tuesday",
        "weekly_health_check_time": "19:10",
        "operation_data_root": data_root,
    }


def test_scheduler_remove_builds_constrained_elevated_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "user-data").resolve()
    task_user_id = "S-1-5-21-1000"
    captured: dict = {}

    monkeypatch.setattr(
        scheduler, "_is_process_elevated", lambda: False, raising=False
    )

    def fake_elevated_operation(payload: dict, operation_data_root: Path) -> None:
        captured.update(payload)
        captured["operation_data_root"] = operation_data_root

    monkeypatch.setattr(
        scheduler,
        "_run_elevated_scheduler_operation",
        fake_elevated_operation,
        raising=False,
    )

    SchedulerService(
        program_root,
        data_root=data_root,
        task_user_id=task_user_id,
    ).remove()

    assert captured == {
        "operation": "remove",
        "program_root": str(program_root),
        "data_root": str(data_root),
        "task_user_id": task_user_id,
        "operation_data_root": data_root,
    }


def test_elevated_scheduler_command_dispatches_to_existing_install_script(
    tmp_path: Path,
):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "user-data").resolve()
    scripts = program_root / "scripts"
    scripts.mkdir(parents=True)
    data_root.mkdir()
    (data_root / "config.yaml").write_text("targets: []\n", encoding="utf-8")
    (scripts / "install-task.ps1").write_text(
        r'''param(
    [string]$ProgramRoot,
    [string]$DataRoot,
    [string]$TaskUserId,
    [string]$DailyHealthCheckTime,
    [string]$DailySendTime,
    [string]$RecoveryDeadline,
    [int]$WeeklyHealthCheckEnabled,
    [string]$WeeklyHealthCheckWeekday,
    [string]$WeeklyHealthCheckTime
)
[pscustomobject]@{
    program_root = $ProgramRoot
    data_root = $DataRoot
    task_user_id = $TaskUserId
    daily_health_check_time = $DailyHealthCheckTime
    daily_send_time = $DailySendTime
    recovery_deadline = $RecoveryDeadline
    weekly_health_check_enabled = $WeeklyHealthCheckEnabled
    weekly_health_check_weekday = $WeeklyHealthCheckWeekday
    weekly_health_check_time = $WeeklyHealthCheckTime
} | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $DataRoot 'capture.json') -Encoding UTF8
''',
        encoding="utf-8",
    )
    result_path = data_root / "result.json"
    payload = {
        "operation": "install",
        "nonce": "contract-nonce",
        "program_root": str(program_root),
        "data_root": str(data_root),
        "task_user_id": "S-1-5-21-1000",
        "daily_health_check_time": "06:20",
        "daily_send_time": "06:30",
        "recovery_deadline": "22:45",
        "weekly_health_check_enabled": False,
        "weekly_health_check_weekday": "Tuesday",
        "weekly_health_check_time": "19:10",
    }
    script = scheduler._elevated_scheduler_script(payload, result_path)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert json.loads(result_path.read_text(encoding="utf-8-sig")) == {
        "success": True,
        "operation": "install",
        "nonce": "contract-nonce",
        "message": "Windows 定时任务已更新",
        "error_code": "",
    }
    assert json.loads(
        (data_root / "capture.json").read_text(encoding="utf-8-sig")
    ) == {
        "program_root": str(program_root),
        "data_root": str(data_root),
        "task_user_id": "S-1-5-21-1000",
        "daily_health_check_time": "06:20",
        "daily_send_time": "06:30",
        "recovery_deadline": "22:45",
        "weekly_health_check_enabled": 0,
        "weekly_health_check_weekday": "Tuesday",
        "weekly_health_check_time": "19:10",
    }


def test_scheduler_apply_does_not_prompt_for_restore_after_uac_cancel(
    tmp_path: Path,
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "targets:\n  - name: fixture\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )
    (tmp_path / "messages.txt").write_text("hello\n", encoding="utf-8")
    previous = AppConfig(targets=[Target(name="fixture")])
    candidate = previous.model_copy(update={"daily_send_time": "08:00"})
    calls: list[str] = []
    cancelled_error = getattr(
        scheduler, "SchedulerElevationCancelled", RuntimeError
    )

    def install(config: AppConfig):
        calls.append(config.daily_send_time)
        raise cancelled_error("用户取消了管理员授权")

    service = SchedulerService(tmp_path, install=install)
    with pytest.raises(cancelled_error, match="取消"):
        service.apply(config_path, previous, candidate)

    assert calls == ["08:00"]


def test_cancelled_elevation_removes_the_temporary_result_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data_root = tmp_path / "user-data"
    data_root.mkdir()
    (data_root / "config.yaml").write_text("targets: []\n", encoding="utf-8")
    payload = {
        "operation": "remove",
        "program_root": str((tmp_path / "program").resolve()),
        "data_root": str(data_root.resolve()),
        "task_user_id": "S-1-5-21-1000",
    }

    def cancel(_script: str, _cwd: Path) -> int:
        raise scheduler.SchedulerElevationCancelled(
            "已取消管理员授权，定时任务未更改"
        )

    monkeypatch.setattr(scheduler, "_launch_elevated_powershell", cancel)

    with pytest.raises(scheduler.SchedulerElevationCancelled, match="取消"):
        scheduler._run_elevated_scheduler_operation(payload, data_root)

    operation_root = data_root / "data" / "scheduler-operations"
    assert operation_root.is_dir()
    assert list(operation_root.iterdir()) == []


def test_scheduler_repair_passes_explicit_task_user_sid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "user-data").resolve()
    task_user_id = "S-1-5-21-1000"
    config = AppConfig(targets=[Target(name="fixture")])
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler, "_is_process_elevated", lambda: True)
    SchedulerService(
        program_root,
        data_root=data_root,
        task_user_id=task_user_id,
        task_rows=lambda: expected_task_rows(
            program_root, data_root, config, task_user_id
        ),
    ).repair(config)

    assert captured[captured.index("-TaskUserId") + 1] == task_user_id


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("state", "Disabled", "schedule_mismatch"),
        ("execute", "cmd.exe", "runtime_root_mismatch"),
        ("arguments", '-File "wrong.ps1"', "runtime_root_mismatch"),
        ("working_directory", "C:/wrong", "runtime_root_mismatch"),
        ("principal_user_id", "S-1-5-21-9999", "principal_mismatch"),
    ],
)
def test_scheduler_repair_fails_when_registered_task_drifts(
    tmp_path: Path, field: str, value: str, reason: str
):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "data").resolve()
    task_user_id = "S-1-5-21-1000"
    config = AppConfig(targets=[Target(name="fixture")])
    rows = expected_task_rows(program_root, data_root, config, task_user_id)
    rows[1][field] = value
    statuses = scheduler_status_rows(
        config,
        rows,
        1,
        program_root=program_root,
        data_root=data_root,
        task_user_id=task_user_id,
    )
    assert next(row for row in statuses if row["name"] == "AutoDy-DailySpark")[
        "drift_reason"
    ] == reason

    service = SchedulerService(
        program_root,
        install=lambda _config: None,
        data_root=data_root,
        task_user_id=task_user_id,
        task_rows=lambda: rows,
    )
    with pytest.raises(RuntimeError, match="定时任务修复后验证失败"):
        service.repair(config)


def test_scheduler_status_detects_visible_powershell_actions_as_drift(tmp_path: Path):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "data").resolve()
    config = AppConfig(targets=[Target(name="fixture")])
    rows = expected_task_rows(program_root, data_root, config)
    for row in rows:
        script_name = (
            "run-scheduled.ps1"
            if row["name"] == "AutoDy-DailySpark"
            else "health-check.ps1"
        )
        row["execute"] = "powershell.exe"
        row["arguments"] = (
            '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
            f'-File "{program_root / "scripts" / script_name}" '
            f'-ProgramRoot "{program_root}" -DataRoot "{data_root}"'
        )

    statuses = scheduler_status_rows(
        config,
        rows,
        1,
        program_root=program_root,
        data_root=data_root,
        require_runtime_metadata=True,
    )

    assert all(row["drift_reason"] == "runtime_root_mismatch" for row in statuses)


def test_scheduler_status_accepts_windowless_wscript_actions(tmp_path: Path):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "data").resolve()
    config = AppConfig(targets=[Target(name="fixture")])
    rows = expected_task_rows(program_root, data_root, config)
    launcher = program_root / "scripts" / "scheduled-task-launcher.vbs"
    for row in rows:
        script_name = (
            "run-scheduled.ps1"
            if row["name"] == "AutoDy-DailySpark"
            else "health-check.ps1"
        )
        row["execute"] = r"C:\Windows\System32\wscript.exe"
        row["arguments"] = (
            f'"{launcher}" "{program_root / "scripts" / script_name}" '
            f'"{program_root}" "{data_root}"'
        )

    statuses = scheduler_status_rows(
        config,
        rows,
        1,
        program_root=program_root,
        data_root=data_root,
        require_runtime_metadata=True,
    )

    assert all(row["drift_reason"] is None for row in statuses)


def test_scheduler_repair_fails_when_required_task_is_missing(tmp_path: Path):
    config = AppConfig(targets=[Target(name="fixture")])
    service = SchedulerService(
        tmp_path / "program",
        install=lambda _config: None,
        data_root=tmp_path / "data",
        task_rows=lambda: [],
    )

    with pytest.raises(RuntimeError, match="定时任务修复后验证失败"):
        service.repair(config)


def test_scheduler_repair_retries_transient_snapshot_failure_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "data").resolve()
    config = AppConfig(targets=[Target(name="fixture")])
    rows = expected_task_rows(program_root, data_root, config)
    attempts = 0

    def transient_rows():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise scheduler.SchedulerSnapshotError("Task Scheduler is warming up")
        return rows

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    SchedulerService(
        program_root,
        install=lambda _config: None,
        data_root=data_root,
        task_rows=transient_rows,
    ).repair(config)

    assert attempts == 3


def test_scheduler_repair_reports_persistent_snapshot_failure_as_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    attempts = 0

    def unavailable_rows():
        nonlocal attempts
        attempts += 1
        raise scheduler.SchedulerSnapshotError("Task Scheduler is unavailable")

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    service = SchedulerService(
        tmp_path / "program",
        install=lambda _config: None,
        data_root=tmp_path / "data",
        task_rows=unavailable_rows,
    )

    with pytest.raises(RuntimeError) as exc_info:
        service.repair(AppConfig(targets=[Target(name="fixture")]))

    error = str(exc_info.value)
    assert "无法读取定时任务修复后的验证快照" in error
    assert "定时任务修复后验证失败" not in error
    assert "AutoDy-Health-Daily" not in error
    assert attempts == 3


def test_scheduler_repair_requires_complete_runtime_metadata(tmp_path: Path):
    program_root = (tmp_path / "program").resolve()
    data_root = (tmp_path / "data").resolve()
    config = AppConfig(targets=[Target(name="fixture")])
    rows = expected_task_rows(program_root, data_root, config)
    rows[0].pop("arguments")
    service = SchedulerService(
        program_root,
        install=lambda _config: None,
        data_root=data_root,
        task_rows=lambda: rows,
    )

    with pytest.raises(RuntimeError, match="定时任务修复后验证失败"):
        service.repair(config)


@pytest.mark.parametrize("enabled", [True, False])
def test_packaged_scheduler_repair_crosses_powershell_native_boolean_boundary(
    tmp_path: Path, enabled: bool, monkeypatch: pytest.MonkeyPatch
):
    program_root = tmp_path / "program"
    data_root = tmp_path / "user-data"
    scripts = program_root / "scripts"
    scripts.mkdir(parents=True)
    data_root.mkdir()
    (scripts / "install-task.ps1").write_text(
        Path("scripts/install-task.ps1").read_text(encoding="utf-8-sig"),
        encoding="utf-8",
    )
    (scripts / "resolve-runtime-roots.ps1").write_text(
        Path("scripts/resolve-runtime-roots.ps1").read_text(encoding="utf-8-sig"),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler, "_is_process_elevated", lambda: True)

    with pytest.raises(RuntimeError) as exc_info:
        SchedulerService(program_root, data_root=data_root).repair(
            AppConfig(
                targets=[Target(name="fixture")],
                weekly_health_check_enabled=enabled,
            )
        )
    error = str(exc_info.value)
    assert "Missing " in error
    assert "python.exe" in error
    assert "argument transformation" not in error

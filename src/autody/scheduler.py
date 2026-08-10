"""Small Windows Task Scheduler adapter used by the dashboard.

The scheduler remains implemented by the portable PowerShell scripts.  This
module only validates dashboard values, previews their impact, and keeps the
YAML file and Windows tasks in sync with a compensating restore on failure.
"""

from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import platform
import re
import subprocess
from typing import Callable

from pydantic import BaseModel, Field, model_validator

from autody.config import AppConfig, enabled_execution_targets, save_config


TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
WEEKDAYS = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
TASK_NAMES = ("AutoDy-Health-Daily", "AutoDy-DailySpark", "AutoDy-Health-Weekly")


class ScheduleSettings(BaseModel):
    daily_health_check_time: str = Field(pattern=TIME_PATTERN)
    daily_send_time: str = Field(pattern=TIME_PATTERN)
    weekly_health_check_enabled: bool = True
    weekly_health_check_weekday: str = Field(pattern=rf"^({WEEKDAYS})$")
    weekly_health_check_time: str = Field(pattern=TIME_PATTERN)
    recovery_deadline: str = Field(pattern=TIME_PATTERN)

    @model_validator(mode="after")
    def valid_window(self):
        if self.recovery_deadline < self.daily_send_time:
            raise ValueError("恢复截止时间不得早于每日发送时间")
        return self

    @classmethod
    def from_config(cls, config: AppConfig) -> "ScheduleSettings":
        return cls.model_validate(config.model_dump())


def validate_schedule_settings(settings: ScheduleSettings) -> ScheduleSettings:
    return ScheduleSettings.model_validate(settings)


def windows_task_rows() -> list[dict]:
    """Read the small AutoDy Task Scheduler snapshot used for health checks."""
    if platform.system() != "Windows":
        return []
    script = """
$rows=@()
foreach($name in @('AutoDy-Health-Daily','AutoDy-DailySpark','AutoDy-Health-Weekly')){
  $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if($task){
    $info=Get-ScheduledTaskInfo -TaskName $name
    $trigger=$task.Triggers | Select-Object -First 1
    $action=$task.Actions | Select-Object -First 1
    $startBoundary=''
    $repetitionInterval=''
    $repetitionDuration=''
    if($trigger){$startBoundary=[string]$trigger.StartBoundary}
    if($trigger -and $trigger.Repetition){
      $repetitionInterval=[string]$trigger.Repetition.Interval
      $repetitionDuration=[string]$trigger.Repetition.Duration
    }
    $rows += [pscustomobject]@{
      name=$name; state=[string]$task.State; next_run=$info.NextRunTime.ToString('s');
      last_run=$info.LastRunTime.ToString('s'); last_result=$info.LastTaskResult;
      start_boundary=$startBoundary;
      repetition_interval=$repetitionInterval;
      repetition_duration=$repetitionDuration;
      execute=if($action){[string]$action.Execute}else{''};
      arguments=if($action){[string]$action.Arguments}else{''};
      working_directory=if($action){[string]$action.WorkingDirectory}else{''}
      principal_user_id=[string]$task.Principal.UserId
    }
  }
}
$rows | ConvertTo-Json -Compress
"""
    try:
        output = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=8,
            check=False,
        ).stdout.strip()
        if not output:
            return []
        data = json.loads(output)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def registered_install_roots() -> tuple[Path, Path] | None:
    """Return the per-user registered roots without exposing them to APIs."""
    if platform.system() != "Windows":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\AutoDy") as key:
            program_root = Path(winreg.QueryValueEx(key, "InstallFolder")[0])
            data_root = Path(winreg.QueryValueEx(key, "DataRoot")[0])
    except (ImportError, OSError, TypeError, ValueError):
        return None
    return program_root.resolve(), data_root.resolve()


def _normalized_path(path: str | Path | None) -> str:
    if path is None or not str(path).strip():
        return ""
    return os.path.normcase(os.path.normpath(str(path))).rstrip("\\/")


def _argument_path(arguments: str, name: str) -> str | None:
    match = re.search(
        rf'(?:^|\s)-{re.escape(name)}\s+(?:"([^"]+)"|(\S+))',
        arguments,
        flags=re.IGNORECASE,
    )
    return (match.group(1) or match.group(2)) if match else None


def _clock_minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _iso_duration_minutes(value: object) -> float | None:
    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    parts = {
        name: float(match.group(name) or 0)
        for name in ("days", "hours", "minutes", "seconds")
    }
    return (
        parts["days"] * 24 * 60
        + parts["hours"] * 60
        + parts["minutes"]
        + parts["seconds"] / 60
    )


def scheduler_status_rows(
    config: AppConfig,
    rows: list[dict],
    executable_target_count: int,
    *,
    program_root: Path | None = None,
    data_root: Path | None = None,
    task_user_id: str | None = None,
    require_runtime_metadata: bool = False,
) -> list[dict]:
    """Combine configured intent with the live Windows task snapshot."""
    expected = {
        "AutoDy-Health-Daily": (True, config.daily_health_check_time, None),
        "AutoDy-DailySpark": (True, config.daily_send_time, executable_target_count),
        "AutoDy-Health-Weekly": (
            config.weekly_health_check_enabled,
            config.weekly_health_check_time,
            None,
        ),
    }
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        name = str(row.get("name", ""))
        if name in expected:
            by_name[name].append(row)

    result = []
    for name, (configured_enabled, configured_time, target_count) in expected.items():
        matches = by_name[name]
        live = matches[0] if matches else {}
        installed = bool(matches)
        state = str(live.get("state") or "Missing")
        windows_enabled = installed and state.casefold() != "disabled"
        start_boundary = str(live.get("start_boundary") or "")
        time_match = re.search(r"T(\d{2}:\d{2})", start_boundary)
        windows_time = time_match.group(1) if time_match else None
        repetition_drift = False
        if name == "AutoDy-DailySpark" and configured_enabled and installed:
            recovery_minutes = (
                _clock_minutes(config.recovery_deadline)
                - _clock_minutes(config.daily_send_time)
            )
            observed_interval = _iso_duration_minutes(
                live.get("repetition_interval")
            )
            observed_duration = _iso_duration_minutes(
                live.get("repetition_duration")
            )
            if recovery_minutes < 1:
                repetition_drift = (
                    observed_interval not in {None, 0}
                    or observed_duration not in {None, 0}
                )
            else:
                repetition_drift = (
                    observed_interval != min(30, recovery_minutes)
                    or observed_duration != recovery_minutes
                )
        schedule_drift = (
            len(matches) > 1
            or windows_enabled != configured_enabled
            or configured_enabled
            and installed
            and windows_time != configured_time
            or repetition_drift
        )
        runtime_root_mismatch = False
        action_metadata_available = any(
            key in live for key in ("execute", "arguments", "working_directory")
        )
        if (
            installed
            and program_root is not None
            and data_root is not None
            and (action_metadata_available or require_runtime_metadata)
        ):
            arguments = str(live.get("arguments") or "")
            script_name = (
                "run-scheduled.ps1"
                if name == "AutoDy-DailySpark"
                else "health-check.ps1"
            )
            checks = []
            if require_runtime_metadata or "execute" in live:
                checks.append(
                    str(live.get("execute") or "")
                    .replace("/", "\\")
                    .rsplit("\\", 1)[-1]
                    .casefold()
                    != "powershell.exe"
                )
            if require_runtime_metadata or "arguments" in live:
                checks.extend(
                    (
                        _normalized_path(_argument_path(arguments, "File"))
                        != _normalized_path(program_root / "scripts" / script_name),
                        _normalized_path(_argument_path(arguments, "ProgramRoot"))
                        != _normalized_path(program_root),
                        _normalized_path(_argument_path(arguments, "DataRoot"))
                        != _normalized_path(data_root),
                    )
                )
            if require_runtime_metadata or "working_directory" in live:
                checks.append(
                    _normalized_path(live.get("working_directory"))
                    != _normalized_path(program_root)
                )
            runtime_root_mismatch = any(checks)
        principal_mismatch = bool(
            installed
            and task_user_id
            and str(live.get("principal_user_id") or "").strip().casefold()
            != task_user_id.strip().casefold()
        )
        drift = schedule_drift or runtime_root_mismatch or principal_mismatch
        result.append(
            {
                "name": name,
                "state": state,
                "next_run": live.get("next_run") or "",
                "last_run": live.get("last_run") or "",
                "last_result": live.get("last_result"),
                "installed": installed,
                "configured_enabled": configured_enabled,
                "configured_time": configured_time,
                "windows_time": windows_time,
                "target_count": target_count,
                "duplicate_count": max(0, len(matches) - 1),
                "drift": drift,
                "drift_reason": (
                    "principal_mismatch"
                    if principal_mismatch
                    else "runtime_root_mismatch"
                    if runtime_root_mismatch
                    else "schedule_mismatch"
                    if schedule_drift
                    else None
                ),
            }
        )
    return result


def installed_runtime_mismatch(
    config: AppConfig,
    rows: list[dict],
    program_root: Path,
    data_root: Path,
) -> bool:
    """Report task root drift only for the registered installed runtime."""
    registered = registered_install_roots()
    if registered is None:
        return False
    if (
        _normalized_path(registered[0]) != _normalized_path(program_root.resolve())
        or _normalized_path(registered[1]) != _normalized_path(data_root.resolve())
    ):
        return False
    try:
        statuses = scheduler_status_rows(
            config,
            rows,
            len(enabled_execution_targets(config)),
            program_root=program_root,
            data_root=data_root,
        )
    except Exception:
        return False
    return any(
        row.get("drift_reason") == "runtime_root_mismatch" for row in statuses
    )


class SchedulerService:
    def __init__(
        self,
        root: Path,
        install: Callable[[AppConfig], None] | None = None,
        data_root: Path | None = None,
        task_user_id: str | None = None,
        task_rows: Callable[[], list[dict]] | None = None,
    ):
        self.root = root.resolve()
        self.data_root = (data_root or root).resolve()
        self.task_user_id = task_user_id.strip() if task_user_id else None
        self._task_rows = task_rows or windows_task_rows
        self._install = install or self._install_windows_tasks

    def preview(self, previous: AppConfig, candidate: AppConfig) -> dict:
        return {
            "old": ScheduleSettings.from_config(previous).model_dump(),
            "new": ScheduleSettings.from_config(candidate).model_dump(),
            "affected_tasks": [
                {"name": "AutoDy-Health-Daily", "action": "update"},
                {"name": "AutoDy-DailySpark", "action": "update"},
                {
                    "name": "AutoDy-Health-Weekly",
                    "action": "update" if candidate.weekly_health_check_enabled else "remove",
                },
            ],
        }

    def apply(self, config_path: Path, previous: AppConfig, candidate: AppConfig) -> None:
        validate_schedule_settings(ScheduleSettings.from_config(candidate))
        try:
            self._install(candidate)
        except Exception:
            # Register-ScheduledTask can update a subset before a later task
            # fails; restoring the previous values makes that operation atomic
            # from the dashboard's perspective.
            try:
                self._install(previous)
            finally:
                raise
        try:
            save_config(config_path, candidate)
        except Exception:
            try:
                self._install(previous)
            finally:
                raise

    def repair(self, config: AppConfig) -> None:
        self._install(config)
        statuses = scheduler_status_rows(
            config,
            self._task_rows(),
            len(enabled_execution_targets(config)),
            program_root=self.root,
            data_root=self.data_root,
            task_user_id=self.task_user_id,
            require_runtime_metadata=True,
        )
        invalid = [
            row
            for row in statuses
            if row["drift"] or row["installed"] != row["configured_enabled"]
        ]
        if invalid:
            names = ", ".join(row["name"] for row in invalid)
            raise RuntimeError(f"定时任务修复后验证失败：{names}")

    def remove(self) -> None:
        script = self.root / "scripts" / "remove-task.ps1"
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode:
            raise RuntimeError((completed.stderr or completed.stdout or "无法移除定时任务").strip())

    def _install_windows_tasks(self, config: AppConfig) -> None:
        script = self.root / "scripts" / "install-task.ps1"
        command = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-ProgramRoot", str(self.root),
            "-DataRoot", str(self.data_root),
            "-DailyHealthCheckTime", config.daily_health_check_time,
            "-DailySendTime", config.daily_send_time,
            "-RecoveryDeadline", config.recovery_deadline,
            "-WeeklyHealthCheckEnabled", "1" if config.weekly_health_check_enabled else "0",
            "-WeeklyHealthCheckWeekday", config.weekly_health_check_weekday,
            "-WeeklyHealthCheckTime", config.weekly_health_check_time,
        ]
        if self.task_user_id:
            command.extend(["-TaskUserId", self.task_user_id])
        completed = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode:
            raise RuntimeError((completed.stderr or completed.stdout or "Windows 定时任务更新失败").strip())

"""Small Windows Task Scheduler adapter used by the dashboard.

The scheduler remains implemented by the portable PowerShell scripts.  This
module only validates dashboard values, previews their impact, and keeps the
YAML file and Windows tasks in sync with a compensating restore on failure.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable

from pydantic import BaseModel, Field, model_validator

from autody.config import AppConfig, save_config


TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
WEEKDAYS = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
TASK_NAMES = ("AutoDy-Health-Daily", "AutoDy-DailySpark", "AutoDy-Health-Weekly")


class ScheduleSettings(BaseModel):
    daily_health_check_time: str = Field(pattern=TIME_PATTERN)
    daily_send_time: str = Field(pattern=TIME_PATTERN)
    weekly_health_check_enabled: bool = True
    weekly_health_check_weekday: str = Field(pattern=rf"^({WEEKDAYS})$")
    weekly_health_check_time: str = Field(pattern=TIME_PATTERN)
    startup_recovery_enabled: bool = True
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


class SchedulerService:
    def __init__(
        self,
        root: Path,
        install: Callable[[AppConfig], None] | None = None,
        data_root: Path | None = None,
    ):
        self.root = root.resolve()
        self.data_root = (data_root or root).resolve()
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

    def install(self, config: AppConfig) -> None:
        self._install(config)

    def repair(self, config: AppConfig) -> None:
        self._install(config)

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
            "-WeeklyHealthCheckEnabled", "$true" if config.weekly_health_check_enabled else "$false",
            "-WeeklyHealthCheckWeekday", config.weekly_health_check_weekday,
            "-WeeklyHealthCheckTime", config.weekly_health_check_time,
        ]
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

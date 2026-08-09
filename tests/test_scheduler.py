from pathlib import Path
from types import SimpleNamespace

import pytest

from autody.config import AppConfig, Target
from autody import scheduler
from autody.scheduler import ScheduleSettings, SchedulerService, validate_schedule_settings


def test_schedule_settings_validate_times_and_recovery_window():
    settings = ScheduleSettings(
        daily_health_check_time="07:20",
        daily_send_time="07:30",
        weekly_health_check_enabled=True,
        weekly_health_check_weekday="Sunday",
        weekly_health_check_time="20:00",
        startup_recovery_enabled=True,
        recovery_deadline="23:59",
    )

    assert validate_schedule_settings(settings) == settings
    with pytest.raises(ValueError, match="不得早于"):
        validate_schedule_settings(settings.model_copy(update={"recovery_deadline": "07:00"}))


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

    SchedulerService(program_root, data_root=data_root).repair(
        AppConfig(targets=[Target(name="fixture")])
    )

    assert captured[captured.index("-ProgramRoot") + 1] == str(program_root.resolve())
    assert captured[captured.index("-DataRoot") + 1] == str(data_root.resolve())

from datetime import datetime
from pathlib import Path

from autody.config import AppConfig, Target
from autody.history import TaskHistoryStore, TaskRunRecord
from autody.recovery import recovery_due
from autody.state import AppState


def config(tmp_path: Path):
    return AppConfig(
        targets=[Target(name="小明")],
        state_file=tmp_path / "state.json",
        daily_send_time="07:30",
        recovery_deadline="23:59",
    )


def test_recovery_does_not_run_before_send_time(tmp_path: Path):
    assert recovery_due(config(tmp_path), AppState(), datetime(2026, 7, 13, 7, 29)) is False


def test_recovery_runs_after_missed_time_and_skips_completed_day(tmp_path: Path):
    loaded = config(tmp_path)
    state = AppState()
    now = datetime(2026, 7, 13, 8, 0)
    assert recovery_due(loaded, state, now) is True
    state.daily["2026-07-13"] = {"succeeded": ["小明"], "consumed": True}
    assert recovery_due(loaded, state, now) is False


def test_recovery_skips_target_with_confirmed_success_in_structured_history(
    tmp_path: Path,
):
    loaded = config(tmp_path)
    loaded.targets[0].stable_id = "target-one"
    loaded.targets[0].candidate_id = "candidate-one"
    TaskHistoryStore(tmp_path / "history" / "task-runs.jsonl").append(
        TaskRunRecord(
            run_id="run-morning-success",
            date="2026-07-13",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-07-13T07:30:00",
            end_time="2026-07-13T07:31:00",
            total_targets=1,
            success_count=1,
            final_status="completed",
            confirmation_results={"target-one": "confirmed"},
        )
    )

    assert recovery_due(
        loaded,
        AppState(),
        datetime(2026, 7, 13, 8, 0),
    ) is False


def test_recovery_respects_same_day_deadline(tmp_path: Path):
    loaded = config(tmp_path)
    loaded.recovery_deadline = "20:00"
    assert recovery_due(loaded, AppState(), datetime(2026, 7, 13, 20, 1)) is False

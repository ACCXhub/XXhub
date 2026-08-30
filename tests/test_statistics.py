from datetime import date

from autody.daily_status import dashboard_statistics
from autody.history import TaskRunRecord


def run(
    day: str,
    status="completed",
    retries=0,
    *,
    success_count: int | None = None,
    failed_count: int | None = None,
    skipped_count: int = 0,
    end_time: str | None = None,
    total_targets: int = 2,
    confirmation_results: dict[str, str] | None = None,
    confirmation_provenance: dict[str, str] | None = None,
):
    return TaskRunRecord(
        run_id=day,
        date=day,
        task_type="daily_send",
        trigger_source="scheduled",
        start_time=f"{day}T07:30:00",
        end_time=end_time or f"{day}T07:31:00",
        duration=60,
        total_targets=total_targets,
        success_count=(
            2 if status == "completed" else 1
        ) if success_count is None else success_count,
        failed_count=(
            0 if status == "completed" else 1
        ) if failed_count is None else failed_count,
        skipped_count=skipped_count,
        retry_count=retries,
        final_status=status,
        confirmation_results=confirmation_results or {},
        confirmation_provenance=confirmation_provenance or {},
    )


def test_dashboard_statistics_use_day_level_structured_history():
    records = [run("2026-07-11"), run("2026-07-12"), run("2026-07-13", retries=2)]

    stats = dashboard_statistics({}, records, date(2026, 7, 13))

    assert stats["consecutive_successful_days"] == 3
    assert stats["success_rate_7d"] == 100.0


def test_already_done_record_is_a_terminal_success_day():
    records = [
        run(
            "2026-07-13",
            status="already_done",
            success_count=0,
            failed_count=0,
            skipped_count=2,
        )
    ]

    stats = dashboard_statistics({}, records, date(2026, 7, 13))

    assert stats["success_rate_7d"] == 100.0
    assert stats["success_rate_30d"] == 100.0


def test_dashboard_statistics_keep_earlier_confirmed_success_terminal():
    records = [
        run("2026-07-13", end_time="2026-07-13T07:31:00"),
        run(
            "2026-07-13",
            status="retry_pending",
            success_count=0,
            failed_count=1,
            skipped_count=1,
            end_time="2026-07-13T14:24:00",
        ),
    ]

    stats = dashboard_statistics({}, records, date(2026, 7, 13))

    assert stats["success_rate_7d"] == 100.0
    assert stats["consecutive_successful_days"] == 1


def test_historical_consumed_day_is_not_recomputed_from_current_targets():
    daily = {
        "2026-07-11": {
            "consumed": True,
            "succeeded": ["historical-target"],
            "failures": {},
        }
    }

    stats = dashboard_statistics(daily, [], date(2026, 7, 13))

    assert stats["success_rate_7d"] == 100.0
    assert stats["success_rate_30d"] == 100.0


def test_missing_calendar_day_breaks_streak_but_not_rate_denominator():
    daily = {
        "2026-07-11": {"consumed": True},
        "2026-07-13": {"consumed": True},
    }

    stats = dashboard_statistics(daily, [], date(2026, 7, 13))

    assert stats["success_rate_7d"] == 100.0
    assert stats["consecutive_successful_days"] == 1


def test_missing_today_starts_streak_from_yesterday():
    daily = {
        "2026-07-11": {"consumed": True},
        "2026-07-12": {"consumed": True},
    }

    stats = dashboard_statistics(daily, [], date(2026, 7, 13))

    assert stats["consecutive_successful_days"] == 2


def test_partial_recovery_uses_explicit_historical_confirmations():
    records = [
        run(
            "2026-07-13",
            status="retry_pending",
            success_count=1,
            failed_count=1,
            confirmation_results={"target-a": "confirmed"},
            confirmation_provenance={"target-a": "post_send_observed"},
        ),
        run(
            "2026-07-13",
            status="retry_pending",
            success_count=1,
            failed_count=0,
            skipped_count=1,
            end_time="2026-07-13T08:31:00",
            confirmation_results={"target-b": "retry_confirmed"},
            confirmation_provenance={"target-b": "post_send_observed"},
        ),
    ]

    stats = dashboard_statistics({}, records, date(2026, 7, 13))

    assert stats["success_rate_7d"] == 100.0
    assert stats["consecutive_successful_days"] == 1


def test_failed_day_counts_once_and_retries_do_not_change_denominator():
    records = [
        run("2026-07-12", status="partial_failed", retries=3),
        run(
            "2026-07-12",
            status="retry_pending",
            retries=2,
            end_time="2026-07-12T08:31:00",
        ),
        run("2026-07-13"),
    ]

    stats = dashboard_statistics({}, records, date(2026, 7, 13))

    assert stats["success_rate_7d"] == 50.0
    assert "retries_7d" not in stats


def test_health_check_records_are_not_day_statistics_facts():
    record = run("2026-07-13")
    record.task_type = "health_check"

    stats = dashboard_statistics({}, [record], date(2026, 7, 13))

    assert stats["success_rate_7d"] == 0.0
    assert stats["consecutive_successful_days"] == 0


def test_recovered_run_is_a_terminal_success():
    stats = dashboard_statistics(
        {},
        [run("2026-07-13", status="recovered", success_count=0, failed_count=0)],
        date(2026, 7, 13),
    )

    assert stats["success_rate_7d"] == 100.0

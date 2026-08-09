from datetime import date

from autody.history import TaskRunRecord, dashboard_statistics


def run(
    day: str,
    status="completed",
    retries=0,
    *,
    success_count: int | None = None,
    failed_count: int | None = None,
    skipped_count: int = 0,
    end_time: str | None = None,
):
    return TaskRunRecord(
        run_id=day,
        date=day,
        task_type="daily_send",
        trigger_source="scheduled",
        start_time=f"{day}T07:30:00",
        end_time=end_time or f"{day}T07:31:00",
        duration=60,
        total_targets=2,
        success_count=(
            2 if status == "completed" else 1
        ) if success_count is None else success_count,
        failed_count=(
            0 if status == "completed" else 1
        ) if failed_count is None else failed_count,
        skipped_count=skipped_count,
        retry_count=retries,
        final_status=status,
    )


def test_dashboard_statistics_use_structured_history():
    records = [run("2026-07-11"), run("2026-07-12"), run("2026-07-13", retries=2)]

    stats = dashboard_statistics(records, date(2026, 7, 13))

    assert stats["consecutive_successful_days"] == 3
    assert stats["success_rate_7d"] == 100.0
    assert stats["retries_7d"] == 2


def test_dashboard_statistics_do_not_count_skipped_targets_as_confirmed_success():
    records = [
        run(
            "2026-07-13",
            status="already_done",
            success_count=0,
            failed_count=0,
            skipped_count=2,
        )
    ]

    stats = dashboard_statistics(records, date(2026, 7, 13))

    assert stats["success_rate_7d"] == 0.0
    assert stats["success_rate_30d"] == 0.0


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

    stats = dashboard_statistics(records, date(2026, 7, 13))

    assert stats["success_rate_7d"] == 100.0
    assert stats["consecutive_successful_days"] == 1

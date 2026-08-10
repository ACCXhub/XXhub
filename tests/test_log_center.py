from datetime import date
from pathlib import Path

from autody.config import AppConfig, Target
from autody.log_center import archive_historical_logs, archive_logs, automatic_cleanup_once_daily, cleanup_logs, log_storage_summary, log_summary, query_logs


def test_logs_default_to_three_days_filter_and_mask_names(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for day, level in [("2026-07-09", "ERROR"), ("2026-07-11", "INFO"), ("2026-07-12", "WARNING"), ("2026-07-13", "ERROR")]:
        (log_dir / f"autody-{day}.log").write_text(
            f"{day} 08:00:00,000 {level} 发送失败：小明\nTraceback line\n",
            encoding="utf-8",
        )
    config = AppConfig(targets=[Target(name="小明")], mask_log_friend_names=True)

    page = query_logs(log_dir, config, today=date(2026, 7, 13), level="ERROR")

    assert page.start_date == "2026-07-11"
    assert page.total == 1
    assert "小明" not in page.items[0].summary
    assert "好友#" in page.items[0].summary
    assert page.items[0].detail == "Traceback line"


def test_archive_moves_old_logs_without_deleting(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old = log_dir / "autody-2026-07-01.log"
    recent = log_dir / "autody-2026-07-13.log"
    old.write_text("old", encoding="utf-8")
    recent.write_text("recent", encoding="utf-8")

    moved = archive_logs(log_dir, date(2026, 7, 10))

    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "old"
    assert not old.exists()
    assert recent.exists()


def test_legacy_rotated_traceback_is_filterable_and_collapsed(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody.log.2026-07-01").write_text(
        "2026-07-01 08:00:00,000 ERROR 发送任务异常：小明\n"
        "Traceback (most recent call last):\n"
        "  File 'runner.py', line 1\n"
        "RuntimeError: failed\n",
        encoding="utf-8",
    )
    config = AppConfig(targets=[Target(name="小明")])

    page = query_logs(
        log_dir,
        config,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        level="ERROR",
    )

    assert page.total == 1
    assert "好友#" in page.items[0].summary
    assert page.items[0].detail.startswith("Traceback")


def test_repeated_tracebacks_are_grouped_and_resolved_after_success(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody-2026-07-13.log").write_text(
        "2026-07-13 07:20:00,000 ERROR ModuleNotFoundError: No module named 'autody.diagnostics'\n"
        "Traceback (most recent call last):\n  File 'cli.py', line 27\n"
        "2026-07-13 07:21:00,000 ERROR ModuleNotFoundError: No module named 'autody.diagnostics'\n"
        "Traceback (most recent call last):\n  File 'cli.py', line 27\n"
        "2026-07-14 07:20:00,000 INFO 登录状态和抖音聊天页正常。\n",
        encoding="utf-8",
    )
    page = query_logs(log_dir, AppConfig(), start_date=date(2026, 7, 13), end_date=date(2026, 7, 14))
    error = next(item for item in page.items if item.level == "ERROR")

    assert error.occurrences == 2
    assert error.status == "resolved"
    assert error.fingerprint
    summary = log_summary(log_dir, AppConfig(), today=date(2026, 7, 14))
    assert summary["active_errors"] == 0


def test_send_block_error_is_resolved_after_later_clean_retry(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody-2026-07-14.log").write_text(
        "2026-07-14 07:30:50,941 INFO 本次发送完成：成功 6 个，失败 2 个。\n"
        "2026-07-14 07:30:50,943 ERROR 浏览器任务已安全停止：页面结构超时\n"
        "2026-07-14 11:23:42,236 INFO 本次发送完成：成功 2 个，失败 0 个。\n",
        encoding="utf-8",
    )

    page = query_logs(log_dir, AppConfig(), start_date=date(2026, 7, 14), end_date=date(2026, 7, 14))
    blocked = next(item for item in page.items if "浏览器任务已安全停止" in item.summary)

    assert blocked.status == "resolved"
    assert log_summary(log_dir, AppConfig(), today=date(2026, 7, 14))["active_errors"] == 0


def test_partial_send_result_is_resolved_after_later_clean_retry(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody-2026-07-13.log").write_text(
        "2026-07-13 07:33:41,674 INFO 本次发送完成：成功 0 个，失败 9 个。\n"
        "2026-07-13 07:33:41,676 ERROR 本次部分失败，再次运行将只补发失败目标。\n"
        "2026-07-13 07:37:05,395 INFO 本次发送完成：成功 9 个，失败 0 个。\n",
        encoding="utf-8",
    )

    page = query_logs(log_dir, AppConfig(), start_date=date(2026, 7, 13), end_date=date(2026, 7, 13))
    partial_failure = next(item for item in page.items if "本次部分失败" in item.summary)

    assert partial_failure.status == "resolved"


def test_friend_scan_error_is_resolved_after_later_complete_scan(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody-2026-07-14.log").write_text(
        "2026-07-14 08:00:00,000 ERROR 好友识别发生未捕获异常。\n"
        "2026-07-14 08:10:00,000 INFO 好友识别完成：发现 78 个候选\n",
        encoding="utf-8",
    )

    page = query_logs(
        log_dir,
        AppConfig(),
        start_date=date(2026, 7, 14),
        end_date=date(2026, 7, 14),
    )
    failure = next(item for item in page.items if item.level == "ERROR")

    assert failure.task_type == "friend_scan"
    assert failure.status == "resolved"


def test_avatar_error_is_resolved_after_later_clean_avatar_refresh(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody-2026-07-14.log").write_text(
        "2026-07-14 08:00:00,000 ERROR 头像更新发生未捕获异常。\n"
        "2026-07-14 08:10:00,000 INFO 头像校正完成：更新 2 个，失败 0 个。\n",
        encoding="utf-8",
    )

    page = query_logs(
        log_dir,
        AppConfig(),
        start_date=date(2026, 7, 14),
        end_date=date(2026, 7, 14),
    )
    failure = next(item for item in page.items if item.level == "ERROR")

    assert failure.task_type == "friend_scan"
    assert failure.status == "resolved"


def test_exhausted_safe_retry_is_resolved_after_later_clean_send(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody-2026-07-14.log").write_text(
        "2026-07-14 08:00:00,000 ERROR 安全重试已耗尽：无法定位目标。\n"
        "2026-07-14 08:10:00,000 INFO 本次发送完成：成功 1 个，失败 0 个。\n",
        encoding="utf-8",
    )

    page = query_logs(
        log_dir,
        AppConfig(),
        start_date=date(2026, 7, 14),
        end_date=date(2026, 7, 14),
    )
    failure = next(item for item in page.items if item.level == "ERROR")

    assert failure.task_type == "daily_send"
    assert failure.status == "resolved"


def test_send_log_masks_friend_removed_from_current_configuration(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    removed_friend = "旧好友甲"
    (log_dir / "autody-2026-07-14.log").write_text(
        f"2026-07-14 11:23:39,909 INFO 发送已确认：{removed_friend}（confirmed）\n"
        f"2026-07-14 11:23:40,000 INFO 发送成功：{removed_friend}\n",
        encoding="utf-8",
    )

    page = query_logs(log_dir, AppConfig(), start_date=date(2026, 7, 14), end_date=date(2026, 7, 14))

    summaries = "\n".join(item.summary for item in page.items)
    assert removed_friend not in summaries
    assert summaries.count("好友#") == 2


def test_legacy_rollover_log_is_historical_and_archived_by_classification(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    legacy = log_dir / "autody.log.2026-07-04"
    legacy.write_text("2026-07-04 10:00:00,000 ERROR TimedRotatingFileHandler PermissionError [WinError 32]\n", encoding="utf-8")
    page = query_logs(log_dir, AppConfig(), start_date=date(2026, 7, 4), end_date=date(2026, 7, 4))

    assert page.items[0].status == "historical"
    assert archive_historical_logs(log_dir, AppConfig())
    assert not legacy.exists()


def test_cleanup_archives_old_active_logs_and_deletes_only_old_archives(tmp_path: Path):
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    old = log_dir / "autody-2026-06-01.log"; recent = log_dir / "autody-2026-07-14.log"
    old.write_text("old", encoding="utf-8"); recent.write_text("recent", encoding="utf-8")
    archive = log_dir / "archive"; archive.mkdir()
    expired = archive / "autody-2026-04-01.log"; keep = archive / "autody-2026-07-01.log"; unrelated = archive / "notes.txt"
    expired.write_text("expired", encoding="utf-8"); keep.write_text("keep", encoding="utf-8"); unrelated.write_text("safe", encoding="utf-8")

    preview = cleanup_logs(log_dir, active_days=14, archive_days=90, today=date(2026, 7, 15), apply=False)
    result = cleanup_logs(log_dir, active_days=14, archive_days=90, today=date(2026, 7, 15))

    assert preview["to_archive"] == result["archived"] == 1
    assert preview["to_delete"] == result["deleted"] == 1
    assert (archive / old.name).exists() and recent.exists()
    assert not expired.exists() and keep.exists() and unrelated.exists()


def test_automatic_cleanup_runs_only_once_per_day(tmp_path: Path):
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    (log_dir / "autody-2026-06-01.log").write_text("old", encoding="utf-8")

    assert automatic_cleanup_once_daily(log_dir, today=date(2026, 7, 15)) is not None
    assert automatic_cleanup_once_daily(log_dir, today=date(2026, 7, 15)) is None


def test_cleanup_failure_does_not_block_housekeeping(tmp_path: Path, monkeypatch):
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    monkeypatch.setattr("autody.log_center.cleanup_logs", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("locked")))

    assert automatic_cleanup_once_daily(log_dir, today=date(2026, 7, 15)) is None


def test_storage_summary_and_preview_do_not_change_files(tmp_path: Path):
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    old = log_dir / "autody-2026-06-01.log"; old.write_text("old", encoding="utf-8")

    preview = cleanup_logs(log_dir, today=date(2026, 7, 15), apply=False)
    summary = log_storage_summary(log_dir)

    assert preview["to_archive"] == 1
    assert old.exists()
    assert not (log_dir / "archive").exists()
    assert summary["active_files"] == 1
    assert summary["active_bytes"] == len("old")
    assert summary["archived_files"] == 0
    assert summary["next_cleanup_date"]


def test_cleanup_keeps_protected_project_files_untouched(tmp_path: Path):
    log_dir = tmp_path / "data" / "logs"; log_dir.mkdir(parents=True)
    (log_dir / "autody-2026-06-01.log").write_text("old", encoding="utf-8")
    protected = [tmp_path / "data" / "history" / "task-runs.jsonl", tmp_path / "config.yaml", tmp_path / "messages.txt"]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text("protected", encoding="utf-8")

    cleanup_logs(log_dir, today=date(2026, 7, 15))

    assert all(path.read_text(encoding="utf-8") == "protected" for path in protected)

from datetime import date, datetime
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import threading

from autody.config import AppConfig, Target
from autody.logging_setup import DailyAppendFileHandler, read_daily_logs, setup_logging


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        targets=[Target(name="小明")],
        state_file=tmp_path / "data" / "state.json",
    )


def test_logging_writes_directly_to_date_named_file_without_rotation(tmp_path: Path):
    logger = logging.getLogger(f"autody-test-{tmp_path.name}")
    logger.handlers.clear()

    path = setup_logging(make_config(tmp_path), today=date(2026, 7, 4), logger=logger)

    assert path == tmp_path / "data" / "logs" / "autody-2026-07-04.log"
    assert any(isinstance(handler, DailyAppendFileHandler) for handler in logger.handlers)
    assert not any(
        isinstance(handler, TimedRotatingFileHandler) for handler in logger.handlers
    )
    logger.info("测试日志")
    assert "测试日志" in path.read_text(encoding="utf-8")


def test_logging_selects_file_from_each_record_date(tmp_path: Path):
    logger = logging.getLogger(f"autody-boundary-{tmp_path.name}")
    logger.handlers.clear()
    setup_logging(make_config(tmp_path), logger=logger)

    before = logger.makeRecord(logger.name, logging.INFO, __file__, 1, "跨日前", (), None)
    before.created = datetime(2026, 7, 29, 23, 59, 59).timestamp()
    after = logger.makeRecord(logger.name, logging.INFO, __file__, 1, "跨日后", (), None)
    after.created = datetime(2026, 7, 30, 0, 0, 1).timestamp()
    handler = next(item for item in logger.handlers if isinstance(item, DailyAppendFileHandler))
    handler.handle(before)
    handler.handle(after)

    files = sorted((tmp_path / "data" / "logs").glob("autody-*.log"))
    assert len(files) == 2
    assert "跨日前" in files[0].read_text(encoding="utf-8")
    assert "跨日后" in files[1].read_text(encoding="utf-8")


def test_two_loggers_append_complete_lines_without_shared_open_handle(tmp_path: Path):
    log_dir = tmp_path / "logs"
    handlers = [DailyAppendFileHandler(log_dir, fixed_date=date(2026, 7, 30)) for _ in range(2)]
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))

    def write(index: int):
        logger = logging.getLogger(f"autody-concurrent-{tmp_path.name}-{index}")
        logger.handlers[:] = [handlers[index]]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        for line in range(100):
            logger.info("writer-%d-line-%03d", index, line)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = (log_dir / "autody-2026-07-30.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    assert len(set(lines)) == 200
    assert all(line.startswith("writer-") for line in lines)


def test_locked_log_write_is_suppressed_without_raw_logging_traceback(
    tmp_path: Path, monkeypatch, capsys
):
    handler = DailyAppendFileHandler(tmp_path / "logs", fixed_date=date(2026, 7, 30))
    handler.setFormatter(logging.Formatter("%(message)s"))
    real_open = os.open

    def locked_open(path, flags, mode=0o777):
        if str(path).endswith("autody-2026-07-30.log"):
            raise PermissionError(32, "file is locked")
        return real_open(path, flags, mode)

    monkeypatch.setattr("autody.logging_setup.os.open", locked_open)
    logger = logging.getLogger(f"autody-locked-{tmp_path.name}")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("业务仍应继续")
    logger.info("第二次失败也不输出 traceback")

    captured = capsys.readouterr()
    assert "Logging error" not in captured.err
    fallback = tmp_path / "logs" / "internal-logging-fallback.log"
    assert fallback.read_text(encoding="utf-8").count("business operation continued") == 1


def test_read_daily_logs_merges_recent_files_in_time_order(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "autody-2026-07-03.log").write_text("旧记录\n", encoding="utf-8")
    (log_dir / "autody-2026-07-04.log").write_text("新记录\n", encoding="utf-8")
    (log_dir / "autody.log").write_text("旧轮转格式\n", encoding="utf-8")

    text = read_daily_logs(log_dir, line_limit=20)

    assert text.splitlines() == ["旧记录", "新记录"]

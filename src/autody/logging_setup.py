from datetime import date
from datetime import datetime
import logging
import os
from pathlib import Path

from autody.config import AppConfig
from autody.locking import SingleInstanceLock


class DailyAppendFileHandler(logging.Handler):
    """Append one formatted record per open/write/close cycle.

    AutoDy has several short-lived processes on Windows. Keeping a shared log
    file open prevents retention jobs from moving old files, while rotating a
    shared file by rename is inherently racy. This handler derives the filename
    from each record's local timestamp and never keeps a file descriptor open.
    """

    def __init__(self, log_dir: Path, *, fixed_date: date | None = None):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.fixed_date = fixed_date
        self._fallback_reported = False

    @staticmethod
    def _append(path: Path, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOINHERIT", 0)
        descriptor = os.open(path, flags, 0o666)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)

    def _path_for(self, record: logging.LogRecord) -> Path:
        record_date = self.fixed_date or datetime.fromtimestamp(record.created).date()
        return self.log_dir / f"autody-{record_date:%Y-%m-%d}.log"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = f"{self.format(record)}\n".encode("utf-8", errors="replace")
            with SingleInstanceLock(
                self.log_dir / ".autody-log-write.lock",
                timeout_seconds=2.0,
                poll_interval=0.01,
            ):
                self._append(self._path_for(record), line)
        except Exception:
            # logging must never alter send/retry outcomes. Do not call
            # Handler.handleError(), which writes a raw traceback to stderr and
            # is later ingested by the scheduler log.
            if self._fallback_reported:
                return
            self._fallback_reported = True
            try:
                self._append(
                    self.log_dir / "internal-logging-fallback.log",
                    b"AutoDy log write failed; business operation continued.\n",
                )
            except Exception:
                pass


def setup_logging(
    config: AppConfig,
    today: date | None = None,
    logger: logging.Logger | None = None,
) -> Path:
    active_logger = logger or logging.getLogger()
    log_dir = config.state_file.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"autody-{today or date.today():%Y-%m-%d}.log"

    for handler in list(active_logger.handlers):
        if getattr(handler, "_autody_daily_handler", False):
            active_logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = DailyAppendFileHandler(log_dir, fixed_date=today)
    file_handler.setFormatter(formatter)
    file_handler._autody_daily_handler = True
    active_logger.setLevel(logging.INFO)
    active_logger.addHandler(file_handler)
    return log_path


def read_daily_logs(log_dir: Path, line_limit: int = 400) -> str:
    files = sorted(log_dir.glob("autody-????-??-??.log"))
    if not files:
        legacy = log_dir / "autody.log"
        files = [legacy] if legacy.exists() else []
    lines: list[str] = []
    for path in files[-14:]:
        lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return "\n".join(lines[-line_limit:])

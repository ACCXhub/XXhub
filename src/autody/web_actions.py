from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import threading
import uuid


BROWSER_ACTIONS = {
    "run",
    "login",
    "health-check",
    "scan-friends",
    "background-discovery",
    "refresh-friend-avatars",
    "startup-recovery",
    "repair-playwright",
    "refresh-account-profile",
    "preflight",
    "module-preflight",
}


class ActionAlreadyRunning(RuntimeError):
    pass


@dataclass
class ActionJob:
    id: str
    action: str
    status: str = "running"
    started_at: str = ""
    finished_at: str | None = None
    exit_code: int | None = None


class ActionManager:
    def __init__(self, root: Path, config_path: Path, executor=None):
        self.root = root
        self.config_path = config_path
        self.jobs: dict[str, ActionJob] = {}
        self._lock = threading.Lock()
        self._executor = executor or subprocess.run
        self.module_data_root: Path | None = None

    def start(self, action: str) -> dict:
        with self._lock:
            if action in BROWSER_ACTIONS and any(
                job.status == "running" and job.action in BROWSER_ACTIONS
                for job in self.jobs.values()
            ):
                raise ActionAlreadyRunning("已有 AutoDy 任务正在运行，本次跳过。")
            job = ActionJob(
                id=uuid.uuid4().hex,
                action=action,
                started_at=datetime.now().isoformat(timespec="seconds"),
            )
            self.jobs[job.id] = job
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return asdict(job)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self.jobs.get(job_id)
            return asdict(job) if job else None

    def _command(self, action: str) -> list[str]:
        if action == "module-preflight":
            return [
                sys.executable,
                "-m",
                "autody.cli",
                "preflight",
                "--config",
                str(self.config_path),
                "--module-data",
                str(self.module_data_root or self.root / "data" / "modules" / "autody-test-center" / "data"),
            ]
        if action == "startup-recovery":
            return [
                sys.executable,
                "-m",
                "autody.cli",
                "run",
                "--config",
                str(self.config_path),
                "--source",
                "startup_recovery",
            ]
        if action == "background-discovery":
            return [
                sys.executable,
                "-m",
                "autody.cli",
                "scan-friends",
                "--config",
                str(self.config_path),
                "--background",
            ]
        if action in {
            "run",
            "login",
            "health-check",
            "scan-friends",
            "refresh-friend-avatars",
            "repair-playwright",
            "refresh-account-profile", "preflight", "module-preflight",
        }:
            return [
                sys.executable,
                "-m",
                "autody.cli",
                action,
                "--config",
                str(self.config_path),
            ]
        script = {
            "install-scheduler": "install-task.ps1",
            "remove-scheduler": "remove-task.ps1",
        }.get(action)
        if not script:
            raise ValueError(f"unsupported action: {action}")
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.root / "scripts" / script),
        ]

    def _execute(self, job: ActionJob) -> None:
        try:
            completed = self._executor(
                self._command(job.action),
                cwd=self.root,
                check=False,
            )
            with self._lock:
                job.exit_code = completed.returncode
                job.status = "success" if completed.returncode == 0 else "failed"
        except Exception:
            with self._lock:
                job.exit_code = 1
                job.status = "failed"
        finally:
            with self._lock:
                job.finished_at = datetime.now().isoformat(timespec="seconds")

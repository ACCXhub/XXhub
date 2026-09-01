from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from autody.failures import failure_detail

BROWSER_ACTIONS = {
    "run",
    "run-target",
    "safe-supplement",
    "login",
    "health-check",
    "scan-friends",
    "refresh-friend-avatars",
    "repair-playwright",
    "refresh-account-profile",
    "startup-refresh",
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
    target_id: str | None = None
    target_ids: list[str] | None = None
    failure: dict | None = None


class ActionManager:
    def __init__(self, root: Path, config_path: Path, executor=None):
        self.root = root
        self.config_path = config_path
        self.jobs: dict[str, ActionJob] = {}
        self._lock = threading.Lock()
        self._executor = executor or subprocess.run
        self.module_data_root: Path | None = None

    def start(
        self,
        action: str,
        *,
        target_id: str | None = None,
        target_ids: list[str] | None = None,
    ) -> dict:
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
                target_id=target_id,
                target_ids=list(dict.fromkeys(target_ids or [])) or None,
            )
            self.jobs[job.id] = job
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return asdict(job)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self.jobs.get(job_id)
            return asdict(job) if job else None

    def browser_action_running(self) -> bool:
        with self._lock:
            return any(
                job.status == "running" and job.action in BROWSER_ACTIONS
                for job in self.jobs.values()
            )

    def _command(
        self,
        action: str,
        target_id: str | None = None,
        target_ids: list[str] | None = None,
    ) -> list[str]:
        if action == "run-target":
            if not target_id:
                raise ValueError("target_id is required for run-target")
            return [
                sys.executable,
                "-m",
                "autody.cli",
                "run",
                "--config",
                str(self.config_path),
                "--source",
                "retry",
                "--target-id",
                target_id,
            ]
        if action == "safe-supplement":
            if not target_ids:
                raise ValueError("target_ids are required for safe-supplement")
            command = [
                sys.executable,
                "-m",
                "autody.cli",
                "run",
                "--config",
                str(self.config_path),
                "--source",
                "retry",
            ]
            for selected_target_id in target_ids:
                command.extend(["--target-id", selected_target_id])
            return command
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
        if action in {
            "run",
            "login",
            "health-check",
            "scan-friends",
            "refresh-friend-avatars",
            "repair-playwright",
            "refresh-account-profile", "preflight", "module-preflight",
            "startup-refresh",
        }:
            return [
                sys.executable,
                "-m",
                "autody.cli",
                action,
                "--config",
                str(self.config_path),
            ]
        raise ValueError(f"unsupported action: {action}")

    def _execute(self, job: ActionJob) -> None:
        try:
            completed = self._executor(
                self._command(job.action, job.target_id, job.target_ids),
                cwd=self.root,
                check=False,
            )
            with self._lock:
                job.exit_code = completed.returncode
                job.status = "success" if completed.returncode == 0 else "failed"
                if completed.returncode != 0:
                    job.failure = failure_detail(
                        "unknown_exception",
                        stage="browser_opened",
                        diagnostic_details={
                            "action": job.action,
                            "exit_code": completed.returncode,
                        },
                    ).model_dump(mode="json")
        except Exception as exc:
            with self._lock:
                job.exit_code = 1
                job.status = "failed"
                job.failure = failure_detail(
                    "unknown_exception",
                    stage="browser_opened",
                    diagnostic_details={
                        "action": job.action,
                        "exception_type": type(exc).__name__,
                    },
                ).model_dump(mode="json")
        finally:
            with self._lock:
                job.finished_at = datetime.now().isoformat(timespec="seconds")

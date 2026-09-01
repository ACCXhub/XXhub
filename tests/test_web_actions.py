from pathlib import Path
import sys
import threading

import pytest

from autody.web_actions import ActionAlreadyRunning, ActionManager


def test_action_manager_rejects_second_browser_action_while_first_runs(tmp_path: Path):
    started = threading.Event()
    release = threading.Event()

    def execute(_command, **_kwargs):
        started.set()
        release.wait(timeout=5)
        return type("Completed", (), {"returncode": 0})()

    manager = ActionManager(tmp_path, tmp_path / "config.yaml", executor=execute)
    manager.start("run")
    assert started.wait(timeout=2)

    for action in ["health-check", "login", "scan-friends", "refresh-friend-avatars", "repair-playwright"]:
        with pytest.raises(ActionAlreadyRunning):
            manager.start(action)

    release.set()


def test_targeted_retry_action_uses_stable_target_id_argument(tmp_path: Path):
    commands = []

    def execute(command, **_kwargs):
        commands.append(command)
        return type("Completed", (), {"returncode": 0})()

    manager = ActionManager(tmp_path, tmp_path / "config.yaml", executor=execute)
    job = manager.start("run-target", target_id="target-stable")

    for _attempt in range(100):
        completed = manager.get(job["id"])
        if completed and completed["status"] != "running":
            break
        threading.Event().wait(0.01)

    assert commands == [[
        sys.executable,
        "-m",
        "autody.cli",
        "run",
        "--config",
        str(tmp_path / "config.yaml"),
        "--source",
        "retry",
        "--target-id",
        "target-stable",
    ]]


def test_safe_supplement_uses_one_cli_run_for_all_safe_target_ids(tmp_path: Path):
    commands = []

    def execute(command, **_kwargs):
        commands.append(command)
        return type("Completed", (), {"returncode": 0})()

    manager = ActionManager(tmp_path, tmp_path / "config.yaml", executor=execute)
    job = manager.start(
        "safe-supplement",
        target_ids=["target-a", "target-b", "target-c"],
    )

    for _attempt in range(100):
        completed = manager.get(job["id"])
        if completed and completed["status"] != "running":
            break
        threading.Event().wait(0.01)

    assert commands == [[
        sys.executable,
        "-m",
        "autody.cli",
        "run",
        "--config",
        str(tmp_path / "config.yaml"),
        "--source",
        "retry",
        "--target-id",
        "target-a",
        "--target-id",
        "target-b",
        "--target-id",
        "target-c",
    ]]


def test_scheduler_writes_are_not_exposed_through_generic_actions(tmp_path: Path):
    manager = ActionManager(tmp_path, tmp_path / "config.yaml")

    with pytest.raises(ValueError, match="unsupported action"):
        manager._command("install-scheduler")
    with pytest.raises(ValueError, match="unsupported action"):
        manager._command("remove-scheduler")


def test_startup_refresh_uses_the_read_only_targeted_command(tmp_path: Path):
    manager = ActionManager(tmp_path, tmp_path / "config.yaml")

    command = manager._command("startup-refresh")

    assert command == [
        sys.executable,
        "-m",
        "autody.cli",
        "startup-refresh",
        "--config",
        str(tmp_path / "config.yaml"),
    ]


def test_failed_action_job_exposes_structured_chinese_failure(tmp_path: Path):
    def execute(_command, **_kwargs):
        return type("Completed", (), {"returncode": 7})()

    manager = ActionManager(tmp_path, tmp_path / "config.yaml", executor=execute)
    job = manager.start("run")

    for _attempt in range(100):
        completed = manager.get(job["id"])
        if completed and completed["status"] != "running":
            break
        threading.Event().wait(0.01)

    assert completed["status"] == "failed"
    assert completed["failure"]["stage"] == "browser_opened"
    assert completed["failure"]["reason_code"] == "unknown_exception"
    assert completed["failure"]["user_summary_zh"]
    assert completed["failure"]["suggested_action_zh"]
    assert completed["failure"]["diagnostic_details"] == {
        "action": "run",
        "exit_code": 7,
    }

from io import BytesIO
from datetime import datetime
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from autody.web_api import create_app
from autody.scheduler import scheduler_status_rows
from autody.history import TaskHistoryStore, TaskRunRecord
from autody.config import Target, load_config, save_config
from autody.preflight import PreflightStore
from autody.modules import OFFICIAL_TEST_CENTER_CORE_RANGE, OFFICIAL_TEST_CENTER_VERSION, MODULE_ID, ModuleManager, build_module_archive
from autody.account_profile import mark_bindings_for_revalidation
from autody.failures import failure_detail


def make_project(tmp_path: Path) -> Path:
    (tmp_path / "messages.txt").write_text("早安\n晚安\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        """
targets:
  - name: 小明
  - name: 小红
messages_file: messages.txt
profile_dir: data/browser-profile
state_file: data/state.json
lock_file: data/autody.lock
artifact_dir: data/artifacts
retry_count: 3
timeout_ms: 30000
headless: true
default_message_pack: daily
""".strip(),
        encoding="utf-8",
    )
    state = {
        "rotation": {"order": ["晚安"], "consumed": ["早安"]},
        "daily": {
            "2026-06-24": {
                "message": "早安",
                "succeeded": ["小明"],
                "failures": {"小红": "target not found"},
                "consumed": False,
            }
        },
    }
    path = tmp_path / "data" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    logs = tmp_path / "data" / "logs"
    logs.mkdir()
    (logs / "autody.log").write_text("发送成功：小明\n", encoding="utf-8")
    packs = tmp_path / "message-packs"
    packs.mkdir()
    (packs / "daily.txt").write_text("早安呀\n今天顺利\n", encoding="utf-8")
    (packs / "index.json").write_text(
        json.dumps(
            {
                "packs": [
                    {
                        "id": "daily",
                        "name": "日常",
                        "description": "日常测试包",
                        "version": "1.0.0",
                        "file": "daily.txt",
                        "count": 2,
                        "category": "daily",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path / "config.yaml"


def write_verified_account(tmp_path: Path, suffix: str = "a") -> str:
    profile_id = "account-" + suffix * 24
    data = tmp_path / "data"
    (data / "account-avatar").mkdir(parents=True, exist_ok=True)
    (data / "account-avatar" / "profile.png").write_bytes(b"avatar")
    (data / "account-profile.json").write_text(
        json.dumps(
            {
                "account_profile_id": profile_id,
                "account_id_digest": suffix * 64,
                "display_name": f"本地账号{suffix}",
                "avatar_cache_key": "profile",
                "avatar_version": "v1",
                "is_self": True,
                "verification_source": "test",
                "profile_status": "verified",
                "verified_at": "2026-07-30T08:00:00",
                "last_updated_at": "2026-07-30T08:00:00",
                "switched": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return profile_id


def test_status_returns_dashboard_summary(tmp_path: Path):
    client = TestClient(create_app(make_project(tmp_path)))
    response = client.get("/api/status?today=2026-06-24")
    assert response.status_code == 200
    data = response.json()
    assert data["today"]["succeeded"] == 1
    assert data["today"]["total"] == 2
    assert data["today"]["message"] == "早安"
    assert data["friends"][0]["status"] == "failed"
    assert data["login"]["status"] == "unknown"


def test_overview_friends_follow_the_enabled_target_set_after_batch_mutation(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[1].stable_id = "target-two"
    save_config(config_path, config)
    client = TestClient(create_app(config_path))

    before = client.get("/api/status?today=2026-06-24").json()
    cancelled = client.patch(
        "/api/friends/batch",
        json={"target_ids": ["target-one"], "action": "disable"},
    )
    after_cancel = client.get("/api/status?today=2026-06-24").json()
    restored = client.patch(
        "/api/friends/batch",
        json={"target_ids": ["target-one"], "action": "enable"},
    )
    after_restore = client.get("/api/status?today=2026-06-24").json()

    assert len(before["friends"]) == before["today"]["total"] == 2
    assert cancelled.json() == {"affected": 1}
    assert len(after_cancel["friends"]) == after_cancel["today"]["total"] == 1
    assert after_cancel["statistics"]["enabled_friend_count"] == 1
    assert [friend["target_id"] for friend in after_cancel["friends"]] == [
        "target-two"
    ]
    assert restored.json() == {"affected": 1}
    assert len(after_restore["friends"]) == after_restore["today"]["total"] == 2
    assert after_restore["statistics"]["enabled_friend_count"] == 2


def test_historical_day_statistics_ignore_later_target_configuration_changes(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["daily"]["2026-06-24"]["consumed"] = True
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    history = TaskHistoryStore(
        tmp_path / "data" / "history" / "task-runs.jsonl"
    )
    history.append(
        TaskRunRecord(
            date="2026-06-24",
            task_type="health_check",
            trigger_source="scheduled",
            start_time="2026-06-24T07:20:00",
            end_time="2026-06-24T07:20:01",
            final_status="completed",
        )
    )
    client = TestClient(create_app(config_path))

    before = client.get("/api/status?today=2026-06-24").json()["statistics"]
    config = load_config(config_path)
    config.targets = [Target(name="later-target", enabled=False)]
    save_config(config_path, config)
    after = client.get("/api/status?today=2026-06-24").json()["statistics"]

    assert before["success_rate_7d"] == 100.0
    assert after["success_rate_7d"] == before["success_rate_7d"]
    assert after["success_rate_30d"] == before["success_rate_30d"]


def test_friend_scan_status_handles_missing_optional_progress_file(tmp_path: Path):
    response = TestClient(create_app(make_project(tmp_path))).get(
        "/api/friends/scan-status"
    )

    assert response.status_code == 200
    assert response.json() == {
        "refresh_running": False,
        "stage": "completed",
        "progress": {},
        "last_result": {},
    }


def test_status_reads_legacy_state_and_skips_malformed_history_without_rewrite(
    tmp_path: Path,
):
    config = make_project(tmp_path)
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["rotation"]["next_index"] = 4
    state["rotation"]["future_optional_field"] = True
    state["daily"]["malformed-record"] = ["not", "a", "mapping"]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    before = (state_path.read_bytes(), state_path.stat().st_mtime_ns)
    history = tmp_path / "data" / "history" / "task-runs.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text("{malformed historical record\n", encoding="utf-8")

    response = TestClient(create_app(config)).get(
        "/api/status?today=2026-06-24"
    )

    assert response.status_code == 200
    assert response.json()["today"]["succeeded"] == 1
    assert (state_path.read_bytes(), state_path.stat().st_mtime_ns) == before


def test_status_degrades_when_an_optional_scheduler_section_fails(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        "autody.scheduler.windows_task_rows",
        lambda: (_ for _ in ()).throw(RuntimeError("fixture scheduler failure")),
    )

    response = TestClient(create_app(make_project(tmp_path))).get("/api/status")

    assert response.status_code == 200
    assert response.json()["scheduler"] == []
    assert response.json()["statistics"]["next_daily_send"] is None


def test_diagnose_and_repair_uses_existing_safe_repair_primitives(
    tmp_path: Path,
    monkeypatch,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    account_id = "account-" + "a" * 24
    config.targets = [
        Target(
            name="稳定目标",
            stable_id="target-stable",
            candidate_id="candidate-stable",
            binding_identity_key="row:stable-target",
            binding_identity_source="row_attribute",
            binding_account_scope=account_id,
        )
    ]
    save_config(config_path, config)
    mark_bindings_for_revalidation(config.state_file.parent)
    (tmp_path / "data" / "health.json").write_text(
        '{"status":"success"}', encoding="utf-8"
    )
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps({
            "scanned_at": "2026-08-19T08:00:00",
            "account_scope": account_id,
            "last_result": {
                "status": "completed_bottom_reached",
                "completed_bottom_reached": True,
                "partial": False,
            },
            "candidates": [{
                "candidate_id": "candidate-stable",
                "display_name": "稳定目标",
                "avatar_status": "missing",
                "discovered_at": "2026-08-19T08:00:00",
                "match_status": "configured",
                "presence_status": "current",
                "identity_key": "row:stable-target",
                "identity_source": "row_attribute",
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime_available = False
    actions: list[str] = []
    scheduler_repairs: list[bool] = []

    def run_action(action: str):
        nonlocal runtime_available
        actions.append(action)
        if action == "repair-playwright":
            runtime_available = True
        elif action == "refresh-account-profile":
            write_verified_account(tmp_path)
        return {"id": f"job-{action}", "action": action, "status": "success", "exit_code": 0}

    class RecordingSchedulerService:
        def __init__(self, _program_root, **_kwargs):
            pass

        def repair(self, _config):
            scheduler_repairs.append(True)

    monkeypatch.setattr(
        "autody.web_api._runtime_available", lambda _root: runtime_available
    )
    monkeypatch.setattr("autody.web_api.SchedulerService", RecordingSchedulerService)
    monkeypatch.setattr("autody.scheduler.windows_task_rows", lambda: [])
    client = TestClient(create_app(config_path, action_runner=run_action))

    response = client.post("/api/repair")

    assert response.status_code == 200
    payload = response.json()
    assert actions == ["repair-playwright", "refresh-account-profile"]
    assert scheduler_repairs == [True]
    assert {item["id"] for item in payload["repaired"]} == {
        "runtime", "scheduler", "account_profile", "bindings"
    }
    assert payload["manual"] == []
    assert not (tmp_path / "data" / "account-binding-state.json").exists()


def test_diagnose_and_repair_never_reassociates_an_ambiguous_binding(
    tmp_path: Path,
    monkeypatch,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(name="同名目标", stable_id="target-a", candidate_id="candidate-old")
    ]
    save_config(config_path, config)
    write_verified_account(tmp_path)
    mark_bindings_for_revalidation(config.state_file.parent)
    (tmp_path / "data" / "health.json").write_text(
        '{"status":"success"}', encoding="utf-8"
    )
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps({
            "scanned_at": "2026-08-19T08:00:00",
            "account_scope": "stable-account-scope",
            "candidates": [
                {
                    "candidate_id": "candidate-new-a",
                    "display_name": "同名目标",
                    "avatar_status": "missing",
                    "discovered_at": "2026-08-19T08:00:00",
                    "match_status": "needs_reassociation",
                    "presence_status": "current",
                },
                {
                    "candidate_id": "candidate-new-b",
                    "display_name": "同名目标",
                    "avatar_status": "missing",
                    "discovered_at": "2026-08-19T08:00:00",
                    "match_status": "ambiguous",
                    "presence_status": "current",
                },
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("autody.web_api._runtime_available", lambda _root: True)
    monkeypatch.setattr("autody.scheduler.windows_task_rows", lambda: [])
    monkeypatch.setattr(
        "autody.web_api.SchedulerService",
        type("NoopScheduler", (), {
            "__init__": lambda self, *_args, **_kwargs: None,
            "repair": lambda self, _config: None,
        }),
    )
    client = TestClient(create_app(
        config_path,
        action_runner=lambda action: {
            "id": f"job-{action}", "action": action, "status": "success", "exit_code": 0
        },
    ))

    response = client.post("/api/repair")

    assert response.status_code == 200
    payload = response.json()
    assert load_config(config_path).targets[0].candidate_id == "candidate-old"
    assert (tmp_path / "data" / "account-binding-state.json").exists()
    assert any(item["id"] == "bindings" for item in payload["manual"])
    assert all(item["id"] != "bindings" for item in payload["repaired"])


def test_scheduler_route_uses_sid_captured_from_normal_dashboard_process(
    tmp_path: Path,
    monkeypatch,
):
    task_user_id = "S-1-5-21-1000"
    captured: list[str | None] = []

    monkeypatch.setattr(
        "autody.scheduler.current_process_user_sid",
        lambda: task_user_id,
        raising=False,
    )
    monkeypatch.setattr("autody.scheduler.windows_task_rows", lambda: [])

    class RecordingSchedulerService:
        def __init__(self, _program_root, **kwargs):
            captured.append(kwargs.get("task_user_id"))

        def repair(self, _config):
            return None

    monkeypatch.setattr(
        "autody.web_api.SchedulerService", RecordingSchedulerService
    )

    response = TestClient(create_app(make_project(tmp_path))).post(
        "/api/scheduler/repair"
    )

    assert response.status_code == 200
    assert captured == [task_user_id]


def test_scheduler_write_invalidates_cached_windows_task_state(
    tmp_path: Path,
    monkeypatch,
):
    calls = 0
    installed_rows = [
        {
            "name": "AutoDy-DailySpark",
            "state": "Ready",
            "next_run": "2026-08-12T07:30:00",
            "last_run": "",
            "last_result": 0,
            "start_boundary": "2026-08-12T07:30:00",
            "repetition_interval": "PT30M",
            "repetition_duration": "PT16H29M",
        }
    ]

    def task_rows():
        nonlocal calls
        calls += 1
        return installed_rows if calls == 1 else []

    class RecordingSchedulerService:
        def __init__(self, _program_root, **_kwargs):
            pass

        def remove(self):
            return None

    monkeypatch.setattr("autody.scheduler.windows_task_rows", task_rows)
    monkeypatch.setattr(
        "autody.web_api.SchedulerService", RecordingSchedulerService
    )
    client = TestClient(create_app(make_project(tmp_path)))

    before = client.get("/api/status").json()["scheduler"]
    removed = client.post("/api/scheduler/remove")
    after = client.get("/api/status").json()["scheduler"]

    assert next(row for row in before if row["name"] == "AutoDy-DailySpark")[
        "installed"
    ] is True
    assert removed.status_code == 200
    assert all(row["installed"] is False for row in after)
    assert calls == 3


def test_scheduler_status_marks_duplicate_or_drifted_windows_tasks(tmp_path: Path):
    config = load_config(make_project(tmp_path))
    live = {
        "name": "AutoDy-DailySpark",
        "state": "Ready",
        "next_run": "2026-06-25T08:00:00",
        "last_run": "",
        "last_result": 0,
        "start_boundary": "2026-06-24T08:00:00",
    }

    rows = scheduler_status_rows(config, [live, dict(live)], 8)

    send = next(row for row in rows if row["name"] == "AutoDy-DailySpark")
    assert send["target_count"] == 8
    assert send["configured_time"] == "07:30"
    assert send["windows_time"] == "08:00"
    assert send["duplicate_count"] == 1
    assert send["drift"] is True


def test_scheduler_status_marks_missing_windows_trigger_as_drift(tmp_path: Path):
    config = load_config(make_project(tmp_path))
    live = {
        "name": "AutoDy-DailySpark",
        "state": "Ready",
        "next_run": "",
        "last_run": "",
        "last_result": None,
        "start_boundary": "",
    }

    rows = scheduler_status_rows(config, [live], 8)

    send = next(row for row in rows if row["name"] == "AutoDy-DailySpark")
    assert send["installed"] is True
    assert send["windows_time"] is None
    assert send["drift"] is True


def test_scheduler_status_rejects_task_action_from_another_runtime_root(
    tmp_path: Path,
):
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    config = load_config(make_project(data_root))
    program_root = tmp_path / "installed-program"
    wrong_root = tmp_path / "source-checkout"
    live = {
        "name": "AutoDy-DailySpark",
        "state": "Ready",
        "next_run": "2026-06-25T07:30:00",
        "last_run": "",
        "last_result": 0,
        "start_boundary": "2026-06-24T07:30:00",
        "arguments": (
            '-NoProfile -ExecutionPolicy Bypass '
            f'-File "{wrong_root / "scripts" / "run-scheduled.ps1"}" '
            f'-ProgramRoot "{wrong_root}" -DataRoot "{wrong_root}"'
        ),
        "working_directory": str(wrong_root),
    }

    rows = scheduler_status_rows(
        config,
        [live],
        8,
        program_root=program_root,
        data_root=config.state_file.parent,
    )

    send = next(row for row in rows if row["name"] == "AutoDy-DailySpark")
    assert send["drift"] is True
    assert send["drift_reason"] == "runtime_root_mismatch"
    assert "arguments" not in send
    assert "working_directory" not in send


def test_scheduler_status_rejects_non_wscript_action_with_correct_roots(
    tmp_path: Path,
):
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    config = load_config(make_project(data_root))
    program_root = tmp_path / "installed-program"
    live = {
        "name": "AutoDy-DailySpark",
        "state": "Ready",
        "next_run": "2026-06-25T07:30:00",
        "last_run": "",
        "last_result": 0,
        "start_boundary": "2026-06-24T07:30:00",
        "execute": "C:/Windows/System32/cmd.exe",
        "arguments": (
            '-NoProfile -ExecutionPolicy Bypass '
            f'-File "{program_root / "scripts" / "run-scheduled.ps1"}" '
            f'-ProgramRoot "{program_root}" -DataRoot "{config.state_file.parent}"'
        ),
        "working_directory": str(program_root),
    }

    rows = scheduler_status_rows(
        config,
        [live],
        8,
        program_root=program_root,
        data_root=config.state_file.parent,
    )

    send = next(row for row in rows if row["name"] == "AutoDy-DailySpark")
    assert send["drift"] is True
    assert send["drift_reason"] == "runtime_root_mismatch"


def test_dashboard_exposes_runtime_root_drift_as_path_free_repair_issue(
    tmp_path: Path,
    monkeypatch,
):
    data_root = tmp_path / "data-root"
    program_root = tmp_path / "installed-program"
    wrong_root = tmp_path / "source-checkout"
    data_root.mkdir()
    config_path = make_project(data_root)
    task_user_id = "S-1-5-21-1000"
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(program_root))
    monkeypatch.setattr(
        "autody.scheduler.current_process_user_sid", lambda: task_user_id
    )
    monkeypatch.setattr(
        "autody.scheduler.registered_install_roots",
        lambda: (program_root.resolve(), data_root.resolve()),
    )
    monkeypatch.setattr(
        "autody.scheduler.windows_task_rows",
        lambda: [{
            "name": "AutoDy-DailySpark",
            "state": "Ready",
            "next_run": "2026-06-25T07:30:00",
            "last_run": "",
            "last_result": 0,
            "start_boundary": "2026-06-24T07:30:00",
            "arguments": (
                '-NoProfile -ExecutionPolicy Bypass '
                f'-File "{wrong_root / "scripts" / "run-scheduled.ps1"}" '
                f'-ProgramRoot "{wrong_root}" -DataRoot "{wrong_root}"'
                ),
                "working_directory": str(wrong_root),
                "principal_user_id": task_user_id,
            }],
        )

    payload = TestClient(create_app(config_path)).get("/api/status").json()

    issue = next(
        item for item in payload["issues"]
        if item["id"] == "scheduler_runtime_mismatch"
    )
    assert issue["action"] == "scheduler"
    assert issue["action_label"] == "修复任务"
    assert str(wrong_root) not in issue["explanation"]


def test_service_identity_reports_local_runtime_without_private_browser_data(tmp_path: Path):
    client = TestClient(create_app(make_project(tmp_path)))

    response = client.get("/api/service-identity")

    assert response.status_code == 200
    data = response.json()
    assert data["application"] == "AutoDy"
    assert data["version"]
    assert data["python_executable"]
    assert data["package_path"].endswith("src\\autody") or data["package_path"].endswith("src/autody")
    assert data["frontend_static_path"].endswith("web\\static") or data["frontend_static_path"].endswith("web/static")
    assert data["bundled_module"]["module_version"] == "1.2.0"
    assert data["bundled_module"]["module_api_version"] == "1"
    assert data["bundled_module"]["required_autody_version"] == ">=1.3.0,<2.0.0"
    assert len(data["bundled_module"]["sha256"]) == 64
    assert "cookie" not in str(data).lower()
    assert "browser-profile" not in str(data).lower()


def test_startup_readiness_runs_targeted_refresh_once_and_exposes_jsonp(tmp_path: Path):
    calls = []
    client = TestClient(
        create_app(
            make_project(tmp_path),
            action_runner=lambda action: calls.append(action) or {
                "id": "startup-job",
                "action": action,
                "status": "success",
                "exit_code": 0,
            },
            startup_refresh_enabled=True,
        )
    )

    readiness = client.get("/api/startup-readiness")
    script = client.get("/api/startup-readiness.js")

    assert readiness.json()["ready"] is True
    assert readiness.json()["successful"] is True
    assert calls == ["startup-refresh"]
    assert script.headers["content-type"].startswith("application/javascript")
    assert script.text.startswith("window.autodyStartupReady(")


def test_frontend_entrypoint_is_not_cached_between_production_builds(tmp_path: Path):
    response = TestClient(create_app(make_project(tmp_path))).get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_status_reports_latest_login_health_result(tmp_path: Path):
    config = make_project(tmp_path)
    log = tmp_path / "data" / "logs" / "autody.log"
    log.write_text(
        "2026-06-24 INFO 登录状态和抖音聊天页正常。\n"
        "2026-06-25 ERROR 登录健康检查失败：需要重新登录\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(config))
    assert client.get("/api/status").json()["login"]["status"] == "failed"

    log.write_text(
        log.read_text(encoding="utf-8")
        + "2026-06-25 INFO 登录状态和抖音聊天页正常。\n",
        encoding="utf-8",
    )
    assert client.get("/api/status").json()["login"]["status"] == "success"


def test_config_and_messages_can_be_updated(tmp_path: Path):
    config = make_project(tmp_path)
    client = TestClient(create_app(config))
    response = client.put(
        "/api/config",
        json={
            "targets": ["小明", "小蓝"],
            "retry_count": 2,
            "timeout_ms": 45000,
            "headless": False,
            "message_suffix": {
                "enabled": True,
                "text": "每日问候",
                "style": "bracket",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["targets"] == ["小明", "小蓝"]
    assert response.json()["message_suffix"]["text"] == "每日问候"

    response = client.put("/api/messages", json={"messages": ["甲", "乙", "甲"]})
    assert response.status_code == 200
    assert response.json()["messages"] == ["甲", "乙"]
    assert (tmp_path / "messages.txt").read_text(encoding="utf-8") == "甲\n乙\n"


def test_preflight_routes_validate_target_ids_and_return_masked_persistence(tmp_path: Path):
    calls = []
    config = make_project(tmp_path)
    loaded = load_config(config)
    loaded.targets[0].stable_id = "target-one"; loaded.targets[0].candidate_id = "candidate-one"
    save_config(config, loaded)
    client = TestClient(create_app(config, action_runner=lambda action: calls.append(action) or {"id": "preflight-1", "action": action, "status": "running"}))

    invalid = client.post("/api/preflight/run", json={"target_ids": ["unknown"]})
    started = client.post("/api/preflight/run", json={"target_ids": ["target-one"]})

    assert invalid.status_code == 422
    assert started.status_code == 202
    assert calls == ["preflight"]
    request = json.loads((tmp_path / "data" / "preflight" / "request.json").read_text(encoding="utf-8"))
    assert request == {"target_ids": ["target-one"]}
    assert client.post("/api/preflight/cancel").json() == {"cancelled": True}


def _install_test_center(config_path: Path) -> None:
    archive = build_module_archive(config_path.parent / "AutoDy-Test-Center.autody-module.zip", version="1.1.0", core_version="1.3.0")
    ModuleManager(config_path.parent / "data", core_version="1.3.0").install(archive)


def test_official_module_status_and_generated_install_share_the_version_policy(tmp_path: Path):
    config = make_project(tmp_path)
    client = TestClient(create_app(config))

    status = client.get("/api/modules").json()["modules"][0]
    installed = client.post("/api/modules/autody-test-center/install").json()
    manifest = __import__("json").loads((tmp_path / "data" / "modules" / MODULE_ID / "manifest.json").read_text(encoding="utf-8"))

    assert status["bundled_version"] == OFFICIAL_TEST_CENTER_VERSION
    assert status["core_version"] == "1.5.0"
    assert installed["version"] == OFFICIAL_TEST_CENTER_VERSION
    assert manifest["module_version"] == OFFICIAL_TEST_CENTER_VERSION
    assert manifest["required_autody_version"] == OFFICIAL_TEST_CENTER_CORE_RANGE


def test_official_install_upgrades_same_version_when_package_hash_differs_and_preserves_data(tmp_path: Path):
    config = make_project(tmp_path)
    client = TestClient(create_app(config))
    installed = client.post("/api/modules/autody-test-center/install")
    assert installed.status_code == 200
    module_root = tmp_path / "data" / "modules" / MODULE_ID
    runtime_path = module_root / "data" / "history" / "preserved.jsonl"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_bytes(b'{"private":"runtime"}\n')
    registry_path = tmp_path / "data" / "modules" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["modules"][MODULE_ID]["package_sha256"] = "0" * 64
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    stale = client.get("/api/modules").json()["modules"][0]
    upgraded = client.post("/api/modules/autody-test-center/install")
    current = client.get("/api/modules").json()["modules"][0]

    assert stale["version"] == stale["bundled_version"] == OFFICIAL_TEST_CENTER_VERSION
    assert stale["update_available"] is True
    assert upgraded.status_code == 200
    assert current["update_available"] is False
    assert current["package_sha256"] == current["bundled_package"]["sha256"]
    assert runtime_path.read_bytes() == b'{"private":"runtime"}\n'


def test_test_center_preview_selection_is_module_local_and_never_starts_a_send_action(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[1].stable_id = "target-two"
    save_config(config_path, config)
    calls: list[str] = []
    client = TestClient(create_app(config_path, action_runner=lambda action: calls.append(action) or {"id": "job", "status": "running"}))
    assert client.post("/api/modules/autody-test-center/install").status_code == 200

    selected = client.post("/api/modules/autody-test-center/preview/select", json={"target_id": "target-one"})
    changed = client.post("/api/modules/autody-test-center/preview/select", json={"target_id": "target-two"})

    assert selected.status_code == 200
    assert selected.json()["selected_target_id"] == "target-one"
    assert selected.json()["real_composer_writes"] == 0
    assert changed.json()["selected_target_id"] == "target-two"
    assert changed.json()["navigation_status"] == "待人工查看"
    assert calls == []
    assert (tmp_path / "messages.txt").read_text(encoding="utf-8") == "早安\n晚安\n"


def test_test_center_dry_run_settings_and_status_are_module_local_and_redacted(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[0].candidate_id = "candidate-conversation-one"
    save_config(config_path, config)
    client = TestClient(create_app(config_path))
    assert client.post("/api/modules/autody-test-center/install").status_code == 200

    initial = client.get("/api/modules/autody-test-center/dry-run/status")
    invalid = client.put("/api/modules/autody-test-center/dry-run/settings", json={"typing_delay_ms": 301})
    updated = client.put("/api/modules/autody-test-center/dry-run/settings", json={"typing_delay_ms": 120})
    selected = client.post(
        "/api/modules/autody-test-center/dry-run/select",
        json={"target_id": "target-one", "request_revision": 1},
    )
    stale = client.post(
        "/api/modules/autody-test-center/dry-run/select",
        json={"target_id": "target-one", "request_revision": 1},
    )

    assert initial.status_code == 200
    assert initial.json()["settings"]["page_ready_delay_ms"] == 1500
    assert initial.json()["counters"]["send_attempts"] == 0
    assert invalid.status_code == 422
    assert updated.json()["typing_delay_ms"] == 120
    assert selected.json()["selected_target_id"] == "target-one"
    assert selected.json()["expected_conversation_id"] == "candidate-conversation-one"
    assert selected.json()["visible_conversation_id"] is None
    assert selected.json()["selected_display_name"] == config.targets[0].name
    assert selected.json()["visible_display_name"] is None
    assert selected.json()["identity_match"] is None
    assert selected.json()["identity_match_reason"] is None
    assert selected.json()["run_id"] is None
    assert selected.json()["request_revision"] == 1
    assert stale.status_code == 409
    assert client.get("/api/modules/autody-test-center/dry-run/status").json()["selected_target_id"] == "target-one"
    restarted = TestClient(create_app(config_path))
    restarted_status = restarted.get("/api/modules/autody-test-center/dry-run/status").json()
    assert restarted_status["selected_target_id"] == "target-one"
    assert restarted_status["expected_conversation_id"] == "candidate-conversation-one"
    assert restarted_status["selected_display_name"] == config.targets[0].name
    assert restarted_status["request_revision"] == 1
    files = list((tmp_path / "data" / "modules" / MODULE_ID / "data").rglob("*"))
    assert all("模块测试文本" not in path.read_text(encoding="utf-8") for path in files if path.is_file())


def test_test_center_batch_selection_defaults_to_eligible_and_prunes_stale_ids(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[0].candidate_id = "candidate-one"
    config.targets[1].stable_id = "target-two"
    config.targets[1].candidate_id = "candidate-two"
    save_config(config_path, config)
    client = TestClient(create_app(config_path))
    assert client.post("/api/modules/autody-test-center/install").status_code == 200

    initial = client.get("/api/modules/autody-test-center/dry-run/status").json()
    assert initial["settings"]["selected_batch_target_ids"] == ["target-one", "target-two"]

    saved = client.put(
        "/api/modules/autody-test-center/dry-run/settings",
        json={
            **initial["settings"],
            "selected_batch_target_ids": ["target-two", "deleted-target"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["selected_batch_target_ids"] == ["target-two"]

    config = load_config(config_path)
    config.targets[1].enabled = False
    save_config(config_path, config)
    pruned = client.get("/api/modules/autody-test-center/dry-run/status").json()
    assert pruned["settings"]["selected_batch_target_ids"] == []
    target_two = next(item for item in pruned["targets"] if item["target_id"] == "target-two")
    assert target_two["batch_eligible"] is False
    assert target_two["batch_exclusion_reason"] == "已停用"


def test_test_center_today_message_preview_is_target_specific_and_read_only(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[0].candidate_id = "conversation-one"
    config.targets[0].suffix_mode = "custom"
    config.targets[0].suffix_override = "专属后缀"
    save_config(config_path, config)
    client = TestClient(create_app(config_path))
    assert client.post("/api/modules/autody-test-center/install").status_code == 200
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    response = client.get(
        "/api/modules/autody-test-center/dry-run/message-preview",
        params={"target_id": "target-one"},
    )

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["mode"] == "today"
    assert response.json()["text"].endswith(" —— 专属后缀")
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_target_settings_and_today_plan_are_test_center_routes(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[1].stable_id = "target-two"
    save_config(config_path, config)
    before = (tmp_path / "data" / "state.json").read_bytes()
    core_client = TestClient(create_app(config_path))
    assert core_client.get("/api/today-plan?today=2026-06-24").status_code == 404
    assert core_client.put("/api/friends/target-one/settings", json={}).status_code == 404
    _install_test_center(config_path)
    client = TestClient(create_app(config_path))

    updated = client.put(
        "/api/modules/autody-test-center/targets/target-one/settings",
        json={
            "message_pack": "daily",
            "suffix_mode": "disabled",
            "delay_offset_minutes": 12,
            "message_selection": "per_friend",
            "send_order": 3,
            "note": "测试备注",
        },
    )
    plan = client.get("/api/modules/autody-test-center/today-plan?today=2026-06-24")

    assert updated.status_code == 200
    assert updated.json()["settings"]["delay_offset_minutes"] == 12
    assert load_config(config_path).targets[1].delay_offset_minutes == 0
    assert plan.status_code == 200
    first = next(item for item in plan.json()["targets"] if item["target_id"] == "target-one")
    assert first["planned_at"].endswith("07:42")
    assert first["message_source"] == "daily"
    assert first["suffix"] == "已禁用"
    assert (tmp_path / "data" / "state.json").read_bytes() == before


def test_dashboard_and_plan_count_only_enabled_targets_with_executable_bindings(
    tmp_path: Path,
    monkeypatch,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="当前有效目标",
            stable_id="target-current",
            candidate_id="candidate-current",
        ),
        Target(name="缺少绑定目标"),
    ]
    save_config(config_path, config)
    account_id = write_verified_account(tmp_path)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "candidates": [
                    {
                        "candidate_id": "candidate-current",
                        "display_name": "当前有效目标",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _install_test_center(config_path)
    task_user_id = "S-1-5-21-1000"
    monkeypatch.setattr(
        "autody.scheduler.current_process_user_sid", lambda: task_user_id
    )
    monkeypatch.setattr(
        "autody.scheduler.windows_task_rows",
        lambda: [
            {
                "name": "AutoDy-DailySpark",
                "state": "Ready",
                "next_run": "2026-06-25T07:30:00",
                    "last_run": "2026-06-24T07:30:00",
                    "last_result": 0,
                    "start_boundary": "2026-06-24T07:30:00",
                    "repetition_interval": "PT30M",
                    "repetition_duration": "PT16H29M",
                    "principal_user_id": task_user_id,
                }
            ],
        )
    client = TestClient(
        create_app(config_path, now_provider=lambda: datetime(2026, 6, 24, 8, 0))
    )

    dashboard = client.get("/api/status?today=2026-06-24").json()
    plan = client.get(
        "/api/modules/autody-test-center/today-plan?today=2026-06-24"
    ).json()

    assert dashboard["today"]["total"] == 1
    assert dashboard["statistics"]["enabled_friend_count"] == 1
    send_task = next(
        task
        for task in dashboard["scheduler"]
        if task["name"] == "AutoDy-DailySpark"
    )
    assert send_task["target_count"] == 1
    assert send_task["configured_time"] == "07:30"
    assert send_task["drift"] is False
    assert plan["enabled_target_count"] == 1
    assert plan["pending_count"] == 1
    assert plan["blocked_count"] == 1
    missing = next(
        item for item in plan["targets"] if item["display_name"] == "缺少绑定目标"
    )
    assert missing["status"] == "blocked"
    assert missing["blocked_reason"] == "目标缺少稳定绑定，需要重新关联。"


def test_failed_target_center_is_shared_by_overview_and_test_center(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[1].stable_id = "target-two"
    save_config(config_path, config)
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"小明": "composer_missing", "小红": "confirmation_failed_uncertain"}
    daily["confirmation_results"] = {"target-two": "confirmation_failed"}
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    started: list[str] = []
    core_client = TestClient(create_app(config_path, action_runner=lambda action: started.append(action) or {"id": "job", "status": "running"}))
    assert core_client.get("/api/failed-targets?today=2026-06-24").status_code == 200
    _install_test_center(config_path)
    client = TestClient(create_app(config_path, action_runner=lambda action: started.append(action) or {"id": "job", "status": "running"}))

    data = client.get("/api/modules/autody-test-center/failed-targets?today=2026-06-24").json()
    uncertain = next(item for item in data["items"] if item["target_id"] == "target-two")
    safe = next(item for item in data["items"] if item["target_id"] == "target-one")

    assert data["summary"] == {"success": 0, "failed": 2, "uncertain": 1, "needs_attention": 2}
    assert uncertain["safe_retry_available"] is False
    assert uncertain["no_send_action_definitely_occurred"] is False
    assert safe["safe_retry_available"] is False
    assert client.post("/api/modules/autody-test-center/failed-targets/target-two/retry", json={}).status_code == 409
    assert started == []


def test_status_exposes_target_failure_detail_in_overview_and_history(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[0].candidate_id = "candidate-one"
    account_id = write_verified_account(tmp_path)
    config.targets[0].binding_identity_key = "row:friend-one"
    config.targets[0].binding_identity_source = "row_attribute"
    config.targets[0].binding_account_scope = account_id
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                    "scanned_at": "2026-06-24T07:20:00",
                    "account_scope": account_id,
                    "last_result": {
                        "status": "completed_bottom_reached",
                        "completed_bottom_reached": True,
                        "partial": False,
                    },
                "candidates": [
                    {
                        "candidate_id": "candidate-one",
                        "display_name": "候选一",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                            "match_status": "configured",
                            "presence_status": "current",
                            "identity_key": "row:friend-one",
                            "identity_source": "row_attribute",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    detail = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        run_id="run-one",
        target_stable_id="target-one",
        account_scope="account-one",
        binding_valid=True,
        account_scope_matches=True,
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = ["小红"]
    daily["failures"] = {"小明": "target not found"}
    daily["target_failures"] = {
        "target-one": detail.model_dump(mode="json")
    }
    state_path.write_text(
        json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )
    TaskHistoryStore(tmp_path / "data" / "history" / "task-runs.jsonl").append(
        TaskRunRecord(
            run_id="run-one",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T07:30:00",
            end_time="2026-06-24T07:31:00",
            total_targets=2,
            success_count=1,
            failed_count=1,
            final_status="retry_pending",
            failed_target_ids=["target-one"],
            target_failures={"target-one": detail},
        )
    )

    status = TestClient(create_app(
        config_path, now_provider=lambda: datetime(2026, 6, 24, 8, 0)
    )).get(
        "/api/status?today=2026-06-24"
    ).json()

    failed_friend = next(
        item for item in status["friends"] if item["status"] == "failed"
    )
    assert failed_friend["current_health"] == {
        "status": "healthy",
        "reason_code": "binding_valid",
        "summary_zh": "绑定有效",
    }
    assert failed_friend["failure"]["stage"] == "conversation_located"
    assert failed_friend["failure"]["user_summary_zh"] == (
        "无法在当前会话列表中找到目标"
    )
    assert status["history"][0]["target_failures"]["target-one"][
        "suggested_action_zh"
    ] == "仅重试此目标"


def test_daily_summary_uses_each_targets_latest_execution_result(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[1].stable_id = "target-two"
    save_config(config_path, config)

    original_failure = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        run_id="run-morning-failure",
        target_stable_id="target-one",
        binding_valid=True,
        account_scope_matches=True,
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"小明": "conversation_not_found"}
    daily["target_failures"] = {
        "target-one": original_failure.model_dump(mode="json")
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    store = TaskHistoryStore(
        tmp_path / "data" / "history" / "task-runs.jsonl"
    )
    store.append(
        TaskRunRecord(
            run_id="run-morning-failure",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T09:00:00",
            end_time="2026-06-24T09:01:00",
            total_targets=2,
            failed_count=1,
            skipped_count=1,
            final_status="retry_pending",
            failed_target_ids=["target-one"],
            target_failures={"target-one": original_failure},
        )
    )
    store.append(
        TaskRunRecord(
            run_id="run-evening-success",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="retry",
            start_time="2026-06-24T19:30:00",
            end_time="2026-06-24T19:31:00",
            total_targets=2,
            success_count=1,
            skipped_count=1,
            final_status="completed",
            confirmation_results={"target-one": "retry_confirmed"},
        )
    )

    _install_test_center(config_path)
    client = TestClient(create_app(config_path))
    status = client.get("/api/status?today=2026-06-24").json()
    plan = client.get(
        "/api/modules/autody-test-center/today-plan?today=2026-06-24"
    )

    assert status["statistics"]["successful_today"] == 1
    assert status["statistics"]["failed_today"] == 0
    assert status["today"] == {
        "date": "2026-06-24",
        "message": "早安",
        "succeeded": 1,
        "failed": 0,
        "total": 2,
        "complete": False,
    }
    assert next(
        friend for friend in status["friends"]
        if friend["target_id"] == "target-one"
    )["status"] == "success"
    assert status["history"][1]["target_failures"]["target-one"][
        "resolved"
    ] is True
    assert plan.status_code == 200
    assert plan.json()["completed_count"] == 1
    assert plan.json()["pending_count"] == 1
    assert plan.json()["blocked_count"] == 0


def test_daily_summary_keeps_confirmed_success_after_later_recovery_failure(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    save_config(config_path, config)

    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"小明": "conversation_not_found"}
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    later_failure = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        run_id="run-recovery-failure",
        target_stable_id="target-one",
        binding_valid=True,
        account_scope_matches=True,
    )
    store = TaskHistoryStore(
        tmp_path / "data" / "history" / "task-runs.jsonl"
    )
    store.append(
        TaskRunRecord(
            run_id="run-morning-success",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T07:30:00",
            end_time="2026-06-24T07:31:00",
            total_targets=2,
            success_count=1,
            final_status="completed",
            confirmation_results={"target-one": "confirmed"},
        )
    )
    store.append(
        TaskRunRecord(
            run_id="run-recovery-failure",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="startup_recovery",
            start_time="2026-06-24T14:20:00",
            end_time="2026-06-24T14:21:00",
            total_targets=2,
            failed_count=1,
            final_status="retry_pending",
            failed_target_ids=["target-one"],
            target_failures={"target-one": later_failure},
        )
    )

    client = TestClient(create_app(config_path))
    status = client.get("/api/status?today=2026-06-24").json()
    failed_targets = client.get(
        "/api/failed-targets?today=2026-06-24"
    ).json()
    friends = client.get("/api/friends?today=2026-06-24").json()["friends"]

    assert status["statistics"]["successful_today"] == 1
    assert status["statistics"]["failed_today"] == 0
    assert next(
        friend for friend in status["friends"]
        if friend["target_id"] == "target-one"
    )["status"] == "success"
    assert all(
        item["target_id"] != "target-one" for item in failed_targets["items"]
    )
    assert failed_targets["summary"]["success"] == 1
    assert next(
        friend for friend in friends if friend["target_id"] == "target-one"
    )["today_status"] == "success"


def test_daily_summary_keeps_failure_when_day_has_no_confirmed_success(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    save_config(config_path, config)

    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"小明": "conversation_not_found"}
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    failure = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        run_id="run-failure",
        target_stable_id="target-one",
        binding_valid=True,
        account_scope_matches=True,
    )
    TaskHistoryStore(
        tmp_path / "data" / "history" / "task-runs.jsonl"
    ).append(
        TaskRunRecord(
            run_id="run-failure",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T07:30:00",
            end_time="2026-06-24T07:31:00",
            total_targets=2,
            failed_count=1,
            final_status="retry_pending",
            failed_target_ids=["target-one"],
            target_failures={"target-one": failure},
        )
    )

    status = TestClient(create_app(config_path)).get(
        "/api/status?today=2026-06-24"
    ).json()

    assert status["statistics"]["successful_today"] == 0
    assert status["statistics"]["failed_today"] == 1


def test_daily_summary_ignores_unmatched_deleted_target_confirmation(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name=f"当前目标 {index}",
            stable_id=f"target-{index}",
            candidate_id=f"candidate-{index}",
        )
        for index in range(1, 9)
    ]
    save_config(config_path, config)
    state_path = tmp_path / "data" / "state.json"
    state_path.write_text(
        json.dumps({"rotation": {}, "daily": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    history_path = tmp_path / "data" / "history" / "task-runs.jsonl"
    TaskHistoryStore(history_path).append(
        TaskRunRecord(
            run_id="run-nine-confirmed",
            date="2026-08-09",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-08-09T07:30:00",
            end_time="2026-08-09T07:31:00",
            total_targets=9,
            success_count=9,
            final_status="completed",
            confirmation_results={
                **{f"target-{index}": "confirmed" for index in range(1, 9)},
                "target-deleted": "confirmed",
            },
        )
    )
    before = history_path.read_bytes()

    payload = TestClient(create_app(config_path)).get(
        "/api/status?today=2026-08-09"
    ).json()

    assert payload["today"] == {
        "date": "2026-08-09",
        "message": "",
        "succeeded": 8,
        "failed": 0,
        "total": 8,
        "complete": True,
    }
    assert payload["statistics"]["successful_today"] == 8
    assert payload["statistics"]["failed_today"] == 0
    assert history_path.read_bytes() == before


def test_daily_summary_uses_target_day_history_beyond_recent_dashboard_page(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    save_config(config_path, config)

    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"小明": "conversation_not_found"}
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    store = TaskHistoryStore(
        tmp_path / "data" / "history" / "task-runs.jsonl"
    )
    store.append(
        TaskRunRecord(
            run_id="run-retry-success",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="retry",
            start_time="2026-06-24T19:30:00",
            end_time="2026-06-24T19:31:00",
            total_targets=2,
            success_count=1,
            skipped_count=1,
            final_status="completed",
            confirmation_results={"target-one": "retry_confirmed"},
        )
    )
    for index in range(31):
        store.append(
            TaskRunRecord(
                run_id=f"later-health-check-{index}",
                date="2026-06-25",
                task_type="health_check",
                trigger_source="scheduled",
                start_time="2026-06-25T08:00:00",
                end_time="2026-06-25T08:00:01",
                total_targets=0,
                final_status="completed",
            )
        )

    status = TestClient(create_app(config_path)).get(
        "/api/status?today=2026-06-24"
    ).json()

    assert status["statistics"]["successful_today"] == 1
    assert status["statistics"]["failed_today"] == 0


def test_status_current_health_rejects_missing_binding_and_unavailable_snapshot(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [Target(name="缺少绑定")]
    save_config(config_path, config)

    missing = TestClient(create_app(config_path)).get(
        "/api/status?today=2026-06-24"
    ).json()["friends"][0]["current_health"]

    assert missing == {
        "status": "abnormal",
        "reason_code": "binding_missing",
        "summary_zh": "目标缺少权威稳定绑定，需要重新关联",
    }

    config.targets[0].stable_id = "target-one"
    config.targets[0].candidate_id = "candidate-one"
    save_config(config_path, config)
    unavailable = TestClient(create_app(config_path)).get(
        "/api/status?today=2026-06-24"
    ).json()["friends"][0]["current_health"]

    assert unavailable == {
        "status": "unknown",
        "reason_code": "scan_unavailable",
        "summary_zh": "当前扫描不可用",
    }


def test_status_current_health_rejects_ambiguous_candidate_and_account_mismatch(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="当前绑定",
            stable_id="target-one",
            candidate_id="candidate-one",
            binding_identity_key="row:friend-one",
            binding_identity_source="row_attribute",
            binding_account_scope="account-" + "a" * 24,
        )
    ]
    save_config(config_path, config)
    account_id = write_verified_account(tmp_path)
    snapshot = {
        "scanned_at": "2026-06-24T07:20:00",
        "account_scope": account_id,
        "last_result": {
            "status": "completed_bottom_reached",
            "completed_bottom_reached": True,
            "partial": False,
        },
        "candidates": [
            {
                "candidate_id": "candidate-one",
                "display_name": "当前绑定",
                "avatar_status": "missing",
                "discovered_at": "2026-06-24T07:20:00",
                "match_status": "ambiguous",
                "presence_status": "current",
                "identity_key": "row:friend-one",
                "identity_source": "row_attribute",
            },
            {
                "candidate_id": "candidate-duplicate",
                "display_name": "当前绑定",
                "avatar_status": "missing",
                "discovered_at": "2026-06-24T07:20:00",
                "match_status": "ambiguous",
                "presence_status": "current",
                "identity_key": "row:friend-one",
                "identity_source": "row_attribute",
            }
        ],
    }
    path = tmp_path / "data" / "discovered_friends.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    ambiguous = TestClient(create_app(
        config_path, now_provider=lambda: datetime(2026, 6, 24, 8, 0)
    )).get(
        "/api/status?today=2026-06-24"
    ).json()["friends"][0]["current_health"]

    assert ambiguous == {
        "status": "abnormal",
        "reason_code": "identity_ambiguous",
        "summary_zh": "当前候选身份存在歧义，未自动关联",
    }

    snapshot["candidates"][0]["match_status"] = "configured"
    snapshot["account_scope"] = "account-" + "b" * 24
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    mismatch = TestClient(create_app(
        config_path, now_provider=lambda: datetime(2026, 6, 24, 8, 0)
    )).get(
        "/api/status?today=2026-06-24"
    ).json()["friends"][0]["current_health"]

    assert mismatch == {
        "status": "abnormal",
        "reason_code": "account_scope_mismatch",
        "summary_zh": "当前登录账号与目标所属账号不一致",
    }


def test_status_current_health_does_not_trust_cache_after_latest_scan_failed(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="当前绑定",
            stable_id="target-one",
            candidate_id="candidate-one",
        )
    ]
    save_config(config_path, config)
    account_id = write_verified_account(tmp_path)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "failed",
                    "finished_at": "2026-06-24T07:21:00",
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-one",
                        "display_name": "当前绑定",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    health = TestClient(
        create_app(config_path, now_provider=lambda: datetime(2026, 6, 24, 8, 0))
    ).get("/api/status?today=2026-06-24").json()["friends"][0][
        "current_health"
    ]

    assert health == {
        "status": "unknown",
        "reason_code": "scan_failed",
        "summary_zh": "最近一次实时扫描失败",
    }


def test_status_enriches_matching_legacy_history_with_stable_target_failure(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[0].candidate_id = "candidate-one"
    account_id = write_verified_account(tmp_path)
    config.targets[0].binding_identity_key = "participant:friend-one"
    config.targets[0].binding_identity_source = "participant_sec_user_id"
    config.targets[0].binding_account_scope = account_id
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-one",
                        "display_name": "候选一",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-one",
                        "identity_source": "participant_sec_user_id",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["task_run_id"] = "run-legacy-detail"
    daily["succeeded"] = ["小红"]
    daily["failures"] = {"小明": "target not found"}
    daily.pop("target_failures", None)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    history_store = TaskHistoryStore(
        tmp_path / "data" / "history" / "task-runs.jsonl"
    )
    history_store.append(
        TaskRunRecord(
            run_id="run-legacy-detail",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T07:30:00",
            end_time="2026-06-24T07:30:30",
            total_targets=2,
            success_count=1,
            failed_count=1,
            final_status="retry_pending",
        )
    )
    history_store.append(
        TaskRunRecord(
            run_id="run-legacy-detail",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T07:30:00",
            end_time="2026-06-24T07:31:00",
            total_targets=2,
            success_count=1,
            failed_count=1,
            final_status="partial_failed",
            failed_target_ids=["target-one"],
        )
    )

    status = TestClient(create_app(config_path)).get(
        "/api/status?today=2026-06-24"
    ).json()

    failures = status["history"][0]["target_failures"]
    assert list(failures) == ["target-one"]
    assert failures["target-one"]["stage"] == "conversation_located"
    assert failures["target-one"]["user_summary_zh"] == (
        "无法在当前会话列表中找到目标"
    )
    assert failures["target-one"]["suggested_action_zh"] == "仅重试此目标"


def test_status_marks_only_authoritatively_resolved_target_failures(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[1].stable_id = "target-two"
    save_config(config_path, config)
    store = TaskHistoryStore(
        tmp_path / "data" / "history" / "task-runs.jsonl"
    )
    target_one_failure = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        run_id="run-failed",
        target_stable_id="target-one",
        binding_valid=True,
        account_scope_matches=True,
    )
    target_two_failure = target_one_failure.model_copy(
        update={"target_stable_id": "target-two"}
    )
    store.append(
        TaskRunRecord(
            run_id="run-failed",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T07:30:00",
            end_time="2026-06-24T07:31:00",
            total_targets=2,
            failed_count=2,
            final_status="retry_pending",
            failed_target_ids=["target-one", "target-two"],
            target_failures={
                "target-one": target_one_failure,
                "target-two": target_two_failure,
            },
        )
    )
    store.append(
        TaskRunRecord(
            run_id="run-success",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="retry",
            start_time="2026-06-24T08:00:00",
            end_time="2026-06-24T08:01:00",
            total_targets=1,
            success_count=1,
            final_status="completed",
            confirmation_results={"target-one": "retry_confirmed"},
        )
    )

    client = TestClient(create_app(config_path))
    first_status = client.get("/api/status?today=2026-06-24").json()
    first_failures = first_status["history"][1]["target_failures"]

    assert first_failures["target-one"]["resolved"] is True
    assert first_failures["target-one"]["resolved_at"] == (
        "2026-06-24T08:01:00"
    )
    assert first_failures["target-one"]["resolution_zh"] == (
        "已通过后续成功补发解决"
    )
    assert first_failures["target-one"]["retry_action_available"] is False
    assert first_failures["target-two"]["resolved"] is False
    assert first_failures["target-two"]["retry_action_available"] is True

    newer_failure = target_one_failure.model_copy(
        update={
            "run_id": "run-newer-failure",
            "timestamp": "2026-06-24T09:00:00",
        }
    )
    store.append(
        TaskRunRecord(
            run_id="run-newer-failure",
            date="2026-06-24",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-06-24T09:00:00",
            end_time="2026-06-24T09:01:00",
            total_targets=1,
            failed_count=1,
            final_status="retry_pending",
            failed_target_ids=["target-one"],
            target_failures={"target-one": newer_failure},
        )
    )

    second_status = client.get("/api/status?today=2026-06-24").json()
    target_one_views = [
        row["target_failures"]["target-one"]
        for row in second_status["history"]
        if "target-one" in row["target_failures"]
    ]

    assert len(target_one_views) == 2
    assert all(item["resolved"] is True for item in target_one_views)
    assert all(
        item["retry_action_available"] is False
        for item in target_one_views
    )


def test_status_uses_target_failure_instead_of_legacy_technical_notification(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].stable_id = "target-one"
    config.targets[0].candidate_id = "candidate-one"
    account_id = write_verified_account(tmp_path)
    config.targets[0].binding_identity_key = "participant:friend-one"
    config.targets[0].binding_identity_source = "participant_sec_user_id"
    config.targets[0].binding_account_scope = account_id
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-one",
                        "display_name": "候选一",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-one",
                        "identity_source": "participant_sec_user_id",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = ["小红"]
    daily["failures"] = {"小明": "target not found"}
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    notice = tmp_path / "data" / "notifications" / "need-attention.txt"
    notice.parent.mkdir(parents=True)
    notice.write_text(
        "AutoDy 每日发送任务失败。\n退出码：3\n请查看：C:\\private\\scheduler.log",
        encoding="utf-8",
    )

    status = TestClient(
        create_app(config_path, now_provider=lambda: datetime(2026, 6, 24, 8, 0))
    ).get(
        "/api/status?today=2026-06-24"
    ).json()

    issue = next(
        item for item in status["issues"] if item["id"] == "send_failure_retryable"
    )
    assert "无法在当前会话列表中找到目标" in issue["explanation"]
    assert issue["action"] == "retry_target"
    assert "退出码" not in issue["explanation"]
    assert "scheduler.log" not in issue["explanation"]


def test_overview_retry_endpoint_starts_only_the_current_safe_target(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="安全失败目标",
            stable_id="target-one",
            candidate_id="candidate-one",
        ),
        Target(
            name="不确定目标",
            stable_id="target-two",
            candidate_id="candidate-two",
        ),
    ]
    account_id = write_verified_account(tmp_path)
    for target, identity_key in zip(
        config.targets,
        ["participant:friend-one", "participant:friend-two"],
        strict=True,
    ):
        target.binding_identity_key = identity_key
        target.binding_identity_source = "participant_sec_user_id"
        target.binding_account_scope = account_id
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-one",
                        "display_name": "候选一",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-one",
                        "identity_source": "participant_sec_user_id",
                    },
                    {
                        "candidate_id": "candidate-two",
                        "display_name": "候选二",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-two",
                        "identity_source": "participant_sec_user_id",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {
        "安全失败目标": "target not found",
        "不确定目标": "confirmation_failed_uncertain",
    }
    daily["target_failures"] = {
        "target-one": failure_detail(
            "conversation_not_found",
            stage="conversation_located",
            send_attempts=0,
            run_id="run-one",
            target_stable_id="target-one",
            binding_valid=True,
            account_scope_matches=True,
        ).model_dump(mode="json"),
        "target-two": failure_detail(
            "confirmation_failed_uncertain",
            stage="confirmation_observed",
            send_attempts=1,
            run_id="run-one",
            target_stable_id="target-two",
            binding_valid=True,
            account_scope_matches=True,
        ).model_dump(mode="json"),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    started = []

    def start_action(action: str, **kwargs):
        started.append((action, kwargs))
        return {"id": "job-target", "action": action, "status": "running"}

    client = TestClient(
        create_app(
            config_path,
            action_runner=start_action,
            now_provider=lambda: datetime(2026, 6, 24, 8, 0),
        )
    )

    failures = client.get("/api/failed-targets?today=2026-06-24")
    retry = client.post(
        "/api/failed-targets/target-one/retry?today=2026-06-24",
        json={},
    )
    unsafe = client.post(
        "/api/failed-targets/target-two/retry?today=2026-06-24",
        json={},
    )

    assert failures.status_code == 200
    safe = next(
        item
        for item in failures.json()["items"]
        if item["target_id"] == "target-one"
    )
    assert safe["safe_retry_available"] is True
    assert retry.status_code == 202
    assert started == [("run-target", {"target_id": "target-one"})]
    assert unsafe.status_code == 409


def test_failed_target_rechecks_binding_before_offering_retry(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="绑定已过期目标",
            stable_id="target-one",
            candidate_id="candidate-old",
        )
    ]
    save_config(config_path, config)
    account_id = write_verified_account(tmp_path)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "candidates": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"绑定已过期目标": "target not found"}
    daily["target_failures"] = {
        "target-one": failure_detail(
            "conversation_not_found",
            stage="conversation_located",
            run_id="run-one",
            target_stable_id="target-one",
            binding_valid=True,
            account_scope_matches=True,
        ).model_dump(mode="json")
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    item = TestClient(create_app(config_path)).get(
        "/api/failed-targets?today=2026-06-24"
    ).json()["items"][0]

    assert item["safe_retry_available"] is False
    assert item["reason_code"] == "binding_stale"
    assert item["suggested_action"] == "reassociate"


def test_status_routes_a_non_retryable_stale_binding_to_friend_management(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="绑定已过期目标",
            stable_id="target-one",
            candidate_id="candidate-old",
        )
    ]
    save_config(config_path, config)
    account_id = write_verified_account(tmp_path)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "candidates": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"绑定已过期目标": "target not found"}
    daily["target_failures"] = {
        "target-one": failure_detail(
            "conversation_not_found",
            stage="conversation_located",
            run_id="run-one",
            target_stable_id="target-one",
            binding_valid=True,
            account_scope_matches=True,
        ).model_dump(mode="json")
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    status = TestClient(create_app(config_path)).get(
        "/api/status?today=2026-06-24"
    ).json()

    issue = next(
        item
        for item in status["issues"]
        if item["id"] == "send_failure_reassociate"
    )
    assert issue["action"] == "friends"
    assert issue["action_label"] == "重新关联"
    assert "重新关联" in issue["explanation"]


def test_status_keeps_healthy_binding_and_routes_current_failure_from_detail(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    account_id = write_verified_account(tmp_path)
    config.targets = [
        Target(
            name="当前失败目标",
            stable_id="target-current",
            candidate_id="candidate-current",
            binding_identity_key="row:friend-current",
            binding_identity_source="row_attribute",
            binding_account_scope=account_id,
        )
    ]
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-current",
                        "display_name": "当前缓存名称",
                        "avatar_cache_key": "candidate-current",
                        "avatar_status": "cached",
                        "avatar_updated_at": "2026-06-24T07:20:00",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "row:friend-current",
                        "identity_source": "row_attribute",
                        "conversation_id": "candidate-conversation-current",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    avatar_cache = tmp_path / "data" / "avatar-cache"
    avatar_cache.mkdir(parents=True)
    (avatar_cache / "candidate-current.png").write_bytes(b"avatar")
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"当前失败目标": "conversation not found"}
    daily["target_failures"] = {
        "target-current": failure_detail(
            "conversation_not_found",
            stage="conversation_located",
            send_attempts=0,
            run_id="run-current",
            target_stable_id="target-current",
            binding_valid=True,
            account_scope_matches=True,
        ).model_dump(mode="json")
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    status = TestClient(
        create_app(
            config_path,
            now_provider=lambda: datetime(2026, 6, 24, 8, 0, 0),
        )
    ).get(
        "/api/status?today=2026-06-24"
    ).json()

    friend = status["friends"][0]
    issue = next(
        item
        for item in status["issues"]
        if item["id"] == "send_failure_retryable"
    )
    assert friend["current_health"]["status"] == "healthy"
    assert friend["status"] == "failed"
    assert friend["name"] == "当前缓存名称"
    assert friend["avatar_url"].startswith("/api/avatars/candidate-current")
    assert issue["action"] == "retry_target"
    assert issue["target_ids"] == ["target-current"]


def test_current_digest_scope_restores_retry_from_legacy_mismatch_and_key(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="历史失败目标",
            stable_id="target-current",
            candidate_id="candidate-current",
            binding_identity_key="participant:friend-current",
            binding_identity_source="participant_sec_user_id",
            binding_account_scope="a" * 64,
        )
    ]
    save_config(config_path, config)
    write_verified_account(tmp_path)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": "a" * 64,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-current",
                        "display_name": "历史失败目标",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-current",
                        "identity_source": "participant_sec_user_id",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"历史失败目标": "account_scope_mismatch"}
    daily["target_failures"] = {
        "pre-migration-key": failure_detail(
            "account_scope_mismatch",
            stage="account_verified",
            send_attempts=0,
            run_id="legacy-run",
            target_stable_id="pre-migration-key",
            account_scope="legacy-run-scope",
            binding_valid=False,
            account_scope_matches=False,
        ).model_dump(mode="json")
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    started = []

    def start_action(action: str, **kwargs):
        started.append((action, kwargs))
        return {"id": "job-target", "action": action, "status": "running"}

    client = TestClient(
        create_app(
            config_path,
            action_runner=start_action,
            now_provider=lambda: datetime(2026, 6, 24, 8, 0),
        )
    )
    status = client.get("/api/status?today=2026-06-24").json()
    failed_friend = next(item for item in status["friends"] if item["status"] == "failed")
    retry = client.post(
        "/api/failed-targets/target-current/retry?today=2026-06-24",
        json={},
    )

    failure = failed_friend["failure"]
    assert failure["reason_code"] == "send_failed_before_action"
    assert failure["safe_retry_available"] is True
    assert failure["suggested_action"] == "retry"
    assert retry.status_code == 202
    assert started == [("run-target", {"target_id": "target-current"})]


def test_current_scope_revalidation_preserves_genuine_account_mismatch(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    account_id = write_verified_account(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="其他账号目标",
            stable_id="target-current",
            candidate_id="candidate-current",
            binding_identity_key="participant:friend-current",
            binding_identity_source="participant_sec_user_id",
            binding_account_scope=account_id,
        )
    ]
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-06-24T07:20:00",
                "account_scope": "account-" + "b" * 24,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-current",
                        "display_name": "其他账号目标",
                        "avatar_status": "missing",
                        "discovered_at": "2026-06-24T07:20:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-current",
                        "identity_source": "participant_sec_user_id",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    daily = state["daily"]["2026-06-24"]
    daily["succeeded"] = []
    daily["failures"] = {"其他账号目标": "account_scope_mismatch"}
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    failure = TestClient(
        create_app(config_path, now_provider=lambda: datetime(2026, 6, 24, 8, 0))
    ).get(
        "/api/status?today=2026-06-24"
    ).json()["friends"][0]["failure"]

    assert failure["reason_code"] == "account_scope_mismatch"
    assert failure["suggested_action"] == "switch_account"
    assert failure["diagnostic_details"]["account_comparison"] == (
        "binding_scope_matches_neither_current_namespace"
    )


def test_preflight_status_exposes_masked_progress(tmp_path: Path):
    config = make_project(tmp_path)
    store = PreflightStore(tmp_path / "data" / "preflight")
    store.save_progress({"running": True, "completed_targets": 2, "total_targets": 3, "current_status": "ready"})

    response = TestClient(create_app(config)).get("/api/preflight/status")

    assert response.status_code == 200
    assert response.json()["progress"] == {
        "running": True, "completed_targets": 2, "total_targets": 3, "current_status": "ready"
    }


def test_log_retention_config_validation_and_cleanup_api(tmp_path: Path):
    config = make_project(tmp_path)
    client = TestClient(create_app(config))
    logs = tmp_path / "data" / "logs"
    old = logs / "autody-2026-06-01.log"; old.write_text("old", encoding="utf-8")
    protected = tmp_path / "data" / "history" / "task-runs.jsonl"
    protected.parent.mkdir(parents=True); protected.write_text("protected", encoding="utf-8")

    payload = client.get("/api/config").json()
    payload.update({"active_log_retention_days": 14, "archive_log_retention_days": 90, "log_cleanup_enabled": True})
    assert client.put("/api/config", json=payload).status_code == 200
    payload["active_log_retention_days"] = 2
    assert client.put("/api/config", json=payload).status_code == 422
    payload["active_log_retention_days"] = 14
    payload["archive_log_retention_days"] = 3
    assert client.put("/api/config", json=payload).status_code == 422
    assert client.get("/api/logs/cleanup").status_code == 404

    summary = client.get("/api/logs/storage-summary")
    preview = client.post("/api/logs/cleanup-preview")
    rejected = client.post("/api/logs/cleanup", json={})

    assert summary.status_code == 200
    assert summary.json()["active_files"] >= 1
    assert preview.json()["to_archive"] == 1
    assert old.exists()
    assert rejected.status_code == 422
    applied = client.post("/api/logs/cleanup", json={"confirmed": True})
    assert applied.json()["archived"] == 1
    assert client.get("/api/logs/storage-summary").json()["last_cleanup_result"]["archived"] == 1
    assert protected.read_text(encoding="utf-8") == "protected"


def test_logs_and_backup_exclude_browser_profile(tmp_path: Path):
    config = make_project(tmp_path)
    profile = tmp_path / "data" / "browser-profile"
    profile.mkdir(parents=True)
    (profile / "secret.cookie").write_text("secret", encoding="utf-8")
    client = TestClient(create_app(config))
    (tmp_path / "data" / "logs" / "scheduler-2026-07-29.log").write_text(
        "较早调度记录\n", encoding="utf-8"
    )
    (tmp_path / "data" / "logs" / "scheduler-2026-07-30.log").write_text(
        "最新调度记录\n", encoding="utf-8"
    )

    logs = client.get("/api/logs").json()
    assert "发送成功" in logs["application"]
    assert logs["scheduler"].splitlines() == ["较早调度记录", "最新调度记录"]

    response = client.get("/api/backup")
    assert response.status_code == 200
    archive = zipfile.ZipFile(BytesIO(response.content))
    assert set(archive.namelist()) == {
        "config.yaml",
        "messages.txt",
        "manifest.json",
        "state.json",
    }
    assert "secret.cookie" not in "\n".join(archive.namelist())


def test_avatar_route_uses_cached_file_or_local_fallback_and_rejects_unsafe_ids(
    tmp_path: Path,
):
    config = make_project(tmp_path)
    cache = tmp_path / "data" / "avatar-cache"
    cache.mkdir(parents=True)
    (cache / "friend-safe.png").write_bytes(b"cached-avatar")
    client = TestClient(create_app(config))

    cached = client.get("/api/avatars/friend-safe")
    fallback = client.get("/api/avatars/friend-missing")
    unsafe = client.get("/api/avatars/friend.safe")

    assert cached.status_code == 200
    assert cached.content == b"cached-avatar"
    assert cached.headers["cache-control"] == "private, max-age=86400, immutable"
    assert cached.headers["etag"]
    assert cached.headers["last-modified"]
    assert fallback.status_code == 200
    assert fallback.headers["content-type"].startswith("image/svg+xml")
    assert fallback.headers["cache-control"] == "private, max-age=300"
    assert unsafe.status_code == 404


def test_account_profile_api_uses_local_avatar_and_never_exposes_private_identifiers(tmp_path: Path):
    config = make_project(tmp_path)
    account_dir = tmp_path / "data" / "account-avatar"
    account_dir.mkdir()
    (account_dir / "profile.png").write_bytes(b"profile-image")
    (tmp_path / "data" / "account-profile.json").write_text(
        json.dumps({
            "account_profile_id": "account-digest", "account_id_digest": "private-raw-id",
            "display_name": "本人", "avatar_cache_key": "profile", "avatar_version": "version",
            "is_self": True, "verification_source": "bootstrap_current_login_user",
            "profile_status": "verified", "verified_at": "2026-07-15T08:00:00",
            "last_updated_at": "2026-07-15T08:00:00", "switched": False,
            "avatar_source": "https://remote-image",
        }, ensure_ascii=False), encoding="utf-8",
    )
    client = TestClient(create_app(config))

    payload = client.get("/api/account-profile").json()
    avatar = client.get("/api/account-profile/avatar")

    assert payload["display_name"] == "本人"
    assert payload["avatar_url"] == "/api/account-profile/avatar?v=version"
    assert payload["is_self"] is True
    assert "private-raw-id" not in json.dumps(payload, ensure_ascii=False)
    assert "remote-image" not in json.dumps(payload, ensure_ascii=False)
    assert avatar.content == b"profile-image"


def test_openapi_registers_all_account_profile_routes(tmp_path: Path):
    paths = TestClient(create_app(make_project(tmp_path))).get("/openapi.json").json()["paths"]

    assert "get" in paths["/api/account-profile"]
    assert "post" in paths["/api/account-profile/refresh"]
    assert "get" in paths["/api/account-profile/avatar"]


def test_diagnostic_export_explicitly_excludes_avatar_cache(tmp_path: Path):
    client = TestClient(create_app(make_project(tmp_path)))

    response = client.get("/api/logs/diagnostic-export")
    archive = zipfile.ZipFile(BytesIO(response.content))
    manifest = json.loads(archive.read("manifest.json"))

    assert "avatar-cache" in manifest["excludes"]
    assert "discovered_friends.json" in manifest["excludes"]
    assert not any("avatar" in name for name in archive.namelist())


def test_history_endpoint_filters_structured_runs(tmp_path: Path):
    config = make_project(tmp_path)
    store = TaskHistoryStore(tmp_path / "data" / "history" / "task-runs.jsonl")
    store.append(
        TaskRunRecord(
            run_id="partial",
            date="2026-07-13",
            task_type="daily_send",
            trigger_source="scheduled",
            start_time="2026-07-13T07:30:00",
            end_time="2026-07-13T07:31:00",
            final_status="partial_failed",
        )
    )
    client = TestClient(create_app(config))

    response = client.get("/api/history?status_filter=partial_failed").json()

    assert response["total"] == 1
    assert response["items"][0]["run_id"] == "partial"


def test_backup_import_validates_and_restores(tmp_path: Path):
    config = make_project(tmp_path)
    client = TestClient(create_app(config))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "config.yaml",
            "targets:\n  - name: 恢复好友\nmessages_file: messages.txt\n",
        )
        archive.writestr("messages.txt", "恢复文案\n")
        archive.writestr("manifest.json", '{"format":"autody-backup","version":1}')
    response = client.post(
        "/api/backup/import",
        files={"file": ("backup.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200
    assert response.json()["targets"] == ["恢复好友"]
    assert (tmp_path / "messages.txt").read_text(encoding="utf-8") == "恢复文案\n"


def test_actions_are_started_and_reported(tmp_path: Path):
    calls = []

    def runner(action: str):
        calls.append(action)
        return {"id": "job-1", "action": action, "status": "running"}

    client = TestClient(create_app(make_project(tmp_path), action_runner=runner))
    response = client.post("/api/actions/run")
    assert response.status_code == 202
    assert response.json()["id"] == "job-1"
    assert calls == ["run"]


def test_message_pack_list_preview_and_merge_import(tmp_path: Path):
    client = TestClient(create_app(make_project(tmp_path)))

    catalog = client.get("/api/message-packs")
    assert catalog.status_code == 200
    assert catalog.json()["packs"][0]["id"] == "daily"
    assert set(catalog.json()) == {"packs", "revision"}

    preview = client.get("/api/message-packs/daily")
    assert preview.status_code == 200
    assert preview.json()["messages"] == ["早安呀", "今天顺利"]

    imported = client.post(
        "/api/message-packs/daily/import", json={"mode": "merge"}
    )
    assert imported.status_code == 200
    assert imported.json()["added_count"] == 2
    assert imported.json()["total_count"] == 4
    assert imported.json()["backup_path"].endswith(".txt")
    assert "早安呀" in (tmp_path / "messages.txt").read_text(encoding="utf-8")


def test_message_pack_management_api_creates_renames_and_reorders(tmp_path: Path):
    client = TestClient(create_app(make_project(tmp_path)))
    catalog = client.get("/api/message-packs").json()

    created = client.post(
        "/api/message-packs",
        json={"expected_revision": catalog["revision"]},
    )
    renamed = client.patch(
        f"/api/message-packs/{created.json()['pack']['id']}",
        json={
            "name": "晨间",
            "expected_revision": created.json()["revision"],
        },
    )
    ids = [pack["id"] for pack in renamed.json()["catalog"]["packs"]]
    reordered = client.put(
        "/api/message-packs/order",
        json={
            "pack_ids": list(reversed(ids)),
            "expected_revision": renamed.json()["revision"],
        },
    )

    assert created.status_code == 200
    assert renamed.status_code == 200
    assert renamed.json()["pack"]["id"] == created.json()["pack"]["id"]
    assert renamed.json()["pack"]["name"] == "晨间"
    assert reordered.status_code == 200
    assert [
        pack["id"] for pack in reordered.json()["catalog"]["packs"]
    ] == list(reversed(ids))


def test_message_pack_management_api_rejects_stale_revision(tmp_path: Path):
    client = TestClient(create_app(make_project(tmp_path)))
    revision = client.get("/api/message-packs").json()["revision"]
    assert client.post(
        "/api/message-packs",
        json={"expected_revision": revision},
    ).status_code == 200

    stale = client.post(
        "/api/message-packs",
        json={"expected_revision": revision},
    )

    assert stale.status_code == 409
    assert "刷新" in stale.json()["detail"]


def test_message_pack_management_api_imports_edits_fuses_and_splits(tmp_path: Path):
    config_path = make_project(tmp_path)
    client = TestClient(create_app(config_path))
    revision = client.get("/api/message-packs").json()["revision"]
    imported = client.post(
        "/api/message-packs/import",
        data={"expected_revision": str(revision)},
        files={"file": ("morning.txt", "晨间\n早安\n早安\n", "text/plain")},
    )
    pack_id = imported.json()["pack"]["id"]
    added = client.post(
        f"/api/message-packs/{pack_id}/messages",
        json={
            "text": "新增",
            "expected_revision": imported.json()["revision"],
        },
    )
    message_id = added.json()["entry"]["id"]
    edited = client.patch(
        f"/api/message-packs/{pack_id}/messages/{message_id}",
        json={
            "text": "已编辑",
            "expected_revision": added.json()["revision"],
        },
    )
    fused = client.post(
        f"/api/message-packs/{pack_id}/fuse",
        json={
            "destination_id": "daily",
            "expected_revision": edited.json()["revision"],
        },
    )
    split = client.post(
        "/api/message-packs/daily/split",
        json={
            "source_id": pack_id,
            "expected_revision": fused.json()["revision"],
        },
    )

    assert imported.status_code == 200
    assert imported.json()["pack"]["count"] == 3
    assert edited.status_code == 200
    assert edited.json()["entry"]["text"] == "已编辑"
    assert fused.status_code == 200
    assert pack_id not in [pack["id"] for pack in fused.json()["catalog"]["packs"]]
    assert split.status_code == 200
    assert pack_id in [pack["id"] for pack in split.json()["catalog"]["packs"]]


def test_message_pack_management_api_rejects_deleting_referenced_pack(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[0].message_pack = "daily"
    save_config(config_path, config)
    client = TestClient(create_app(config_path))
    revision = client.get("/api/message-packs").json()["revision"]

    response = client.request(
        "DELETE",
        "/api/message-packs/daily",
        json={"expected_revision": revision},
    )

    assert response.status_code == 409
    assert "目标使用" in response.json()["detail"]


def test_installed_message_packs_come_from_program_root(
    tmp_path: Path, monkeypatch
):
    data_root = tmp_path / "data-root"
    program_root = tmp_path / "program-root"
    data_root.mkdir()
    config_path = make_project(data_root)
    source_packs = data_root / "message-packs"
    program_packs = program_root / "message-packs"
    program_packs.parent.mkdir(parents=True, exist_ok=True)
    source_packs.rename(program_packs)
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(program_root))

    response = TestClient(create_app(config_path)).get("/api/message-packs")

    assert response.status_code == 200
    assert response.json()["packs"][0]["id"] == "daily"


def test_scan_friends_endpoint_starts_action_and_lists_candidates(tmp_path: Path):
    calls = []

    def runner(action: str):
        calls.append(action)
        return {"id": "scan-1", "action": action, "status": "running"}

    config = make_project(tmp_path)
    discovered = tmp_path / "data" / "discovered_friends.json"
    discovered.write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-04T12:30:00",
                "candidates": [
                    {
                        "candidate_id": "friend-xiaoming",
                        "display_name": "小明",
                        "avatar_cache_path": "friend-xiaoming.png",
                        "avatar_status": "cached",
                        "discovered_at": "2026-07-04T12:30:00",
                        "match_status": "configured",
                        "configured_target_id": "friend-xiaoming",
                        "configured_enabled": True,
                    },
                    {
                        "candidate_id": "candidate-new",
                        "display_name": "新朋友",
                        "avatar_cache_path": "candidate-new.png",
                        "avatar_status": "cached",
                        "discovered_at": "2026-07-04T12:30:00",
                        "match_status": "unconfigured",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(config, action_runner=runner))

    response = client.post("/api/friends/scan")
    candidates = client.get("/api/friends/discovered")

    assert response.status_code == 202
    assert calls == ["scan-friends"]
    assert candidates.json()["candidates"][1]["display_name"] == "新朋友"
    assert candidates.json()["candidates"][1]["avatar_url"] == "/api/avatars/candidate-new"


def test_stale_discovery_returns_cached_rows_without_starting_browser_work(tmp_path: Path):
    calls = []
    config = make_project(tmp_path)
    (tmp_path / "data" / "health.json").write_text('{"status":"success"}', encoding="utf-8")
    discovered = tmp_path / "data" / "discovered_friends.json"
    discovered.write_text(
        json.dumps({
            "scanned_at": "2026-07-03T08:00:00",
            "candidates": [{
                "candidate_id": "candidate-cached", "display_name": "缓存候选",
                "avatar_status": "missing", "discovered_at": "2026-07-03T08:00:00",
                "match_status": "unconfigured",
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    client = TestClient(create_app(
        config,
        action_runner=lambda action: calls.append(action) or {"id": "scan-1", "action": action, "status": "running"},
        now_provider=lambda: datetime(2026, 7, 5, 8, 0, 0),
    ))

    first = client.get("/api/friends/discovered")
    second = client.get("/api/friends/discovered")

    assert first.status_code == 200
    assert first.json()["candidates"][0]["display_name"] == "缓存候选"
    assert first.json()["stale"] is True
    assert first.json()["refresh_running"] is False
    assert second.json()["refresh_running"] is False
    assert calls == []


def test_fresh_discovery_does_not_start_an_unnecessary_background_refresh(tmp_path: Path):
    calls = []
    config = make_project(tmp_path)
    (tmp_path / "data" / "health.json").write_text('{"status":"success"}', encoding="utf-8")
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps({"scanned_at": "2026-07-05T08:00:00", "candidates": []}),
        encoding="utf-8",
    )
    client = TestClient(create_app(
        config,
        action_runner=lambda action: calls.append(action) or {"id": "scan-1", "action": action, "status": "running"},
        now_provider=lambda: datetime(2026, 7, 5, 8, 0, 0),
    ))

    response = client.get("/api/friends/discovered")

    assert response.status_code == 200
    assert response.json()["stale"] is False
    assert response.json()["refresh_running"] is False
    assert calls == []


def test_dashboard_status_reads_do_not_start_background_discovery(tmp_path: Path):
    calls = []
    config = make_project(tmp_path)
    (tmp_path / "data" / "health.json").write_text('{"status":"success"}', encoding="utf-8")
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps({"scanned_at": "2026-07-03T08:00:00", "candidates": []}),
        encoding="utf-8",
    )
    client = TestClient(create_app(
        config,
        action_runner=lambda action: calls.append(action) or {"id": "scan-1", "action": action, "status": "running"},
        now_provider=lambda: datetime(2026, 7, 5, 8, 0, 0),
    ))

    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status").status_code == 200

    assert calls == []


def test_discovered_candidate_batch_add_keeps_its_cached_avatar_and_friend_state(
    tmp_path: Path,
):
    config = make_project(tmp_path)
    account_id = write_verified_account(tmp_path)
    discovered = tmp_path / "data" / "discovered_friends.json"
    discovered.write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-04T12:30:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [{
                    "candidate_id": "candidate-new",
                    "display_name": "新朋友",
                    "avatar_cache_path": "candidate-new.png",
                    "avatar_status": "cached",
                    "discovered_at": "2026-07-04T12:30:00",
                    "match_status": "unconfigured",
                    "identity_key": "participant:friend-new",
                    "identity_source": "participant_sec_user_id",
                    "conversation_id": "candidate-conversation-new",
                }],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "data" / "avatar-cache"
    cache.mkdir(parents=True)
    (cache / "candidate-new.png").write_bytes(b"new-avatar")
    client = TestClient(create_app(config))

    added = client.post("/api/friends/discovered/batch", json={"candidate_ids": ["candidate-new"]})
    friends = client.get("/api/friends?today=2026-06-24").json()["friends"]

    assert added.status_code == 200
    assert added.json()["added"] == 1
    friend = next(item for item in friends if item["display_name"] == "新朋友")
    assert friend["id"] != "candidate-new"
    assert friend["id"].startswith("target-")
    assert friend["avatar_url"] == "/api/avatars/candidate-new"
    assert friend["today_status"] == "pending"
    assert friend["last_success_date"] is None


def test_configured_target_resolves_its_avatar_through_linked_candidate_id(tmp_path: Path):
    config = make_project(tmp_path)
    loaded = load_config(config)
    loaded.targets = [
        Target(name="已关联", stable_id="target-one", candidate_id="candidate-one"),
    ]
    save_config(config, loaded)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-04T12:30:00",
                "account_scope": "account-" + "a" * 24,
                "candidates": [{
                    "candidate_id": "candidate-one",
                    "display_name": "已关联",
                    "avatar_cache_key": "candidate-one",
                    "avatar_status": "cached",
                    "avatar_updated_at": "2026-07-04T12:30:00",
                    "discovered_at": "2026-07-04T12:30:00",
                    "match_status": "configured",
                }],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "data" / "avatar-cache"
    cache.mkdir(parents=True)
    (cache / "candidate-one.png").write_bytes(b"linked-avatar")

    friend = TestClient(create_app(config)).get("/api/friends").json()["friends"][0]

    assert friend["target_id"] == "target-one"
    assert friend["avatar_url"] == "/api/avatars/candidate-one?v=2026-07-04T12%3A30%3A00"
    assert friend["avatar_status"] == "cached"


def test_candidate_add_is_idempotent_by_candidate_id_and_keeps_duplicate_names_separate(
    tmp_path: Path,
):
    config = make_project(tmp_path)
    account_id = write_verified_account(tmp_path)
    discovered = tmp_path / "data" / "discovered_friends.json"
    discovered.write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-04T12:30:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "identity_key": "participant:friend-a",
                        "identity_source": "participant_sec_user_id",
                        "conversation_id": "candidate-conversation-a",
                        "display_name": "同名候选",
                        "avatar_cache_key": "candidate-a",
                        "avatar_status": "cached",
                        "discovered_at": "2026-07-04T12:30:00",
                        "match_status": "unconfigured",
                    },
                    {
                        "candidate_id": "candidate-b",
                        "identity_key": "participant:friend-b",
                        "identity_source": "participant_sec_user_id",
                        "conversation_id": "candidate-conversation-b",
                        "display_name": "同名候选",
                        "avatar_cache_key": "candidate-b",
                        "avatar_status": "cached",
                        "discovered_at": "2026-07-04T12:30:00",
                        "match_status": "unconfigured",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "data" / "avatar-cache"
    cache.mkdir(parents=True)
    (cache / "candidate-a.png").write_bytes(b"avatar-a")
    (cache / "candidate-b.png").write_bytes(b"avatar-b")
    client = TestClient(create_app(config))

    first = client.post("/api/friends/candidate-a/add-to-targets")
    repeated = client.post("/api/friends/candidate-a/add-to-targets")
    second = client.post("/api/friends/candidate-b/add-to-targets")
    discovered_after_add = client.get("/api/friends/discovered").json()["candidates"]
    friends = client.get("/api/friends").json()["friends"]
    target_ids = [friend["target_id"] for friend in friends if friend["target_id"]]
    removed = client.patch("/api/friends/batch", json={"target_ids": [target_ids[0]], "action": "delete"})
    discovered_after_remove = client.get("/api/friends/discovered").json()["candidates"]
    readded = client.post("/api/friends/candidate-a/add-to-targets")
    discovered_after_readd = client.get("/api/friends/discovered").json()["candidates"]

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert second.status_code == 200
    assert second.json()["created"] is True
    assert len(target_ids) == 2
    assert all(target_id and target_id.startswith("target-") for target_id in target_ids)
    assert first.json()["target"]["target_id"] != "candidate-a"
    assert [candidate["configured"] for candidate in discovered_after_add] == [True, True]
    assert removed.json()["affected"] == 1
    assert [candidate["configured"] for candidate in discovered_after_remove] == [False, True]
    assert readded.status_code == 200
    assert readded.json()["created"] is True
    assert readded.json()["target"]["target_id"] != target_ids[0]
    assert [candidate["configured"] for candidate in discovered_after_readd] == [True, True]


def test_orphan_binding_requires_explicit_candidate_relink_and_can_be_ignored(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    account_id = write_verified_account(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(name="待关联目标", stable_id="target-one", candidate_id="candidate-old")
    ]
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-30T08:00:00",
                "scan_id": "scan-current",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-current",
                        "display_name": "待关联目标",
                        "avatar_status": "missing",
                        "discovered_at": "2026-07-30T08:00:00",
                        "match_status": "unconfigured",
                        "presence_status": "current",
                        "identity_key": "participant:stable-current",
                        "identity_source": "participant_sec_user_id",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(config_path, now_provider=lambda: datetime(2026, 7, 30, 8, 5))
    )

    before = client.get("/api/friends/discovered").json()
    assert before["candidates"][0]["match_status"] == "needs_reassociation"
    assert before["candidates"][0]["reassociation_target_id"] == "target-one"
    assert before["orphans"] == [
        {"target_id": "target-one", "display_name": "待关联目标", "enabled": True}
    ]

    relinked = client.post(
        "/api/friends/candidate-current/relink",
        json={"target_id": "target-one"},
    )
    assert relinked.status_code == 200
    assert load_config(config_path).targets[0].candidate_id == "candidate-current"
    assert client.get("/api/friends/discovered").json()["orphans"] == []

    config = load_config(config_path)
    config.targets[0].candidate_id = "candidate-old"
    save_config(config_path, config)
    ignored = client.post("/api/friends/target-one/ignore-orphan")
    assert ignored.status_code == 200
    after_ignore = client.get("/api/friends/discovered").json()
    assert after_ignore["orphans"] == []
    assert after_ignore["candidates"][0]["match_status"] == "ignored_reassociation"
    blocked_after_ignore = client.post(
        "/api/friends/candidate-current/add-to-targets"
    )
    assert blocked_after_ignore.status_code == 409


def test_authoritative_relink_immediately_converges_current_binding_health(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="待恢复目标",
            stable_id="target-one",
            candidate_id="candidate-stale",
            binding_identity_key="row:friend-one",
            binding_identity_source="row_attribute",
            binding_account_scope="account-" + "a" * 24,
        )
    ]
    save_config(config_path, config)
    account_id = write_verified_account(tmp_path)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-30T08:00:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-current",
                        "display_name": "待恢复目标",
                        "avatar_status": "missing",
                        "discovered_at": "2026-07-30T08:00:00",
                        "match_status": "unconfigured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-one",
                        "identity_source": "participant_sec_user_id",
                        "conversation_id": "candidate-conversation-current",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    mark_bindings_for_revalidation(tmp_path / "data")
    client = TestClient(
        create_app(config_path, now_provider=lambda: datetime(2026, 7, 30, 8, 5))
    )

    relinked = client.post(
        "/api/friends/candidate-current/relink",
        json={"target_id": "target-one"},
    )

    assert relinked.status_code == 200
    target = load_config(config_path).targets[0]
    assert target.candidate_id == "candidate-current"
    assert target.binding_identity_key == "participant:friend-one"
    assert target.binding_identity_source == "participant_sec_user_id"
    assert target.binding_account_scope == account_id
    assert client.get("/api/status?today=2026-06-24").json()["friends"][0][
        "current_health"
    ]["reason_code"] == "binding_valid"


def test_proof_cleared_current_candidate_is_explicitly_reassociated(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    account_id = write_verified_account(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="待恢复目标",
            stable_id="target-one",
            candidate_id="candidate-current",
        )
    ]
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-30T08:00:00",
                "account_scope": account_id,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": "candidate-current",
                        "display_name": "待恢复目标",
                        "avatar_status": "missing",
                        "discovered_at": "2026-07-30T08:00:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": "participant:friend-one",
                        "identity_source": "participant_sec_user_id",
                        "conversation_id": "conversation-current",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(config_path, now_provider=lambda: datetime(2026, 7, 30, 8, 5))
    )

    discovered = client.get("/api/friends/discovered").json()
    relinked = client.post(
        "/api/friends/candidate-current/relink",
        json={"target_id": "target-one"},
    )
    target = load_config(config_path).targets[0]

    assert discovered["candidates"][0]["match_status"] == "needs_reassociation"
    assert discovered["candidates"][0]["reassociation_target_id"] == "target-one"
    assert relinked.status_code == 200
    assert target.binding_identity_key == "participant:friend-one"
    assert target.binding_identity_source == "participant_sec_user_id"
    assert target.binding_account_scope == account_id


def test_actionable_orphan_cannot_be_bypassed_by_direct_candidate_add(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(name="待关联目标", stable_id="target-one", candidate_id="candidate-old")
    ]
    save_config(config_path, config)
    (tmp_path / "data" / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-30T08:00:00",
                "candidates": [{
                    "candidate_id": "candidate-current",
                    "display_name": "待关联目标",
                    "avatar_status": "missing",
                    "discovered_at": "2026-07-30T08:00:00",
                    "match_status": "unconfigured",
                    "presence_status": "current",
                }],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(config_path))

    single = client.post("/api/friends/candidate-current/add-to-targets")
    batch = client.post(
        "/api/friends/discovered/batch",
        json={"candidate_ids": ["candidate-current"]},
    )

    assert single.status_code == 409
    assert batch.status_code == 200
    assert batch.json() == {"added": 0, "skipped": 1}
    assert len(load_config(config_path).targets) == 1


def test_managed_logout_requires_browser_lock_and_returns_logged_out_profile(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    calls = []
    client = TestClient(
        create_app(
            config_path,
            account_logout_runner=lambda loaded: calls.append(loaded.profile_dir),
        )
    )

    from autody.locking import SingleInstanceLock

    not_confirmed = client.post(
        "/api/account-profile/logout",
        json={"confirmed": False},
    )
    with SingleInstanceLock(config.lock_file):
        blocked = client.post(
            "/api/account-profile/logout",
            json={"confirmed": True},
        )
    completed = client.post(
        "/api/account-profile/logout",
        json={"confirmed": True},
    )

    assert not_confirmed.status_code == 400
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["reason_code"] == "account_operation_busy"
    assert blocked.json()["detail"]["user_summary_zh"]
    assert blocked.json()["detail"]["suggested_action_zh"]
    assert completed.status_code == 200
    assert calls == [config.profile_dir]
    assert completed.json()["logged_in"] is False
    assert completed.json()["profile_status"] == "unverified"


def test_account_profile_api_migrates_adds_and_switches_isolated_profiles(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets = [
        Target(
            name="账号甲目标",
            stable_id="target-a",
            candidate_id="candidate-a",
        )
    ]
    save_config(config_path, config)
    active_id = write_verified_account(tmp_path)
    actions = []

    def run_action(name: str):
        actions.append(name)
        return {
            "id": f"job-{name}",
            "action": name,
            "status": "running",
            "exit_code": None,
        }

    client = TestClient(create_app(config_path, action_runner=run_action))

    initial = client.get("/api/account-profiles")
    added = client.post("/api/account-profiles/add", json={})
    after_add = client.get("/api/account-profiles")
    targets_after_add = load_config(config_path).targets
    switched = client.post(f"/api/account-profiles/{active_id}/switch", json={})

    assert initial.status_code == 200
    assert initial.json()["active_profile_id"] == active_id
    assert initial.json()["migration_required"] is False
    assert added.status_code == 202
    assert actions == ["login"]
    pending_id = added.json()["profile"]["profile_id"]
    assert pending_id.startswith("pending-")
    assert after_add.json()["active_profile_id"] == pending_id
    assert targets_after_add == []
    assert switched.status_code == 200
    assert switched.json()["active_profile_id"] == active_id
    assert [target.stable_id for target in load_config(config_path).targets] == [
        "target-a"
    ]


def test_multi_account_logout_clears_only_active_auth_and_preserves_runtime(
    tmp_path: Path,
):
    config_path = make_project(tmp_path)
    profile_id = write_verified_account(tmp_path)
    preserved = {
        tmp_path / "data" / "discovered_friends.json": b'{"candidates":[]}',
        tmp_path / "data" / "state.json": (
            tmp_path / "data" / "state.json"
        ).read_bytes(),
    }
    for path, content in preserved.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    calls = []
    client = TestClient(
        create_app(
            config_path,
            account_logout_runner=lambda loaded: calls.append(loaded.profile_dir),
        )
    )
    assert client.get("/api/account-profiles").status_code == 200

    response = client.post(
        "/api/account-profile/logout",
        json={"confirmed": True},
    )

    assert response.status_code == 200
    assert calls == [
        tmp_path
        / "data"
        / "account-profiles"
        / profile_id
        / "browser-profile"
    ]
    for path, content in preserved.items():
        assert path.read_bytes() == content
    profiles = client.get("/api/account-profiles").json()
    active = next(item for item in profiles["profiles"] if item["active"])
    assert active["logged_in"] is False


def test_account_binding_guard_clears_account_scoped_overview_count(tmp_path: Path):
    config_path = make_project(tmp_path)
    config = load_config(config_path)
    config.targets[1].enabled = False
    save_config(config_path, config)
    mark_bindings_for_revalidation(config.state_file.parent)

    status = TestClient(create_app(config_path)).get("/api/status").json()

    assert status["statistics"]["configured_friend_count"] == 2
    assert status["statistics"]["enabled_friend_count"] == 0
    assert all(
        friend["binding_status"] == "revalidation_required"
        for friend in status["friends"]
    )
    assert any(
        issue["id"] == "account_bindings_revalidation_required"
        for issue in status["issues"]
    )


def test_enabled_duplicate_names_are_flagged_for_safe_sending(tmp_path: Path):
    config = make_project(tmp_path)
    loaded = load_config(config)
    loaded.targets = [Target(name="同名", stable_id="target-a"), Target(name=" 同名 ", stable_id="target-b")]
    save_config(config, loaded)

    friends = TestClient(create_app(config)).get("/api/friends").json()["friends"]

    assert [friend["ambiguous_duplicate"] for friend in friends] == [True, True]


def test_refresh_avatars_starts_only_the_safe_browser_scan(tmp_path: Path):
    calls = []
    client = TestClient(
        create_app(
            make_project(tmp_path),
            action_runner=lambda action: calls.append(action) or {"id": "avatar-1", "action": action, "status": "running"},
        )
    )

    response = client.post("/api/friends/refresh-avatars")

    assert response.status_code == 202
    assert calls == ["refresh-friend-avatars"]


def test_standalone_friend_import_export_routes_are_removed(tmp_path: Path):
    client = TestClient(create_app(make_project(tmp_path)))

    assert client.post("/api/friends/import").status_code == 404
    assert client.post("/api/friends/import/preview").status_code == 404
    assert client.get("/api/friends/export").status_code == 404


def test_status_returns_actionable_issues_without_friends_or_messages(
    tmp_path: Path, monkeypatch
):
    config = make_project(tmp_path)
    config.write_text(
        "targets: []\nmessages_file: messages.txt\nstate_file: data/state.json\n",
        encoding="utf-8",
    )
    (tmp_path / "messages.txt").write_text("\n", encoding="utf-8")
    monkeypatch.setattr("autody.scheduler.windows_task_rows", lambda: [])
    client = TestClient(create_app(config))

    response = client.get("/api/status?today=2026-07-04")

    assert response.status_code == 200
    issue_ids = {issue["id"] for issue in response.json()["issues"]}
    assert {"no_friends", "no_messages", "scheduler_missing", "runtime_missing"} <= issue_ids
    assert "remote_library" not in issue_ids

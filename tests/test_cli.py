from pathlib import Path

from typer.testing import CliRunner

from autody.cli import app
from autody.friend_discovery import AvatarRefreshResult, FriendDiscoveryResult
from autody.locking import SingleInstanceLock
from autody.runner import RunResult, RunStatus


runner = CliRunner()


def test_check_config_reports_success(tmp_path: Path):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["check-config", "--config", str(tmp_path / "config.yaml")]
    )
    assert result.exit_code == 0
    assert "配置有效" in result.stdout


def test_check_config_rejects_missing_message_library(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "targets:\n  - name: 小明\nmessages_file: missing.txt\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["check-config", "--config", str(tmp_path / "config.yaml")]
    )
    assert result.exit_code != 0
    assert "文案库不存在" in result.output


def test_health_check_reports_success(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("autody.cli.open_chat", lambda *args, **kwargs: Context())
    monkeypatch.setattr("autody.cli.recovery_due", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "autody.cli.discover_friends",
        lambda *_args, **_kwargs: FriendDiscoveryResult(
            "2026-07-05T08:00:00", [], tmp_path / "data" / "discovered_friends.json"
        ),
    )
    result = runner.invoke(
        app, ["health-check", "--config", str(tmp_path / "config.yaml")]
    )
    assert result.exit_code == 0
    assert "登录状态正常" in result.stdout


def test_health_check_refreshes_stale_candidate_cache_without_sending(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("targets: []\nmessages_file: messages.txt\n", encoding="utf-8")
    calls = []

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def discover(_loaded, page, *_args, **_kwargs):
        calls.append(page)
        return FriendDiscoveryResult("2026-07-05T08:00:00", [], tmp_path / "data" / "discovered_friends.json")

    monkeypatch.setattr("autody.cli.open_chat", lambda *_args, **_kwargs: Context())
    monkeypatch.setattr("autody.cli.recovery_due", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("autody.cli.discover_friends", discover)
    monkeypatch.setattr("autody.cli.DouyinChat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("health refresh must not send")))

    result = runner.invoke(app, ["health-check", "--config", str(config)])

    assert result.exit_code == 0
    assert len(calls) == 1


def test_health_check_skips_when_another_browser_task_holds_lock(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n"
        "lock_file: data/locks/autody.lock\n",
        encoding="utf-8",
    )
    called = False

    def should_not_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("browser must not open while lock is held")

    monkeypatch.setattr("autody.cli.open_chat", should_not_open)
    lock_path = tmp_path / "data" / "locks" / "autody.lock"
    with SingleInstanceLock(lock_path):
        result = runner.invoke(
            app, ["health-check", "--config", str(tmp_path / "config.yaml")]
        )

    assert result.exit_code == 0
    assert "已有 AutoDy 任务正在运行，本次跳过。" in result.stdout
    assert called is False


def test_scan_friends_uses_same_global_lock(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "targets: []\nmessages_file: messages.txt\n"
        "lock_file: data/locks/autody.lock\n",
        encoding="utf-8",
    )
    called = False

    def should_not_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("browser must not open while lock is held")

    monkeypatch.setattr("autody.cli.open_chat", should_not_open)
    with SingleInstanceLock(tmp_path / "data" / "locks" / "autody.lock"):
        result = runner.invoke(app, ["scan-friends", "--config", str(config)])

    assert result.exit_code == 0
    assert "已有 AutoDy 任务正在运行，本次跳过。" in result.stdout
    assert called is False


def test_scan_friends_defers_while_daily_sending_is_marked_active(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "targets: []\nmessages_file: messages.txt\nlock_file: data/locks/autody.lock\n",
        encoding="utf-8",
    )
    marker = tmp_path / "data" / "locks" / "daily-send-active.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("autody.cli.open_chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("scan must defer")))
    result = runner.invoke(app, ["scan-friends", "--config", str(config)])

    assert result.exit_code == 0
    assert "每日发送任务正在运行，候选扫描已延后。" in result.stdout


def test_rejected_daily_run_keeps_existing_send_activity_marker(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n"
        "lock_file: data/locks/autody.lock\n",
        encoding="utf-8",
    )
    marker = tmp_path / "data" / "locks" / "daily-send-active.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"pid": 1}', encoding="utf-8")

    monkeypatch.setattr(
        "autody.cli.open_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a rejected run must not open the browser")
        ),
    )
    with SingleInstanceLock(tmp_path / "data" / "locks" / "autody.lock"):
        result = runner.invoke(app, ["run", "--config", str(config)])

    assert result.exit_code == 10
    assert "安全重试已安排" in result.output
    assert marker.is_file()


def test_refresh_friend_avatars_uses_identity_safe_discovery_correction(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("targets:\n  - name: 小明\nmessages_file: messages.txt\n", encoding="utf-8")

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    calls = []

    def discover(loaded, _page, _selectors, output, **kwargs):
        calls.append((output, kwargs))
        loaded.targets[0].stable_id = "friend-xiaoming"
        return FriendDiscoveryResult(
            "2026-07-05T08:00:00",
            [],
            output,
            config_changed=True,
            last_result={"avatars_updated": 1, "avatars_failed": 0},
        )

    monkeypatch.setattr("autody.cli.open_chat", lambda *_args, **_kwargs: Context())
    monkeypatch.setattr("autody.cli.discover_friends", discover)

    result = runner.invoke(app, ["refresh-friend-avatars", "--config", str(config)])

    assert result.exit_code == 0
    assert "头像校正完成：更新 1 个，失败 0 个。" in result.stdout
    assert calls[0][1]["force_avatar_refresh"] is True
    assert "stable_id: friend-xiaoming" in config.read_text(encoding="utf-8")


def test_login_success_starts_one_read_only_friend_scan(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("targets: []\nmessages_file: messages.txt\n", encoding="utf-8")
    calls = []

    def browser_login(_profile, *, home, on_ready):
        calls.append(("login", home))
        on_ready(object())

    def discover(_loaded, page, *_args, **_kwargs):
        calls.append(("scan", page))
        return FriendDiscoveryResult("2026-07-05T08:00:00", [], tmp_path / "data" / "discovered_friends.json")

    monkeypatch.setattr("autody.cli.browser_login", browser_login)
    monkeypatch.setattr("autody.cli.discover_friends", discover)

    result = runner.invoke(app, ["login", "--config", str(config)])

    assert result.exit_code == 0
    assert [item[0] for item in calls] == ["login", "scan"]
    assert "登录状态已保存。" in result.stdout
    assert "候选好友已刷新" in result.stdout


def test_login_keeps_success_when_automatic_scan_fails(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("targets: []\nmessages_file: messages.txt\n", encoding="utf-8")
    failures = []

    def browser_login(_profile, *, home, on_ready):
        on_ready(object())

    monkeypatch.setattr("autody.cli.browser_login", browser_login)
    monkeypatch.setattr("autody.cli.discover_friends", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("scan failed")))
    monkeypatch.setattr("autody.cli.record_discovery_failure", lambda *_args, **kwargs: failures.append(kwargs.get("error")))

    result = runner.invoke(app, ["login", "--config", str(config)])

    assert result.exit_code == 0
    assert "登录状态已保存。" in result.stdout
    assert "候选好友扫描失败" in result.stdout
    assert failures == ["scan failed"]


def test_run_reports_already_done_without_claiming_new_send(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("autody.cli.open_chat", lambda *_args, **_kwargs: Context())
    monkeypatch.setattr("autody.cli.DouyinChat", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "autody.cli.run_daily",
        lambda *_args, **_kwargs: RunResult(
            status=RunStatus.ALREADY_DONE,
            total_targets=1,
            sent_count=0,
            skipped_count=1,
            failed_count=0,
        ),
    )

    result = runner.invoke(app, ["run", "--config", str(config)])

    assert result.exit_code == 0
    assert "当天所有目标此前已完成。" in result.stdout
    assert "当天所有目标已完成。" not in result.stdout


def test_run_reports_completed_counts(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("autody.cli.open_chat", lambda *_args, **_kwargs: Context())
    monkeypatch.setattr("autody.cli.DouyinChat", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "autody.cli.run_daily",
        lambda *_args, **_kwargs: RunResult(
            status=RunStatus.COMPLETED,
            total_targets=2,
            sent_count=2,
            skipped_count=0,
            failed_count=0,
        ),
    )

    result = runner.invoke(app, ["run", "--config", str(config)])

    assert result.exit_code == 0
    assert "本次发送完成：成功 2 个，失败 0 个。" in result.stdout


def test_run_reports_retry_pending_without_a_failure_exit(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )

    class Context:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("autody.cli.open_chat", lambda *_args, **_kwargs: Context())
    monkeypatch.setattr("autody.cli.DouyinChat", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "autody.cli.run_daily",
        lambda *_args, **_kwargs: RunResult(
            status=RunStatus.RETRY_PENDING,
            total_targets=2,
            sent_count=1,
            skipped_count=0,
            failed_count=1,
        ),
    )

    result = runner.invoke(app, ["run", "--config", str(config)])

    assert result.exit_code == 10
    assert "本次发送完成：成功 1 个，失败 1 个。" in result.stdout
    assert "安全重试已安排" in result.output


def test_ui_starts_local_server(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr("autody.cli.uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    result = runner.invoke(
        app,
        [
            "ui",
            "--config",
            str(tmp_path / "config.yaml"),
            "--no-open",
            "--port",
            "9876",
        ],
    )
    assert result.exit_code == 0
    assert calls == [{"host": "127.0.0.1", "port": 9876, "log_level": "warning"}]


def test_dry_run_starts_without_browser_or_state_mutation(tmp_path: Path, monkeypatch):
    (tmp_path / "messages.txt").write_text("早安\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("targets:\n  - name: 小明\nmessages_file: messages.txt\n", encoding="utf-8")
    monkeypatch.setattr("autody.cli.open_chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("browser must stay closed")))

    result = runner.invoke(app, ["run", "--config", str(config), "--dry-run"])

    assert result.exit_code == 0
    assert "未打开浏览器，未发送消息" in result.stdout
    assert not (tmp_path / "data" / "state.json").exists()

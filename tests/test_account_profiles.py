import json
from pathlib import Path

import pytest

from autody.account_profiles import AccountProfileStoreError, MultiAccountStore
from autody.config import AppConfig, Target, load_config, save_config


def _write_verified_profile(root: Path, profile_id: str, name: str) -> None:
    data = root / "data"
    (data / "account-avatar").mkdir(parents=True, exist_ok=True)
    (data / "account-avatar" / "profile.png").write_bytes(b"avatar")
    (data / "account-profile.json").write_text(
        json.dumps(
            {
                "account_profile_id": profile_id,
                "account_id_digest": "a" * 64,
                "display_name": name,
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


def _make_project(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config = AppConfig(
        targets=[
            Target(
                name="账号甲目标",
                stable_id="target-a",
                candidate_id="candidate-a",
                message_pack="daily",
            )
        ],
        messages_file=tmp_path / "messages.txt",
        profile_dir=tmp_path / "data" / "browser-profile",
        state_file=tmp_path / "data" / "state.json",
        lock_file=tmp_path / "data" / "locks" / "autody.lock",
        artifact_dir=tmp_path / "data" / "artifacts",
        daily_send_time="07:31",
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_config(config_path, config)
    config.messages_file.write_text("测试", encoding="utf-8")
    config.state_file.parent.mkdir(parents=True, exist_ok=True)
    config.state_file.write_text('{"daily":{"a":{}}}', encoding="utf-8")
    history = tmp_path / "data" / "history"
    history.mkdir()
    (history / "task-runs.jsonl").write_text(
        '{"run_id":"account-a-history"}\n',
        encoding="utf-8",
    )
    (tmp_path / "data" / "discovered_friends.json").write_text(
        '{"candidates":[{"candidate_id":"candidate-a"}]}',
        encoding="utf-8",
    )
    test_center = (
        tmp_path
        / "data"
        / "modules"
        / "autody-test-center"
        / "data"
    )
    test_center.mkdir(parents=True)
    (test_center / "settings.json").write_text(
        '{"selected_target_ids":["target-a"]}',
        encoding="utf-8",
    )
    config.profile_dir.mkdir(parents=True)
    (config.profile_dir / "auth-a").write_text("local-auth-a", encoding="utf-8")
    _write_verified_profile(tmp_path, "account-" + "a" * 24, "账号甲")
    return config_path


def test_versioned_migration_preserves_current_data_with_rollback_manifest(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    store = MultiAccountStore(tmp_path, config_path)

    registry = store.ensure_migrated()

    assert registry["format_version"] == 1
    assert registry["active_profile_id"] == "account-" + "a" * 24
    assert registry["migration"]["status"] == "completed"
    rollback = Path(registry["migration"]["rollback_manifest"])
    assert rollback.is_file()
    profile_root = store.profile_root(registry["active_profile_id"])
    settings = json.loads(
        (profile_root / "account-settings.json").read_text(encoding="utf-8")
    )
    assert settings["targets"][0]["stable_id"] == "target-a"
    assert settings["daily_send_time"] == "07:31"
    assert (profile_root / "runtime" / "state.json").is_file()
    assert (
        profile_root
        / "runtime"
        / "modules"
        / "autody-test-center"
        / "data"
        / "settings.json"
    ).is_file()
    assert (profile_root / "browser-profile" / "auth-a").is_file()
    assert load_config(config_path).profile_dir == profile_root / "browser-profile"


def test_versioned_migration_recovers_when_the_verified_browser_already_exists(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    store = MultiAccountStore(tmp_path, config_path)
    authoritative = "account-" + "a" * 24
    previous_browser = store.profile_root(authoritative) / "browser-profile"
    previous_browser.mkdir(parents=True)
    (previous_browser / "previous-session").write_text(
        "same-account-previous-session",
        encoding="utf-8",
    )

    registry = store.ensure_migrated()

    profile_root = store.profile_root(authoritative)
    assert registry["active_profile_id"] == authoritative
    assert (profile_root / "browser-profile" / "auth-a").is_file()
    recovery = Path(registry["migration"]["rollback_manifest"]).parent
    assert (
        recovery / "previous-browser-profile" / "previous-session"
    ).read_text(encoding="utf-8") == "same-account-previous-session"
    restored = load_config(config_path)
    assert [target.stable_id for target in restored.targets] == ["target-a"]
    assert restored.daily_send_time == "07:31"
    assert json.loads(restored.state_file.read_text(encoding="utf-8"))["daily"] == {
        "a": {}
    }


def test_two_profiles_keep_targets_schedules_candidates_test_center_and_auth_isolated(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    store = MultiAccountStore(tmp_path, config_path)
    first = store.ensure_migrated()["active_profile_id"]
    second = store.create_empty_profile()["profile_id"]

    store.activate(second)
    second_config = load_config(config_path)
    assert second_config.targets == []
    second_config.targets = [
        Target(
            name="账号乙目标",
            stable_id="target-b",
            candidate_id="candidate-b",
        )
    ]
    second_config.daily_send_time = "09:45"
    save_config(config_path, second_config)
    second_config.state_file.write_text('{"daily":{"b":{}}}', encoding="utf-8")
    second_history = tmp_path / "data" / "history"
    second_history.mkdir()
    (second_history / "task-runs.jsonl").write_text(
        '{"run_id":"account-b-history"}\n',
        encoding="utf-8",
    )
    (tmp_path / "data" / "discovered_friends.json").write_text(
        '{"candidates":[{"candidate_id":"candidate-b"}]}',
        encoding="utf-8",
    )
    test_center = (
        tmp_path
        / "data"
        / "modules"
        / "autody-test-center"
        / "data"
    )
    test_center.mkdir(parents=True, exist_ok=True)
    (test_center / "settings.json").write_text(
        '{"selected_target_ids":["target-b"]}',
        encoding="utf-8",
    )
    (store.profile_root(second) / "browser-profile" / "auth-b").write_text(
        "local-auth-b",
        encoding="utf-8",
    )
    store.persist_active()

    store.activate(first)
    first_config = load_config(config_path)
    assert [target.stable_id for target in first_config.targets] == ["target-a"]
    assert first_config.daily_send_time == "07:31"
    assert "candidate-a" in (
        tmp_path / "data" / "discovered_friends.json"
    ).read_text(encoding="utf-8")
    assert "target-a" in (test_center / "settings.json").read_text(
        encoding="utf-8"
    )
    assert (first_config.profile_dir / "auth-a").is_file()
    assert not (first_config.profile_dir / "auth-b").exists()
    assert "account-a-history" in (
        tmp_path / "data" / "history" / "task-runs.jsonl"
    ).read_text(encoding="utf-8")
    assert "account-b-history" not in (
        tmp_path / "data" / "history" / "task-runs.jsonl"
    ).read_text(encoding="utf-8")

    store.activate(second)
    active = load_config(config_path)
    assert [target.stable_id for target in active.targets] == ["target-b"]
    assert active.daily_send_time == "09:45"
    assert "candidate-b" in (
        tmp_path / "data" / "discovered_friends.json"
    ).read_text(encoding="utf-8")
    assert "target-b" in (test_center / "settings.json").read_text(
        encoding="utf-8"
    )
    assert (active.profile_dir / "auth-b").is_file()
    assert not (active.profile_dir / "auth-a").exists()
    assert "account-b-history" in (
        tmp_path / "data" / "history" / "task-runs.jsonl"
    ).read_text(encoding="utf-8")
    assert "account-a-history" not in (
        tmp_path / "data" / "history" / "task-runs.jsonl"
    ).read_text(encoding="utf-8")


def test_logout_affects_only_active_auth_and_preserves_profile_settings(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    store = MultiAccountStore(tmp_path, config_path)
    first = store.ensure_migrated()["active_profile_id"]
    second = store.create_empty_profile()["profile_id"]
    (store.profile_root(second) / "browser-profile" / "auth-b").write_text(
        "local-auth-b",
        encoding="utf-8",
    )
    store.activate(second)

    cleared = []

    def clear_auth(profile_dir: Path) -> None:
        cleared.append(profile_dir)
        (profile_dir / "auth-b").unlink()

    before = (
        store.profile_root(second) / "account-settings.json"
    ).read_bytes()
    store.logout_active(clear_auth)

    assert cleared == [store.profile_root(second) / "browser-profile"]
    assert not (store.profile_root(second) / "browser-profile" / "auth-b").exists()
    assert (store.profile_root(first) / "browser-profile" / "auth-a").is_file()
    assert (
        store.profile_root(second) / "account-settings.json"
    ).read_bytes() == before
    entry = next(
        item
        for item in store.public_payload()["profiles"]
        if item["profile_id"] == second
    )
    assert entry["logged_in"] is False


def test_pending_profile_is_associated_with_new_authoritative_identity(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    store = MultiAccountStore(tmp_path, config_path)
    store.ensure_migrated()
    pending = store.create_empty_profile()["profile_id"]
    store.activate(pending)
    authoritative = "account-" + "b" * 24
    _write_verified_profile(tmp_path, authoritative, "账号乙")

    associated = store.associate_active_verified_profile()

    assert associated["profile_id"] == authoritative
    assert associated["profile_status"] == "verified"
    assert associated["logged_in"] is True
    payload = store.public_payload()
    assert payload["active_profile_id"] == authoritative
    assert pending not in {item["profile_id"] for item in payload["profiles"]}
    assert load_config(config_path).profile_dir == (
        store.profile_root(authoritative) / "browser-profile"
    )


def test_pending_relogin_to_same_stable_account_restores_existing_local_state(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    store = MultiAccountStore(tmp_path, config_path)
    authoritative = store.ensure_migrated()["active_profile_id"]
    pending = store.create_empty_profile()["profile_id"]
    store.activate(pending)
    pending_browser = store.profile_root(pending) / "browser-profile"
    (pending_browser / "new-session").write_text(
        "verified-same-account-session",
        encoding="utf-8",
    )
    _write_verified_profile(tmp_path, authoritative, "账号甲")

    associated = store.associate_active_verified_profile()

    assert associated["profile_id"] == authoritative
    assert store.public_payload()["active_profile_id"] == authoritative
    assert pending not in {
        item["profile_id"] for item in store.public_payload()["profiles"]
    }
    restored = load_config(config_path)
    assert [target.stable_id for target in restored.targets] == ["target-a"]
    assert restored.daily_send_time == "07:31"
    assert json.loads(restored.state_file.read_text(encoding="utf-8"))["daily"] == {
        "a": {}
    }
    assert "candidate-a" in (
        tmp_path / "data" / "discovered_friends.json"
    ).read_text(encoding="utf-8")
    assert "account-a-history" in (
        tmp_path / "data" / "history" / "task-runs.jsonl"
    ).read_text(encoding="utf-8")
    assert "target-a" in (
        tmp_path
        / "data"
        / "modules"
        / "autody-test-center"
        / "data"
        / "settings.json"
    ).read_text(encoding="utf-8")
    metadata = json.loads(
        (tmp_path / "data" / "account-profile.json").read_text(encoding="utf-8")
    )
    assert metadata["profile_status"] == "verified"
    assert (
        store.profile_root(authoritative) / "browser-profile" / "new-session"
    ).read_text(encoding="utf-8") == "verified-same-account-session"


def test_unverified_project_returns_empty_public_profile_list_without_migrating(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    (tmp_path / "data" / "account-profile.json").unlink()
    store = MultiAccountStore(tmp_path, config_path)

    assert store.public_payload_if_available() == {
        "active_profile_id": None,
        "profiles": [],
        "migration_required": True,
    }
    assert not store.registry_path.exists()


def test_corrupt_registry_is_never_overwritten_by_a_new_migration(
    tmp_path: Path,
):
    config_path = _make_project(tmp_path)
    store = MultiAccountStore(tmp_path, config_path)
    store.registry_path.parent.mkdir(parents=True, exist_ok=True)
    original = b"{not-valid-json"
    store.registry_path.write_bytes(original)

    with pytest.raises(AccountProfileStoreError, match="注册表"):
        store.ensure_migrated()
    with pytest.raises(AccountProfileStoreError, match="注册表"):
        store.public_payload_if_available()

    assert store.registry_path.read_bytes() == original

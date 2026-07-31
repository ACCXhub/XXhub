"""Versioned local account-profile isolation and activation.

The active account keeps a compatibility working set under ``data`` so the
existing runtime can continue to use stable paths. Every activation snapshots
that working set into an account-scoped directory before loading another one.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from autody.account_profile import load_account_profile
from autody.config import AppConfig, Target, load_config, save_config


FORMAT_VERSION = 1
_PROFILE_ID = re.compile(r"^(?:account-[a-f0-9]{24}|pending-[a-f0-9]{24})$")
_ACCOUNT_CONFIG_FIELDS = (
    "targets",
    "daily_send_time",
    "daily_health_check_time",
    "weekly_health_check_enabled",
    "weekly_health_check_weekday",
    "weekly_health_check_time",
    "startup_recovery_enabled",
    "recovery_deadline",
)
_RUNTIME_FILES = (
    "state.json",
    "discovered_friends.json",
    "ignored-friend-bindings.json",
    "health.json",
    "friend_scan_progress.json",
    "account-binding-state.json",
)
_RUNTIME_DIRS = (
    "avatar-cache",
    "history",
    "notifications",
    "modules/autody-test-center/data",
)


class AccountProfileStoreError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _copy_path(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


class MultiAccountStore:
    def __init__(self, root: Path, config_path: Path):
        self.root = root.resolve()
        self.config_path = config_path.resolve()
        self.data_root = self.root / "data"
        self.profiles_root = self.data_root / "account-profiles"
        self.registry_path = self.profiles_root / "registry.json"

    def profile_root(self, profile_id: str) -> Path:
        if not _PROFILE_ID.fullmatch(profile_id):
            raise AccountProfileStoreError("本地账号标识无效")
        destination = (self.profiles_root / profile_id).resolve()
        if destination.parent != self.profiles_root.resolve():
            raise AccountProfileStoreError("本地账号目录越界")
        return destination

    def _load_registry(self) -> dict | None:
        if not self.registry_path.exists():
            return None
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise AccountProfileStoreError("本地账号注册表损坏，已停止操作") from exc
        if not isinstance(value, dict):
            raise AccountProfileStoreError("本地账号注册表格式无效")
        if value.get("format_version") != FORMAT_VERSION:
            raise AccountProfileStoreError("本地账号配置版本不受支持")
        return value

    def _save_registry(self, value: dict) -> None:
        _atomic_json(self.registry_path, value)

    def _settings_payload(self, config: AppConfig) -> dict:
        return {
            "format_version": FORMAT_VERSION,
            "targets": [
                target.model_dump(mode="json", exclude_none=True)
                for target in config.targets
            ],
            **{
                field: getattr(config, field)
                for field in _ACCOUNT_CONFIG_FIELDS
                if field != "targets"
            },
        }

    def _write_settings(self, profile_id: str, config: AppConfig) -> None:
        _atomic_json(
            self.profile_root(profile_id) / "account-settings.json",
            self._settings_payload(config),
        )

    def _apply_settings(self, profile_id: str, config: AppConfig) -> None:
        path = self.profile_root(profile_id) / "account-settings.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise AccountProfileStoreError("本地账号设置不可用") from exc
        config.targets = [
            Target.model_validate(item)
            for item in value.get("targets", [])
        ]
        for field in _ACCOUNT_CONFIG_FIELDS:
            if field == "targets" or field not in value:
                continue
            setattr(config, field, value[field])
        config.profile_dir = self.profile_root(profile_id) / "browser-profile"

    def _snapshot_runtime(self, profile_id: str) -> None:
        profile = self.profile_root(profile_id)
        runtime = profile / "runtime"
        for relative in _RUNTIME_FILES:
            source = self.data_root / relative
            destination = runtime / relative
            if source.exists():
                _copy_path(source, destination)
            elif destination.exists():
                destination.unlink()
        for relative in _RUNTIME_DIRS:
            source = self.data_root / relative
            destination = runtime / relative
            if source.exists():
                _copy_path(source, destination)
            elif destination.exists():
                shutil.rmtree(destination)
        for relative in ("account-profile.json", "account-avatar"):
            source = self.data_root / relative
            destination = profile / relative
            if source.exists():
                _copy_path(source, destination)

    def _clear_working_runtime(self) -> None:
        data_root = self.data_root.resolve()
        for relative in (*_RUNTIME_FILES, *_RUNTIME_DIRS):
            target = (self.data_root / relative).resolve()
            if not target.is_relative_to(data_root) or target == data_root:
                raise AccountProfileStoreError("账号运行目录越界")
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        for relative in ("account-profile.json", "account-avatar"):
            target = (self.data_root / relative).resolve()
            if not target.is_relative_to(data_root) or target == data_root:
                raise AccountProfileStoreError("账号资料目录越界")
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    def _restore_runtime(self, profile_id: str) -> None:
        profile = self.profile_root(profile_id)
        runtime = profile / "runtime"
        for relative in _RUNTIME_FILES:
            _copy_path(runtime / relative, self.data_root / relative)
        for relative in _RUNTIME_DIRS:
            _copy_path(runtime / relative, self.data_root / relative)
        for relative in ("account-profile.json", "account-avatar"):
            _copy_path(profile / relative, self.data_root / relative)

    def ensure_migrated(self) -> dict:
        existing = self._load_registry()
        if existing is not None:
            return existing
        profile = load_account_profile(self.root)
        if profile is None or not _PROFILE_ID.fullmatch(profile.account_profile_id):
            raise AccountProfileStoreError(
                "当前账号尚未经过权威身份验证，无法迁移本地数据"
            )
        profile_id = profile.account_profile_id
        destination = self.profile_root(profile_id)
        backup = (
            self.data_root
            / "account-profile-migration-backups"
            / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        backup.mkdir(parents=True, exist_ok=False)
        config_backup = backup / "config.yaml"
        shutil.copy2(self.config_path, config_backup)
        legacy_config = load_config(self.config_path)
        legacy_browser = legacy_config.profile_dir.resolve()
        browser_destination = destination / "browser-profile"
        manifest = backup / "rollback-manifest.json"
        _atomic_json(
            manifest,
            {
                "format_version": FORMAT_VERSION,
                "status": "prepared",
                "config_backup": str(config_backup),
                "browser_from": str(legacy_browser),
                "browser_to": str(browser_destination),
            },
        )
        destination.mkdir(parents=True, exist_ok=True)
        moved_browser = False
        try:
            if (
                legacy_browser != browser_destination.resolve()
                and legacy_browser.exists()
            ):
                if browser_destination.exists():
                    raise AccountProfileStoreError(
                        "目标账号认证目录已存在，迁移已停止"
                    )
                shutil.move(str(legacy_browser), str(browser_destination))
                moved_browser = True
            else:
                browser_destination.mkdir(parents=True, exist_ok=True)
            legacy_config.profile_dir = browser_destination
            save_config(self.config_path, legacy_config)
            self._write_settings(profile_id, legacy_config)
            self._snapshot_runtime(profile_id)
            registry = {
                "format_version": FORMAT_VERSION,
                "active_profile_id": profile_id,
                "profiles": {
                    profile_id: {
                        "profile_id": profile_id,
                        "display_name": profile.display_name,
                        "profile_status": "verified",
                        "logged_in": True,
                        "created_at": _timestamp(),
                        "last_active_at": _timestamp(),
                    }
                },
                "migration": {
                    "status": "completed",
                    "completed_at": _timestamp(),
                    "rollback_manifest": str(manifest),
                },
            }
            self._save_registry(registry)
            rollback = json.loads(manifest.read_text(encoding="utf-8"))
            rollback["status"] = "completed"
            _atomic_json(manifest, rollback)
            return registry
        except Exception:
            if moved_browser and browser_destination.exists() and not legacy_browser.exists():
                legacy_browser.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(browser_destination), str(legacy_browser))
            shutil.copy2(config_backup, self.config_path)
            raise

    def persist_active(self) -> None:
        registry = self.ensure_migrated()
        profile_id = str(registry["active_profile_id"])
        config = load_config(self.config_path)
        self._write_settings(profile_id, config)
        self._snapshot_runtime(profile_id)
        metadata = load_account_profile(self.root)
        entry = registry["profiles"][profile_id]
        if metadata is not None:
            entry.update(
                {
                    "display_name": metadata.display_name,
                    "profile_status": metadata.profile_status,
                    "logged_in": True,
                }
            )
        entry["last_saved_at"] = _timestamp()
        self._save_registry(registry)

    def create_empty_profile(self) -> dict:
        registry = self.ensure_migrated()
        profile_id = f"pending-{uuid.uuid4().hex[:24]}"
        profile = self.profile_root(profile_id)
        (profile / "browser-profile").mkdir(parents=True)
        current = load_config(self.config_path)
        defaults = AppConfig(
            messages_file=current.messages_file,
            profile_dir=profile / "browser-profile",
            state_file=current.state_file,
            lock_file=current.lock_file,
            artifact_dir=current.artifact_dir,
        )
        self._write_settings(profile_id, defaults)
        _atomic_json(
            profile / "runtime" / "state.json",
            {"rotation": {"next_index": 0}, "daily": {}},
        )
        entry = {
            "profile_id": profile_id,
            "display_name": None,
            "profile_status": "unverified",
            "logged_in": False,
            "created_at": _timestamp(),
            "last_active_at": None,
        }
        registry["profiles"][profile_id] = entry
        self._save_registry(registry)
        return entry

    def activate(self, profile_id: str) -> dict:
        registry = self.ensure_migrated()
        if profile_id not in registry["profiles"]:
            raise AccountProfileStoreError("本地账号不存在")
        if registry["active_profile_id"] == profile_id:
            return registry["profiles"][profile_id]
        self.persist_active()
        registry = self.ensure_migrated()
        self._clear_working_runtime()
        self._restore_runtime(profile_id)
        config = load_config(self.config_path)
        self._apply_settings(profile_id, config)
        save_config(self.config_path, config)
        registry["active_profile_id"] = profile_id
        registry["profiles"][profile_id]["last_active_at"] = _timestamp()
        self._save_registry(registry)
        return registry["profiles"][profile_id]

    def logout_active(self, auth_clearer) -> dict:
        registry = self.ensure_migrated()
        profile_id = str(registry["active_profile_id"])
        profile = self.profile_root(profile_id)
        auth_clearer(profile / "browser-profile")
        entry = registry["profiles"][profile_id]
        entry["logged_in"] = False
        entry["profile_status"] = "unverified"
        entry["logged_out_at"] = _timestamp()
        _atomic_json(
            self.data_root / "account-binding-state.json",
            {
                "status": "revalidation_required",
                "updated_at": _timestamp(),
            },
        )
        self._write_settings(profile_id, load_config(self.config_path))
        self._snapshot_runtime(profile_id)
        self._save_registry(registry)
        return entry

    def associate_active_verified_profile(self) -> dict:
        """Replace an active pending key with the verified account-derived key."""
        registry = self.ensure_migrated()
        active = str(registry["active_profile_id"])
        metadata = load_account_profile(self.root)
        if metadata is None or not metadata.account_profile_id.startswith("account-"):
            raise AccountProfileStoreError("新增账号尚未完成权威身份验证")
        authoritative = metadata.account_profile_id
        if not _PROFILE_ID.fullmatch(authoritative):
            raise AccountProfileStoreError("权威账号标识无效")
        if active == authoritative:
            entry = registry["profiles"][active]
            entry.update(
                {
                    "display_name": metadata.display_name,
                    "profile_status": "verified",
                    "logged_in": True,
                    "last_active_at": _timestamp(),
                }
            )
            self._save_registry(registry)
            return entry
        if not active.startswith("pending-"):
            raise AccountProfileStoreError("当前本地账号与已验证身份不一致")
        if authoritative in registry["profiles"]:
            raise AccountProfileStoreError("该账号已保存在本机，请直接切换到已有账号")

        self.persist_active()
        registry = self.ensure_migrated()
        source = self.profile_root(active)
        destination = self.profile_root(authoritative)
        if destination.exists():
            raise AccountProfileStoreError("权威账号目录已存在，关联已停止")
        source.rename(destination)
        config = load_config(self.config_path)
        config.profile_dir = destination / "browser-profile"
        save_config(self.config_path, config)
        entry = registry["profiles"].pop(active)
        entry.update(
            {
                "profile_id": authoritative,
                "display_name": metadata.display_name,
                "profile_status": "verified",
                "logged_in": True,
                "last_active_at": _timestamp(),
                "associated_at": _timestamp(),
            }
        )
        registry["profiles"][authoritative] = entry
        registry["active_profile_id"] = authoritative
        self._save_registry(registry)
        return entry

    def public_payload(self) -> dict:
        registry = self.ensure_migrated()
        active = registry["active_profile_id"]
        profiles = []
        for profile_id, value in registry["profiles"].items():
            profiles.append(
                {
                    "profile_id": profile_id,
                    "display_name": value.get("display_name"),
                    "active": profile_id == active,
                    "logged_in": bool(value.get("logged_in")),
                    "profile_status": value.get(
                        "profile_status",
                        "unverified",
                    ),
                }
            )
        profiles.sort(key=lambda item: (not item["active"], item["display_name"] or ""))
        return {"active_profile_id": active, "profiles": profiles}

    def public_payload_if_available(self) -> dict:
        try:
            payload = self.public_payload()
        except AccountProfileStoreError:
            if self.registry_path.exists():
                raise
            return {
                "active_profile_id": None,
                "profiles": [],
                "migration_required": True,
            }
        return {**payload, "migration_required": False}

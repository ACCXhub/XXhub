"""Validated, local-only import and export helpers for the dashboard.

This deliberately uses a small ZIP + JSON/TXT format instead of a database or
migration framework.  Every archive is fully checked before any local file is
changed, and a non-sensitive rollback package is written before an import.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import csv
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import zipfile

from autody.config import AppConfig, MessageSuffixConfig, Target, load_config, save_config
from autody.messages import read_messages


class TransferError(ValueError):
    pass


class ImportMode(str, Enum):
    MERGE = "merge"
    REPLACE = "replace"


class ExportCategory(str, Enum):
    FRIENDS = "friends"
    MESSAGES = "messages"
    SUFFIX = "suffix"
    SCHEDULE = "schedule"
    SENDING = "sending"
    MESSAGE_PACKS = "message_packs"
    SETTINGS = "settings"
    ROTATION_STATE = "rotation_state"


ALL_CATEGORIES = set(ExportCategory)
DEFAULT_CATEGORIES = ALL_CATEGORIES - {ExportCategory.ROTATION_STATE}
CATEGORY_FILES = {
    ExportCategory.FRIENDS: "friends.json",
    ExportCategory.MESSAGES: "messages.txt",
    ExportCategory.SUFFIX: "suffix.json",
    ExportCategory.SCHEDULE: "schedule.json",
    ExportCategory.SENDING: "sending.json",
    ExportCategory.MESSAGE_PACKS: "message-packs.json",
    ExportCategory.SETTINGS: "settings.json",
    ExportCategory.ROTATION_STATE: "rotation-state.json",
}
SCHEDULE_FIELDS = (
    "daily_health_check_time", "daily_send_time", "weekly_health_check_enabled",
    "weekly_health_check_weekday", "weekly_health_check_time",
    "recovery_deadline",
)
SENDING_FIELDS = (
    "retry_count", "timeout_ms", "headless", "min_delay_seconds", "max_delay_seconds",
    "page_load_timeout_ms", "friend_search_timeout_ms", "confirmation_timeout_ms",
    "friend_order", "message_selection", "completion_notifications_enabled",
    "mask_log_friend_names", "log_retention_days",
)
SETTINGS_FIELDS = ("mask_log_friend_names", "completion_notifications_enabled", "log_retention_days")
BLOCKED_SUFFIXES = {".exe", ".cmd", ".bat", ".ps1", ".dll", ".py", ".js", ".vbs"}


@dataclass(frozen=True)
class MessageImportPreview:
    messages: list[str]
    total_count: int
    valid_count: int
    exact_duplicates: int
    empty_count: int
    long_count: int
    link_count: int


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _config_fields(config: AppConfig, fields: tuple[str, ...]) -> dict:
    return {name: getattr(config, name) for name in fields}


def create_backup(config: AppConfig, categories: set[ExportCategory] | None = None) -> bytes:
    categories = categories or ALL_CATEGORIES
    unknown = set(categories) - ALL_CATEGORIES
    if unknown:
        raise TransferError("包含未知导出类别")
    files: dict[str, bytes] = {}
    for category in categories:
        name = CATEGORY_FILES[category]
        if category is ExportCategory.FRIENDS:
            files[name] = _json([item.model_dump(mode="json", exclude_none=True) for item in config.targets])
        elif category is ExportCategory.MESSAGES:
            files[name] = config.messages_file.read_bytes() if config.messages_file.exists() else b""
        elif category is ExportCategory.SUFFIX:
            files[name] = _json(config.message_suffix.model_dump(mode="json"))
        elif category is ExportCategory.SCHEDULE:
            files[name] = _json(_config_fields(config, SCHEDULE_FIELDS))
        elif category is ExportCategory.SENDING:
            files[name] = _json(_config_fields(config, SENDING_FIELDS))
        elif category is ExportCategory.MESSAGE_PACKS:
            files[name] = _json({})
        elif category is ExportCategory.SETTINGS:
            files[name] = _json(_config_fields(config, SETTINGS_FIELDS))
        elif category is ExportCategory.ROTATION_STATE:
            state = config.state_file.read_bytes() if config.state_file.exists() else b"{}"
            files[name] = state
    checksums = {name: _sha256(payload) for name, payload in files.items()}
    manifest = {
        "format": "autody-backup",
        "version": 2,
        "autody_version": "1.0.0",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "categories": sorted(category.value for category in categories),
        "checksums": checksums,
        "files": {name: {"sha256": checksums[name], "size": len(payload)} for name, payload in files.items()},
    }
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json(manifest))
        for name, payload in files.items():
            archive.writestr(name, payload)
    return out.getvalue()


def _safe_zip(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    if len(raw) > 20 * 1024 * 1024:
        raise TransferError("备份包过大")
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise TransferError("备份包不是有效 ZIP") from exc
    with archive:
        names = archive.namelist()
        allowed = {"manifest.json", *CATEGORY_FILES.values()}
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or path.name != name or name not in allowed:
                raise TransferError("备份包包含不安全路径或未知文件")
            if path.suffix.lower() in BLOCKED_SUFFIXES:
                raise TransferError("备份包包含不允许的可执行文件")
        if "manifest.json" not in names:
            raise TransferError("备份包缺少 manifest.json")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (json.JSONDecodeError, KeyError) as exc:
            raise TransferError("备份包 manifest 无效") from exc
        if manifest.get("format") != "autody-backup" or manifest.get("version") != 2:
            raise TransferError("不支持的备份包版本")
        files = {name: archive.read(name) for name in names if name != "manifest.json"}
    expected = manifest.get("files")
    if not isinstance(expected, dict) or set(expected) != set(files):
        raise TransferError("备份包文件清单不匹配")
    for name, payload in files.items():
        if not isinstance(expected[name], dict) or expected[name].get("sha256") != _sha256(payload):
            raise TransferError(f"备份包校验失败：{name}")
    return manifest, files


def parse_message_import(raw: bytes, filename: str) -> MessageImportPreview:
    suffix = Path(filename).suffix.lower()
    try:
        text = raw.decode("utf-8-sig")
        if suffix == ".txt":
            rows = text.splitlines()
        elif suffix == ".csv":
            rows = [row.get("message", row.get("text", "")) for row in csv.DictReader(StringIO(text))]
        elif suffix == ".json":
            value = json.loads(text)
            rows = value.get("messages", value) if isinstance(value, dict) else value
            if not isinstance(rows, list):
                raise TransferError("文案 JSON 必须是数组")
        else:
            raise TransferError("文案导入仅支持 TXT、CSV 或 JSON")
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
        raise TransferError(f"文案文件无法解析：{exc}") from exc
    messages: list[str] = []
    seen: set[str] = set()
    empty = duplicate = long = links = 0
    for item in rows:
        value = str(item).strip()
        if not value:
            empty += 1
            continue
        if len(value) > 500:
            long += 1
            continue
        if "http://" in value.lower() or "https://" in value.lower():
            links += 1
        if value in seen:
            duplicate += 1
            continue
        seen.add(value)
        messages.append(value)
    return MessageImportPreview(messages, len(rows), len(messages), duplicate, empty, long, links)


def _read_json(payload: bytes, label: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TransferError(f"{label} 无法解析") from exc


def _parse_backup_targets(raw: bytes) -> list[Target]:
    """Parse only the private JSON member of a complete AutoDy backup."""
    value = _read_json(raw, "好友配置")
    rows = value.get("friends", value) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise TransferError("好友配置格式无效")
    targets: list[Target] = []
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TransferError("好友配置包含无效条目")
        try:
            target = Target.model_validate(
                {key: value for key, value in row.items() if key in Target.model_fields}
            )
        except Exception as exc:
            raise TransferError("好友配置包含无效条目") from exc
        if target.name in names:
            raise TransferError("好友配置包含重复名称")
        names.add(target.name)
        targets.append(target)
    return targets


def preview_backup(raw: bytes, config: AppConfig) -> dict:
    manifest, files = _safe_zip(raw)
    categories = [ExportCategory(item) for item in manifest.get("categories", [])]
    result: dict = {
        "package_version": manifest["version"], "autody_version": manifest.get("autody_version"),
        "categories": [item.value for item in categories], "friend_count": 0, "message_count": 0,
        "schedule_changes": {}, "suffix_change": False, "conflicts": [],
    }
    if "friends.json" in files:
        targets = _parse_backup_targets(files["friends.json"])
        result["friend_count"] = len(targets)
        result["conflicts"] = [item.name for item in targets if any(old.name == item.name for old in config.targets)]
    if "messages.txt" in files:
        result["message_count"] = parse_message_import(files["messages.txt"], "messages.txt").valid_count
    if "schedule.json" in files:
        value = _read_json(files["schedule.json"], "计划设置")
        if not isinstance(value, dict):
            raise TransferError("计划设置格式无效")
        result["schedule_changes"] = {key: {"old": getattr(config, key), "new": value.get(key)} for key in SCHEDULE_FIELDS if key in value and getattr(config, key) != value[key]}
    if "suffix.json" in files:
        value = _read_json(files["suffix.json"], "后缀设置")
        result["suffix_change"] = value != config.message_suffix.model_dump(mode="json")
    return result


def _write_messages(path: Path, messages: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(messages) + ("\n" if messages else ""), encoding="utf-8")
    os.replace(temporary, path)


def _backup_before_import(config: AppConfig) -> Path:
    backup_dir = config.state_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"before-import-{datetime.now():%Y%m%d-%H%M%S}.zip"
    path.write_bytes(create_backup(config, ALL_CATEGORIES))
    return path


def apply_backup(raw: bytes, config_path: Path, config: AppConfig, *, mode: ImportMode) -> dict:
    preview = preview_backup(raw, config)  # Full validation must happen first.
    _manifest, files = _safe_zip(raw)
    candidate = config.model_copy(deep=True)
    result = {"friends": {"imported": 0, "skipped": 0, "duplicated": 0, "conflicted": 0}, "messages": {"imported": 0, "skipped": 0, "duplicated": 0, "conflicted": 0}, "failed": 0, "backup": ""}
    message_final: list[str] | None = None
    if "friends.json" in files:
        imported = _parse_backup_targets(files["friends.json"])
        if mode is ImportMode.REPLACE:
            candidate.targets = imported
            result["friends"]["imported"] = len(imported)
        else:
            existing = {item.name for item in candidate.targets}
            for item in imported:
                if item.name in existing:
                    result["friends"]["conflicted"] += 1
                else:
                    candidate.targets.append(item)
                    existing.add(item.name)
                    result["friends"]["imported"] += 1
    if "messages.txt" in files:
        incoming = parse_message_import(files["messages.txt"], "messages.txt").messages
        existing = read_messages(config.messages_file) if config.messages_file.exists() else []
        if mode is ImportMode.REPLACE:
            message_final = incoming
            result["messages"]["imported"] = len(incoming)
        else:
            additions = [item for item in incoming if item not in set(existing)]
            message_final = existing + additions
            result["messages"]["imported"] = len(additions)
            result["messages"]["duplicated"] = len(incoming) - len(additions)
    if "suffix.json" in files:
        candidate.message_suffix = MessageSuffixConfig.model_validate(_read_json(files["suffix.json"], "后缀设置"))
    for file_name, fields in (("schedule.json", SCHEDULE_FIELDS), ("sending.json", SENDING_FIELDS), ("settings.json", SETTINGS_FIELDS)):
        if file_name in files:
            value = _read_json(files[file_name], file_name)
            if not isinstance(value, dict):
                raise TransferError(f"{file_name} 格式无效")
            for key in fields:
                if key in value:
                    setattr(candidate, key, value[key])
    # Re-run pydantic validation after all imported fields are applied.
    candidate = AppConfig.model_validate(candidate.model_dump())
    backup = _backup_before_import(config)
    config_bytes = config_path.read_bytes() if config_path.exists() else None
    message_bytes = config.messages_file.read_bytes() if config.messages_file.exists() else None
    try:
        save_config(config_path, candidate)
        if message_final is not None:
            _write_messages(config.messages_file, message_final)
        if "rotation-state.json" in files:
            state = config.state_file
            state.parent.mkdir(parents=True, exist_ok=True)
            temporary = state.with_suffix(".tmp")
            temporary.write_bytes(files["rotation-state.json"])
            os.replace(temporary, state)
    except Exception as exc:
        if config_bytes is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(config_bytes)
        if message_bytes is None:
            config.messages_file.unlink(missing_ok=True)
        else:
            config.messages_file.write_bytes(message_bytes)
        raise TransferError(f"导入失败，已回滚：{exc}") from exc
    result["backup"] = str(backup)
    result["preview"] = preview
    return result


def apply_message_import(
    config: AppConfig, imported: MessageImportPreview, *, mode: ImportMode
) -> dict:
    existing = read_messages(config.messages_file) if config.messages_file.exists() else []
    if mode is ImportMode.REPLACE:
        final = imported.messages
        added = len(final)
        duplicates = imported.exact_duplicates
    else:
        existing_set = set(existing)
        additions = [item for item in imported.messages if item not in existing_set]
        final = existing + additions
        added = len(additions)
        duplicates = imported.exact_duplicates + len(imported.messages) - len(additions)
    backup = _backup_before_import(config)
    original = config.messages_file.read_bytes() if config.messages_file.exists() else None
    try:
        _write_messages(config.messages_file, final)
    except Exception as exc:
        if original is None:
            config.messages_file.unlink(missing_ok=True)
        else:
            config.messages_file.write_bytes(original)
        raise TransferError(f"文案导入失败，已回滚：{exc}") from exc
    return {
        "imported": added, "skipped": 0, "duplicated": duplicates, "conflicted": 0,
        "failed": imported.empty_count + imported.long_count, "backup": str(backup),
        "total": len(final),
    }

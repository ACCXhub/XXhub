from enum import Enum
import os
from pathlib import Path
import time
import uuid

from pydantic import BaseModel, Field, model_validator
import yaml


class Target(BaseModel):
    name: str = Field(min_length=1)
    enabled: bool = True
    note: str = ""
    stable_id: str | None = None
    candidate_id: str | None = None
    message_pack: str | None = None
    suffix_override: str | None = None
    suffix_mode: str = Field(default="global", pattern=r"^(global|disabled|custom)$")
    delay_offset_minutes: int = Field(default=0, ge=0, le=30)
    message_selection: str | None = Field(default=None, pattern=r"^(one_for_all|per_friend)$")
    send_order: int | None = Field(default=None, ge=0)


class MessageSuffixStyle(str, Enum):
    DASH = "dash"
    BRACKET = "bracket"
    NEWLINE = "newline"
    NONE = "none"


class MessageSuffixConfig(BaseModel):
    enabled: bool = True
    text: str = "gpt小助手"
    style: MessageSuffixStyle = MessageSuffixStyle.DASH


class AppConfig(BaseModel):
    targets: list[Target] = Field(default_factory=list)
    messages_file: Path = Path("messages.txt")
    profile_dir: Path = Path("data/browser-profile")
    state_file: Path = Path("data/state.json")
    lock_file: Path = Path("data/locks/autody.lock")
    artifact_dir: Path = Path("data/artifacts")
    retry_count: int = Field(default=3, ge=1, le=5)
    timeout_ms: int = Field(default=30_000, ge=5_000, le=120_000)
    headless: bool = True
    message_suffix: MessageSuffixConfig = Field(default_factory=MessageSuffixConfig)
    message_pack_index_url: str | None = None
    daily_send_time: str = Field(default="07:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_health_check_time: str = Field(default="07:20", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekly_health_check_enabled: bool = True
    weekly_health_check_weekday: str = Field(default="Sunday", pattern=r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$")
    weekly_health_check_time: str = Field(default="20:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    startup_recovery_enabled: bool = True
    recovery_deadline: str = Field(default="23:59", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    min_delay_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    max_delay_seconds: float = Field(default=3.0, ge=0.0, le=60.0)
    page_load_timeout_ms: int = Field(default=30_000, ge=5_000, le=120_000)
    friend_search_timeout_ms: int = Field(default=30_000, ge=5_000, le=120_000)
    confirmation_timeout_ms: int = Field(default=12_000, ge=2_000, le=60_000)
    friend_order: str = Field(default="configured", pattern=r"^(configured|randomized)$")
    message_selection: str = Field(default="one_for_all", pattern=r"^(one_for_all|per_friend)$")
    completion_notifications_enabled: bool = True
    preflight_after_health_enabled: bool = True
    log_retention_days: int = Field(default=30, ge=7, le=3650)
    log_cleanup_enabled: bool = True
    active_log_retention_days: int = Field(default=14, ge=3, le=3650)
    archive_log_retention_days: int = Field(default=90, ge=3, le=3650)
    mask_log_friend_names: bool = True
    friend_scan_lock_timeout_ms: int = Field(default=5_000, ge=1_000, le=30_000)
    friend_scan_overall_timeout_ms: int = Field(default=90_000, ge=10_000, le=300_000)
    friend_scan_max_rounds: int = Field(default=20, ge=1, le=100)
    avatar_capture_timeout_ms: int = Field(default=2_000, ge=500, le=30_000)

    @model_validator(mode="after")
    def unique_targets(self):
        names = [target.name.strip() for target in self.targets]
        for target, name in zip(self.targets, names, strict=True):
            target.name = name
        stable_ids = [target.stable_id for target in self.targets if target.stable_id]
        if len(stable_ids) != len(set(stable_ids)):
            raise ValueError("target stable IDs must be unique")
        candidate_ids = [target.candidate_id for target in self.targets if target.candidate_id]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("target candidate IDs must be unique")
        if self.message_pack_index_url is not None:
            self.message_pack_index_url = self.message_pack_index_url.strip() or None
        if self.recovery_deadline < self.daily_send_time:
            raise ValueError("recovery deadline must not be earlier than daily send time")
        if self.min_delay_seconds > self.max_delay_seconds:
            raise ValueError("minimum delay must not exceed maximum delay")
        if self.archive_log_retention_days < self.active_log_retention_days:
            raise ValueError("archive log retention must not be shorter than active retention")
        return self


def enabled_execution_targets(config: AppConfig) -> list[Target]:
    """Return the enabled target set that a modern binding-aware run can use.

    Legacy name-only projects remain supported until at least one candidate
    binding exists. Once candidate bindings are present, partially migrated or
    detached records must not inflate planning counts or reach the sender.
    """
    enabled = [target for target in config.targets if target.enabled]
    if not any(target.candidate_id for target in config.targets):
        return enabled
    return [
        target
        for target in enabled
        if target.stable_id and target.candidate_id
    ]


def load_config(path: Path) -> AppConfig:
    path = path.resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = AppConfig.model_validate(data)
    for field_name in ("messages_file", "profile_dir", "state_file", "lock_file", "artifact_dir"):
        value = getattr(config, field_name)
        if not value.is_absolute():
            setattr(config, field_name, path.parent / value)
    return config


def save_config(path: Path, config: AppConfig) -> None:
    path = path.resolve()
    root = path.parent

    def portable(value: Path) -> str:
        try:
            return str(value.resolve().relative_to(root))
        except ValueError:
            return str(value)

    data = {
        "targets": [target.model_dump(mode="json", exclude_none=True) for target in config.targets],
        "messages_file": portable(config.messages_file),
        "profile_dir": portable(config.profile_dir),
        "state_file": portable(config.state_file),
        "lock_file": portable(config.lock_file),
        "artifact_dir": portable(config.artifact_dir),
        "retry_count": config.retry_count,
        "timeout_ms": config.timeout_ms,
        "headless": config.headless,
        "message_suffix": config.message_suffix.model_dump(mode="json"),
        "message_pack_index_url": config.message_pack_index_url,
        "daily_send_time": config.daily_send_time,
        "daily_health_check_time": config.daily_health_check_time,
        "weekly_health_check_enabled": config.weekly_health_check_enabled,
        "weekly_health_check_weekday": config.weekly_health_check_weekday,
        "weekly_health_check_time": config.weekly_health_check_time,
        "startup_recovery_enabled": config.startup_recovery_enabled,
        "recovery_deadline": config.recovery_deadline,
        "min_delay_seconds": config.min_delay_seconds,
        "max_delay_seconds": config.max_delay_seconds,
        "page_load_timeout_ms": config.page_load_timeout_ms,
        "friend_search_timeout_ms": config.friend_search_timeout_ms,
        "confirmation_timeout_ms": config.confirmation_timeout_ms,
        "friend_order": config.friend_order,
        "message_selection": config.message_selection,
        "completion_notifications_enabled": config.completion_notifications_enabled,
        "preflight_after_health_enabled": config.preflight_after_health_enabled,
        "log_retention_days": config.log_retention_days,
        "log_cleanup_enabled": config.log_cleanup_enabled,
        "active_log_retention_days": config.active_log_retention_days,
        "archive_log_retention_days": config.archive_log_retention_days,
        "mask_log_friend_names": config.mask_log_friend_names,
        "friend_scan_lock_timeout_ms": config.friend_scan_lock_timeout_ms,
        "friend_scan_overall_timeout_ms": config.friend_scan_overall_timeout_ms,
        "friend_scan_max_rounds": config.friend_scan_max_rounds,
        "avatar_capture_timeout_ms": config.avatar_capture_timeout_ms,
    }
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)

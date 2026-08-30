from collections import defaultdict
from datetime import date, datetime, timedelta
from email.utils import formatdate
from io import BytesIO, StringIO
from importlib.metadata import PackageNotFoundError, version
import csv
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import quote
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import yaml

from autody import scheduler
from autody.config import (
    AppConfig,
    MessageSuffixConfig,
    Target,
    enabled_daily_targets,
    enabled_execution_targets,
    load_config,
    save_config,
    target_identity,
)
from autody.chat import (
    DOUYIN_CONFIRMATION_SELECTORS,
    DOUYIN_SELECTORS,
    AuthenticationError,
    DouyinChat,
    FatalChatError,
    open_chat,
)
from autody.daily_status import (
    dashboard_statistics,
    effective_daily_target_statuses,
    is_post_send_confirmation,
)
from autody.account_profile import (
    bindings_revalidation_required,
    clear_managed_authentication,
    complete_binding_revalidation,
    evaluate_account_scope,
    load_account_profile,
    logout_managed_account,
    public_profile_payload,
)
from autody.account_profiles import AccountProfileStoreError, MultiAccountStore
from autody.binding_recovery import (
    AUTHORITATIVE_BINDING_IDENTITY_SOURCES,
    all_stable_bindings_proven,
    reassociate_stable_binding,
    resolve_stable_binding,
)
from autody.failures import FailureDetail, failure_detail
from autody.friend_discovery import is_discovery_stale, load_discovered_friends
from autody.history import TaskHistoryStore, bootstrap_legacy_daily_history
from autody.log_center import archive_historical_logs, archive_logs, automatic_cleanup_once_daily, cleanup_logs, log_storage_summary, log_summary, query_logs, record_cleanup_result
from autody.message_packs import ImportMode, MessagePackError, MessagePackService
from autody.message_pack_catalog import MessagePackConflict
from autody.modules import (
    MODULE_ID,
    OFFICIAL_TEST_CENTER_VERSION,
    ModuleManager,
    ModulePackageError,
    ensure_official_module_archive,
)
from autody.messages import read_messages
from autody.locking import SingleInstanceLock, TaskAlreadyRunning
from autody.preflight import PreflightStore
from autody.runner import (
    TodayDeliveryReconciliation,
    apply_today_delivery_reconciliation,
    plan_today_delivery_reconciliation,
    preview_today_target_message,
    reconcile_today_delivery,
)
from autody.state import StateStore
from autody.scheduler import ScheduleSettings, SchedulerService
from autody.test_center_dry_run import (
    batch_target_exclusion_reasons,
    DryRunController,
    DryRunSettings,
    eligible_batch_targets,
)
from autody.transfer import (
    DEFAULT_CATEGORIES,
    ExportCategory,
    ImportMode as BackupImportMode,
    TransferError,
    apply_backup,
    apply_message_import,
    create_backup,
    parse_message_import,
    preview_backup,
)
from autody.web_actions import ActionAlreadyRunning, ActionManager


def _application_version() -> str:
    try:
        return version("autody")
    except PackageNotFoundError:
        return "unknown"


def _git_commit(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=2,
            check=False,
        )
    except OSError:
        return None
    return completed.stdout.strip() or None


class ConfigUpdate(BaseModel):
    targets: list[str] = Field(default_factory=list)
    retry_count: int = Field(ge=1, le=5)
    timeout_ms: int = Field(ge=5_000, le=120_000)
    headless: bool
    message_suffix: MessageSuffixConfig = Field(default_factory=MessageSuffixConfig)
    default_message_pack: str | None = "daily-greeting"
    daily_send_time: str = Field(default="07:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_health_check_time: str = Field(default="07:20", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekly_health_check_enabled: bool = True
    weekly_health_check_weekday: str = Field(default="Sunday", pattern=r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$")
    weekly_health_check_time: str = Field(default="20:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    recovery_deadline: str = Field(default="23:59", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    min_delay_seconds: float = Field(default=0.0, ge=0, le=60)
    max_delay_seconds: float = Field(default=0.0, ge=0, le=60)
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


class MessagesUpdate(BaseModel):
    messages: list[str]


class DryRunStartRequest(BaseModel):
    target_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1, max_length=80)
    request_revision: int = Field(ge=1)
    test_text: str = Field(default="", max_length=1000)
    use_today_message: bool = True
    automatic: bool = False
    navigation_only: bool = False
    batch_target_ids: list[str] | None = Field(default=None, max_length=50)


class DryRunSelectRequest(BaseModel):
    target_id: str = Field(min_length=1)
    request_revision: int = Field(ge=1)


class MessagePackImportRequest(BaseModel):
    mode: ImportMode


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class CreateMessagePackRequest(RevisionRequest):
    name: str = Field(default="新建文案包", min_length=1, max_length=80)


class RenameMessagePackRequest(RevisionRequest):
    name: str = Field(min_length=1, max_length=80)


class ReorderMessagePacksRequest(RevisionRequest):
    pack_ids: list[str]


class MessagePackTextRequest(RevisionRequest):
    text: str = Field(min_length=1, max_length=500)


class FuseMessagePackRequest(RevisionRequest):
    destination_id: str = Field(min_length=1)


class SplitMessagePackRequest(RevisionRequest):
    source_id: str = Field(min_length=1)


class ScheduleUpdate(ScheduleSettings):
    pass


class BackupExportRequest(BaseModel):
    categories: list[ExportCategory] = Field(default_factory=lambda: list(DEFAULT_CATEGORIES))


class FriendBatchUpdate(BaseModel):
    target_ids: list[str] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    action: str = Field(pattern=r"^(enable|disable|delete)$")


class DiscoveredFriendBatchAdd(BaseModel):
    candidate_ids: list[str] = Field(default_factory=list)


class FriendRelinkRequest(BaseModel):
    target_id: str = Field(min_length=1, max_length=80)


class AccountLogoutRequest(BaseModel):
    confirmed: bool = False


class PreflightRunRequest(BaseModel):
    target_ids: list[str] | None = None


class TargetSettingsUpdate(BaseModel):
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=120)
    message_pack: str | None = Field(default=None, max_length=80)
    suffix_mode: str | None = Field(default=None, pattern=r"^(global|disabled|custom)$")
    suffix_override: str | None = Field(default=None, max_length=120)
    delay_offset_minutes: int | None = Field(default=None, ge=0, le=30)
    message_selection: str | None = Field(default=None, pattern=r"^(one_for_all|per_friend)$")
    send_order: int | None = Field(default=None, ge=0)
    reset_overrides: bool = False


_SAFE_AVATAR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_FALLBACK_AVATAR = """<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 48 48\" role=\"img\" aria-label=\"默认头像\"><circle cx=\"24\" cy=\"24\" r=\"24\" fill=\"#e6f0fc\"/><circle cx=\"24\" cy=\"18\" r=\"8\" fill=\"#7e9ec4\"/><path d=\"M10 42c2-9 8-14 14-14s12 5 14 14\" fill=\"#7e9ec4\"/></svg>""".encode("utf-8")


def _tail(path: Path, limit: int = 400) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 131_072))
        text = handle.read().decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-limit:])


def _tail_scheduler_logs(log_dir: Path, limit: int = 400) -> str:
    files = sorted(log_dir.glob("scheduler-????-??-??.log"))
    legacy = log_dir / "scheduler.log"
    if not files and legacy.is_file():
        files = [legacy]
    lines: list[str] = []
    for path in files[-14:]:
        lines.extend(_tail(path, limit).splitlines())
    return "\n".join(lines[-limit:])


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _avatar_id(value: str | None) -> str | None:
    return value if value and _SAFE_AVATAR_ID.fullmatch(value) else None


def _avatar_url(value: str | None, version: str | None = None) -> str:
    url = f"/api/avatars/{_avatar_id(value) or 'missing'}"
    return f"{url}?v={quote(version, safe='')}" if version else url


def _avatar_path(cache_dir: Path, identifier: str) -> Path:
    return cache_dir / f"{identifier}.png"


def _effective_target_settings(target: Target, config: AppConfig, override: dict | None = None) -> dict:
    override = override or {}
    suffix_mode = override.get("suffix_mode", "global")
    suffix_override = override.get("suffix_override")
    if suffix_mode == "disabled":
        suffix = "已禁用"
    elif suffix_mode == "custom" and isinstance(suffix_override, str) and suffix_override.strip():
        suffix = suffix_override.strip()
    else:
        suffix = "全局后缀" if config.message_suffix.enabled else "未设置后缀"
    return {
        "message_source": (
            override.get("message_pack")
            or config.default_message_pack
            or "全局本地文案库"
        ),
        "message_source_origin": "override" if override.get("message_pack") else "global",
        "suffix": suffix,
        "suffix_origin": "override" if suffix_mode != "global" else "global",
        "message_selection": override.get("message_selection") or config.message_selection,
        "message_selection_origin": "override" if override.get("message_selection") else "global",
        "delay_offset_minutes": int(override.get("delay_offset_minutes", 0)),
        "send_order": override.get("send_order"),
    }


_SAFE_RETRY_CODES = {
    "friend_not_found",
    "login_required",
    "browser_unavailable",
    "browser_busy",
    "conversation_open_failed",
    "conversation_load_timeout",
    "composer_missing",
    "composer_hidden",
    "composer_disabled",
    "send_control_missing",
    "send_failed_before_action",
    "account_scope_mismatch",
}

_FAILURE_EXPLANATIONS = {
    "friend_not_found": "未找到对应聊天，尚未触发发送。",
    "login_required": "登录状态不可用，尚未触发发送。",
    "browser_unavailable": "浏览器运行环境不可用，尚未触发发送。",
    "browser_busy": "浏览器正在执行其他任务，尚未触发发送。",
    "conversation_open_failed": "无法打开对应聊天，尚未触发发送。",
    "conversation_load_timeout": "聊天页面加载超时，尚未触发发送。",
    "composer_missing": "未找到消息输入区域，尚未触发发送。",
    "composer_hidden": "消息输入区域不可见，尚未触发发送。",
    "composer_disabled": "消息输入区域不可用，尚未触发发送。",
    "send_control_missing": "未找到发送控件，尚未触发发送。",
    "send_failed_before_action": "发送前准备失败，尚未触发发送。",
    "confirmation_failed_uncertain": "发送结果不确定，为避免重复发送，已禁止自动重试。",
    "blocked_ambiguous_target": "昵称存在歧义，已阻止自动发送。",
    "page_structure_changed": "页面结构可能变化，已停止自动发送。",
    "unexpected_error": "任务出现异常，请查看详细原因。",
}


def _failure_code(value: object, confirmation: object) -> str:
    text = f"{value or ''} {confirmation or ''}".casefold()
    if "confirmation" in text or "confirm" in text:
        return "confirmation_failed_uncertain"
    if "ambiguous" in text:
        return "blocked_ambiguous_target"
    for code in _SAFE_RETRY_CODES:
        if code in text:
            return code
    if "not found" in text or "未找到" in text:
        return "friend_not_found"
    if "composer" in text:
        return "composer_missing"
    if "conversation" in text or "chat" in text:
        return "conversation_open_failed"
    if "login" in text or "登录" in text:
        return "login_required"
    return "unexpected_error"


def _legacy_failure_detail(
    config: AppConfig,
    target: Target,
    *,
    value: object,
    confirmation: object,
    run_id: str | None,
) -> FailureDetail:
    code = _failure_code(value, confirmation)
    reason_code = {
        "friend_not_found": "conversation_not_found",
        "conversation_open_failed": "conversation_not_found",
        "conversation_load_timeout": "page_load_timeout",
        "send_control_missing": "composer_missing",
        "send_failed_before_action": "send_failed_before_action",
        "blocked_ambiguous_target": "blocked_ambiguous_target",
        "confirmation_failed_uncertain": "confirmation_failed_uncertain",
        "login_required": "login_required",
        "account_scope_mismatch": "account_scope_mismatch",
        "browser_busy": "browser_busy",
    }.get(code, "unknown_exception")
    stage = {
        "conversation_not_found": "conversation_located",
        "page_load_timeout": "browser_opened",
        "composer_missing": "composer_found",
        "send_failed_before_action": "message_prepared",
        "blocked_ambiguous_target": "target_binding_resolved",
        "confirmation_failed_uncertain": "confirmation_observed",
        "login_required": "account_verified",
        "account_scope_mismatch": "account_verified",
        "browser_busy": "browser_opened",
    }.get(reason_code, "conversation_located")
    return failure_detail(
        reason_code,
        stage=stage,
        send_attempts=int(reason_code == "confirmation_failed_uncertain"),
        run_id=run_id,
        target_stable_id=target_identity(target),
        binding_valid=None,
        account_scope_matches=None,
        diagnostic_details={
            "legacy_failure": True,
            "confirmation_status": str(confirmation or ""),
        },
    )


def _revalidate_failure_detail(
    config: AppConfig,
    target: Target,
    detail: FailureDetail,
) -> FailureDetail:
    identity = target_identity(target)
    discovered = load_discovered_friends(
        config.state_file.parent / "discovered_friends.json"
    )
    profile = load_account_profile(config.state_file.parent.parent)
    guarded = bindings_revalidation_required(config.state_file.parent)
    resolution = resolve_stable_binding(
        target,
        discovered,
        profile,
        revalidation_required=guarded,
    )
    diagnostics = {**detail.diagnostic_details, "binding_resolution": resolution.status}
    if resolution.account_comparison:
        diagnostics["account_comparison"] = resolution.account_comparison
    context = {
        "run_id": detail.run_id,
        "target_stable_id": identity,
        "account_scope": discovered.account_scope
        if discovered
        else detail.account_scope,
        "diagnostic_details": diagnostics,
    }
    if detail.uncertain_send or detail.send_attempts > 0:
        return detail.model_copy(
            update={
                "target_stable_id": identity,
                "diagnostic_details": diagnostics,
            }
        )
    if resolution.status == "account_unverified":
        return failure_detail(
            "login_required",
            stage="account_verified",
            binding_valid=False,
            account_scope_matches=False,
            **context,
        )
    if resolution.status == "account_mismatch":
        return failure_detail(
            "account_scope_mismatch",
            stage="account_verified",
            binding_valid=False,
            account_scope_matches=False,
            **context,
        )
    if resolution.status == "binding_missing":
        return failure_detail(
            "binding_missing",
            stage="target_binding_resolved",
            binding_valid=False,
            account_scope_matches=None,
            **context,
        )
    if not resolution.valid:
        return failure_detail(
            "blocked_ambiguous_target"
            if resolution.status == "identity_ambiguous"
            else "binding_stale",
            stage="target_binding_resolved",
            binding_valid=False,
            account_scope_matches=None,
            **context,
        )
    if (
        detail.reason_code in {"account_scope_mismatch", "login_required"}
        and detail.send_attempts == 0
        and not detail.uncertain_send
    ):
        return failure_detail(
            "send_failed_before_action",
            stage=detail.stage,
            binding_valid=True,
            account_scope_matches=True,
            **context,
        )
    return detail.model_copy(
        update={
            "target_stable_id": identity,
            "account_scope": discovered.account_scope,
            "binding_valid": True,
            "account_scope_matches": True,
            "diagnostic_details": diagnostics,
        }
    )


def _daily_failure_detail(
    config: AppConfig,
    target: Target,
    daily: dict,
) -> FailureDetail | None:
    identity = target_identity(target)
    stored = daily.get("target_failures", {}).get(identity)
    if stored:
        try:
            detail = FailureDetail.model_validate(stored)
            return _revalidate_failure_detail(config, target, detail)
        except (TypeError, ValueError):
            pass
    if target.name not in daily.get("failures", {}):
        return None
    return _revalidate_failure_detail(config, target, _legacy_failure_detail(
        config,
        target,
        value=daily.get("failures", {}).get(target.name),
        confirmation=daily.get("confirmation_results", {}).get(identity),
        run_id=daily.get("task_run_id"),
    ))


def _current_target_health(
    target: Target,
    *,
    discovered,
    profile,
    binding_guarded: bool,
    now: datetime,
    resolution=None,
) -> dict[str, str]:
    resolution = resolution or resolve_stable_binding(
        target,
        discovered,
        profile,
        revalidation_required=binding_guarded,
        now=now,
    )
    if resolution.valid:
        return {
            "status": "healthy",
            "reason_code": "binding_valid",
            "summary_zh": "绑定有效",
        }
    status, reason_code, summary_zh = {
        "binding_missing": ("abnormal", "binding_missing", "目标缺少权威稳定绑定，需要重新关联"),
        "stale_locator": ("abnormal", "binding_stale", "当前扫描中的会话定位已失效，需要重新关联"),
        "identity_ambiguous": ("abnormal", "identity_ambiguous", "当前候选身份存在歧义，未自动关联"),
        "account_mismatch": ("abnormal", "account_scope_mismatch", "当前登录账号与目标所属账号不一致"),
        "account_unverified": ("unknown", "account_scope_unverified", "当前账号范围尚未验证"),
        "scan_unavailable": ("unknown", "scan_unavailable", "当前扫描不可用"),
        "scan_incomplete": ("unknown", "scan_incomplete", "当前扫描未完成"),
        "scan_failed": ("unknown", "scan_failed", "最近一次实时扫描失败"),
        "scan_stale": ("unknown", "scan_stale", "当前扫描已过期"),
        "revalidation_required": ("abnormal", "binding_revalidation_required", "目标绑定待重新验证"),
    }[resolution.status]
    return {"status": status, "reason_code": reason_code, "summary_zh": summary_zh}


def _history_target_failures(
    config: AppConfig,
    record,
    daily_by_date: dict[str, dict],
) -> dict[str, FailureDetail]:
    failures = dict(record.target_failures)
    daily = daily_by_date.get(record.date)
    if (
        not daily
        or daily.get("task_run_id") != record.run_id
        or record.task_type != "daily_send"
    ):
        return failures
    failed_target_ids = set(record.failed_target_ids)
    if failed_target_ids:
        failures = {
            target_id: detail
            for target_id, detail in failures.items()
            if target_id in failed_target_ids
        }
    for target in config.targets:
        identity = target_identity(target)
        if identity not in failed_target_ids:
            continue
        detail = _daily_failure_detail(config, target, daily)
        if detail is not None:
            failures[identity] = detail
    return failures


def _history_failure_views(
    config: AppConfig,
    records: list,
    daily_by_date: dict[str, dict],
) -> list[dict[str, dict]]:
    failures_by_record = [
        _history_target_failures(config, record, daily_by_date)
        for record in records
    ]
    confirmed_at_by_day: dict[str, dict[str, str]] = defaultdict(dict)
    for record in records:
        for target_id, result in record.confirmation_results.items():
            if not is_post_send_confirmation(
                result,
                record.confirmation_provenance.get(target_id),
            ):
                continue
            previous = confirmed_at_by_day[record.date].get(target_id)
            confirmed_at_by_day[record.date][target_id] = max(
                previous or record.end_time,
                record.end_time,
            )
    views: list[dict[str, dict]] = []
    for record, failures in zip(records, failures_by_record):
        record_view: dict[str, dict] = {}
        for target_id, detail in failures.items():
            confirmed_at = confirmed_at_by_day[record.date].get(target_id)
            resolved = confirmed_at is not None
            record_view[target_id] = {
                **detail.model_dump(mode="json"),
                "resolved": resolved,
                "resolved_at": confirmed_at,
                "resolution_zh": (
                    "已通过后续成功补发解决"
                    if confirmed_at and confirmed_at > record.end_time
                    else "当日已有确认成功"
                    if resolved
                    else None
                ),
                "retry_action_available": (
                    detail.safe_retry_available and not resolved
                ),
            }
        views.append(record_view)
    return views


def _today_plan(config: AppConfig, state, day: date, overrides: dict[str, dict] | None = None) -> dict:
    daily = state.daily.get(day.isoformat(), {})
    failures = daily.get("failures", {})
    required = enabled_daily_targets(config)
    executable = enabled_execution_targets(config)
    effective_statuses = effective_daily_target_statuses(config, state, day)
    executable_ids = {id(target) for target in executable}
    normalized: dict[str, int] = defaultdict(int)
    for target in required:
        normalized[" ".join(target.name.split()).casefold()] += 1
    base = datetime.combine(day, datetime.strptime(config.daily_send_time, "%H:%M").time())
    enabled = required
    ordered = sorted(
        enumerate(enabled),
        key=lambda row: ((overrides or {}).get(target_identity(row[1]), {}).get("send_order") is None, (overrides or {}).get(target_identity(row[1]), {}).get("send_order", row[0]), row[0]),
    )
    rows = []
    blocked = 0
    for fallback_order, target in ordered:
        identity = target_identity(target)
        binding_missing = id(target) not in executable_ids
        ambiguous = normalized[" ".join(target.name.split()).casefold()] > 1
        failure = failures.get(target.name)
        code = _failure_code(failure, daily.get("confirmation_results", {}).get(identity)) if failure else None
        effective_status = effective_statuses.get(identity)
        status = (
            "blocked"
            if binding_missing or ambiguous or code == "blocked_ambiguous_target"
            else effective_status or "pending"
        )
        if status == "blocked":
            blocked += 1
        settings = _effective_target_settings(target, config, (overrides or {}).get(identity))
        rows.append({
            "target_id": identity,
            "display_name": target.name,
            "planned_at": (base + timedelta(minutes=settings["delay_offset_minutes"])).isoformat(timespec="minutes"),
            "status": status,
            "blocked_reason": (
                "目标缺少稳定绑定，需要重新关联。"
                if binding_missing
                else "昵称存在歧义，已阻止自动发送。"
                if ambiguous
                else _FAILURE_EXPLANATIONS.get(code or "", None)
            ),
            **settings,
        })
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "configuration_source": "current",
        "main_scheduled_time": config.daily_send_time,
        "enabled_target_count": len(required),
        "completed_count": sum(row["status"] == "success" for row in rows),
        "pending_count": sum(row["status"] == "pending" for row in rows),
        "blocked_count": blocked,
        "estimated_finish": max((row["planned_at"] for row in rows), default=base.isoformat(timespec="minutes")),
        "targets": rows,
    }


def _failed_targets(config: AppConfig, state, day: date) -> dict:
    daily = state.daily.get(day.isoformat(), {})
    effective_statuses = effective_daily_target_statuses(config, state, day)
    rows = []
    for target in enabled_daily_targets(config):
        identity = target_identity(target)
        if effective_statuses.get(identity) != "failed":
            continue
        detail = _daily_failure_detail(config, target, daily)
        if detail is None:
            continue
        rows.append({
            "target_id": identity,
            "display_name": target.name,
            "failure_time": f"{day.isoformat()}T{config.daily_send_time}:00",
            "trigger_source": "retry" if daily.get("message") else "scheduled",
            **detail.model_dump(mode="json"),
            "explanation": detail.user_summary_zh,
            "no_send_action_definitely_occurred": detail.send_attempts == 0,
            "uncertain": detail.uncertain_send,
            "safe_retry_available": detail.safe_retry_available,
            "latest_preflight_status": None,
            "latest_send_status": daily.get("confirmation_results", {}).get(identity) or "failed_before_action",
            "resolved": False,
        })
    return {
        "items": rows,
        "summary": {
            "success": sum(
                status == "success" for status in effective_statuses.values()
            ),
            "failed": len(rows),
            "uncertain": sum(row["uncertain"] for row in rows),
            "needs_attention": len(rows),
        },
    }


def _last_success_date(state, name: str) -> str | None:
    for key in sorted(state.daily, reverse=True):
        if name in state.daily[key].get("succeeded", []):
            return key
    return None


def _login_status(path: Path, log_dir: Path | None = None) -> str:
    if not path.exists():
        if log_dir is None:
            return "unknown"
        dated = sorted(log_dir.glob("autody-????-??-??.log"))
        fallback = dated[-1] if dated else log_dir / "autody.log"
        if not fallback.exists():
            return "unknown"
        text = _tail(fallback, 200)
        successful = text.rfind("登录状态和抖音聊天页正常")
        failed = max(text.rfind("登录健康检查失败"), text.rfind("浏览器任务已安全停止"))
        if successful < 0 and failed < 0:
            return "unknown"
        return "failed" if failed > successful else "success"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("status", "unknown"))
    except (json.JSONDecodeError, OSError, TypeError):
        return "unknown"


def _message_count(path: Path) -> int:
    try:
        return len(read_messages(path))
    except (FileNotFoundError, ValueError):
        return 0


def _runtime_available(root: Path) -> bool:
    configured = os.environ.get("AUTODY_BROWSERS_PATH", "").strip()
    browsers = Path(configured).resolve() if configured else root / "data" / "ms-playwright"
    return any(browsers.glob("chromium-*/chrome-win*/chrome.exe"))


def _config_payload(config: AppConfig) -> dict:
    return {
        "targets": [target.name for target in config.targets],
        "retry_count": config.retry_count,
        "timeout_ms": config.timeout_ms,
        "headless": config.headless,
        "message_suffix": config.message_suffix.model_dump(mode="json"),
        "default_message_pack": config.default_message_pack,
        "daily_send_time": config.daily_send_time,
        "daily_health_check_time": config.daily_health_check_time,
        "weekly_health_check_enabled": config.weekly_health_check_enabled,
        "weekly_health_check_weekday": config.weekly_health_check_weekday,
        "weekly_health_check_time": config.weekly_health_check_time,
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


def _portable_config(config_path: Path, config: AppConfig) -> bytes:
    root = config_path.parent

    def portable(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return str(path)

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
        "daily_send_time": config.daily_send_time,
        "daily_health_check_time": config.daily_health_check_time,
        "weekly_health_check_enabled": config.weekly_health_check_enabled,
        "weekly_health_check_weekday": config.weekly_health_check_weekday,
        "weekly_health_check_time": config.weekly_health_check_time,
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
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")


def create_app(
    config_path: Path,
    action_runner=None,
    now_provider=None,
    account_logout_runner=None,
    startup_refresh_enabled: bool = False,
    today_reconciler=None,
) -> FastAPI:
    config_path = config_path.resolve()
    root = config_path.parent
    program_root = Path(os.environ.get("AUTODY_PROGRAM_ROOT", root)).resolve()
    scheduler_task_user_id = scheduler.current_process_user_sid()
    manager = ActionManager(program_root, config_path)
    account_store = MultiAccountStore(root, config_path)
    run_action = action_runner or manager.start
    current_time = now_provider or datetime.now
    startup_job: dict | None = None
    app = FastAPI(title="AutoDy", docs_url=None, redoc_url=None)
    # Log maintenance is deliberately best-effort and limited by its local date
    # marker; it must never prevent the dashboard from starting.
    initial_config = load_config(config_path)
    manager.module_data_root = initial_config.state_file.parent / "modules" / MODULE_ID / "data"
    module_manager = ModuleManager(initial_config.state_file.parent, core_version=_application_version())
    dry_run_controller = DryRunController(config_path, module_manager.module_root / "data")

    def message_pack_service() -> MessagePackService:
        return MessagePackService(
            program_root,
            root,
            now=current_time,
        )

    def message_pack_http_exception(exc: MessagePackError) -> HTTPException:
        if isinstance(exc, MessagePackConflict):
            return HTTPException(409, str(exc))
        message = str(exc)
        if message.startswith("未知文案包"):
            return HTTPException(404, message)
        if (
            message.startswith("文案包目录无效")
            or "事务日志" in message
            or message.startswith("内置文案包索引无效")
        ):
            return HTTPException(503, message)
        return HTTPException(422, message)
    try:
        bundled_module = ensure_official_module_archive(root)
        bundled_module_error = None
    except ModulePackageError as exc:
        bundled_module = None
        bundled_module_error = str(exc)

    def module_overrides() -> dict[str, dict]:
        path = module_manager.module_root / "data" / "overrides.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def save_module_overrides(value: dict[str, dict]) -> None:
        path = module_manager.module_root / "data" / "overrides.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def ignored_orphan_target_ids() -> set[str]:
        path = initial_config.state_file.parent / "ignored-friend-bindings.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return set()
        values = payload.get("target_ids", []) if isinstance(payload, dict) else []
        return {str(value) for value in values if isinstance(value, str)}

    def save_ignored_orphan_target_ids(values: set[str]) -> None:
        path = initial_config.state_file.parent / "ignored-friend-bindings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"target_ids": sorted(values)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def actionable_orphan_names(config: AppConfig, discovered) -> set[str]:
        current_ids = {
            candidate.candidate_id
            for candidate in (discovered.candidates if discovered else [])
            if candidate.presence_status == "current"
        }
        return {
            " ".join(target.name.split()).casefold()
            for target in config.targets
            if target.stable_id
            and target.candidate_id
            and target.candidate_id not in current_ids
        }

    def refresh_binding_guard(config: AppConfig, discovered=None) -> bool:
        discovered = discovered or load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        return complete_binding_revalidation(
            config.state_file.parent,
            bindings_proven=all_stable_bindings_proven(
                config.targets,
                discovered,
                load_account_profile(config.messages_file.parent),
            ),
        )
    if initial_config.log_cleanup_enabled:
        automatic_cleanup_once_daily(initial_config.state_file.parent / "logs", active_days=initial_config.active_log_retention_days, archive_days=initial_config.archive_log_retention_days)
    task_cache: dict[str, object] = {"expires": 0.0, "rows": []}
    preflight_job_id: str | None = None
    module_preflight_job_id: str | None = None

    def module_data_path(name: str) -> Path:
        root_path = module_manager.module_root / "data"
        path = (root_path / name).resolve()
        if not name or path == root_path.resolve() or root_path.resolve() not in path.parents:
            raise HTTPException(400, "模块数据路径不安全")
        return path

    def append_module_history(title: str) -> None:
        path = module_data_path("history.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"title": title, "created_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")

    def preview_state() -> dict:
        config = load_config(config_path)
        path = module_data_path("preview/state.json")
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        targets = [
            {"target_id": target_identity(target), "display_name": target.name}
            for target in enabled_execution_targets(config)
        ]
        selected = str(stored.get("selected_target_id", "")) or None
        if selected not in {item["target_id"] for item in targets}:
            selected = None
        return {
            "targets": targets,
            "selected_target_id": selected,
            "navigation_status": "待人工查看" if selected else "未选择目标",
            "page_open": False,
            "conversation_matches": False,
            "manual_ready": False,
            "last_refreshed_at": stored.get("updated_at"),
            "real_composer_writes": 0,
        }

    def dry_run_targets() -> list[dict]:
        config = load_config(config_path)
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        presence = {
            candidate.candidate_id: candidate.presence_status
            for candidate in discovered.candidates
        } if discovered else {}
        exclusion_reasons = batch_target_exclusion_reasons(config, presence)
        return [
            {
                "target_id": target_identity(target),
                "conversation_id": target.candidate_id,
                "display_name": target.name,
                "single_eligible": bool(target.enabled and target.stable_id and target.candidate_id),
                "batch_eligible": exclusion_reasons[index] is None,
                "batch_exclusion_reason": exclusion_reasons[index],
            }
            for index, target in enumerate(config.targets)
        ]

    def sanitized_dry_run_settings(
        settings: DryRunSettings | None = None,
        *,
        persist: bool,
    ) -> DryRunSettings:
        current = settings or dry_run_controller.settings()
        eligible_ids = [
            item["target_id"]
            for item in dry_run_targets()
            if item["batch_eligible"]
        ]
        if current.selected_batch_target_ids is None:
            selected_ids = eligible_ids
        else:
            requested = set(current.selected_batch_target_ids)
            selected_ids = [target_id for target_id in eligible_ids if target_id in requested]
        sanitized = current.model_copy(update={"selected_batch_target_ids": selected_ids})
        if persist and sanitized != current:
            dry_run_controller.save_settings(sanitized)
        return sanitized

    def dry_run_target(target_id: str):
        config = load_config(config_path)
        return next(
            (
                target for target in config.targets
                if target.enabled and target.stable_id == target_id
            ),
            None,
        )

    def dry_run_payload() -> dict:
        payload = dry_run_controller.status()
        targets = dry_run_targets()
        payload["settings"] = sanitized_dry_run_settings(persist=True).model_dump()
        selected = next(
            (item for item in targets if item["target_id"] == payload.get("selected_target_id")),
            None,
        )
        if selected is None:
            payload["selected_target_id"] = None
            payload["expected_conversation_id"] = None
            payload["selected_display_name"] = None
        else:
            payload["expected_conversation_id"] = selected["conversation_id"]
            payload["selected_display_name"] = selected["display_name"]
        payload["targets"] = targets
        payload["eligible_target_count"] = sum(
            bool(item["batch_eligible"]) for item in targets
        )
        return payload

    def preflight_payload(config: AppConfig, result: dict | None) -> dict:
        if not result:
            return {"result": None}
        names = {target_identity(target): target.name for target in config.targets}
        safe = dict(result)
        safe["targets"] = [
            {**row, "display_name": names.get(row.get("target_id"), "已移除目标")}
            for row in result.get("targets", [])
        ]
        return {"result": safe}

    def cached_task_rows() -> list[dict]:
        if time.monotonic() >= float(task_cache["expires"]):
            task_cache["rows"] = scheduler.windows_task_rows()
            task_cache["expires"] = time.monotonic() + 10
        return list(task_cache["rows"])  # type: ignore[arg-type]

    def dashboard_scheduler_service() -> SchedulerService:
        return SchedulerService(
            program_root,
            data_root=root,
            task_user_id=scheduler_task_user_id,
        )

    def invalidate_task_cache() -> None:
        task_cache["rows"] = []
        task_cache["expires"] = 0.0

    def finish_repair_action(action: str) -> dict:
        job = run_action(action)
        if job.get("status") != "running" or action_runner is not None:
            return job
        for _ in range(2400):
            current = manager.get(str(job.get("id", "")))
            if current is None or current.get("status") != "running":
                return current or job
            time.sleep(0.25)
        return {**job, "status": "failed", "exit_code": None}

    def reconcile_repair_today_delivery(
        config: AppConfig,
        today: date,
    ) -> tuple[TodayDeliveryReconciliation | None, str | None]:
        """Run one serialized chat audit and supplemental send, if warranted.

        This deliberately consumes the already persisted targeted discovery
        snapshot.  It never invokes friend discovery itself.
        """
        required = enabled_daily_targets(config)
        if not required:
            return TodayDeliveryReconciliation({}, 0, False), None
        plan = plan_today_delivery_reconciliation(config, today)

        def unresolved_live_audit(
            source: str,
        ) -> TodayDeliveryReconciliation:
            outcomes = {
                **plan.outcomes,
                **{
                    target_id: "unknown"
                    for target_id in plan.live_audit_target_ids
                },
            }
            evidence_sources = {
                **plan.evidence_sources,
                **{
                    target_id: source
                    for target_id in plan.live_audit_target_ids
                },
            }
            statuses = apply_today_delivery_reconciliation(
                config,
                outcomes,
                today,
                evidence_sources=evidence_sources,
            )
            return TodayDeliveryReconciliation(
                outcomes,
                sum(status == "success" for status in statuses.values()),
                bool(required)
                and all(
                    statuses.get(target_identity(target)) == "success"
                    for target in required
                ),
                live_audit_required=len(plan.live_audit_target_ids),
                evidence_sources=evidence_sources,
            )

        if not plan.live_audit_target_ids:
            return (
                reconcile_today_delivery(config, None, today, plan=plan),
                None,
            )

        live_audit_ids = set(plan.live_audit_target_ids)
        live_audit_targets = [
            target
            for target in required
            if target_identity(target) in live_audit_ids
        ]
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        profile = load_account_profile(config.state_file.parent.parent)
        guarded = bindings_revalidation_required(config.state_file.parent)
        if not any(
            resolve_stable_binding(
                target,
                discovered,
                profile,
                revalidation_required=guarded,
                now=current_time(),
            ).valid
            for target in live_audit_targets
        ):
            return unresolved_live_audit("binding_unavailable"), None
        try:
            with SingleInstanceLock(config.lock_file):
                with open_chat(
                    config.profile_dir,
                    config.page_load_timeout_ms,
                    config.headless,
                    config.artifact_dir,
                    home=root,
                ) as page:
                    chat = DouyinChat(
                        page,
                        DOUYIN_SELECTORS,
                        config.artifact_dir,
                        DOUYIN_CONFIRMATION_SELECTORS,
                        confirmation_delay_ms=max(
                            250, config.confirmation_timeout_ms // 3
                        ),
                        friend_search_timeout_ms=config.friend_search_timeout_ms,
                    )
                    return reconcile_today_delivery(
                        config, chat, today, plan=plan
                    ), None
        except (TaskAlreadyRunning, AuthenticationError, FatalChatError, RuntimeError) as exc:
            return unresolved_live_audit("live_chat_audit_unavailable"), type(exc).__name__

    @app.get("/api/service-identity")
    def service_identity():
        package_path = Path(__file__).resolve().parent
        return {
            "application": "AutoDy",
            "version": _application_version(),
            "git_commit": _git_commit(root),
            "python_executable": sys.executable,
            "package_path": str(package_path),
            "project_path": str(root),
            "frontend_build_version": _application_version(),
            "frontend_static_path": str(Path(__file__).parent / "web" / "static"),
            "bundled_module": bundled_module,
            "startup_error": bundled_module_error,
        }

    def startup_readiness_payload() -> dict:
        nonlocal startup_job
        config = load_config(config_path)
        if not startup_refresh_enabled or not any(
            target.enabled for target in config.targets
        ):
            return {
                "ready": True,
                "successful": True,
                "status": "not_required",
                "message": "启动准备完成",
                "progress": None,
            }
        if startup_job is None:
            try:
                startup_job = run_action("startup-refresh")
            except ActionAlreadyRunning:
                return {
                    "ready": False,
                    "successful": False,
                    "status": "waiting_for_browser",
                    "message": "正在等待浏览器任务",
                    "progress": None,
                }
        current = startup_job
        if action_runner is None and current.get("id"):
            current = manager.get(str(current["id"])) or current
            startup_job = current
        progress_path = config.state_file.parent / "friend_scan_progress.json"
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            progress = None
        terminal = current.get("status") in {"success", "failed"}
        message = (
            str(progress.get("message"))
            if isinstance(progress, dict) and progress.get("message")
            else "正在准备续火目标"
        )
        return {
            "ready": terminal,
            "successful": current.get("status") == "success",
            "status": current.get("status", "running"),
            "message": message,
            "progress": progress,
        }

    @app.get("/api/startup-readiness")
    def startup_readiness():
        return startup_readiness_payload()

    @app.get("/api/startup-readiness.js")
    def startup_readiness_script():
        payload = json.dumps(startup_readiness_payload(), ensure_ascii=False)
        return Response(
            f"window.autodyStartupReady({payload});",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/repair")
    def diagnose_and_repair():
        before = status()
        config = load_config(config_path)
        repaired: list[dict[str, str]] = []
        manual: list[dict[str, str]] = []
        checks: list[dict[str, str]] = []

        if _runtime_available(root):
            checks.append({"id": "runtime", "label": "浏览器运行时正常"})
        else:
            try:
                job = finish_repair_action("repair-playwright")
                if job.get("status") == "success" and _runtime_available(root):
                    repaired.append({"id": "runtime", "label": "浏览器运行时已恢复"})
                else:
                    manual.append({"id": "runtime", "label": "浏览器运行时未能自动恢复，请查看运行日志"})
            except (ActionAlreadyRunning, RuntimeError) as exc:
                manual.append({"id": "runtime", "label": f"浏览器运行时修复未完成：{exc}"})

        scheduler_issue_ids = {"scheduler_missing", "scheduler_runtime_mismatch"}
        scheduler_needs_repair = bool(
            scheduler_issue_ids & {item["id"] for item in before["issues"]}
            or any(
                row.get("drift")
                or row.get("installed") != row.get("configured_enabled")
                for row in before["scheduler"]
            )
        )
        if scheduler_needs_repair:
            try:
                dashboard_scheduler_service().repair(config)
                invalidate_task_cache()
                repaired.append({"id": "scheduler", "label": "Scheduler 已恢复"})
            except RuntimeError as exc:
                manual.append({"id": "scheduler", "label": f"Scheduler 未能自动恢复：{exc}"})
        else:
            checks.append({"id": "scheduler", "label": "Scheduler 状态正常"})

        binding_guarded = bindings_revalidation_required(config.state_file.parent)
        profile_missing = load_account_profile(root) is None
        if (profile_missing or binding_guarded) and before["login"]["status"] != "failed":
            try:
                job = finish_repair_action("refresh-account-profile")
                if job.get("status") == "success" and load_account_profile(root) is not None:
                    repaired.append({"id": "account_profile", "label": "账号状态已刷新"})
                elif profile_missing:
                    manual.append({"id": "account_profile", "label": "当前账号资料无法自动验证，请手动刷新"})
            except (ActionAlreadyRunning, RuntimeError) as exc:
                manual.append({"id": "account_profile", "label": f"账号资料刷新未完成：{exc}"})
        elif before["login"]["status"] == "failed":
            manual.append({"id": "account_profile", "label": "登录已失效，请手动扫码登录"})
        else:
            checks.append({"id": "account_profile", "label": "账号状态正常"})

        if binding_guarded:
            if refresh_binding_guard(load_config(config_path)):
                repaired.append({"id": "bindings", "label": "目标稳定绑定已恢复"})
            else:
                manual.append({
                    "id": "bindings",
                    "label": "无法用稳定身份确认原目标，已保留 needs_reassociation，请手动重新关联",
                })
        else:
            checks.append({"id": "bindings", "label": "目标稳定绑定正常"})

        config = load_config(config_path)
        reconciliation, reconciliation_error = (
            today_reconciler(config, current_time().date())
            if today_reconciler is not None
            else reconcile_repair_today_delivery(config, current_time().date())
        )
        evidence_counts: dict[str, int] = defaultdict(int)
        if reconciliation:
            for source in reconciliation.evidence_sources.values():
                evidence_counts[source] += 1
        today_delivery = {
            "outcomes": reconciliation.outcomes if reconciliation else {},
            "confirmed_sent": sum(
                outcome == "confirmed_sent"
                for outcome in (reconciliation.outcomes.values() if reconciliation else [])
            ),
            "confirmed_missing": sum(
                outcome == "confirmed_missing"
                for outcome in (reconciliation.outcomes.values() if reconciliation else [])
            ),
            "unknown": sum(
                outcome == "unknown"
                for outcome in (reconciliation.outcomes.values() if reconciliation else [])
            ),
            "pre_supplement_success_count": (
                reconciliation.pre_supplement_success_count if reconciliation else 0
            ),
            "pre_supplement_complete": (
                reconciliation.pre_supplement_complete if reconciliation else False
            ),
            "supplemented": (
                reconciliation.supplement_result.sent_count
                if reconciliation and reconciliation.supplement_result
                else 0
            ),
            "scan_count": 0,
            "live_audit_required": (
                reconciliation.live_audit_required if reconciliation else 0
            ),
            "evidence": dict(evidence_counts),
        }
        if reconciliation_error:
            manual.append({
                "id": "today_delivery",
                "label": "今日聊天记录待核实，未自动补发",
            })
        elif today_delivery["confirmed_missing"]:
            repaired.append({
                "id": "today_delivery",
                "label": f"已按聊天记录补发 {today_delivery['supplemented']} 位好友",
            })
        elif today_delivery["unknown"]:
            checks.append({"id": "today_delivery", "label": "今日投递记录待核实，未自动补发"})
        else:
            checks.append({"id": "today_delivery", "label": "今日投递记录已核对"})

        repaired_count = len(repaired)
        manual_count = len(manual)
        summary = f"已修复 {repaired_count} 项"
        if manual_count:
            summary += f"；仍需手动处理 {manual_count} 项"
        elif repaired_count == 0:
            summary = "诊断完成，当前无需修复"
        return {
            "repaired": repaired,
            "manual": manual,
            "checks": checks,
            "today_delivery": today_delivery,
            "summary": summary,
        }

    @app.get("/api/status")
    def status(today: str | None = None):
        config = load_config(config_path)
        state = StateStore(config.state_file).load()
        key = today or date.today().isoformat()
        daily = state.daily.get(
            key, {"message": "", "succeeded": [], "failures": {}, "consumed": False}
        )
        required_targets = enabled_daily_targets(config)
        executable_targets = enabled_execution_targets(config)
        history_store = TaskHistoryStore(
            config.state_file.parent / "history" / "task-runs.jsonl"
        )
        bootstrap_legacy_daily_history(history_store, state.daily, len(config.targets))
        history_page = history_store.query(page_size=30)
        effective_statuses = effective_daily_target_statuses(
            config,
            state,
            date.fromisoformat(key),
        )
        executable_succeeded = {
            target_id
            for target_id, target_status in effective_statuses.items()
            if target_status == "success"
        }
        executable_failures = {
            target_id
            for target_id, target_status in effective_statuses.items()
            if target_status == "failed"
        }
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        profile = load_account_profile(config.state_file.parent.parent)
        binding_guarded = bindings_revalidation_required(config.state_file.parent)
        friends = []
        candidates_by_id = {
            candidate.candidate_id: candidate
            for candidate in (discovered.candidates if discovered else [])
        }
        cache_dir = config.state_file.parent / "avatar-cache"
        executable_ids = {id(target) for target in executable_targets}
        for target in required_targets:
            target_id = target_identity(target)
            effective_status = effective_statuses.get(target_id)
            detail = (
                _daily_failure_detail(config, target, daily)
                if effective_status == "failed"
                else None
            )
            friend_status = (
                "blocked"
                if id(target) not in executable_ids
                else effective_status or "pending"
            )
            reconciliation_outcome = daily.get("delivery_reconciliation", {}).get(
                target_id
            )
            resolution = resolve_stable_binding(
                target,
                discovered,
                profile,
                revalidation_required=binding_guarded,
                now=current_time(),
            )
            candidate = candidates_by_id.get(
                resolution.candidate_id or target.candidate_id or ""
            )
            avatar_key = (
                candidate.avatar_cache_key or candidate.candidate_id
                if candidate and candidate.avatar_status == "cached"
                else None
            )
            avatar_status = (
                "cached"
                if avatar_key and _avatar_path(cache_dir, avatar_key).is_file()
                else "missing"
            )
            friends.append(
                {
                    "target_id": target_id,
                    "name": candidate.display_name if candidate else target.name,
                    "avatar_url": (
                        _avatar_url(avatar_key, candidate.avatar_updated_at)
                        if avatar_status == "cached" and candidate
                        else ""
                    ),
                    "avatar_status": avatar_status,
                    "status": friend_status,
                    "error": (
                        detail.user_summary_zh
                        if detail is not None
                        else "今日聊天记录不足，待核实。"
                        if reconciliation_outcome == "unknown"
                        else None
                    ),
                    "failure": (
                        detail.model_dump(mode="json")
                        if detail is not None
                        else None
                    ),
                    "current_health": _current_target_health(
                        target,
                        discovered=discovered,
                        profile=profile,
                        binding_guarded=binding_guarded,
                        now=current_time(),
                        resolution=resolution,
                    ),
                }
            )
        friends.sort(
            key=lambda friend: (
                0
                if friend["status"] == "failed"
                else 1
                if friend["current_health"]["status"] != "healthy"
                else 2
                if friend["status"] == "pending"
                else 3
            )
        )
        records = list(reversed(history_page.items))
        history_failure_views = _history_failure_views(
            config,
            history_page.items,
            state.daily,
        )
        history = [
            {
                "run_id": item.run_id,
                "date": item.date,
                "task_type": item.task_type,
                "trigger_source": item.trigger_source,
                "success_count": item.success_count,
                "failed_count": item.failed_count,
                "skipped_count": item.skipped_count,
                "total_targets": item.total_targets,
                "retry_count": item.retry_count,
                "final_status": item.final_status,
                "end_time": item.end_time,
                "target_failures": history_failure_views[index],
            }
            for index, item in enumerate(history_page.items)
        ]
        scheduler_available = True
        try:
            tasks = cached_task_rows()
        except Exception:
            tasks = []
            scheduler_available = False
        scheduler_rows = (
            scheduler.scheduler_status_rows(
                config,
                tasks,
                len(executable_targets),
                program_root=program_root,
                data_root=root,
                task_user_id=scheduler_task_user_id,
            )
            if scheduler_available
            else []
        )
        message_count = _message_count(config.messages_file)
        next_run = next(
            (
                task.get("next_run")
                for task in tasks
                if task.get("name") == "AutoDy-DailySpark"
            ),
            None,
        )
        next_health = next(
            (task.get("next_run") for task in tasks if task.get("name") == "AutoDy-Health-Daily"),
            None,
        )
        login_status = _login_status(
            config.state_file.parent / "health.json",
            config.state_file.parent / "logs",
        )
        issues = []
        if login_status == "failed":
            issues.append({
                "id": "login_expired", "status": "error",
                "explanation": "抖音登录已失效或需要安全验证。",
                "action": "login", "action_label": "扫码登录",
            })
        if binding_guarded:
            issues.append({
                "id": "account_bindings_revalidation_required",
                "status": "warning",
                "explanation": "当前账号的好友绑定待重新验证，定时发送已暂停。",
                "action": "friends",
                "action_label": "重新扫描好友",
            })
        if not _runtime_available(root):
            issues.append({
                "id": "runtime_missing", "status": "error",
                "explanation": "项目内 Chromium 缺失或不可用。",
                "action": "repair-playwright", "action_label": "修复运行时",
            })
        if not config.targets:
            issues.append({
                "id": "no_friends", "status": "warning",
                "explanation": "尚未配置续火好友。",
                "action": "friends", "action_label": "管理好友",
            })
        if message_count == 0:
            issues.append({
                "id": "no_messages", "status": "warning",
                "explanation": "本地文案库为空。",
                "action": "packs", "action_label": "导入文案",
            })
        if not any(task.get("name") == "AutoDy-DailySpark" for task in tasks):
            issues.append({
                "id": "scheduler_missing", "status": "warning",
                "explanation": "每日定时任务尚未安装。",
                "action": "scheduler", "action_label": "安装任务",
            })
        elif any(
            row.get("drift_reason") == "runtime_root_mismatch"
            for row in scheduler_rows
        ):
            issues.append({
                "id": "scheduler_runtime_mismatch",
                "status": "warning",
                "explanation": "定时任务运行位置与当前 AutoDy 安装不一致，可在定时任务页修复。",
                "action": "scheduler",
                "action_label": "修复任务",
            })
        current_failure_friends = [
            friend
            for friend in friends
            if friend["status"] == "failed"
            and friend["failure"] is not None
            and friend["failure"].get("resolved") is not True
        ]
        grouped_failures: dict[str, dict[str, object]] = {}
        for friend in current_failure_friends:
            failure = friend["failure"]
            if failure.get("uncertain_send"):
                kind, issue_status, action, action_label = (
                    "uncertain", "error", "logs", "查看详情",
                )
            elif failure.get("safe_retry_available"):
                kind, issue_status, action, action_label = (
                    "retryable", "warning", "retry_target", "安全补发",
                )
            elif failure.get("suggested_action") == "reassociate":
                kind, issue_status, action, action_label = (
                    "reassociate", "warning", "friends", "重新关联",
                )
            elif failure.get("suggested_action") == "switch_account":
                kind, issue_status, action, action_label = (
                    "account", "error", "login", "切换或登录账号",
                )
            else:
                kind, issue_status, action, action_label = (
                    "details", "warning", "logs", "查看详情",
                )
            group = grouped_failures.setdefault(
                kind,
                {
                    "status": issue_status,
                    "action": action,
                    "action_label": action_label,
                    "friends": [],
                },
            )
            group["friends"].append(friend)

        group_explanations = {
            "uncertain": "位好友的发送结果尚不确定，已禁止自动重试",
            "retryable": "位好友今日发送失败，当前满足安全补发条件",
            "reassociate": "位好友今日发送失败，需要先重新关联",
            "account": "位好友受当前账号状态阻止",
            "details": "位好友今日发送失败，需要人工查看详情",
        }
        for kind, group in grouped_failures.items():
            affected = group.pop("friends")
            if len(affected) == 1:
                friend = affected[0]
                explanation = (
                    f"{friend['name']}：{friend['failure']['user_summary_zh']}。"
                )
            else:
                explanation = f"今日有 {len(affected)} {group_explanations[kind]}。"
            issues.append(
                {
                    "id": f"send_failure_{kind}",
                    "explanation": explanation,
                    "target_ids": [friend["target_id"] for friend in affected],
                    **group,
                }
            )
        notice = config.state_file.parent / "notifications" / "need-attention.txt"
        if notice.exists() and not current_failure_friends:
            issues.append({
                "id": "notification", "status": "error",
                "explanation": "最近一次后台任务未完成，请查看当前日志中的处理建议。",
                "action": "logs", "action_label": "查看日志",
            })
        statistics = dashboard_statistics(
            state.daily,
            records,
            date.fromisoformat(key),
        )
        for friend in friends:
            friend["binding_status"] = (
                "revalidation_required" if binding_guarded else "verified"
            )
        try:
            pack_count = len(message_pack_service().list_packs().packs)
        except MessagePackError:
            pack_count = 0
        statistics.update({
            "successful_today": len(executable_succeeded),
            "failed_today": len(executable_failures),
            "configured_friend_count": len(config.targets),
            "enabled_friend_count": (
                0 if binding_guarded else len(required_targets)
            ),
            "local_message_count": message_count,
            "active_message_pack_count": pack_count,
            "next_health_check": next_health,
            "next_daily_send": next_run,
            "most_recent_issue": issues[0]["explanation"] if issues else None,
            "log_summary": log_summary(config.state_file.parent / "logs", config),
        })
        return {
            "today": {
                "date": key,
                "message": daily.get("message", ""),
                "succeeded": len(executable_succeeded),
                "failed": len(executable_failures),
                "total": len(required_targets),
                "complete": bool(required_targets)
                and len(executable_succeeded) == len(required_targets),
            },
            "friends": friends,
            "history": history[:30],
            "scheduler": scheduler_rows,
            "next_run": next_run,
            "login": {"status": login_status},
            "message_count": message_count,
            "issues": issues,
            "statistics": statistics,
        }

    def today_plan(today: str | None = None):
        config = load_config(config_path)
        try:
            plan_day = date.fromisoformat(today) if today else date.today()
        except ValueError as exc:
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from exc
        return _today_plan(config, StateStore(config.state_file).load(), plan_day, module_overrides())

    def failed_targets(today: str | None = None):
        config = load_config(config_path)
        try:
            failure_day = date.fromisoformat(today) if today else date.today()
        except ValueError as exc:
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from exc
        return _failed_targets(config, StateStore(config.state_file).load(), failure_day)

    def retry_failed_target(target_id: str, today: str | None = None):
        config = load_config(config_path)
        try:
            failure_day = date.fromisoformat(today) if today else date.today()
        except ValueError as exc:
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from exc
        failures = _failed_targets(config, StateStore(config.state_file).load(), failure_day)
        target = next((item for item in failures["items"] if item["target_id"] == target_id), None)
        if not target or not target["safe_retry_available"]:
            raise HTTPException(409, "该目标的发送结果不确定或未满足安全重试条件，已禁止自动重试。")
        try:
            return run_action("run-target", target_id=target_id)
        except ActionAlreadyRunning as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/failed-targets")
    def overview_failed_targets(today: str | None = None):
        return failed_targets(today)

    @app.post("/api/failed-targets/{target_id}/retry", status_code=202)
    def overview_retry_failed_target(target_id: str, today: str | None = None):
        return retry_failed_target(target_id, today)

    @app.get("/api/modules")
    def module_status():
        bundled_version = bundled_module["module_version"] if bundled_module else OFFICIAL_TEST_CENTER_VERSION
        status = module_manager.status()
        status["bundled_available"] = bundled_module is not None
        status["bundled_version"] = bundled_version
        status["core_version"] = module_manager.core_version
        status["update_available"] = bool(
            status["installed"]
            and bundled_module is not None
            and (
                status.get("version") != bundled_version
                or status.get("module_api_version") != bundled_module["module_api_version"]
                or status.get("package_sha256") != bundled_module["sha256"]
                or status.get("package_checksum") != bundled_module["package_checksum"]
            )
        )
        status["bundled_package"] = bundled_module
        status["load_error"] = bundled_module_error or status["load_error"]
        return {"modules": [status]}

    @app.post("/api/modules/autody-test-center/install")
    async def install_test_center(file: UploadFile | None = File(default=None)):
        if dry_run_controller.status()["running"]:
            raise HTTPException(409, "测试中心正在运行；停止并完成安全清理后才能更新。")
        temporary: Path | None = None
        try:
            if file is None:
                if bundled_module is None:
                    raise ModulePackageError(bundled_module_error or "官方模块包不可用")
                package = Path(bundled_module["path"])
            else:
                suffix = ".autody-module.zip"
                temporary = initial_config.state_file.parent / f".{uuid.uuid4().hex}{suffix}"
                temporary.write_bytes(await file.read())
                package = temporary
            return module_manager.install(package)
        except ModulePackageError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @app.post("/api/modules/autody-test-center/uninstall")
    def uninstall_test_center(payload: dict):
        if payload.get("confirmed") is not True:
            raise HTTPException(422, "卸载测试中心后，所有测试历史、测试设置和测试目标覆盖将被永久删除。AutoDy 的正常好友、文案、发送记录和浏览器数据不会受到影响。")
        if dry_run_controller.status()["running"]:
            dry_run_controller.stop()
            raise HTTPException(409, "测试中心正在安全清理输入框；完成后才能卸载。")
        cancel_path = module_manager.module_root / "data" / "preflight" / "cancel.json"
        if cancel_path.parent.is_dir():
            cancel_path.write_text(json.dumps({"requested_at": datetime.now().isoformat()}), encoding="utf-8")
        try:
            module_manager.uninstall()
        except ModulePackageError as exc:
            raise HTTPException(409, str(exc)) from exc
        return module_manager.status()

    def _require_test_center() -> None:
        if not module_manager.installed():
            raise HTTPException(404, "测试中心未安装")

    @app.get("/api/modules/autody-test-center/today-plan")
    def test_center_today_plan(today: str | None = None):
        _require_test_center()
        return today_plan(today)

    @app.get("/api/modules/autody-test-center/failed-targets")
    def test_center_failed_targets(today: str | None = None):
        _require_test_center()
        return failed_targets(today)

    @app.post("/api/modules/autody-test-center/failed-targets/{target_id}/retry", status_code=202)
    def test_center_retry_failed_target(target_id: str):
        _require_test_center()
        return retry_failed_target(target_id)

    @app.get("/api/modules/autody-test-center/diagnostics")
    def test_center_diagnostics():
        _require_test_center()
        return {"environment": "本机环境已加载；测试中心仅执行只读检查。", "launcher": "使用项目本地运行时。", "history": "暂无模块测试历史"}

    @app.get("/api/modules/autody-test-center/history")
    def test_center_history():
        _require_test_center()
        path = module_data_path("history.jsonl")
        items = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines()[-30:]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and isinstance(item.get("title"), str):
                    items.append({"title": item["title"], "created_at": str(item.get("created_at", ""))})
        return {"items": list(reversed(items))}

    @app.get("/api/modules/autody-test-center/preview")
    def test_center_preview():
        _require_test_center()
        return preview_state()

    @app.post("/api/modules/autody-test-center/preview/select")
    def test_center_select_preview(payload: dict):
        _require_test_center()
        target_id = str(payload.get("target_id", ""))
        current = preview_state()
        if target_id not in {item["target_id"] for item in current["targets"]}:
            raise HTTPException(422, "预览目标无效或已停用")
        path = module_data_path("preview/state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"selected_target_id": target_id, "updated_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")
        append_module_history("已切换预览目标（未操作真实聊天输入框）")
        return preview_state()

    @app.get("/api/modules/autody-test-center/dry-run/status")
    def test_center_dry_run_status():
        _require_test_center()
        return dry_run_payload()

    @app.get("/api/modules/autody-test-center/dry-run/message-preview")
    def test_center_dry_run_message_preview(target_id: str):
        _require_test_center()
        config = load_config(config_path)
        target = next(
            (
                item
                for item in config.targets
                if item.enabled and target_identity(item) == target_id
            ),
            None,
        )
        if target is None:
            raise HTTPException(422, "测试目标无效或已停用")
        try:
            preview = preview_today_target_message(config, target, date.today())
        except (OSError, ValueError, MessagePackError) as exc:
            raise HTTPException(422, f"今日文案不可用：{exc}") from exc
        return {"available": True, "text": preview.text, "mode": "today"}

    @app.put("/api/modules/autody-test-center/dry-run/settings")
    def test_center_dry_run_settings(settings: DryRunSettings):
        _require_test_center()
        return dry_run_controller.save_settings(
            sanitized_dry_run_settings(settings, persist=False)
        ).model_dump()

    @app.post("/api/modules/autody-test-center/dry-run/select")
    def test_center_dry_run_select(payload: DryRunSelectRequest):
        _require_test_center()
        target = dry_run_target(payload.target_id)
        if target is None:
            raise HTTPException(422, "测试目标无效或已停用")
        try:
            accepted = dry_run_controller.select(
                payload.target_id,
                request_revision=payload.request_revision,
                expected_conversation_id=target.candidate_id,
                selected_display_name=target.name,
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not accepted:
            raise HTTPException(409, "请求版本已过期")
        return dry_run_payload()

    @app.post("/api/modules/autody-test-center/dry-run/start", status_code=202)
    def test_center_dry_run_start(payload: DryRunStartRequest):
        _require_test_center()
        if dry_run_controller.store.recovery_warning():
            raise HTTPException(409, "检测到未完成的测试输入，请先人工检查聊天输入框。")
        target = dry_run_target(payload.target_id)
        if target is None:
            raise HTTPException(422, "测试目标无效或已停用")
        if not target.candidate_id:
            raise HTTPException(422, "测试目标缺少稳定会话身份")
        try:
            return dry_run_controller.start(
                payload.target_id,
                payload.test_text,
                automatic=payload.automatic,
                run_id=payload.run_id,
                request_revision=payload.request_revision,
                navigation_only=payload.navigation_only,
                use_today_message=payload.use_today_message,
                batch_target_ids=payload.batch_target_ids,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/modules/autody-test-center/dry-run/pause")
    def test_center_dry_run_pause():
        _require_test_center()
        return dry_run_controller.pause()

    @app.post("/api/modules/autody-test-center/dry-run/resume")
    def test_center_dry_run_resume():
        _require_test_center()
        return dry_run_controller.resume()

    @app.post("/api/modules/autody-test-center/dry-run/stop")
    def test_center_dry_run_stop():
        _require_test_center()
        return dry_run_controller.stop()

    @app.post("/api/modules/autody-test-center/dry-run/focus-browser")
    def test_center_dry_run_focus_browser():
        _require_test_center()
        try:
            return dry_run_controller.focus_browser()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/modules/autody-test-center/dry-run/history")
    def test_center_dry_run_history():
        _require_test_center()
        return {"items": dry_run_controller.history()}

    @app.post("/api/modules/autody-test-center/fixtures")
    def test_center_fixtures():
        _require_test_center()
        path = module_data_path("fixtures/safe-fixture.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"targets": ["好友A", "好友B"], "source": "documentation-safe-fixture"}, ensure_ascii=False), encoding="utf-8")
        append_module_history("已生成安全夹具数据")
        return {"created": True}

    @app.post("/api/modules/autody-test-center/simulate-failure")
    def test_center_simulate_failure():
        _require_test_center()
        append_module_history("已完成受控失败模拟（未调用真实发送）")
        return {"simulated": True, "send_actions": 0}

    @app.get("/api/modules/autody-test-center/preflight/status")
    def test_center_preflight_status():
        _require_test_center()
        config = load_config(config_path)
        store = PreflightStore(module_manager.module_root / "data" / "preflight")
        job = manager.get(module_preflight_job_id) if module_preflight_job_id else None
        return {"running": bool(job and job["status"] == "running"), "job": job, "progress": store.load_progress(), **preflight_payload(config, store.load_latest())}

    @app.post("/api/modules/autody-test-center/preflight/run", status_code=202)
    def test_center_preflight_run(payload: PreflightRunRequest):
        nonlocal module_preflight_job_id
        _require_test_center()
        config = load_config(config_path)
        valid_ids = {
            target_identity(target)
            for target in enabled_execution_targets(config)
        }
        if payload.target_ids is not None and (not payload.target_ids or any(item not in valid_ids for item in payload.target_ids)):
            raise HTTPException(422, "续火目标无效或已停用")
        request_path = module_data_path("preflight/request.json")
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps({"target_ids": payload.target_ids}, ensure_ascii=False), encoding="utf-8")
        try:
            job = run_action("module-preflight")
        except ActionAlreadyRunning as exc:
            raise HTTPException(409, str(exc)) from exc
        module_preflight_job_id = str(job["id"])
        append_module_history("已启动只读发送前自检")
        return job

    @app.post("/api/modules/autody-test-center/preflight/cancel")
    def test_center_preflight_cancel():
        _require_test_center()
        path = module_data_path("preflight/cancel.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"requested_at": datetime.now().isoformat()}), encoding="utf-8")
        return {"cancelled": True}

    @app.get("/api/modules/autody-test-center/frontend/{path:path}")
    def test_center_frontend(path: str):
        _require_test_center()
        frontend_root = module_manager.module_root / "frontend"
        target = (frontend_root / path).resolve()
        if not path or frontend_root.resolve() not in target.parents or not target.is_file():
            raise HTTPException(404, "模块资源不存在")
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    @app.get("/api/account-profile")
    def account_profile():
        config = load_config(config_path)
        return public_profile_payload(
            root,
            logged_in=_login_status(config.state_file.parent / "health.json", config.state_file.parent / "logs") == "success",
        )

    @app.post("/api/account-profile/refresh", status_code=202)
    def refresh_account_profile():
        config = load_config(config_path)
        try:
            job = run_action("refresh-account-profile")
        except ActionAlreadyRunning as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            **public_profile_payload(
                root,
                logged_in=_login_status(config.state_file.parent / "health.json", config.state_file.parent / "logs") == "success",
                refresh_running=job.get("status") == "running",
            ),
            "job": job,
        }

    def account_change_guard(config: AppConfig, action_zh: str) -> None:
        def busy() -> HTTPException:
            return HTTPException(
                409,
                failure_detail(
                    "account_operation_busy",
                    stage="account_verified",
                    diagnostic_details={"operation": action_zh},
                ).model_dump(mode="json"),
            )

        send_marker = config.lock_file.parent / "daily-send-active.json"
        if send_marker.is_file() or dry_run_controller.status().get("running"):
            raise busy()
        if action_runner is None and manager.browser_action_running():
            raise busy()

    @app.get("/api/account-profiles")
    def account_profiles():
        try:
            if action_runner is None and not manager.browser_action_running():
                registry = account_store._load_registry()
                if (
                    registry
                    and str(registry.get("active_profile_id", "")).startswith(
                        "pending-"
                    )
                    and load_account_profile(root) is not None
                ):
                    account_store.associate_active_verified_profile()
            return account_store.public_payload_if_available()
        except AccountProfileStoreError as exc:
            raise HTTPException(
                409,
                failure_detail(
                    "account_profile_unavailable",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc

    @app.post("/api/account-profiles/{profile_id}/switch")
    def switch_account_profile(profile_id: str):
        config = load_config(config_path)
        account_change_guard(config, "切换账号")
        try:
            with SingleInstanceLock(config.lock_file):
                account_store.activate(profile_id)
        except TaskAlreadyRunning as exc:
            raise HTTPException(
                409,
                failure_detail(
                    "account_operation_busy",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc
        except AccountProfileStoreError as exc:
            raise HTTPException(
                409,
                failure_detail(
                    "account_switch_failed",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc
        return account_store.public_payload_if_available()

    @app.post("/api/account-profiles/add", status_code=202)
    def add_account_profile():
        config = load_config(config_path)
        account_change_guard(config, "添加账号")
        previous_profile_id = account_store.public_payload_if_available().get(
            "active_profile_id"
        )
        try:
            with SingleInstanceLock(config.lock_file):
                profile = account_store.create_empty_profile()
                account_store.activate(profile["profile_id"])
            job = run_action("login")
        except TaskAlreadyRunning as exc:
            raise HTTPException(
                409,
                failure_detail(
                    "account_operation_busy",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc
        except ActionAlreadyRunning as exc:
            if previous_profile_id:
                with SingleInstanceLock(load_config(config_path).lock_file):
                    account_store.activate(str(previous_profile_id))
            raise HTTPException(
                409,
                failure_detail(
                    "account_operation_busy",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc
        except AccountProfileStoreError as exc:
            raise HTTPException(
                409,
                failure_detail(
                    "account_profile_unavailable",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc
        return {
            "profile": profile,
            "job": job,
            **account_store.public_payload_if_available(),
        }

    @app.post("/api/account-profile/logout")
    def logout_account_profile(payload: AccountLogoutRequest):
        if not payload.confirmed:
            raise HTTPException(400, "必须明确确认退出账号")
        config = load_config(config_path)
        account_change_guard(config, "退出账号")
        try:
            with SingleInstanceLock(config.lock_file):
                profiles = account_store.public_payload_if_available()
                if profiles["profiles"]:
                    def clear_active_authentication(profile_dir: Path) -> None:
                        active_config = load_config(config_path)
                        if account_logout_runner is not None:
                            account_logout_runner(active_config)
                        else:
                            clear_managed_authentication(
                                profile_dir,
                                root=root,
                                data_root=active_config.state_file.parent,
                            )

                    account_store.logout_active(clear_active_authentication)
                elif account_logout_runner is not None:
                    account_logout_runner(config)
                else:
                    logout_managed_account(
                        config.profile_dir,
                        root=root,
                        data_root=config.state_file.parent,
                    )
        except TaskAlreadyRunning as exc:
            raise HTTPException(
                409,
                failure_detail(
                    "account_operation_busy",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc
        except AccountProfileStoreError as exc:
            raise HTTPException(
                409,
                failure_detail(
                    "account_logout_failed",
                    stage="account_verified",
                    diagnostic_details={"exception_type": type(exc).__name__},
                ).model_dump(mode="json"),
            ) from exc
        return {
            **public_profile_payload(root, logged_in=False),
            "profile_status": "unverified",
        }

    @app.get("/api/account-profile/avatar")
    def account_profile_avatar():
        _profile, avatar = (root / "data" / "account-profile.json", root / "data" / "account-avatar" / "profile.png")
        if avatar.is_file():
            return FileResponse(avatar, media_type="image/png", headers={"Cache-Control": "private, max-age=86400, immutable"})
        return Response(_FALLBACK_AVATAR, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=300"})

    @app.get("/api/config")
    def get_config():
        return _config_payload(load_config(config_path))

    @app.put("/api/config")
    def update_config(payload: ConfigUpdate):
        if payload.archive_log_retention_days < payload.active_log_retention_days:
            raise HTTPException(422, "归档日志保留天数不能小于活跃日志保留天数")
        config = load_config(config_path)
        requested_default_pack = (
            payload.default_message_pack
            if "default_message_pack" in payload.model_fields_set
            else config.default_message_pack
        )
        if (
            "default_message_pack" in payload.model_fields_set
            and requested_default_pack is not None
        ):
            try:
                pack_ids = {
                    pack.id for pack in message_pack_service().list_packs().packs
                }
            except MessagePackError as exc:
                raise message_pack_http_exception(exc) from exc
            if requested_default_pack not in pack_ids:
                raise HTTPException(422, "默认文案包不存在")
        existing: dict[str, list[Target]] = defaultdict(list)
        for target in config.targets:
            existing[target.name].append(target)
        config.targets = [
            existing[name].pop(0) if existing[name] else Target(name=name)
            for name in payload.targets
        ]
        for field_name, value in payload.model_dump().items():
            if field_name != "targets":
                if (
                    field_name == "default_message_pack"
                    and field_name not in payload.model_fields_set
                ):
                    continue
                if field_name == "message_suffix":
                    value = MessageSuffixConfig.model_validate(value)
                setattr(config, field_name, value)
        config = AppConfig.model_validate(config.model_dump())
        save_config(config_path, config)
        return _config_payload(config)

    @app.post("/api/scheduler/preview")
    def scheduler_preview(payload: ScheduleUpdate):
        current = load_config(config_path)
        candidate = current.model_copy(update=payload.model_dump())
        candidate = AppConfig.model_validate(candidate.model_dump())
        return dashboard_scheduler_service().preview(current, candidate)

    @app.post("/api/scheduler/apply")
    def scheduler_apply(payload: ScheduleUpdate):
        current = load_config(config_path)
        candidate = AppConfig.model_validate(current.model_copy(update=payload.model_dump()).model_dump())
        try:
            dashboard_scheduler_service().apply(config_path, current, candidate)
        except RuntimeError as exc:
            raise HTTPException(409, f"定时任务未更新：{exc}") from exc
        invalidate_task_cache()
        return {"config": _config_payload(candidate), "tasks": scheduler.windows_task_rows(), "message": "定时任务已更新"}

    @app.post("/api/scheduler/{operation}")
    def scheduler_operation(operation: str):
        service = dashboard_scheduler_service()
        config = load_config(config_path)
        try:
            if operation in {"install", "update", "repair"}:
                service.repair(config)
            elif operation == "remove":
                service.remove()
            else:
                raise HTTPException(404, "未知定时任务操作")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        invalidate_task_cache()
        return {"tasks": scheduler.windows_task_rows(), "message": "定时任务操作完成"}

    @app.get("/api/history")
    def task_history(
        start_date: date | None = None,
        end_date: date | None = None,
        status_filter: str | None = None,
        task_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ):
        config = load_config(config_path)
        return TaskHistoryStore(
            config.state_file.parent / "history" / "task-runs.jsonl"
        ).query(
            start_date=start_date,
            end_date=end_date,
            status=status_filter,
            task_type=task_type,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/messages")
    def get_messages():
        config = load_config(config_path)
        return {"messages": read_messages(config.messages_file)}

    @app.put("/api/messages")
    def update_messages(payload: MessagesUpdate):
        config = load_config(config_path)
        messages = list(dict.fromkeys(item.strip() for item in payload.messages if item.strip()))
        if not messages:
            raise HTTPException(422, "文案库不能为空")
        temporary = config.messages_file.with_suffix(".tmp")
        temporary.write_text("\n".join(messages) + "\n", encoding="utf-8")
        os.replace(temporary, config.messages_file)
        return {"messages": messages}

    @app.post("/api/messages/import/preview")
    async def preview_message_import(file: UploadFile = File(...)):
        try:
            result = parse_message_import(await file.read(), file.filename or "messages.txt")
        except TransferError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "total_entries": result.total_count, "valid_entries": result.valid_count,
            "exact_duplicates": result.exact_duplicates, "empty_entries": result.empty_count,
            "overly_long_entries": result.long_count, "entries_with_links": result.link_count,
        }

    @app.post("/api/messages/import")
    async def import_messages(file: UploadFile = File(...), mode: BackupImportMode = BackupImportMode.MERGE):
        config = load_config(config_path)
        try:
            result = apply_message_import(config, parse_message_import(await file.read(), file.filename or "messages.txt"), mode=mode)
        except TransferError as exc:
            raise HTTPException(422, str(exc)) from exc
        return result

    @app.post("/api/messages/deduplicate")
    def deduplicate_messages():
        config = load_config(config_path)
        try:
            messages = read_messages(config.messages_file)
            before = len([line for line in config.messages_file.read_text(encoding="utf-8").splitlines() if line.strip()])
            parsed = parse_message_import("\n".join(messages).encode("utf-8"), "messages.txt")
            result = apply_message_import(config, parsed, mode=BackupImportMode.REPLACE)
        except (FileNotFoundError, ValueError, TransferError) as exc:
            raise HTTPException(422, str(exc)) from exc
        result["removed"] = max(0, before - len(messages))
        return result

    @app.get("/api/messages/export")
    def export_messages(format: str = "txt", source: str = "local", category: str | None = None, pack_id: str | None = None):
        config = load_config(config_path)
        try:
            if source == "local":
                messages = read_messages(config.messages_file)
            else:
                service = message_pack_service()
                packs = service.list_packs().packs
                selected = [item for item in packs if (pack_id and item.id == pack_id) or (category and item.category == category)]
                if not selected:
                    raise MessagePackError("未找到指定文案包或类别")
                messages = []
                for pack in selected:
                    messages.extend(service.preview(pack.id).messages)
                messages = list(dict.fromkeys(messages))
        except (FileNotFoundError, ValueError, MessagePackError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if format == "json":
            return Response(_json_bytes({"messages": messages}), media_type="application/json", headers={"Content-Disposition": "attachment; filename=autody-messages.json"})
        if format == "csv":
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(["message"])
            writer.writerows([[item] for item in messages])
            return PlainTextResponse(output.getvalue(), headers={"Content-Disposition": "attachment; filename=autody-messages.csv"})
        return PlainTextResponse("\n".join(messages) + "\n", headers={"Content-Disposition": "attachment; filename=autody-messages.txt"})

    @app.get("/api/message-packs")
    def message_packs():
        try:
            return message_pack_service().list_packs()
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.post("/api/message-packs")
    def create_message_pack(payload: CreateMessagePackRequest):
        try:
            return message_pack_service().create_pack(
                payload.expected_revision,
                payload.name,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.post("/api/message-packs/import")
    async def import_message_pack_file(
        file: UploadFile = File(...),
        expected_revision: int = Form(..., ge=1),
    ):
        try:
            return message_pack_service().import_text(
                await file.read(),
                file.filename or "",
                expected_revision,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.put("/api/message-packs/order")
    def reorder_message_packs(payload: ReorderMessagePacksRequest):
        try:
            return message_pack_service().reorder_packs(
                payload.pack_ids,
                payload.expected_revision,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.get("/api/message-packs/{pack_id}")
    def preview_message_pack(pack_id: str):
        try:
            return message_pack_service().preview(pack_id)
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.patch("/api/message-packs/{pack_id}")
    def rename_message_pack(pack_id: str, payload: RenameMessagePackRequest):
        try:
            return message_pack_service().rename_pack(
                pack_id,
                payload.name,
                payload.expected_revision,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.delete("/api/message-packs/{pack_id}")
    def delete_message_pack(pack_id: str, payload: RevisionRequest):
        config = load_config(config_path)
        referenced = {
            target.message_pack
            for target in config.targets
            if target.message_pack is not None
        }
        if config.default_message_pack is not None:
            referenced.add(config.default_message_pack)
        try:
            return message_pack_service().delete_pack(
                pack_id,
                payload.expected_revision,
                referenced,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.post("/api/message-packs/{pack_id}/messages")
    def add_message_pack_message(pack_id: str, payload: MessagePackTextRequest):
        try:
            return message_pack_service().add_message(
                pack_id,
                payload.text,
                payload.expected_revision,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.patch("/api/message-packs/{pack_id}/messages/{message_id}")
    def update_message_pack_message(
        pack_id: str,
        message_id: str,
        payload: MessagePackTextRequest,
    ):
        try:
            return message_pack_service().update_message(
                pack_id,
                message_id,
                payload.text,
                payload.expected_revision,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.delete("/api/message-packs/{pack_id}/messages/{message_id}")
    def delete_message_pack_message(
        pack_id: str,
        message_id: str,
        payload: RevisionRequest,
    ):
        try:
            return message_pack_service().delete_message(
                pack_id,
                message_id,
                payload.expected_revision,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.post("/api/message-packs/{source_id}/fuse")
    def fuse_message_pack(source_id: str, payload: FuseMessagePackRequest):
        try:
            return message_pack_service().fuse(
                source_id,
                payload.destination_id,
                payload.expected_revision,
                config_path,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.post("/api/message-packs/{destination_id}/split")
    def split_message_pack(
        destination_id: str,
        payload: SplitMessagePackRequest,
    ):
        try:
            return message_pack_service().split(
                destination_id,
                payload.source_id,
                payload.expected_revision,
            )
        except MessagePackError as exc:
            raise message_pack_http_exception(exc) from exc

    @app.post("/api/message-packs/{pack_id}/import")
    def import_message_pack(pack_id: str, payload: MessagePackImportRequest):
        config = load_config(config_path)
        try:
            return message_pack_service().import_pack(
                pack_id,
                config.messages_file,
                payload.mode,
            )
        except MessagePackError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/friends/scan", status_code=202)
    def scan_friends():
        try:
            return run_action("scan-friends")
        except ActionAlreadyRunning as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/friends/discover", status_code=202)
    def discover_friends_now():
        """Force one read-only scan even when the local cache is still fresh."""
        return scan_friends()

    @app.get("/api/friends/discovered")
    def discovered_friends():
        config = load_config(config_path)
        progress_path = config.state_file.parent / "friend_scan_progress.json"
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            progress = {}
        result = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        stale = is_discovery_stale(result.scanned_at if result else None, current_time())
        refresh_is_running = bool(progress.get("running"))
        if result is None:
            return {
                "scanned_at": None,
                "stale": True,
                "refresh_running": refresh_is_running,
                "last_result": {},
                "progress": progress,
                "candidates": [],
                "orphans": [],
            }
        targets = {
            target.candidate_id: target
            for target in config.targets
            if target.candidate_id and target.stable_id and _avatar_id(target.stable_id)
        }
        profile = load_account_profile(config.messages_file.parent)
        current_candidate_ids = {
            candidate.candidate_id
            for candidate in result.candidates
            if candidate.presence_status == "current"
        }
        orphan_targets = [
            target
            for target in config.targets
            if target.stable_id
            and target.candidate_id
            and target.candidate_id not in current_candidate_ids
        ]
        ignored_orphans = ignored_orphan_target_ids()
        orphan_targets_by_name: dict[str, list[Target]] = defaultdict(list)
        for target in orphan_targets:
            orphan_targets_by_name[" ".join(target.name.split()).casefold()].append(target)
        candidate_payloads = []
        for candidate in result.candidates:
            configured_target = targets.get(candidate.candidate_id)
            normalized_name = " ".join(candidate.display_name.split()).casefold()
            matching_orphans = orphan_targets_by_name.get(normalized_name, [])
            actionable = [
                target
                for target in matching_orphans
                if target.stable_id not in ignored_orphans
            ]
            if configured_target is not None:
                current_resolution = resolve_stable_binding(
                    configured_target,
                    result,
                    profile,
                    now=current_time(),
                )
                proposed = configured_target.model_copy(deep=True)
                reassociation = reassociate_stable_binding(
                    config,
                    proposed,
                    candidate.candidate_id,
                    result,
                    profile,
                    now=current_time(),
                )
                if not current_resolution.valid and reassociation.valid:
                    match_status = "needs_reassociation"
                    reassociation_target_id = configured_target.stable_id
                else:
                    match_status = "configured"
                    reassociation_target_id = None
            elif len(actionable) == 1:
                match_status = "needs_reassociation"
                reassociation_target_id = actionable[0].stable_id
            elif matching_orphans and not actionable:
                match_status = "ignored_reassociation"
                reassociation_target_id = None
            elif len(actionable) > 1:
                match_status = "ambiguous"
                reassociation_target_id = None
            else:
                match_status = candidate.match_status
                reassociation_target_id = None
            candidate_payloads.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "display_name": candidate.display_name,
                    "avatar_url": _avatar_url(candidate.avatar_cache_key or candidate.candidate_id, candidate.avatar_updated_at) if candidate.avatar_status == "cached" else "",
                    "avatar_version": candidate.avatar_updated_at,
                    "avatar_status": candidate.avatar_status,
                    "discovered_at": candidate.discovered_at,
                    "match_status": match_status,
                    "configured": configured_target is not None,
                    "target_id": configured_target.stable_id if configured_target else None,
                    "enabled": configured_target.enabled if configured_target else None,
                    "configured_target_id": configured_target.stable_id if configured_target else None,
                    "configured_enabled": configured_target.enabled if configured_target else None,
                    "reassociation_target_id": reassociation_target_id,
                    "avatar_updated_at": candidate.avatar_updated_at,
                    "avatar_cache_key": candidate.avatar_cache_key,
                    "first_discovered_at": candidate.first_discovered_at,
                    "last_seen_at": candidate.last_seen_at,
                    "last_scan_id": candidate.last_scan_id,
                    "presence_status": candidate.presence_status,
                    "stale": candidate.presence_status == "stale",
                }
            )
        return {
            "scanned_at": result.scanned_at,
            "stale": stale,
            "refresh_running": refresh_is_running,
            "last_result": result.last_result,
            "progress": progress,
            "candidates": candidate_payloads,
            "orphans": [
                {
                    "target_id": target.stable_id,
                    "display_name": target.name,
                    "enabled": target.enabled,
                }
                for target in orphan_targets
                if target.stable_id not in ignored_orphans
            ],
        }

    @app.get("/api/friends/scan-status")
    def friend_scan_status():
        config = load_config(config_path)
        progress_path = config.state_file.parent / "friend_scan_progress.json"
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            progress = {}
        if not isinstance(progress, dict):
            progress = {}
        result = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        running = bool(progress.get("running"))
        return {
            "refresh_running": running,
            "stage": str(progress.get("stage", "opening_douyin" if running else "completed")),
            "progress": progress,
            "last_result": result.last_result if result else {},
        }

    @app.get("/api/friends")
    def get_friends(today: date | None = None):
        config = load_config(config_path)
        binding_guarded = bindings_revalidation_required(
            config.state_file.parent
        )
        state = StateStore(config.state_file).load()
        status_day = today or date.today()
        effective_statuses = effective_daily_target_statuses(
            config,
            state,
            status_day,
        )
        cache_dir = config.state_file.parent / "avatar-cache"
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        profile = load_account_profile(config.messages_file.parent)
        candidates_by_id = {
            candidate.candidate_id: candidate
            for candidate in (discovered.candidates if discovered else [])
        }
        normalized_names: dict[str, int] = defaultdict(int)
        for target in config.targets:
            if target.enabled:
                normalized_names[" ".join(target.name.split()).casefold()] += 1
        friends = []
        for target in config.targets:
            resolution = resolve_stable_binding(
                target,
                discovered,
                profile,
                revalidation_required=binding_guarded,
                now=current_time(),
            )
            identifier = _avatar_id(target.stable_id)
            candidate = candidates_by_id.get(
                resolution.candidate_id or target.candidate_id or ""
            )
            avatar_key = (
                candidate.avatar_cache_key or candidate.candidate_id
                if candidate and candidate.avatar_status == "cached"
                else None
            )
            avatar_status = "cached" if avatar_key and _avatar_path(cache_dir, avatar_key).is_file() else "missing"
            friends.append(
                {
                    "id": identifier,
                    "target_id": identifier,
                    "display_name": candidate.display_name if candidate else target.name,
                    "enabled": target.enabled,
                    "note": target.note,
                    "avatar_url": _avatar_url(avatar_key, candidate.avatar_updated_at if candidate else None) if avatar_status == "cached" else "",
                    "avatar_status": avatar_status,
                    "today_status": effective_statuses.get(
                        target_identity(target), "pending"
                    ),
                    "last_success_date": _last_success_date(state, target.name),
                    "ambiguous_duplicate": target.enabled and normalized_names[" ".join(target.name.split()).casefold()] > 1,
                    "binding_status": (
                        "verified" if resolution.valid else resolution.status
                    ),
                }
            )
        return {"friends": friends}

    def update_target_settings(target_id: str, payload: TargetSettingsUpdate):
        config = load_config(config_path)
        target = next((item for item in config.targets if target_identity(item) == target_id), None)
        if target is None:
            raise HTTPException(404, "续火目标不存在")
        overrides = module_overrides()
        if payload.reset_overrides:
            overrides.pop(target_id, None)
        else:
            values = payload.model_dump(exclude={"reset_overrides"}, exclude_none=True)
            if values.get("message_pack"):
                try:
                    ids = {
                        pack.id
                        for pack in MessagePackService(program_root).list_packs().packs
                    }
                except MessagePackError as exc:
                    raise HTTPException(422, str(exc)) from exc
                if values["message_pack"] not in ids:
                    raise HTTPException(422, "选择的文案包不存在")
            current = dict(overrides.get(target_id, {}))
            current.update({key: value for key, value in values.items() if key != "enabled"})
            if current.get("suffix_mode") == "custom" and not str(current.get("suffix_override") or "").strip():
                raise HTTPException(422, "自定义后缀不能为空")
            overrides[target_id] = current
        save_module_overrides(overrides)
        return {"target_id": target_identity(target), "settings": _effective_target_settings(target, config, overrides.get(target_id))}

    @app.put("/api/modules/autody-test-center/targets/{target_id}/settings")
    def test_center_target_settings(target_id: str, payload: TargetSettingsUpdate):
        _require_test_center()
        return update_target_settings(target_id, payload)

    @app.post("/api/friends/discovered/batch")
    def add_discovered_friends(payload: DiscoveredFriendBatchAdd):
        config = load_config(config_path)
        result = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        if result is None:
            raise HTTPException(409, "请先扫描好友")
        candidates = {candidate.candidate_id: candidate for candidate in result.candidates}
        profile = load_account_profile(config.messages_file.parent)
        guarded_names = actionable_orphan_names(config, result)
        existing = {target.candidate_id for target in config.targets if target.candidate_id}
        added = skipped = 0
        for candidate_id in dict.fromkeys(payload.candidate_ids):
            candidate = candidates.get(candidate_id)
            if (
                candidate is None
                or candidate.match_status == "ambiguous"
                or candidate.presence_status == "stale"
                or candidate.candidate_id in existing
                or not _avatar_id(candidate.candidate_id)
                or not result.account_scope
                or not candidate.identity_key
                or candidate.identity_source
                not in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
                or " ".join(candidate.display_name.split()).casefold() in guarded_names
            ):
                skipped += 1
                continue
            target = Target(
                name=candidate.display_name,
                stable_id=f"target-{uuid.uuid4().hex}",
                candidate_id=candidate.candidate_id,
                binding_identity_key=candidate.identity_key,
                binding_identity_source=candidate.identity_source,
                binding_account_scope=result.account_scope,
            )
            if not resolve_stable_binding(target, result, profile).proven:
                skipped += 1
                continue
            config.targets.append(target)
            existing.add(candidate.candidate_id)
            added += 1
        if added:
            save_config(config_path, config)
            refresh_binding_guard(config, result)
        return {"added": added, "skipped": skipped}

    @app.post("/api/friends/{candidate_id}/relink")
    def relink_friend_candidate(candidate_id: str, payload: FriendRelinkRequest):
        config = load_config(config_path)
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        target = next(
            (
                item
                for item in config.targets
                if item.stable_id == payload.target_id
            ),
            None,
        )
        if target is None:
            raise HTTPException(404, "待重新关联的续火目标不存在")
        resolution = reassociate_stable_binding(
            config,
            target,
            candidate_id,
            discovered,
            load_account_profile(config.messages_file.parent),
            now=current_time(),
        )
        if resolution.status == "candidate_missing":
            raise HTTPException(404, "未找到当前候选好友")
        if resolution.status == "candidate_occupied":
            raise HTTPException(409, "该候选好友已关联到其他续火目标")
        if resolution.status == "binding_missing":
            raise HTTPException(409, "当前候选缺少权威好友身份，不能安全重新关联")
        if not resolution.valid:
            raise HTTPException(409, "当前扫描尚不能权威证明该候选好友")
        save_config(config_path, config)
        refresh_binding_guard(config, discovered)
        ignored = ignored_orphan_target_ids()
        if target.stable_id in ignored:
            ignored.remove(target.stable_id)
            save_ignored_orphan_target_ids(ignored)
        return {
            "target_id": target.stable_id,
            "candidate_id": target.candidate_id,
            "display_name": target.name,
        }

    @app.post("/api/friends/{target_id}/ignore-orphan")
    def ignore_friend_orphan(target_id: str):
        config = load_config(config_path)
        target = next(
            (item for item in config.targets if item.stable_id == target_id),
            None,
        )
        if target is None:
            raise HTTPException(404, "续火目标不存在")
        ignored = ignored_orphan_target_ids()
        ignored.add(target_id)
        save_ignored_orphan_target_ids(ignored)
        return {"ignored": True}

    @app.post("/api/friends/{candidate_id}/add-to-targets")
    def add_candidate_to_targets(candidate_id: str):
        config = load_config(config_path)
        result = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        if result is None:
            raise HTTPException(409, "请先扫描好友")
        candidate = next(
            (item for item in result.candidates if item.candidate_id == candidate_id),
            None,
        )
        if candidate is None or candidate.presence_status == "stale":
            raise HTTPException(404, "未找到可添加的候选好友")
        existing = next(
            (target for target in config.targets if target.candidate_id == candidate_id),
            None,
        )
        if existing is not None:
            return {
                "created": False,
                "target": {
                    "target_id": existing.stable_id,
                    "display_name": existing.name,
                    "enabled": existing.enabled,
                },
            }
        if (
            candidate.match_status in {"ambiguous", "needs_reassociation", "ignored_reassociation"}
            or " ".join(candidate.display_name.split()).casefold()
            in actionable_orphan_names(config, result)
            or not _avatar_id(candidate.candidate_id)
            or not result.account_scope
            or not candidate.identity_key
            or candidate.identity_source
            not in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
        ):
            raise HTTPException(409, "该候选好友缺少权威好友身份，不能安全关联")
        target = Target(
            name=candidate.display_name,
            stable_id=f"target-{uuid.uuid4().hex}",
            candidate_id=candidate.candidate_id,
            binding_identity_key=candidate.identity_key,
            binding_identity_source=candidate.identity_source,
            binding_account_scope=result.account_scope,
        )
        if not resolve_stable_binding(
            target,
            result,
            load_account_profile(config.messages_file.parent),
        ).proven:
            raise HTTPException(409, "当前扫描尚不能权威证明该候选好友")
        config.targets.append(target)
        save_config(config_path, config)
        refresh_binding_guard(config, result)
        return {
            "created": True,
            "target": {
                "target_id": target.stable_id,
                "display_name": target.name,
                "enabled": target.enabled,
            },
        }

    @app.post("/api/friends/refresh-avatars", status_code=202)
    def refresh_friend_avatars():
        try:
            return run_action("refresh-friend-avatars")
        except ActionAlreadyRunning as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.patch("/api/friends/batch")
    def update_friends_batch(payload: FriendBatchUpdate):
        config = load_config(config_path)
        target_ids = set(payload.target_ids)
        names = set(payload.names)
        affected = 0
        if payload.action == "delete":
            before = len(config.targets)
            config.targets = [
                target
                for target in config.targets
                if target.stable_id not in target_ids and target.name not in names
            ]
            affected = before - len(config.targets)
        else:
            enabled = payload.action == "enable"
            for target in config.targets:
                if target.stable_id in target_ids or target.name in names:
                    target.enabled = enabled
                    affected += 1
        save_config(config_path, config)
        refresh_binding_guard(config)
        return {"affected": affected}

    @app.get("/api/avatars/{friend_id}")
    def get_avatar(friend_id: str):
        identifier = _avatar_id(friend_id)
        if identifier is None:
            raise HTTPException(404, "头像不存在")
        config = load_config(config_path)
        cache_dir = config.state_file.parent / "avatar-cache"
        path = _avatar_path(cache_dir, identifier)
        if path.is_file():
            stat = path.stat()
            headers = {
                "Cache-Control": "private, max-age=86400, immutable",
                "ETag": f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"',
                "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
            }
            return FileResponse(path, media_type="image/png", headers=headers)
        headers = {"Cache-Control": "private, max-age=300"}
        return Response(_FALLBACK_AVATAR, media_type="image/svg+xml", headers=headers)

    @app.get("/api/logs")
    def logs(
        start_date: date | None = None,
        end_date: date | None = None,
        level: str | None = None,
        task_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ):
        config = load_config(config_path)
        log_dir = config.state_file.parent / "logs"
        page_result = query_logs(
            log_dir,
            config,
            start_date=start_date,
            end_date=end_date,
            level=level,
            task_type=task_type,
            status=status,
            page=page,
            page_size=page_size,
        )
        payload = page_result.model_dump(mode="json")
        payload["application"] = "\n".join(
            f"{item.timestamp} {item.level} {item.summary}\n{item.detail}".strip()
            for item in reversed(page_result.items)
        )
        payload["scheduler"] = _tail_scheduler_logs(log_dir)
        return payload

    @app.get("/api/logs/storage-summary")
    def log_storage_summary_endpoint():
        config = load_config(config_path)
        return {
            **log_storage_summary(config.state_file.parent / "logs"),
            "cleanup_enabled": config.log_cleanup_enabled,
            "active_retention_days": config.active_log_retention_days,
            "archive_retention_days": config.archive_log_retention_days,
        }

    @app.post("/api/logs/cleanup-preview")
    def log_cleanup_preview():
        config = load_config(config_path)
        return cleanup_logs(config.state_file.parent / "logs", active_days=config.active_log_retention_days, archive_days=config.archive_log_retention_days, apply=False)

    @app.post("/api/logs/cleanup")
    def log_cleanup(payload: dict):
        if payload.get("confirmed") is not True:
            raise HTTPException(422, "需要确认后才能整理日志")
        config = load_config(config_path)
        log_dir = config.state_file.parent / "logs"
        result = cleanup_logs(log_dir, active_days=config.active_log_retention_days, archive_days=config.archive_log_retention_days)
        try:
            record_cleanup_result(log_dir, result)
        except OSError:
            pass
        return result

    @app.post("/api/logs/archive")
    def archive_application_logs(before: date):
        config = load_config(config_path)
        moved = archive_logs(config.state_file.parent / "logs", before)
        return {"archived_count": len(moved), "archive_dir": str(config.state_file.parent / "logs" / "archive")}

    @app.post("/api/logs/archive-historical")
    def archive_historical_application_logs():
        config = load_config(config_path)
        moved = archive_historical_logs(config.state_file.parent / "logs", config)
        return {"archived_count": len(moved), "archive_dir": str(config.state_file.parent / "logs" / "archive")}

    @app.get("/api/logs/diagnostic-export")
    def export_masked_diagnostics():
        config = load_config(config_path)
        page = query_logs(config.state_file.parent / "logs", config, page_size=200)
        lines = [f"{item.timestamp} {item.level} {item.task_type} [{item.status}] {item.summary}" for item in page.items]
        manifest = {"format": "autody-diagnostics", "version": 1, "masked": True, "includes": ["recent-log-summary"], "excludes": ["sent-message-content", "cookies", "browser-profile", "avatar-cache", "discovered_friends.json"]}
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _json_bytes(manifest))
            archive.writestr("recent-log-summary.txt", "\n".join(lines) + "\n")
        return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=autody-diagnostics-masked.zip"})

    @app.post("/api/logs/open-folder")
    def open_log_folder():
        log_dir = load_config(config_path).state_file.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if platform.system() != "Windows":
            raise HTTPException(409, "仅支持在 Windows 本地打开日志目录")
        try:
            os.startfile(str(log_dir))  # type: ignore[attr-defined]
        except OSError as exc:
            raise HTTPException(409, f"无法打开日志目录：{exc}") from exc
        return {"opened": True}

    @app.get("/api/backup")
    def backup():
        # Retained for old desktop launchers. The selectable export center uses
        # POST /api/backup/export and the checksummed v2 package below.
        config = load_config(config_path)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("config.yaml", _portable_config(config_path, config))
            archive.writestr("messages.txt", config.messages_file.read_bytes())
            archive.writestr("manifest.json", _json_bytes({"format": "autody-backup", "version": 1, "created_at": datetime.now().isoformat(timespec="seconds"), "browser_profile_included": False}))
            if config.state_file.exists():
                archive.writestr("state.json", config.state_file.read_bytes())
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=autody-backup.zip"},
        )

    @app.post("/api/backup/export")
    def export_backup(payload: BackupExportRequest):
        try:
            package = create_backup(load_config(config_path), set(payload.categories))
        except TransferError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(package, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=autody-backup.zip"})

    @app.post("/api/backup/preview")
    async def preview_backup_upload(file: UploadFile = File(...)):
        try:
            return preview_backup(await file.read(), load_config(config_path))
        except TransferError as exc:
            raise HTTPException(422, f"备份无效：{exc}") from exc

    @app.post("/api/backup/import")
    async def import_backup(file: UploadFile = File(...), mode: BackupImportMode = BackupImportMode.MERGE):
        raw = await file.read()
        try:
            result = apply_backup(raw, config_path, load_config(config_path), mode=mode)
        except TransferError as exc:
            # v1 was the previous built-in local backup. It contained no
            # browser profile and remains importable, but all new exports are
            # validated v2 packages with checksums.
            try:
                with zipfile.ZipFile(BytesIO(raw)) as archive:
                    names = set(archive.namelist())
                    manifest = json.loads(archive.read("manifest.json"))
                    if manifest.get("format") != "autody-backup" or manifest.get("version") != 1 or not {"config.yaml", "messages.txt"} <= names:
                        raise ValueError("not a legacy backup")
                    current = load_config(config_path)
                    candidate_path = root / ".autody-legacy-import.yaml"
                    candidate_path.write_bytes(archive.read("config.yaml"))
                    candidate = load_config(candidate_path)
                    candidate_path.unlink(missing_ok=True)
                    candidate.messages_file = current.messages_file
                    candidate.profile_dir = current.profile_dir
                    candidate.state_file = current.state_file
                    candidate.lock_file = current.lock_file
                    candidate.artifact_dir = current.artifact_dir
                    messages = archive.read("messages.txt").decode("utf-8")
                    if not [line for line in messages.splitlines() if line.strip()]:
                        raise ValueError("empty message library")
                    backup_dir = current.state_file.parent / "backups"
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    (backup_dir / f"before-legacy-import-{datetime.now():%Y%m%d-%H%M%S}.zip").write_bytes(create_backup(current, DEFAULT_CATEGORIES))
                    original_config = config_path.read_bytes() if config_path.exists() else None
                    original_messages = current.messages_file.read_bytes() if current.messages_file.exists() else None
                    try:
                        save_config(config_path, candidate)
                        current.messages_file.write_text(messages, encoding="utf-8")
                    except Exception:
                        if original_config is not None: config_path.write_bytes(original_config)
                        if original_messages is not None: current.messages_file.write_bytes(original_messages)
                        raise
                    result = {"legacy": True, "friends": {"imported": len(candidate.targets)}, "messages": {"imported": len(read_messages(current.messages_file))}}
            except (zipfile.BadZipFile, ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as legacy_exc:
                raise HTTPException(422, f"备份无效：{exc}") from legacy_exc
        restored = load_config(config_path)
        return {**result, "targets": [target.name for target in restored.targets], "messages": _message_count(restored.messages_file)}

    @app.get("/api/preflight/latest")
    def preflight_latest():
        config = load_config(config_path)
        return preflight_payload(config, PreflightStore(config.state_file.parent / "preflight").load_latest())

    @app.get("/api/preflight/history")
    def preflight_history():
        config = load_config(config_path)
        rows = PreflightStore(config.state_file.parent / "preflight").history()
        return {"items": [preflight_payload(config, row)["result"] for row in rows[-30:]]}

    @app.get("/api/preflight/status")
    def preflight_status():
        nonlocal preflight_job_id
        job = manager.get(preflight_job_id) if preflight_job_id else None
        config = load_config(config_path)
        store = PreflightStore(config.state_file.parent / "preflight")
        return {
            "running": bool(job and job["status"] == "running"),
            "job": job,
            "progress": store.load_progress(),
            **preflight_payload(config, store.load_latest()),
        }

    @app.post("/api/preflight/run", status_code=202)
    def preflight_run(payload: PreflightRunRequest):
        nonlocal preflight_job_id
        config = load_config(config_path)
        valid_ids = {
            target_identity(target)
            for target in enabled_execution_targets(config)
        }
        if payload.target_ids is not None and (not payload.target_ids or any(item not in valid_ids for item in payload.target_ids)):
            raise HTTPException(422, "续火目标无效或已停用")
        request_path = config.state_file.parent / "preflight" / "request.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = request_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"target_ids": payload.target_ids}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, request_path)
        try:
            job = run_action("preflight")
        except ActionAlreadyRunning as exc:
            raise HTTPException(409, str(exc)) from exc
        preflight_job_id = str(job["id"])
        return job

    @app.post("/api/preflight/cancel")
    def preflight_cancel():
        config = load_config(config_path)
        path = config.state_file.parent / "preflight" / "cancel.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"requested_at": datetime.now().isoformat()}), encoding="utf-8")
        return {"cancelled": True}

    @app.post("/api/actions/{action}", status_code=202)
    def action(action: str):
        if action not in {
            "run",
            "login",
            "health-check",
            "scan-friends",
            "refresh-friend-avatars",
            "repair-playwright",
            "refresh-account-profile",
            "preflight",
        }:
            raise HTTPException(404, "未知操作")
        try:
            return run_action(action)
        except ActionAlreadyRunning as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/actions/{job_id}")
    def action_status(job_id: str):
        job = manager.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def unknown_api(path: str):
        raise HTTPException(404, "接口不存在")

    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            file = (static_dir / path).resolve()
            if path and static_dir.resolve() in file.parents and file.is_file():
                return FileResponse(file)
            return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-store"})

    return app

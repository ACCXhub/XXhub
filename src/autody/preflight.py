"""Read-only readiness checks for the Douyin chat page.

This module deliberately has no sender dependency and never selects a message.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
import uuid
from typing import Callable, Protocol

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from autody.chat import DOUYIN_SELECTORS
from autody.config import AppConfig, Target, target_identity


REASON_MESSAGES = {
    "ready": "具备发送条件",
    "friend_not_found": "未在聊天列表中找到该续火目标",
    "conversation_open_failed": "无法打开该聊天会话",
    "conversation_load_timeout": "聊天页面加载超时",
    "composer_missing": "聊天页面已打开，但未识别到消息输入框",
    "composer_hidden": "消息输入框当前不可见",
    "composer_disabled": "消息输入框当前不可用",
    "send_control_missing": "未识别到发送控件",
    "page_structure_changed": "抖音页面结构可能已更新，请检查 AutoDy",
    "blocked_ambiguous_target": "存在同名目标，当前无法安全确定聊天对象",
    "target_disabled": "该目标已停用",
    "target_configuration_invalid": "目标缺少稳定关联信息",
    "login_required": "抖音登录已失效，请重新登录",
    "browser_busy": "AutoDy 正在执行其他浏览器任务，请稍后重试",
    "browser_unavailable": "浏览器组件不可用，请运行系统检查",
    "chat_page_unavailable": "抖音聊天页面结构可能已更新",
    "cancelled": "发送前自检已取消",
    "unexpected_error": "检测时发生未预期错误",
}

# The inspector has no input or sending API. These counters are persisted with
# each run so a real read-only verification can prove the prohibited action
# categories remained unused.
READ_ONLY_ACTION_COUNTS = {
    "fill": 0,
    "type": 0,
    "press": 0,
    "keyboard_input": 0,
    "send_click": 0,
    "send_message": 0,
}
_MISSING = object()


class ReadOnlyLocator:
    """Small allow-list wrapper around a Playwright locator for preflight."""

    def __init__(self, locator, *, role: str = "inspect"):
        self._locator = locator
        self._role = role

    @property
    def first(self) -> "ReadOnlyLocator":
        return ReadOnlyLocator(self._locator.first, role=self._role)

    def count(self) -> int:
        return self._locator.count()

    def filter(self, **kwargs) -> "ReadOnlyLocator":
        if isinstance(kwargs.get("has"), ReadOnlyLocator):
            kwargs["has"] = kwargs["has"]._locator
        return ReadOnlyLocator(self._locator.filter(**kwargs), role=self._role)

    def evaluate(self, expression: str, argument=_MISSING):
        if re.search(r"dispatchEvent|\.click\(|\.focus\(|\.value\s*=", expression):
            raise RuntimeError("只读预检不允许修改聊天页面")
        if argument is _MISSING:
            return self._locator.evaluate(expression)
        return self._locator.evaluate(expression, argument)

    def wait_for(self, **kwargs) -> None:
        self._locator.wait_for(**kwargs)


class ReadOnlyPage:
    """Allow only DOM inspection plus a deliberately named conversation open."""

    def __init__(self, page):
        self._page = page
        self.action_counts = dict(READ_ONLY_ACTION_COUNTS)
        self.conversation_open_count = 0

    def locator(self, selector: str, **kwargs) -> ReadOnlyLocator:
        role = "conversation" if selector == DOUYIN_SELECTORS.conversation else "inspect"
        return ReadOnlyLocator(self._page.locator(selector, **kwargs), role=role)

    def wait_for_timeout(self, timeout: float) -> None:
        self._page.wait_for_timeout(timeout)

    def open_conversation(self, conversation: ReadOnlyLocator) -> None:
        if not isinstance(conversation, ReadOnlyLocator) or conversation._role != "conversation":
            raise TypeError("预检只能打开只读会话定位器")
        # This raw Playwright call is reachable only through this method; the
        # ReadOnlyLocator intentionally has no generic click method.
        conversation._locator.click()
        self.conversation_open_count += 1


@dataclass(frozen=True)
class Readiness:
    target_status: str
    friend_located: bool = False
    conversation_opened: bool = False
    conversation_loaded: bool = False
    composer_found: bool = False
    composer_visible: bool = False
    composer_enabled: bool = False
    send_control_found: bool = False
    blocking_state: str | None = None
    technical_summary: str | None = None

    @classmethod
    def ready(cls) -> "Readiness":
        return cls("ready", True, True, True, True, True, True, True)


class ReadOnlyChatInspector(Protocol):
    def inspect_target(self, target: Target) -> Readiness: ...


class PlaywrightPreflightInspector:
    """A navigation-only inspector; it intentionally exposes no send operation."""

    def __init__(self, page: Page, *, friend_timeout_ms: int = 30_000):
        self.page = ReadOnlyPage(page)
        self.friend_timeout_ms = friend_timeout_ms

    @property
    def action_counts(self) -> dict[str, int]:
        return dict(self.page.action_counts)

    def chat_ready(self) -> None:
        if self.page.locator(DOUYIN_SELECTORS.verification_marker).count():
            raise RuntimeError("login_required")
        if not self.page.locator(DOUYIN_SELECTORS.conversation_list).count():
            raise RuntimeError("page_structure_changed")

    def _find_conversation(self, name: str):
        conversations = self.page.locator(DOUYIN_SELECTORS.conversation)
        names = self.page.locator(
            DOUYIN_SELECTORS.conversation_name,
            has_text=re.compile(rf"^\s*{re.escape(name)}\s*$"),
        )
        matches = conversations.filter(has=names)
        scrollable = self.page.locator(DOUYIN_SELECTORS.conversation_list)
        if scrollable.count():
            scrollable.first.evaluate("el => { el.scrollTop = 0; }")
        deadline = time.monotonic() + self.friend_timeout_ms / 1000
        while time.monotonic() < deadline:
            count = matches.count()
            if count:
                return matches, count
            if not scrollable.count():
                break
            position = scrollable.first.evaluate(
                "el => ({top: el.scrollTop, max: Math.max(0, el.scrollHeight - el.clientHeight), step: Math.max(200, el.clientHeight * .7)})"
            )
            if position["top"] >= position["max"]:
                break
            scrollable.first.evaluate("(el, step) => { el.scrollTop += step; }", position["step"])
            self.page.wait_for_timeout(200)
        return matches, matches.count()

    def inspect_target(self, target: Target) -> Readiness:
        self.chat_ready()
        try:
            matches, count = self._find_conversation(target.name)
            if count == 0:
                return Readiness("friend_not_found")
            if count != 1:
                return Readiness("blocked_ambiguous_target", friend_located=True)
            # This is the only click in preflight, and it targets a conversation row.
            self.page.open_conversation(matches.first)
            self.page.locator(DOUYIN_SELECTORS.header_name).filter(
                has_text=re.compile(rf"^\s*{re.escape(target.name)}\s*$")
            ).first.wait_for(state="visible")
        except PlaywrightTimeoutError:
            return Readiness("conversation_load_timeout", friend_located=True, conversation_opened=True)
        except Exception as exc:
            return Readiness("conversation_open_failed", friend_located=True, technical_summary=type(exc).__name__)
        composer = self.page.locator(DOUYIN_SELECTORS.input)
        if not composer.count():
            return Readiness("composer_missing", True, True, True)
        state = composer.first.evaluate(
            """element => {
                const style = getComputedStyle(element);
                const box = element.getBoundingClientRect();
                const editable = element.matches('[contenteditable], textarea, input')
                    ? element : element.querySelector('[contenteditable], textarea, input');
                return {
                  visible: style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0,
                  disabled: Boolean(editable && (editable.disabled || editable.readOnly || editable.getAttribute('contenteditable') === 'false'))
                };
            }"""
        )
        if not state["visible"]:
            return Readiness("composer_hidden", True, True, True, True)
        if state["disabled"]:
            return Readiness("composer_disabled", True, True, True, True, True)
        # The current Douyin chat page sends through the editor's Enter action
        # and does not render a separate button while the editor is empty.
        # Check the editor's action container instead; never focus or activate it.
        send_control = bool(composer.first.evaluate(
            "element => Boolean(element.closest('.messageMsgInputcontainer'))"
        ))
        if not send_control:
            return Readiness("send_control_missing", True, True, True, True, True, True)
        # Empty composer buttons are commonly disabled. Presence is the only
        # readiness requirement; this code never enables or clicks the control.
        return Readiness.ready()


class PreflightStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.latest_path = directory / "latest.json"
        self.history_path = directory / "history.jsonl"
        self.progress_path = directory / "progress.json"

    def _atomic_write(self, path: Path, value: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)

    def load_latest(self) -> dict | None:
        try:
            return json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, result: dict) -> None:
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        self._atomic_write(self.latest_path, payload)
        records = self.history()
        records.append(result)
        cutoff = datetime.now().timestamp() - 90 * 24 * 3600
        records = [row for row in records[-90:] if _timestamp(row.get("completed_at")) >= cutoff]
        history = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records)
        self._atomic_write(self.history_path, history)
        self.progress_path.unlink(missing_ok=True)

    def save_progress(self, progress: dict) -> None:
        safe = {
            "running": bool(progress.get("running")),
            "completed_targets": int(progress.get("completed_targets", 0)),
            "total_targets": int(progress.get("total_targets", 0)),
            "current_status": str(progress.get("current_status", "starting")),
        }
        self._atomic_write(self.progress_path, json.dumps(safe, ensure_ascii=False))

    def load_progress(self) -> dict | None:
        try:
            return json.loads(self.progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def history(self) -> list[dict]:
        try:
            return [json.loads(line) for line in self.history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError):
            return []


def _timestamp(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0


def _row(target: Target, readiness: Readiness, started: float) -> dict:
    result = asdict(readiness)
    result.update({
        "target_id": target_identity(target),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "reason_code": readiness.target_status,
        "user_message": REASON_MESSAGES.get(readiness.target_status, REASON_MESSAGES["unexpected_error"]),
        "avatar_available": bool(target.candidate_id),
    })
    return result


def run_preflight(
    config: AppConfig,
    inspector: ReadOnlyChatInspector,
    *,
    target_ids: list[str] | None = None,
    trigger_source: str = "manual",
    cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Inspect configured targets using a read-only inspector.

    No message library, sender, history mutation, or keyboard operation is accepted
    by this function's interface.
    """
    started_at = datetime.now().isoformat(timespec="seconds")
    started = time.monotonic()
    requested = set(target_ids or [])
    enabled = [
        target
        for target in config.targets
        if target.enabled and (not requested or target_identity(target) in requested)
    ]
    names = [target.name.casefold() for target in enabled]
    duplicate_names = {name for name in names if names.count(name) > 1}
    rows: list[dict] = []

    def record(row: dict) -> None:
        rows.append(row)
        if on_progress:
            on_progress({
                "running": True,
                "completed_targets": len(rows),
                "total_targets": len(enabled),
                "current_status": row["target_status"],
            })

    for target in enabled:
        target_started = time.monotonic()
        if cancelled and cancelled():
            record(_row(target, Readiness("cancelled"), target_started))
            break
        if not target.stable_id or not target.candidate_id:
            record(_row(target, Readiness("target_configuration_invalid"), target_started))
            continue
        if target.name.casefold() in duplicate_names:
            record(_row(target, Readiness("blocked_ambiguous_target"), target_started))
            continue
        try:
            record(_row(target, inspector.inspect_target(target), target_started))
        except Exception as exc:
            record(_row(target, Readiness("unexpected_error", technical_summary=type(exc).__name__), target_started))
    statuses = [row["target_status"] for row in rows]
    ready = statuses.count("ready")
    blocked = sum(value.startswith("blocked_") for value in statuses)
    cancelled_result = "cancelled" in statuses
    failed = len(rows) - ready - blocked - statuses.count("cancelled")
    global_status = "cancelled" if cancelled_result else "ready" if failed == blocked == 0 else "ready_with_warnings" if ready else "partial_failure"
    return {
        "check_id": uuid.uuid4().hex,
        "started_at": started_at,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "trigger_source": trigger_source,
        "login_status": "ready",
        "browser_status": "ready",
        "chat_page_status": "ready",
        "global_status": global_status,
        "total_targets": len(rows),
        "ready_count": ready,
        "warning_count": 0,
        "failed_count": failed,
        "blocked_count": blocked,
        "cancelled": cancelled_result,
        "error_summary": None,
        "action_counts": dict(getattr(inspector, "action_counts", READ_ONLY_ACTION_COUNTS)),
        "targets": rows,
    }


def global_failure(status: str, *, trigger_source: str, error_summary: str | None = None) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "check_id": uuid.uuid4().hex, "started_at": now, "completed_at": now,
        "duration_ms": 0, "trigger_source": trigger_source,
        "login_status": "login_required" if status == "login_required" else "unknown",
        "browser_status": "browser_busy" if status == "browser_busy" else "unknown",
        "chat_page_status": "unavailable", "global_status": status,
        "total_targets": 0, "ready_count": 0, "warning_count": 0,
        "failed_count": 0, "blocked_count": 0, "cancelled": status == "cancelled",
        "error_summary": error_summary or REASON_MESSAGES.get(status),
        "action_counts": dict(READ_ONLY_ACTION_COUNTS), "targets": [],
    }

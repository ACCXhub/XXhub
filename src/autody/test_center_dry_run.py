"""Test Center-only real-page composer dry run.

The normal send pipeline never imports this module.  It opens a verified
conversation through ``DouyinChat``, writes only a supplied temporary test
value, and will delete only that exact value after re-checking it.
"""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Callable, Mapping
import uuid

from pydantic import BaseModel, Field

from autody.chat import (
    AuthenticationError,
    ChatPageLoadError,
    ChatSelectors,
    DOUYIN_SELECTORS,
    DouyinChat,
    open_chat,
)
from autody.config import AppConfig, Target, load_config
from autody.friend_discovery import load_discovered_friends
from autody.locking import SingleInstanceLock, TaskAlreadyRunning
from autody.runner import preview_today_target_message


class DryRunSettings(BaseModel):
    page_ready_delay_ms: int = Field(default=1500, ge=500, le=5000)
    typing_delay_ms: int = Field(default=80, ge=30, le=300)
    typed_text_hold_ms: int = Field(default=1500, ge=500, le=10000)
    clear_verify_delay_ms: int = Field(default=500, ge=200, le=3000)
    target_switch_interval_seconds: int = Field(default=8, ge=5, le=60)
    navigation_timeout_seconds: int = Field(default=12, ge=5, le=30)


_COUNTER_NAMES = (
    "real_composer_writes",
    "real_composer_clears",
    "send_button_clicks",
    "enter_key_presses",
    "send_pipeline_calls",
    "send_attempts",
    "existing_drafts_preserved",
    "cleanup_failures",
)


def empty_counters() -> dict[str, int]:
    return {name: 0 for name in _COUNTER_NAMES}


def _normalized_target_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def eligible_batch_targets(
    config: AppConfig,
    candidate_presence: Mapping[str, str] | None = None,
) -> list[Target]:
    """Return safe configured targets in their original configured order."""
    configured = [
        target
        for target in config.targets
        if target.enabled and target.stable_id and target.candidate_id
    ]
    grouped_names: dict[str, int] = {}
    for target in configured:
        normalized = _normalized_target_name(target.name)
        grouped_names[normalized] = grouped_names.get(normalized, 0) + 1
    ambiguous_names = {name for name, count in grouped_names.items() if count > 1}
    return [
        target
        for target in configured
        if _normalized_target_name(target.name) not in ambiguous_names
        and (
            candidate_presence is None
            or candidate_presence.get(target.candidate_id, "current") != "stale"
        )
    ]


class DryRunStore:
    """Module-local persistence that intentionally excludes the test text."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.settings_path = directory / "dry-run" / "settings.json"
        self.history_path = directory / "dry-run" / "history.jsonl"
        self.recovery_path = directory / "dry-run" / "recovery.json"

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def settings(self) -> DryRunSettings:
        try:
            return DryRunSettings.model_validate_json(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DryRunSettings()

    def save_settings(self, settings: DryRunSettings) -> DryRunSettings:
        self._write_json(self.settings_path, settings.model_dump())
        return settings

    @staticmethod
    def _safe_record(value: dict) -> dict:
        allowed = {
            "run_id", "request_revision", "target_id", "selected_target_id",
            "expected_conversation_id", "visible_conversation_id",
            "selected_display_name", "visible_display_name", "identity_match",
            "identity_match_reason", "stage", "result", "text_length", "text_hash",
            "started_at", "completed_at", "duration_seconds", "counters", "message",
        }
        return {key: value[key] for key in allowed if key in value}

    def save_result(self, value: dict) -> None:
        record = self._safe_record(value)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def history(self) -> list[dict]:
        if not self.history_path.is_file():
            return []
        records: list[dict] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines()[-30:]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(self._safe_record(value))
        return list(reversed(records))

    def save_recovery(self, value: dict) -> None:
        allowed = {"run_id", "target_id", "text_hash", "text_length", "stage", "started_at"}
        self._write_json(self.recovery_path, {key: value[key] for key in allowed if key in value})

    def clear_recovery(self) -> None:
        self.recovery_path.unlink(missing_ok=True)

    def recovery_warning(self) -> str | None:
        if not self.recovery_path.is_file():
            return None
        return "检测到未完成的测试输入，请人工检查聊天输入框；系统不会自动清理。"


@dataclass
class DryRunResult:
    run_id: str
    request_revision: int
    target_id: str
    selected_target_id: str
    expected_conversation_id: str | None
    selected_display_name: str
    stage: str
    result: str
    visible_conversation_id: str | None = None
    visible_display_name: str | None = None
    identity_match: bool | None = None
    identity_match_reason: str | None = None
    composer_status: str = "unknown"
    message: str | None = None
    text_length: int = 0
    text_hash: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    completed_at: str | None = None
    duration_seconds: float = 0.0
    counters: dict[str, int] = field(default_factory=empty_counters)

    @property
    def visible_identity(self) -> str | None:
        return self.visible_display_name

    def payload(self) -> dict:
        payload = asdict(self)
        payload["visible_identity"] = self.visible_display_name
        return payload


class TestCenterDryRun:
    """A bounded operation that may never call the normal sender."""

    __test__ = False

    def __init__(self, page, selectors: ChatSelectors, *, artifact_dir):
        self.page = page
        self.selectors = selectors
        self.artifact_dir = artifact_dir

    def run_target(
        self,
        target: Target,
        test_text: str | None,
        settings: DryRunSettings,
        *,
        run_id: str | None = None,
        request_revision: int = 0,
        navigation_only: bool = False,
        on_stage: Callable[[str], None] | None = None,
        on_composer_write_started: Callable[[], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
        resolve_test_text: Callable[[], str] | None = None,
    ) -> DryRunResult:
        if not test_text and resolve_test_text is None and not navigation_only:
            raise ValueError("测试文本不能为空")
        target_id = target.stable_id or target.candidate_id
        if not target_id:
            raise ValueError("测试目标缺少稳定身份")
        resolved_text = test_text or ""
        digest = hashlib.sha256(resolved_text.encode("utf-8")).hexdigest() if resolved_text else ""
        result = DryRunResult(
            run_id=run_id or uuid.uuid4().hex,
            request_revision=request_revision,
            target_id=target_id,
            selected_target_id=target_id,
            expected_conversation_id=target.candidate_id,
            selected_display_name=target.name,
            stage="waiting",
            result="failed",
            text_length=len(resolved_text),
            text_hash=digest,
        )

        def stage(value: str) -> None:
            result.stage = value
            if on_stage is not None:
                on_stage(value)

        started = time.monotonic()
        try:
            stage("opening_conversation")
            chat = DouyinChat(self.page, self.selectors, self.artifact_dir)
            self.page.set_default_timeout(settings.navigation_timeout_seconds * 1000)
            identity = chat.open_conversation_identity(
                target_id,
                target.candidate_id,
                target.name,
                timeout_ms=settings.navigation_timeout_seconds * 1000,
            )
            result.visible_conversation_id = identity.visible_conversation_id
            result.visible_display_name = identity.visible_display_name
            stage("verifying_identity")
            result.identity_match = identity.identity_match
            result.identity_match_reason = identity.identity_match_reason
            if not result.identity_match:
                result.result = "stopped"
                result.stage = "identity_mismatch"
                result.message = "会话不匹配，测试已停止"
                return result
            self.page.bring_to_front()
            if navigation_only:
                result.result = "navigation_verified"
                result.stage = "navigation_verified"
                return result
            self.page.wait_for_timeout(settings.page_ready_delay_ms)
            editor = chat.composer_editor()
            stage("checking_existing_draft")
            composer = chat.composer_state(editor)
            if composer.attachment_present or composer.mention_or_reply_present:
                result.result = "skipped_existing_context"
                result.stage = "skipped_existing_context"
                result.composer_status = "existing_attachment_preserved"
                result.message = "检测到该聊天已有文字、附件或回复内容，未进行测试输入。"
                result.counters["existing_drafts_preserved"] = 1
                return result
            if not composer.composer_empty:
                result.result = "skipped_existing_draft"
                result.stage = "skipped_existing_draft"
                result.composer_status = "existing_draft_preserved"
                result.message = "检测到该聊天已有文字、附件或回复内容，未进行测试输入。"
                result.counters["existing_drafts_preserved"] = 1
                return result
            if resolve_test_text is not None:
                try:
                    resolved_text = resolve_test_text()
                except Exception:
                    result.result = "message_unavailable"
                    result.stage = "failed"
                    result.message = "今日文案不可用"
                    return result
                if not resolved_text.strip():
                    result.result = "message_unavailable"
                    result.stage = "failed"
                    result.message = "今日文案不可用"
                    return result
                result.text_length = len(resolved_text)
                result.text_hash = hashlib.sha256(resolved_text.encode("utf-8")).hexdigest()
            result.composer_status = "empty"
            stage("typing")
            if on_composer_write_started is not None:
                on_composer_write_started()
            result.counters["real_composer_writes"] = 1
            editor.press_sequentially(resolved_text, delay=settings.typing_delay_ms)
            result.composer_status = "test_text_present"
            stage("observing")
            self.page.wait_for_timeout(settings.typed_text_hold_ms)
            stage("clearing")
            typed = chat.composer_state(editor)
            if (
                typed.visible_text != resolved_text
                or typed.attachment_present
                or typed.mention_or_reply_present
            ):
                result.result = "stopped"
                result.stage = "stopped"
                result.message = "输入内容发生变化，已停止测试"
                result.composer_status = "changed"
                result.counters["cleanup_failures"] = 1
                return result
            # The entire editor is selected only after proving it still contains
            # exactly our own text.  Enter and send controls are never used.
            editor.press("Control+A")
            editor.press("Backspace")
            result.counters["real_composer_clears"] = 1
            stage("verifying_empty")
            self.page.wait_for_timeout(settings.clear_verify_delay_ms)
            if not chat.composer_state(editor).composer_empty:
                result.result = "stopped"
                result.stage = "stopped"
                result.message = "输入框未清空，已停止测试"
                result.composer_status = "not_empty"
                result.counters["cleanup_failures"] = 1
                return result
            result.composer_status = "empty"
            result.result = "stopped" if stop_requested and stop_requested() else "completed"
            result.stage = result.result
            return result
        except Exception:
            if result.counters["real_composer_writes"] != result.counters["real_composer_clears"]:
                result.stage = "cleanup_failed"
                result.result = "cleanup_failed"
                result.message = "清除失败，批量测试已停止"
                result.counters["cleanup_failures"] = max(1, result.counters["cleanup_failures"])
            elif result.identity_match is True:
                result.stage = "stopped"
                result.result = "uncertain_composer"
                result.message = "输入框状态不确定，批量测试已停止"
            else:
                result.stage = "failed"
                result.result = "navigation_failed"
                result.message = "无法打开聊天"
            return result
        finally:
            result.completed_at = datetime.now().isoformat(timespec="seconds")
            result.duration_seconds = round(time.monotonic() - started, 3)


class DryRunController:
    """Owns exactly one Test Center-only browser dry-run session.

    The input text remains in this process only.  Recovery state deliberately
    keeps its hash and length, never the text, so a restarted process can warn
    but cannot blindly edit a user's composer.
    """

    def __init__(self, config_path: Path, module_data_root: Path):
        self.config_path = config_path
        self.store = DryRunStore(module_data_root)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pause_requested = False
        self._stop_requested = False
        self._focus_requested = threading.Event()
        self._state: dict = {
            "running": False,
            "paused": False,
            "stage": "waiting",
            "selected_target_id": self._selection()[0],
            "expected_conversation_id": None,
            "selected_display_name": None,
            "current_target_id": None,
            "visible_conversation_id": None,
            "visible_display_name": None,
            "identity_match": None,
            "identity_match_reason": None,
            "composer_status": "unknown",
            "result": None,
            "message": None,
            "run_id": None,
            "request_revision": self._selection()[1],
            "elapsed_seconds": 0,
            "mode": "single",
            "total_targets": 0,
            "current_position": 0,
            "completed_targets": 0,
            "passed_targets": 0,
            "skipped_targets": 0,
            "failed_targets": 0,
            "remaining_targets": 0,
            "results": [],
            "resolved_test_text": None,
            "counters": empty_counters(),
        }

    @property
    def selection_path(self) -> Path:
        return self.store.directory / "dry-run" / "selection.json"

    def _selection(self) -> tuple[str | None, int]:
        try:
            value = json.loads(self.selection_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, 0
        selected = value.get("selected_target_id") if isinstance(value, dict) else None
        revision = value.get("request_revision", 0) if isinstance(value, dict) else 0
        return (str(selected) if selected else None, int(revision) if isinstance(revision, int) else 0)

    def select(
        self,
        target_id: str,
        *,
        request_revision: int | None = None,
        expected_conversation_id: str | None = None,
        selected_display_name: str | None = None,
    ) -> bool:
        with self._lock:
            if self._state["running"]:
                raise RuntimeError("测试中心正在运行")
            current_revision = int(self._state["request_revision"])
            revision = current_revision + 1 if request_revision is None else request_revision
            if revision <= current_revision:
                return False
            self.store._write_json(
                self.selection_path,
                {"selected_target_id": target_id, "request_revision": revision},
            )
            self._state.update({
                "running": False,
                "paused": False,
                "stage": "waiting",
                "selected_target_id": target_id,
                "expected_conversation_id": expected_conversation_id,
                "selected_display_name": selected_display_name,
                "current_target_id": None,
                "visible_conversation_id": None,
                "visible_display_name": None,
                "identity_match": None,
                "identity_match_reason": None,
                "composer_status": "unknown",
                "result": None,
                "message": None,
                "run_id": None,
                "request_revision": revision,
                "elapsed_seconds": 0,
                "mode": "single",
                "total_targets": 0,
                "current_position": 0,
                "completed_targets": 0,
                "passed_targets": 0,
                "skipped_targets": 0,
                "failed_targets": 0,
                "remaining_targets": 0,
                "results": [],
                "resolved_test_text": None,
                "counters": empty_counters(),
            })
        return True

    def settings(self) -> DryRunSettings:
        return self.store.settings()

    def save_settings(self, settings: DryRunSettings) -> DryRunSettings:
        return self.store.save_settings(settings)

    def status(self) -> dict:
        with self._lock:
            value = dict(self._state)
            value["counters"] = dict(self._state["counters"])
            value["results"] = [
                {**item, "counters": dict(item.get("counters", {}))}
                for item in self._state["results"]
            ]
        value["settings"] = self.settings().model_dump()
        value["recovery_warning"] = self.store.recovery_warning()
        return value

    def history(self) -> list[dict]:
        return self.store.history()

    def start(
        self,
        target_id: str,
        test_text: str,
        *,
        automatic: bool,
        run_id: str | None = None,
        request_revision: int | None = None,
        navigation_only: bool = False,
        use_today_message: bool = False,
        batch_target_ids: list[str] | None = None,
    ) -> dict:
        if not use_today_message and not navigation_only and not test_text.strip():
            raise ValueError("测试文本不能为空")
        bound_run_id = run_id or uuid.uuid4().hex
        config = load_config(self.config_path)
        targets = self._targets(
            config,
            target_id,
            automatic,
            candidate_presence=self._candidate_presence(config),
            batch_target_ids=batch_target_ids,
        )
        if automatic and not targets:
            raise ValueError("没有符合安全条件的批量测试目标")
        with self._lock:
            if self._state["running"]:
                raise RuntimeError("测试中心正在运行")
            revision = int(self._state["request_revision"]) if request_revision is None else request_revision
            if revision != self._state["request_revision"] or target_id != self._state["selected_target_id"]:
                raise RuntimeError("目标或请求版本已过期")
            self._stop_requested = False
            self._pause_requested = False
            self._state.update({
                "running": True, "paused": False, "stage": "waiting", "selected_target_id": target_id,
                "current_target_id": None, "visible_conversation_id": None, "visible_display_name": None,
                "identity_match": None, "identity_match_reason": None, "composer_status": "unknown",
                "result": None, "message": None, "run_id": bound_run_id,
                "elapsed_seconds": 0, "mode": "batch" if automatic else "single",
                "total_targets": len(targets), "current_position": 0,
                "completed_targets": 0, "passed_targets": 0,
                "skipped_targets": 0, "failed_targets": 0,
                "remaining_targets": len(targets), "results": [],
                "resolved_test_text": None, "counters": empty_counters(),
            })
        self._thread = threading.Thread(
            target=self._run,
            args=(
                targets,
                test_text,
                automatic,
                bound_run_id,
                revision,
                navigation_only,
                use_today_message,
            ),
            daemon=True,
            name="autody-test-center-dry-run",
        )
        self._thread.start()
        return self.status()

    def pause(self) -> dict:
        with self._lock:
            if self._state["running"]:
                self._pause_requested = True
        return self.status()

    def resume(self) -> dict:
        with self._lock:
            self._pause_requested = False
            if self._state["running"]:
                self._state["paused"] = False
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop_requested = True
            self._pause_requested = False
        return self.status()

    def focus_browser(self) -> dict:
        with self._lock:
            if not self._state["running"]:
                raise RuntimeError("受管浏览器当前未运行")
            self._focus_requested.set()
        return self.status()

    def _update(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)

    def _update_for_run(self, run_id: str, request_revision: int, **changes) -> bool:
        with self._lock:
            if (
                self._state.get("run_id") != run_id
                or self._state.get("request_revision") != request_revision
            ):
                return False
            self._state.update(changes)
            return True

    def _should_stop(self) -> bool:
        with self._lock:
            return self._stop_requested

    def _wait_until_resumed_or_stopped(self) -> bool:
        while True:
            with self._lock:
                if self._stop_requested:
                    return False
                paused = self._pause_requested
                self._state["paused"] = paused
                if paused:
                    self._state["stage"] = "waiting"
            if not paused:
                return True
            time.sleep(0.1)

    def _wait_switch_interval(self, seconds: int) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._wait_until_resumed_or_stopped():
                return False
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return not self._should_stop()

    @staticmethod
    def _candidate_presence(config: AppConfig) -> dict[str, str]:
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        if discovered is None:
            return {}
        return {
            candidate.candidate_id: candidate.presence_status
            for candidate in discovered.candidates
        }

    @staticmethod
    def _targets(
        config: AppConfig,
        selected_target_id: str,
        automatic: bool,
        *,
        candidate_presence: Mapping[str, str] | None = None,
        batch_target_ids: list[str] | None = None,
    ) -> list[Target]:
        if automatic:
            targets = eligible_batch_targets(config, candidate_presence)
            if batch_target_ids is not None:
                requested = set(batch_target_ids)
                targets = [
                    target for target in targets
                    if target.stable_id in requested
                ]
            return targets
        target = next(
            (
                item
                for item in config.targets
                if item.enabled
                and item.stable_id == selected_target_id
                and item.candidate_id
            ),
            None,
        )
        if target is None:
            raise ValueError("预览目标无效、已停用或缺少稳定会话身份")
        return [target]

    @staticmethod
    def _result_category(payload: dict) -> str:
        if payload.get("result") in {"completed", "navigation_verified"}:
            return "passed"
        if payload.get("result") in {
            "skipped_existing_draft",
            "skipped_existing_context",
        }:
            return "skipped"
        return "failed"

    def _finish_target(self, result: DryRunResult) -> bool:
        payload = result.payload()
        self.store.save_result(payload)
        safe_cleanup = self.can_advance(payload)
        if not self.recovery_needed(payload):
            self.store.clear_recovery()
        with self._lock:
            if (
                self._state.get("run_id") != result.run_id
                or self._state.get("request_revision") != result.request_revision
            ):
                return False
            counters = {
                key: int(self._state["counters"].get(key, 0))
                + int(result.counters.get(key, 0))
                for key in _COUNTER_NAMES
            }
            results = list(self._state["results"])
            results.append(payload)
            category = self._result_category(payload)
            completed = len(results)
            self._state.update({
                "stage": result.stage,
                "selected_target_id": result.selected_target_id,
                "expected_conversation_id": result.expected_conversation_id,
                "selected_display_name": result.selected_display_name,
                "visible_conversation_id": result.visible_conversation_id,
                "visible_display_name": result.visible_display_name,
                "identity_match": result.identity_match,
                "identity_match_reason": result.identity_match_reason,
                "composer_status": result.composer_status,
                "result": result.result,
                "message": result.message,
                "counters": counters,
                "results": results,
                "completed_targets": completed,
                "passed_targets": int(self._state["passed_targets"]) + (category == "passed"),
                "skipped_targets": int(self._state["skipped_targets"]) + (category == "skipped"),
                "failed_targets": int(self._state["failed_targets"]) + (category == "failed"),
                "remaining_targets": max(0, int(self._state["total_targets"]) - completed),
            })
        return safe_cleanup

    @staticmethod
    def can_advance(result: dict) -> bool:
        counters = result.get("counters") if isinstance(result, dict) else None
        if not isinstance(counters, dict):
            return False
        return (
            (
                result.get("result") in {
                    "completed",
                    "navigation_verified",
                    "skipped_existing_draft",
                    "skipped_existing_context",
                    "message_unavailable",
                    "navigation_failed",
                }
                or result.get("identity_match") is False
            )
            and counters.get("real_composer_writes", 0) == counters.get("real_composer_clears", 0)
            and counters.get("cleanup_failures", 0) == 0
        )

    @staticmethod
    def recovery_needed(result: dict) -> bool:
        counters = result.get("counters") if isinstance(result, dict) else None
        if not isinstance(counters, dict):
            return True
        return (
            counters.get("real_composer_writes", 0)
            != counters.get("real_composer_clears", 0)
            or counters.get("cleanup_failures", 0) != 0
        )

    def _run(
        self,
        targets: list[Target],
        test_text: str,
        automatic: bool,
        run_id: str,
        request_revision: int,
        navigation_only: bool,
        use_today_message: bool,
    ) -> None:
        started = time.monotonic()
        try:
            config = load_config(self.config_path)
            settings = self.settings()
            for index, target in enumerate(targets):
                if not self._wait_until_resumed_or_stopped():
                    break
                target_id = target.stable_id or target.candidate_id
                self._update_for_run(
                    run_id,
                    request_revision,
                    selected_target_id=target_id,
                    expected_conversation_id=target.candidate_id,
                    selected_display_name=target.name,
                    current_target_id=target_id,
                    current_position=index + 1,
                    stage="waiting",
                    visible_conversation_id=None,
                    visible_display_name=None,
                    identity_match=None,
                    identity_match_reason=None,
                    composer_status="unknown",
                    result=None,
                    message=None,
                    resolved_test_text=None,
                    elapsed_seconds=int(time.monotonic() - started),
                )
                target_started = time.monotonic()
                resolved_text = {"value": test_text}
                started_at = datetime.now().isoformat(timespec="seconds")

                def failed_result(code: str, message: str) -> DryRunResult:
                    return DryRunResult(
                        run_id=run_id,
                        request_revision=request_revision,
                        target_id=target_id,
                        selected_target_id=target_id,
                        expected_conversation_id=target.candidate_id,
                        selected_display_name=target.name,
                        stage="failed",
                        result=code,
                        message=message,
                        completed_at=datetime.now().isoformat(timespec="seconds"),
                        duration_seconds=round(time.monotonic() - target_started, 3),
                    )

                try:
                    with SingleInstanceLock(config.lock_file):
                        with open_chat(
                            config.profile_dir,
                            timeout_ms=settings.navigation_timeout_seconds * 1000,
                            headless=False,
                            home=self.config_path.parent,
                        ) as page:
                            page.bring_to_front()
                            self._focus_requested.clear()
                            runner = TestCenterDryRun(
                                page,
                                DOUYIN_SELECTORS,
                                artifact_dir=config.artifact_dir,
                            )

                            def record_stage(stage: str, current=target_id) -> None:
                                if self._focus_requested.is_set():
                                    page.bring_to_front()
                                    self._focus_requested.clear()
                                self._update_for_run(
                                    run_id,
                                    request_revision,
                                    stage=stage,
                                    current_target_id=current,
                                    elapsed_seconds=int(time.monotonic() - started),
                                )

                            def resolve_current_text() -> str:
                                value = (
                                    preview_today_target_message(config, target, date.today()).text
                                    if use_today_message
                                    else test_text
                                )
                                resolved_text["value"] = value
                                self._update_for_run(
                                    run_id,
                                    request_revision,
                                    resolved_test_text=value,
                                )
                                return value

                            def record_composer_write_started(current=target_id) -> None:
                                value = resolved_text["value"]
                                self.store.save_recovery({
                                    "run_id": run_id,
                                    "target_id": current,
                                    "text_hash": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                                    "text_length": len(value),
                                    "stage": "typing",
                                    "started_at": started_at,
                                })

                            result = runner.run_target(
                                target,
                                None if use_today_message else test_text,
                                settings,
                                run_id=run_id,
                                request_revision=request_revision,
                                navigation_only=navigation_only,
                                on_stage=record_stage,
                                on_composer_write_started=record_composer_write_started,
                                stop_requested=self._should_stop,
                                resolve_test_text=(
                                    resolve_current_text
                                    if use_today_message and not navigation_only
                                    else None
                                ),
                            )
                except TaskAlreadyRunning:
                    result = failed_result("browser_busy", "浏览器忙")
                except AuthenticationError:
                    result = failed_result("login_required", "需要登录")
                except ChatPageLoadError:
                    result = failed_result("navigation_failed", "无法打开聊天")
                except Exception:
                    result = failed_result("navigation_failed", "无法打开聊天")

                safe_cleanup = self._finish_target(result)
                if not safe_cleanup:
                    break
                if self._should_stop():
                    self._update_for_run(
                        run_id,
                        request_revision,
                        stage="stopped",
                        result="stopped",
                        message="用户停止",
                    )
                    break
                if (
                    automatic
                    and index + 1 < len(targets)
                    and not self._wait_switch_interval(
                        settings.target_switch_interval_seconds
                    )
                ):
                    break
            else:
                if automatic:
                    self._update_for_run(
                        run_id,
                        request_revision,
                        stage="completed",
                        result="batch_completed",
                        message="批量测试已完成",
                    )
        except Exception:
            self._update_for_run(
                run_id,
                request_revision,
                stage="failed",
                result="failed",
                message="测试页面操作未完成",
            )
        finally:
            self._focus_requested.clear()
            if self._should_stop():
                self._update_for_run(
                    run_id,
                    request_revision,
                    stage="stopped",
                    result="stopped",
                    message="用户停止",
                )
            self._update_for_run(
                run_id,
                request_revision,
                running=False,
                paused=False,
                elapsed_seconds=int(time.monotonic() - started),
            )

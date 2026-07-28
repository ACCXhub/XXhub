from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import hashlib
import logging
import random
import time

from autody.chat import DeliveryResult, DeliveryStatus, FatalChatError
from autody.config import AppConfig, MessageSuffixConfig, Target
from autody.history import TaskHistoryStore, TaskRunRecord, stable_target_id
from autody.message_packs import MessagePackError, MessagePackService
from autody.messages import MessageRotation, format_message_with_suffix, read_messages
from autody.retry_state import TaskOutcome, TaskOutcomeStore
from autody.state import StateStore


logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    ALREADY_DONE = "already_done"
    COMPLETED = "completed"
    RETRY_PENDING = "retry_pending"
    RECOVERED = "recovered"
    FINAL_FAILED = "final_failed"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    total_targets: int
    sent_count: int
    skipped_count: int
    failed_count: int
    error: str | None = None
    run_id: str | None = None
    retry_count: int = 0
    confirmation_results: dict[str, str] = field(default_factory=dict)


def _history_path(config: AppConfig):
    return config.state_file.parent / "history" / "task-runs.jsonl"


def _outcome_path(config: AppConfig):
    return config.state_file.parent / "history" / "task-outcomes.json"


def _daily_run_id(day: date) -> str:
    return hashlib.sha256(f"daily-send:{day.isoformat()}".encode()).hexdigest()[:24]


def record_safe_pre_send_failure(config: AppConfig, reason: str, *, now: datetime | None = None) -> RunResult:
    """Persist a retry only for failures proven to precede every send action."""
    started = now or datetime.now()
    day = started.date()
    store = StateStore(config.state_file)
    state = store.load()
    daily = state.daily.setdefault(day.isoformat(), {"message": "", "succeeded": [], "failures": {}, "confirmation_results": {}, "consumed": False})
    run_id = daily.setdefault("task_run_id", _daily_run_id(day))
    store.save(state)
    outcomes = TaskOutcomeStore(_outcome_path(config))
    outcome = outcomes.schedule(run_id, day.isoformat(), datetime.combine(day, datetime.strptime(config.recovery_deadline, "%H:%M").time()))
    if outcome.outcome is TaskOutcome.RETRY_PENDING and outcome.next_attempt_at and started < outcome.next_attempt_at:
        status = RunStatus.RETRY_PENDING
        retries = outcome.retry_attempts
    else:
        persisted = outcomes.safe_failure(run_id, started, reason, max_retries=config.retry_count)
        status = RunStatus.RETRY_PENDING if persisted.outcome is TaskOutcome.RETRY_PENDING else RunStatus.FINAL_FAILED
        retries = persisted.retry_attempts
    total = sum(target.enabled for target in config.targets)
    return RunResult(status, total, 0, 0, total, reason, run_id=run_id, retry_count=retries)


def _delivery_result(value) -> DeliveryResult:
    if isinstance(value, DeliveryResult):
        return value
    return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1, confirmation_attempts=1)


def _target_suffix(target: Target, config: AppConfig) -> MessageSuffixConfig:
    if target.suffix_mode == "disabled":
        return MessageSuffixConfig(enabled=False)
    if target.suffix_mode == "custom" and (target.suffix_override or "").strip():
        return MessageSuffixConfig(
            enabled=True,
            text=target.suffix_override.strip(),
            style=config.message_suffix.style,
        )
    return config.message_suffix


def _target_base_message(
    target: Target,
    config: AppConfig,
    daily: dict,
    messages: list[str],
) -> str:
    if not target.message_pack:
        return daily["message"]
    target_id = target.stable_id or target.candidate_id or stable_target_id(target.name)
    key = f"pack:{target_id}"
    cached = daily.setdefault("messages_by_target", {}).get(key)
    if cached:
        return cached
    pack_messages = MessagePackService(config.messages_file.parent, config.message_pack_index_url).preview(target.message_pack).messages
    selected = random.SystemRandom().choice(pack_messages) if (target.message_selection or config.message_selection) == "per_friend" else pack_messages[0]
    daily["messages_by_target"][key] = selected
    return selected


def _record_history(
    config: AppConfig,
    started: datetime,
    source: str,
    result: RunResult,
    base_message: str,
    failed_names: list[str],
) -> None:
    ended = datetime.now(started.tzinfo)
    TaskHistoryStore(_history_path(config)).append(
        TaskRunRecord(
            run_id=result.run_id or hashlib.sha256(started.isoformat().encode()).hexdigest()[:24],
            date=started.date().isoformat(),
            task_type="daily_send",
            trigger_source=source,  # type: ignore[arg-type]
            start_time=started.isoformat(timespec="seconds"),
            end_time=ended.isoformat(timespec="seconds"),
            duration=max(0.0, (ended - started).total_seconds()),
            total_targets=result.total_targets,
            success_count=result.sent_count,
            failed_count=result.failed_count,
            skipped_count=result.skipped_count,
            retry_count=result.retry_count,
            final_status=result.status.value,
            base_message_id=hashlib.sha256(base_message.encode("utf-8")).hexdigest()[:16] if base_message else None,
            message_pack="global",
            error_summary=(result.error or "")[:240] or None,
            failed_target_ids=[stable_target_id(name) for name in failed_names],
            confirmation_results=result.confirmation_results,
        )
    )


def run_daily(
    config: AppConfig,
    chat,
    today: date | None = None,
    *,
    trigger_source: str = "manual",
    now: datetime | None = None,
) -> RunResult:
    started = now or datetime.now()
    today = today or started.date()
    # Tests and controlled recovery callers may supply a historical ``today``
    # without a wall-clock value. Keep the retry window on that requested day.
    if now is None and today != started.date():
        started = datetime.combine(today, datetime.strptime(config.daily_send_time, "%H:%M").time())
    key = today.isoformat()
    store = StateStore(config.state_file)
    rotation = MessageRotation()
    state = store.load()
    daily = state.daily.setdefault(
        key,
        {
            "message": "",
            "succeeded": [],
            "failures": {},
            "confirmation_results": {},
            "consumed": False,
        },
    )
    daily.setdefault("confirmation_results", {})
    targets = [target for target in config.targets if target.enabled]
    if config.friend_order == "randomized":
        random.SystemRandom().shuffle(targets)
    normalized_names = {}
    for target in targets:
        normalized_names.setdefault(" ".join(target.name.split()).casefold(), []).append(target)
    ambiguous_names = {
        name
        for name, grouped_targets in normalized_names.items()
        if len(grouped_targets) > 1
    }
    ambiguous_targets = [
        target for target in targets
        if " ".join(target.name.split()).casefold() in ambiguous_names
    ]
    for target in ambiguous_targets:
        daily["failures"][target.name] = "blocked_ambiguous_target"
    pending = [
        target for target in targets
        if target.name not in daily["succeeded"]
        and " ".join(target.name.split()).casefold() not in ambiguous_names
    ]
    total = len(targets)
    skipped = total - len(pending)
    run_id = daily.setdefault(
        "task_run_id",
        _daily_run_id(today),
    )
    if not pending:
        status = RunStatus.UNCERTAIN if ambiguous_targets else RunStatus.ALREADY_DONE
        result = RunResult(status, total, 0, skipped, len(ambiguous_targets), "blocked_ambiguous_target" if ambiguous_targets else None, run_id=run_id)
        _record_history(config, started, trigger_source, result, daily.get("message", ""), [])
        return result
    if skipped and trigger_source == "manual":
        trigger_source = "retry"

    outcome_store = TaskOutcomeStore(_outcome_path(config))
    deadline = datetime.combine(today, datetime.strptime(config.recovery_deadline, "%H:%M").time())
    outcome = outcome_store.schedule(run_id, key, deadline)
    terminal_statuses = {
        TaskOutcome.FINAL_FAILED: RunStatus.FINAL_FAILED,
        TaskOutcome.UNCERTAIN: RunStatus.UNCERTAIN,
        TaskOutcome.CANCELLED: RunStatus.CANCELLED,
    }
    if outcome.outcome in terminal_statuses:
        result = RunResult(terminal_statuses[outcome.outcome], total, 0, skipped, len(pending), outcome.reason, run_id=run_id, retry_count=outcome.retry_attempts)
        _record_history(config, started, trigger_source, result, daily.get("message", ""), [target.name for target in pending])
        return result
    if outcome.outcome is TaskOutcome.RETRY_PENDING and outcome.next_attempt_at and started < outcome.next_attempt_at:
        result = RunResult(RunStatus.RETRY_PENDING, total, 0, skipped, len(pending), outcome.reason, run_id=run_id, retry_count=outcome.retry_attempts)
        _record_history(config, started, trigger_source, result, daily.get("message", ""), [target.name for target in pending])
        return result
    outcome_store.start(run_id, started)

    messages = read_messages(config.messages_file)
    if not daily["message"]:
        daily["message"] = rotation.peek(messages, state.rotation)
        store.save(state)
    daily.setdefault("messages_by_target", {})

    sent = 0
    retries = outcome.retry_attempts
    confirmation_results: dict[str, str] = {}
    safe_failure_reason: str | None = None
    unsafe_failure_reason: str | None = "blocked_ambiguous_target" if ambiguous_targets else None
    run_started = time.monotonic()
    for target_index, target in enumerate(pending):
        target_name = target.name
        remaining = target.delay_offset_minutes * 60 - (time.monotonic() - run_started)
        if remaining > 0:
            time.sleep(remaining)
        if target.message_pack:
            try:
                base = _target_base_message(target, config, daily, messages)
            except MessagePackError as exc:
                daily["failures"][target_name] = "send_failed_before_action"
                logger.warning("目标文案包不可用：%s：%s", target_name, exc)
                store.save(state)
                continue
            target_message = format_message_with_suffix(base, _target_suffix(target, config))
        elif (target.message_selection or config.message_selection) == "per_friend":
            base = daily["messages_by_target"].get(target_name)
            if not base:
                base = random.SystemRandom().choice(messages)
                daily["messages_by_target"][target_name] = base
                store.save(state)
            target_message = format_message_with_suffix(base, _target_suffix(target, config))
        else:
            target_message = format_message_with_suffix(daily["message"], _target_suffix(target, config))
        target_id = target.stable_id or target.candidate_id or stable_target_id(target_name)
        try:
            delivery = _delivery_result(chat.send(target_name, target_message))
        except FatalChatError as exc:
            delivery = DeliveryResult(DeliveryStatus.BLOCKED, error=str(exc))
        except RuntimeError as exc:
            delivery = DeliveryResult(DeliveryStatus.SEND_FAILED, error=str(exc))
        confirmation_results[target_id] = delivery.status.value
        daily["confirmation_results"][target_id] = delivery.status.value
        retries += max(0, delivery.confirmation_attempts - 1)
        error = delivery.error or delivery.status.value
        if delivery.successful:
            daily["succeeded"].append(target_name)
            daily["failures"].pop(target_name, None)
            sent += 1
            logger.info("发送已确认：%s（%s）", target_name, delivery.status.value)
        else:
            daily["failures"][target_name] = error
            if delivery.send_attempts == 0 and delivery.status in {DeliveryStatus.SEND_FAILED, DeliveryStatus.BLOCKED}:
                safe_failure_reason = safe_failure_reason or error
            else:
                # Any activated send control, outgoing bubble, failed confirmation,
                # or unknown delivery state is terminally uncertain.
                unsafe_failure_reason = unsafe_failure_reason or error
            logger.warning("发送未确认：%s（%s）：%s", target_name, delivery.status.value, error)
        store.save(state)
        if target_index < len(pending) - 1:
            time.sleep(random.uniform(config.min_delay_seconds, config.max_delay_seconds))

    complete = all(target.name in daily["succeeded"] for target in targets)
    if complete and not daily.get("consumed"):
        rotation.consume(daily["message"], state.rotation)
        daily["consumed"] = True
        store.save(state)
    failed_names = [target.name for target in targets if target.name not in daily["succeeded"]]
    if complete:
        status = RunStatus.RECOVERED if outcome.retry_attempts else RunStatus.COMPLETED
        if status is RunStatus.RECOVERED:
            outcome_store.recover(run_id, started)
        else:
            outcome_store.complete(run_id, started)
    elif unsafe_failure_reason:
        status = RunStatus.UNCERTAIN
        outcome_store.uncertain(run_id, started, unsafe_failure_reason)
    elif safe_failure_reason:
        persisted = outcome_store.safe_failure(run_id, started, safe_failure_reason, max_retries=config.retry_count)
        status = RunStatus.RETRY_PENDING if persisted.outcome is TaskOutcome.RETRY_PENDING else RunStatus.FINAL_FAILED
        retries = persisted.retry_attempts
    else:
        status = RunStatus.FINAL_FAILED
        outcome_store.safe_failure(run_id, started, "unknown_pre_send_failure", max_retries=0)
    daily["outcome"] = status.value
    store.save(state)
    result = RunResult(
        status,
        total,
        sent,
        skipped,
        len(failed_names),
        unsafe_failure_reason or safe_failure_reason,
        run_id,
        retries,
        confirmation_results,
    )
    _record_history(config, started, trigger_source, result, daily["message"], failed_names)
    return result

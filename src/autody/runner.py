from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import copy
import hashlib
import inspect
import logging
import random
import time

from autody.chat import (
    DeliveryResult,
    DeliveryStatus,
    FatalChatError,
    conversation_candidate_id,
)
from autody.config import (
    AppConfig,
    MessageSuffixConfig,
    Target,
    enabled_execution_targets,
)
from autody.account_profile import (
    bindings_revalidation_required,
    evaluate_account_scope,
    load_account_profile,
)
from autody.failures import FailureDetail, failure_detail
from autody.friend_discovery import FriendCandidate, load_discovered_friends
from autody.history import (
    TaskHistoryStore,
    TaskRunRecord,
    effective_daily_target_statuses,
    target_identity,
)
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
    target_failures: dict[str, FailureDetail] = field(default_factory=dict)


@dataclass(frozen=True)
class TodayTargetMessage:
    text: str


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
    total = len(enabled_execution_targets(config))
    return RunResult(status, total, 0, 0, total, reason, run_id=run_id, retry_count=retries)


def _delivery_result(value) -> DeliveryResult:
    if isinstance(value, DeliveryResult):
        return value
    return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1, confirmation_attempts=1)


def _verified_conversation_ids(
    config: AppConfig,
    targets: list[Target],
) -> dict[str, str]:
    """Resolve persistent bindings to current DOM identities without guessing."""
    data_root = config.state_file.parent
    discovered = load_discovered_friends(data_root / "discovered_friends.json")
    if (
        discovered is None
        or not discovered.last_result.get("completed_bottom_reached")
        or bindings_revalidation_required(data_root)
    ):
        return {}
    account_evaluation = evaluate_account_scope(
        load_account_profile(config.messages_file.parent),
        binding_scope=discovered.account_scope,
    )
    if account_evaluation.compatible is not True:
        return {}

    current_by_candidate_id: dict[str, list[FriendCandidate]] = {}
    for candidate in discovered.candidates:
        if candidate.presence_status != "current":
            continue
        current_by_candidate_id.setdefault(candidate.candidate_id, []).append(candidate)

    resolved: dict[str, str] = {}
    for target in targets:
        if not target.stable_id or not target.candidate_id:
            continue
        matches = current_by_candidate_id.get(target.candidate_id, [])
        if len(matches) != 1:
            continue
        candidate = matches[0]
        if (
            candidate.match_status != "configured"
            or candidate.configured_target_id != target.stable_id
        ):
            continue
        conversation_id = conversation_candidate_id(candidate.identity_key)
        if conversation_id:
            resolved[target.stable_id] = conversation_id
    return resolved


def _send_target(
    chat,
    target: Target,
    message: str,
    *,
    expected_conversation_id: str | None = None,
) -> DeliveryResult:
    """Pass stable binding evidence when the sender supports it.

    The signature check keeps small test/extension senders compatible without
    hiding a TypeError raised from inside their implementation.
    """
    parameters = inspect.signature(chat.send).parameters
    supports_binding = (
        "expected_conversation_id" in parameters
        or any(
            item.kind is inspect.Parameter.VAR_KEYWORD
            for item in parameters.values()
        )
    )
    if supports_binding:
        return _delivery_result(
            chat.send(
                target.name,
                message,
                selected_target_id=target.stable_id,
                expected_conversation_id=(
                    expected_conversation_id or target.candidate_id
                ),
            )
        )
    return _delivery_result(chat.send(target.name, message))


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


def _stored_target_failures(daily: dict) -> dict[str, FailureDetail]:
    records: dict[str, FailureDetail] = {}
    for target_id, value in daily.get("target_failures", {}).items():
        try:
            records[str(target_id)] = FailureDetail.model_validate(value)
        except (TypeError, ValueError):
            continue
    return records


def _legacy_reason_code(delivery: DeliveryResult) -> str:
    if delivery.reason_code:
        return delivery.reason_code
    text = f"{delivery.error or ''} {delivery.status.value}".casefold()
    if (
        delivery.send_attempts
        or delivery.status is DeliveryStatus.CONFIRMATION_FAILED
    ):
        return "confirmation_failed_uncertain"
    if "not found" in text:
        return "conversation_not_found"
    if "ambiguous" in text:
        return "blocked_ambiguous_target"
    if "timeout" in text or "timed out" in text:
        return "page_load_timeout"
    if "composer" in text:
        return "composer_missing"
    return "send_failed_before_action"


def _detail_for_delivery(
    target: Target,
    delivery: DeliveryResult,
    *,
    run_id: str,
    account_scope: str | None = None,
) -> FailureDetail:
    reason_code = _legacy_reason_code(delivery)
    send_attempts = max(
        delivery.send_attempts,
        int(delivery.status is DeliveryStatus.CONFIRMATION_FAILED),
    )
    binding_valid = bool(target.stable_id and target.candidate_id)
    if reason_code in {"binding_stale", "binding_missing"}:
        binding_valid = False
    return failure_detail(
        reason_code,
        stage=delivery.failure_stage
        or (
            "confirmation_observed"
            if delivery.send_attempts
            else "conversation_located"
        ),
        send_attempts=send_attempts,
        run_id=run_id,
        target_stable_id=target_identity(target),
        account_scope=account_scope,
        binding_valid=binding_valid,
        account_scope_matches=True,
        diagnostic_details={
            "delivery_status": delivery.status.value,
            "confirmation_attempts": delivery.confirmation_attempts,
        },
    )


def _selection_rng(day: date, scope: str, target: Target | None = None) -> random.Random:
    target_id = ""
    if target is not None:
        target_id = target_identity(target)
    seed = hashlib.sha256(f"{day.isoformat()}:{scope}:{target_id}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(seed, "big"))


def _target_base_message(
    target: Target,
    config: AppConfig,
    daily: dict,
    messages: list[str],
    day: date,
) -> str:
    if not target.message_pack:
        return daily["message"]
    target_id = target_identity(target)
    key = f"pack:{target_id}"
    cached = daily.setdefault("messages_by_target", {}).get(key)
    if cached:
        return cached
    pack_messages = MessagePackService(config.messages_file.parent, config.message_pack_index_url).preview(target.message_pack).messages
    selected = _selection_rng(day, "message-pack", target).choice(pack_messages) if (target.message_selection or config.message_selection) == "per_friend" else pack_messages[0]
    daily["messages_by_target"][key] = selected
    return selected


def _resolve_target_message(
    target: Target,
    config: AppConfig,
    day: date,
    daily: dict,
    messages: list[str],
) -> str:
    if target.message_pack:
        base = _target_base_message(target, config, daily, messages, day)
    elif (target.message_selection or config.message_selection) == "per_friend":
        target_id = target_identity(target)
        key = f"target:{target_id}"
        per_target = daily.setdefault("messages_by_target", {})
        base = per_target.get(key) or per_target.get(target.name)
        if not base:
            base = _selection_rng(day, "per-friend", target).choice(messages)
            per_target[key] = base
    else:
        base = daily["message"]
    return format_message_with_suffix(base, _target_suffix(target, config))


def preview_today_target_message(
    config: AppConfig,
    target: Target,
    today: date | None = None,
) -> TodayTargetMessage:
    """Resolve production-equivalent text without persisting or advancing state."""
    day = today or date.today()
    state = copy.deepcopy(StateStore(config.state_file).load())
    daily = state.daily.setdefault(
        day.isoformat(),
        {
            "message": "",
            "succeeded": [],
            "failures": {},
            "confirmation_results": {},
            "consumed": False,
        },
    )
    messages = read_messages(config.messages_file)
    if not daily.get("message"):
        daily["message"] = MessageRotation(_selection_rng(day, "daily")).peek(messages, state.rotation)
    daily.setdefault("messages_by_target", {})
    return TodayTargetMessage(_resolve_target_message(target, config, day, daily, messages))


def _record_history(
    config: AppConfig,
    started: datetime,
    source: str,
    result: RunResult,
    base_message: str,
    failed_target_ids: list[str],
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
            failed_target_ids=failed_target_ids,
            confirmation_results=result.confirmation_results,
            target_failures=result.target_failures,
        )
    )


def run_daily(
    config: AppConfig,
    chat,
    today: date | None = None,
    *,
    trigger_source: str = "manual",
    now: datetime | None = None,
    target_ids: set[str] | None = None,
) -> RunResult:
    started = now or datetime.now()
    today = today or started.date()
    # Tests and controlled recovery callers may supply a historical ``today``
    # without a wall-clock value. Keep the retry window on that requested day.
    if now is None and today != started.date():
        started = datetime.combine(today, datetime.strptime(config.daily_send_time, "%H:%M").time())
    key = today.isoformat()
    store = StateStore(config.state_file)
    rotation = MessageRotation(_selection_rng(today, "daily"))
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
    daily.setdefault("target_failures", {})
    run_id = daily.setdefault(
        "task_run_id",
        _daily_run_id(today),
    )
    targets = enabled_execution_targets(config)
    if config.friend_order == "randomized":
        random.SystemRandom().shuffle(targets)
    requested_target_ids = set(target_ids) if target_ids is not None else None
    if requested_target_ids is not None:
        known_target_ids = {target_identity(target) for target in targets}
        unknown_target_ids = requested_target_ids - known_target_ids
        if unknown_target_ids:
            raise ValueError("定向重试目标不存在或未启用")
        selected_targets = [
            target
            for target in targets
            if target_identity(target) in requested_target_ids
        ]
    else:
        selected_targets = targets
    conversation_ids = _verified_conversation_ids(config, selected_targets)
    effective_before_run = effective_daily_target_statuses(
        config,
        state,
        today,
    )

    targeted_blocking_details: dict[str, FailureDetail] = {}
    if requested_target_ids is not None:
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        profile = load_account_profile(config.messages_file.parent)
        current_candidate_ids = {
            candidate.candidate_id
            for candidate in (discovered.candidates if discovered else [])
            if candidate.presence_status == "current"
        }
        guarded = bindings_revalidation_required(config.state_file.parent)
        account_evaluation = evaluate_account_scope(
            profile,
            binding_scope=discovered.account_scope if discovered else None,
        )
        account_diagnostics = {
            "account_comparison": account_evaluation.account_comparison,
        }
        if account_evaluation.run_scope_comparison:
            account_diagnostics["run_scope_comparison"] = (
                account_evaluation.run_scope_comparison
            )
        stored_failures = _stored_target_failures(daily)
        for target in selected_targets:
            target_id = target_identity(target)
            if target.name in daily.get("succeeded", []):
                continue
            previous = stored_failures.get(target_id)
            if previous is not None and (
                previous.uncertain_send or previous.send_attempts > 0
            ):
                detail = previous.model_copy(
                    update={
                        "target_stable_id": target_id,
                        "diagnostic_details": {
                            **previous.diagnostic_details,
                            **account_diagnostics,
                        },
                    }
                )
                targeted_blocking_details[target_id] = detail
                daily["failures"][target.name] = detail.reason_code
                daily["target_failures"][target_id] = detail.model_dump(mode="json")
                continue
            binding_valid = bool(
                target.stable_id
                and target.candidate_id
                and target.candidate_id in current_candidate_ids
                and not guarded
            )
            if account_evaluation.reason_code == "login_required":
                detail = failure_detail(
                    account_evaluation.reason_code,
                    stage="account_verified",
                    run_id=run_id,
                    target_stable_id=target_id,
                    account_scope=discovered.account_scope if discovered else None,
                    binding_valid=binding_valid,
                    account_scope_matches=False,
                    diagnostic_details=account_diagnostics,
                )
            elif guarded:
                detail = failure_detail(
                    "binding_stale",
                    stage="target_binding_resolved",
                    run_id=run_id,
                    target_stable_id=target_id,
                    account_scope=discovered.account_scope if discovered else None,
                    binding_valid=False,
                    account_scope_matches=(
                        True if account_evaluation.compatible is True else None
                    ),
                    diagnostic_details=account_diagnostics,
                )
            elif account_evaluation.reason_code == "account_scope_mismatch":
                detail = failure_detail(
                    "account_scope_mismatch",
                    stage="account_verified",
                    run_id=run_id,
                    target_stable_id=target_id,
                    account_scope=discovered.account_scope if discovered else None,
                    binding_valid=binding_valid,
                    account_scope_matches=False,
                    diagnostic_details=account_diagnostics,
                )
            elif account_evaluation.compatible is not True:
                detail = failure_detail(
                    "binding_stale",
                    stage="target_binding_resolved",
                    run_id=run_id,
                    target_stable_id=target_id,
                    account_scope=discovered.account_scope if discovered else None,
                    binding_valid=False,
                    account_scope_matches=None,
                    diagnostic_details=account_diagnostics,
                )
            elif not target.stable_id or not target.candidate_id:
                detail = failure_detail(
                    "binding_missing",
                    stage="target_binding_resolved",
                    run_id=run_id,
                    target_stable_id=target_id,
                    account_scope=discovered.account_scope if discovered else None,
                    binding_valid=False,
                    account_scope_matches=True,
                    diagnostic_details=account_diagnostics,
                )
            elif not binding_valid:
                detail = failure_detail(
                    "binding_stale",
                    stage="target_binding_resolved",
                    run_id=run_id,
                    target_stable_id=target_id,
                    account_scope=discovered.account_scope if discovered else None,
                    binding_valid=False,
                    account_scope_matches=True,
                    diagnostic_details=account_diagnostics,
                )
            else:
                if previous is not None:
                    refreshed = previous.model_copy(
                        update={
                            "binding_valid": True,
                            "account_scope_matches": True,
                            "account_scope": discovered.account_scope
                            if discovered
                            else None,
                            "diagnostic_details": {
                                **previous.diagnostic_details,
                                **account_diagnostics,
                            },
                        }
                    )
                    if refreshed.safe_retry_available:
                        daily["target_failures"][target_id] = refreshed.model_dump(
                            mode="json"
                        )
                        continue
                    if (
                        refreshed.reason_code
                        in {"account_scope_mismatch", "login_required"}
                        and refreshed.send_attempts == 0
                        and not refreshed.uncertain_send
                    ):
                        daily["target_failures"][target_id] = failure_detail(
                            "send_failed_before_action",
                            stage=refreshed.stage,
                            run_id=run_id,
                            target_stable_id=target_id,
                            account_scope=discovered.account_scope,
                            binding_valid=True,
                            account_scope_matches=True,
                            diagnostic_details={
                                **refreshed.diagnostic_details,
                                **account_diagnostics,
                            },
                        ).model_dump(mode="json")
                        continue
                    detail = refreshed
                elif str(daily.get("failures", {}).get(target.name, "")) in {
                    "account_scope_mismatch",
                    "login_required",
                }:
                    daily["target_failures"][target_id] = failure_detail(
                        "send_failed_before_action",
                        stage="account_verified",
                        run_id=run_id,
                        target_stable_id=target_id,
                        account_scope=discovered.account_scope,
                        binding_valid=True,
                        account_scope_matches=True,
                        diagnostic_details=account_diagnostics,
                    ).model_dump(mode="json")
                    continue
                else:
                    detail = failure_detail(
                        "unknown_exception",
                        stage="target_loaded",
                        run_id=run_id,
                        target_stable_id=target_id,
                        binding_valid=True,
                        account_scope_matches=True,
                        diagnostic_details=account_diagnostics,
                    )
            targeted_blocking_details[target_id] = detail
            daily["failures"][target.name] = detail.reason_code
            daily["target_failures"][target_id] = detail.model_dump(mode="json")
        if targeted_blocking_details:
            store.save(state)
            unsafe = any(
                detail.uncertain_send or detail.send_attempts > 0
                for detail in targeted_blocking_details.values()
            )
            result = RunResult(
                RunStatus.UNCERTAIN if unsafe else RunStatus.FINAL_FAILED,
                len(targets),
                0,
                len(targets) - len(targeted_blocking_details),
                len(targeted_blocking_details),
                next(iter(targeted_blocking_details.values())).reason_code,
                run_id=run_id,
                target_failures=targeted_blocking_details,
            )
            _record_history(
                config,
                started,
                trigger_source,
                result,
                daily.get("message", ""),
                list(targeted_blocking_details),
            )
            return result

    normalized_names = {}
    for target in selected_targets:
        normalized_names.setdefault(" ".join(target.name.split()).casefold(), []).append(target)
    ambiguous_names = {
        name
        for name, grouped_targets in normalized_names.items()
        if len(grouped_targets) > 1
    }
    ambiguous_targets = [
        target for target in selected_targets
        if " ".join(target.name.split()).casefold() in ambiguous_names
    ]
    for target in ambiguous_targets:
        daily["failures"][target.name] = "blocked_ambiguous_target"
        target_id = target_identity(target)
        daily["target_failures"][target_id] = failure_detail(
            "blocked_ambiguous_target",
            stage="target_binding_resolved",
            run_id=run_id,
            target_stable_id=target_id,
            binding_valid=False,
            account_scope_matches=True,
        ).model_dump(mode="json")
    pending = [
        target for target in selected_targets
        if effective_before_run.get(target_identity(target)) != "success"
        and " ".join(target.name.split()).casefold() not in ambiguous_names
    ]
    total = len(targets)
    skipped = total - len(pending)
    if not pending:
        status = RunStatus.UNCERTAIN if ambiguous_targets else RunStatus.ALREADY_DONE
        details = _stored_target_failures(daily)
        result = RunResult(
            status,
            total,
            0,
            skipped,
            len(ambiguous_targets),
            "blocked_ambiguous_target" if ambiguous_targets else None,
            run_id=run_id,
            target_failures=details,
        )
        _record_history(
            config,
            started,
            trigger_source,
            result,
            daily.get("message", ""),
            list(details),
        )
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
    if outcome.outcome in terminal_statuses and requested_target_ids is None:
        result = RunResult(
            terminal_statuses[outcome.outcome],
            total,
            0,
            skipped,
            len(pending),
            outcome.reason,
            run_id=run_id,
            retry_count=outcome.retry_attempts,
            target_failures=_stored_target_failures(daily),
        )
        _record_history(
            config,
            started,
            trigger_source,
            result,
            daily.get("message", ""),
            [target_identity(target) for target in pending],
        )
        return result
    if (
        requested_target_ids is None
        and outcome.outcome is TaskOutcome.RETRY_PENDING
        and outcome.next_attempt_at
        and started < outcome.next_attempt_at
    ):
        result = RunResult(
            RunStatus.RETRY_PENDING,
            total,
            0,
            skipped,
            len(pending),
            outcome.reason,
            run_id=run_id,
            retry_count=outcome.retry_attempts,
            target_failures=_stored_target_failures(daily),
        )
        _record_history(
            config,
            started,
            trigger_source,
            result,
            daily.get("message", ""),
            [target_identity(target) for target in pending],
        )
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
    blocking_failure_reason: str | None = None
    if requested_target_ids is not None:
        succeeded_names = set(daily.get("succeeded", []))
        for target_id, detail in _stored_target_failures(daily).items():
            if target_id in requested_target_ids:
                continue
            peer = next(
                (target for target in targets if target_identity(target) == target_id),
                None,
            )
            if peer is None or peer.name in succeeded_names:
                continue
            if detail.uncertain_send or detail.send_attempts > 0:
                unsafe_failure_reason = (
                    unsafe_failure_reason or detail.reason_code
                )
            elif detail.retryable:
                safe_failure_reason = safe_failure_reason or detail.reason_code
            else:
                blocking_failure_reason = (
                    blocking_failure_reason or detail.reason_code
                )
    run_started = time.monotonic()
    for target_index, target in enumerate(pending):
        target_name = target.name
        remaining = target.delay_offset_minutes * 60 - (time.monotonic() - run_started)
        if remaining > 0:
            time.sleep(remaining)
        if target.message_pack:
            try:
                target_message = _resolve_target_message(target, config, today, daily, messages)
            except MessagePackError as exc:
                daily["failures"][target_name] = "send_failed_before_action"
                target_id = target_identity(target)
                detail = failure_detail(
                    "message_pack_unavailable",
                    stage="message_prepared",
                    run_id=run_id,
                    target_stable_id=target_id,
                    binding_valid=bool(target.stable_id and target.candidate_id),
                    account_scope_matches=True,
                    diagnostic_details={"exception_type": type(exc).__name__},
                )
                daily["target_failures"][target_id] = detail.model_dump(
                    mode="json"
                )
                blocking_failure_reason = (
                    blocking_failure_reason or detail.reason_code
                )
                logger.warning("目标文案包不可用：%s：%s", target_name, exc)
                store.save(state)
                continue
        elif (target.message_selection or config.message_selection) == "per_friend":
            target_message = _resolve_target_message(target, config, today, daily, messages)
            store.save(state)
        else:
            target_message = _resolve_target_message(target, config, today, daily, messages)
        target_id = target_identity(target)
        try:
            delivery = _send_target(
                chat,
                target,
                target_message,
                expected_conversation_id=conversation_ids.get(
                    target.stable_id or ""
                ),
            )
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
            daily["target_failures"].pop(target_id, None)
            sent += 1
            logger.info("发送已确认：%s（%s）", target_name, delivery.status.value)
        else:
            daily["failures"][target_name] = error
            detail = _detail_for_delivery(target, delivery, run_id=run_id)
            daily["target_failures"][target_id] = detail.model_dump(mode="json")
            if detail.uncertain_send or detail.send_attempts > 0:
                unsafe_failure_reason = (
                    unsafe_failure_reason or detail.reason_code
                )
            elif detail.retryable:
                safe_failure_reason = safe_failure_reason or error
            else:
                blocking_failure_reason = (
                    blocking_failure_reason or detail.reason_code
                )
            logger.warning(
                "目标失败：%s；阶段：%s；原因：%s；建议：%s；"
                "诊断：reason_code=%s send_attempts=%s target_id=%s",
                target_name,
                detail.stage,
                detail.user_summary_zh,
                detail.suggested_action_zh,
                detail.reason_code,
                detail.send_attempts,
                target_id,
            )
        store.save(state)
        if target_index < len(pending) - 1:
            time.sleep(random.uniform(config.min_delay_seconds, config.max_delay_seconds))

    complete = all(
        effective_before_run.get(target_identity(target)) == "success"
        or target.name in daily["succeeded"]
        for target in targets
    )
    if complete and not daily.get("consumed"):
        rotation.consume(daily["message"], state.rotation)
        daily["consumed"] = True
        store.save(state)
    failed_targets = [
        target
        for target in targets
        if effective_before_run.get(target_identity(target)) != "success"
        and target.name not in daily["succeeded"]
    ]
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
    elif blocking_failure_reason:
        status = RunStatus.FINAL_FAILED
        outcome_store.safe_failure(
            run_id,
            started,
            blocking_failure_reason,
            max_retries=0,
        )
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
        len(failed_targets),
        unsafe_failure_reason or safe_failure_reason or blocking_failure_reason,
        run_id,
        retries,
        confirmation_results,
        {
            target_id: detail
            for target_id, detail in _stored_target_failures(daily).items()
            if target_id in {target_identity(target) for target in failed_targets}
        },
    )
    _record_history(
        config,
        started,
        trigger_source,
        result,
        daily["message"],
        [target_identity(target) for target in failed_targets],
    )
    return result

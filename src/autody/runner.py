from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import copy
import hashlib
import inspect
import logging
import os
from pathlib import Path
import random
import time

from autody.chat import (
    DeliveryConfirmationProvenance,
    DeliveryResult,
    DeliveryStatus,
    FatalChatError,
    TodayOutgoingAudit,
    TodayOutgoingStatus,
    conversation_candidate_id,
)
from autody.config import (
    AppConfig,
    MessageSuffixConfig,
    Target,
    enabled_daily_targets,
    enabled_execution_targets,
    target_identity,
)
from autody.daily_status import (
    effective_daily_target_statuses,
    same_day_confirmed_target_ids,
)
from autody.account_profile import (
    bindings_revalidation_required,
    load_account_profile,
)
from autody.binding_recovery import StableBindingResolution, resolve_stable_binding
from autody.failures import FailureDetail, failure_detail
from autody.friend_discovery import load_discovered_friends
from autody.history import (
    TaskHistoryStore,
    TaskRunRecord,
)
from autody.message_packs import MessagePackError, MessagePackService
from autody.messages import MessageRotation, format_message_with_suffix, read_messages
from autody.retry_state import TaskOutcome, TaskOutcomeStore
from autody.state import StateStore


logger = logging.getLogger(__name__)
RUN_TRIGGER_SOURCES = frozenset({"scheduled", "manual", "retry"})


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
    confirmation_provenance: dict[str, str] = field(default_factory=dict)
    today_audit_outcomes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TodayTargetMessage:
    text: str


@dataclass(frozen=True)
class TodayDeliveryReconciliation:
    outcomes: dict[str, str]
    pre_supplement_success_count: int
    pre_supplement_complete: bool
    supplement_result: RunResult | None = None
    live_audit_required: int = 0
    evidence_sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TodayDeliveryReconciliationPlan:
    """The immutable evidence plan for one repair invocation."""

    outcomes: dict[str, str]
    evidence_sources: dict[str, str]
    live_audit_target_ids: tuple[str, ...]
    confirmed_missing_target_ids: tuple[str, ...] = ()


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
    total = len(enabled_daily_targets(config))
    return RunResult(status, total, 0, 0, total, reason, run_id=run_id, retry_count=retries)


def _delivery_result(value) -> DeliveryResult:
    if isinstance(value, DeliveryResult):
        return value
    return DeliveryResult(
        DeliveryStatus.CONFIRMED,
        send_attempts=1,
        confirmation_attempts=1,
        confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
    )


def _verified_conversation_ids(
    config: AppConfig,
    targets: list[Target],
) -> dict[str, str]:
    return {
        target_id: resolution.conversation_id
        for target_id, resolution in _binding_resolutions(config, targets).items()
        if resolution.valid and resolution.conversation_id
    }


def _binding_resolutions(
    config: AppConfig, targets: list[Target]
) -> dict[str, StableBindingResolution]:
    """Return the one canonical binding decision for every modern target."""
    data_root = config.state_file.parent
    discovered = load_discovered_friends(data_root / "discovered_friends.json")
    profile = load_account_profile(config.messages_file.parent)
    guarded = bindings_revalidation_required(data_root)
    resolved: dict[str, StableBindingResolution] = {}
    for target in targets:
        if not target.stable_id:
            continue
        resolved[target.stable_id] = resolve_stable_binding(
            target,
            discovered,
            profile,
            revalidation_required=guarded,
        )
    return resolved


def _binding_failure_detail(
    target: Target,
    resolution,
    *,
    run_id: str,
    account_scope: str | None,
) -> FailureDetail:
    reason = {
        "account_mismatch": "account_scope_mismatch",
        "account_unverified": "login_required",
        "binding_missing": "binding_missing",
        "identity_ambiguous": "blocked_ambiguous_target",
    }.get(resolution.status, "binding_stale")
    diagnostics = {"binding_resolution": resolution.status}
    if resolution.account_comparison:
        diagnostics["account_comparison"] = resolution.account_comparison
    return failure_detail(
        reason,
        stage="target_binding_resolved",
        run_id=run_id,
        target_stable_id=target_identity(target),
        account_scope=account_scope,
        binding_valid=False,
        account_scope_matches=(False if reason == "account_scope_mismatch" else None),
        diagnostic_details=diagnostics,
    )


def _send_target(
    chat,
    target: Target,
    message: str,
    *,
    expected_conversation_id: str | None = None,
    conversation_verified: bool = False,
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
        send_kwargs = {
            "selected_target_id": target.stable_id,
            "expected_conversation_id": expected_conversation_id,
        }
        if "conversation_verified" in parameters or any(
            item.kind is inspect.Parameter.VAR_KEYWORD
            for item in parameters.values()
        ):
            send_kwargs["conversation_verified"] = conversation_verified
        return _delivery_result(
            chat.send(
                target.name,
                message,
                **send_kwargs,
            )
        )
    return _delivery_result(chat.send(target.name, message))


@dataclass(frozen=True)
class TodayTargetExecution:
    """The sole audit-to-send boundary for a target in one chat session."""

    audit: TodayOutgoingAudit | None = None
    delivery: DeliveryResult | None = None


def _supports_today_target_pipeline(chat) -> bool:
    return callable(getattr(chat, "open_conversation_identity", None)) and callable(
        getattr(chat, "audit_today_outgoing", None)
    )


def _execute_today_target(
    chat,
    target: Target,
    message: str,
    today: date,
    *,
    expected_conversation_id: str | None,
    allow_send: bool = True,
) -> TodayTargetExecution:
    """Audit a verified conversation and, only when missing, send in place.

    Production chat adapters always use this pipeline.  The small legacy fake
    senders retained by older unit tests do not expose read-only audit methods;
    they stay on their existing in-memory path and never represent a browser
    execution path.
    """
    if not _supports_today_target_pipeline(chat) or not expected_conversation_id:
        return TodayTargetExecution(
            delivery=_send_target(
                chat,
                target,
                message,
                expected_conversation_id=expected_conversation_id,
            )
        )
    identity = chat.open_conversation_identity(
        target_identity(target),
        expected_conversation_id,
        target.name,
        timeout_ms=getattr(chat, "friend_search_timeout_ms", None),
    )
    if not identity.identity_match:
        return TodayTargetExecution(
            delivery=DeliveryResult(
                DeliveryStatus.BLOCKED,
                send_attempts=0,
                error=getattr(identity, "identity_match_reason", None),
                failure_stage="identity_verified",
                reason_code="identity_verification_failed",
            )
        )
    audit = chat.audit_today_outgoing(today)
    if audit.status is not TodayOutgoingStatus.CONFIRMED_MISSING or not allow_send:
        return TodayTargetExecution(audit=audit)
    return TodayTargetExecution(
        audit=audit,
        delivery=_send_target(
            chat,
            target,
            message,
            expected_conversation_id=expected_conversation_id,
            conversation_verified=True,
        ),
    )


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


def automatic_daily_run_gate(
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> RunResult | None:
    """Return a no-browser result when an automatic daily run is not due."""
    started = now or datetime.now()
    day = started.date()
    state = StateStore(config.state_file).load()
    daily = state.daily.get(day.isoformat(), {})
    targets = enabled_daily_targets(config)
    statuses = effective_daily_target_statuses(config, state, day)
    pending = [
        target
        for target in targets
        if statuses.get(target_identity(target)) != "success"
    ]
    run_id = str(daily.get("task_run_id") or _daily_run_id(day))
    details = _stored_target_failures(daily)
    if not pending:
        return RunResult(
            RunStatus.ALREADY_DONE,
            len(targets),
            0,
            len(targets),
            0,
            run_id=run_id,
        )

    outcomes = TaskOutcomeStore(_outcome_path(config))
    outcome = outcomes.get(run_id)
    deadline = datetime.combine(
        day,
        datetime.strptime(config.recovery_deadline, "%H:%M").time(),
    )
    if (
        outcome is not None
        and outcome.outcome is TaskOutcome.FINAL_FAILED
        and started < deadline
    ):
        resolved = _verified_conversation_ids(config, pending)
        safely_resolvable = all(
            target.stable_id
            and target.stable_id in resolved
            and (detail := details.get(target_identity(target))) is not None
            and detail.safe_retry_available
            for target in pending
        )
        if safely_resolvable:
            outcome = outcomes.resume_safe_recovery(
                run_id,
                started,
                "scheduler_safe_recovery",
            )
    terminal_statuses = {
        TaskOutcome.FINAL_FAILED: RunStatus.FINAL_FAILED,
        TaskOutcome.UNCERTAIN: RunStatus.UNCERTAIN,
        TaskOutcome.CANCELLED: RunStatus.CANCELLED,
    }
    if outcome is not None and outcome.outcome in terminal_statuses:
        return RunResult(
            terminal_statuses[outcome.outcome],
            len(targets),
            0,
            len(targets) - len(pending),
            len(pending),
            outcome.reason,
            run_id=run_id,
            retry_count=outcome.retry_attempts,
            target_failures=details,
        )

    if started >= deadline:
        if outcome is None:
            outcomes.schedule(run_id, day.isoformat(), deadline)
        persisted = outcomes.safe_failure(
            run_id,
            started,
            "recovery_deadline_reached",
            max_retries=0,
        )
        return RunResult(
            RunStatus.FINAL_FAILED,
            len(targets),
            0,
            len(targets) - len(pending),
            len(pending),
            "recovery_deadline_reached",
            run_id=run_id,
            retry_count=persisted.retry_attempts,
            target_failures=details,
        )
    if (
        outcome is not None
        and outcome.outcome is TaskOutcome.RETRY_PENDING
        and outcome.next_attempt_at is not None
        and started < outcome.next_attempt_at
    ):
        return RunResult(
            RunStatus.RETRY_PENDING,
            len(targets),
            0,
            len(targets) - len(pending),
            len(pending),
            outcome.reason,
            run_id=run_id,
            retry_count=outcome.retry_attempts,
            target_failures=details,
        )
    return None


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
    rotation_state,
    *,
    persist_catalog: bool = True,
) -> str:
    pack_id = (
        target.message_pack
        if target.message_pack is not None
        else config.default_message_pack
    )
    if not pack_id:
        return daily["message"]
    selection = target.message_selection or config.message_selection
    target_id = target_identity(target)
    key = (
        f"pack:one-for-all:{pack_id}"
        if selection == "one_for_all"
        else f"pack:{target_id}"
    )
    cached = daily.setdefault("messages_by_target", {}).get(key)
    if cached:
        return cached
    pack_root = Path(
        os.environ.get("AUTODY_PROGRAM_ROOT", config.messages_file.parent)
    ).resolve()
    pack_messages = MessagePackService(
        pack_root,
        config.messages_file.parent,
    ).preview(pack_id, persist_catalog=persist_catalog).messages
    selected = (
        _selection_rng(day, "message-pack", target).choice(pack_messages)
        if selection == "per_friend"
        else MessageRotation(
            _selection_rng(day, f"message-pack:{pack_id}")
        ).peek(pack_messages, rotation_state)
    )
    daily["messages_by_target"][key] = selected
    return selected


def _resolve_target_message(
    target: Target,
    config: AppConfig,
    day: date,
    daily: dict,
    messages: list[str],
    rotation_state,
    *,
    persist_catalog: bool = True,
) -> str:
    if target.message_pack is not None or config.default_message_pack:
        base = _target_base_message(
            target,
            config,
            daily,
            messages,
            day,
            rotation_state,
            persist_catalog=persist_catalog,
        )
    elif (target.message_selection or config.message_selection) == "per_friend":
        target_id = target_identity(target)
        key = f"target:{target_id}"
        per_target = daily.setdefault("messages_by_target", {})
        base = per_target.get(key) or per_target.get(target.name)
        if not base:
            base = _selection_rng(day, "per-friend", target).choice(messages)
            per_target[key] = base
    else:
        if not daily["message"]:
            daily["message"] = MessageRotation(
                _selection_rng(day, "daily")
            ).peek(messages, rotation_state)
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
    return TodayTargetMessage(
        _resolve_target_message(
            target,
            config,
            day,
            daily,
            messages,
            state.rotation,
            persist_catalog=False,
        )
    )


_TODAY_RECONCILIATION_OUTCOMES = frozenset(
    item.value for item in TodayOutgoingStatus
)
_TODAY_RECONCILIATION_EVIDENCE_SOURCES = frozenset(
    {
        "live_chat_audit",
        "live_chat_audit_unavailable",
        "binding_unavailable",
        "same_day_delivery_confirmation",
        "human_verified_today",
    }
)


def apply_today_delivery_reconciliation(
    config: AppConfig,
    outcomes: dict[str, str],
    today: date,
    *,
    evidence_sources: dict[str, str] | None = None,
) -> dict[str, str]:
    """Persist current-day delivery evidence and return effective statuses.

    Reconciliation facts are intentionally small (target identity plus one of
    three outcomes and an explicit evidence source) and take precedence over
    stale local send records.  They never store message or conversation
    content.
    """
    targets = enabled_daily_targets(config)
    known = {target_identity(target): target for target in targets}
    store = StateStore(config.state_file)
    state = store.load()
    daily = state.daily.setdefault(
        today.isoformat(),
        {
            "message": "",
            "succeeded": [],
            "failures": {},
            "confirmation_results": {},
            "consumed": False,
        },
    )
    daily.setdefault("confirmation_results", {})
    daily.setdefault("confirmation_provenance", {})
    daily.setdefault("target_failures", {})
    daily.setdefault("delivery_reconciliation", {})
    daily.setdefault("delivery_reconciliation_evidence", {})
    facts = daily.setdefault("delivery_reconciliation", {})
    recorded_evidence = daily.setdefault("delivery_reconciliation_evidence", {})
    evidence_sources = evidence_sources or {}
    name_counts: dict[str, int] = {}
    for target in targets:
        name_counts[target.name] = name_counts.get(target.name, 0) + 1
    for target_id, outcome in outcomes.items():
        target = known.get(target_id)
        if target is None or outcome not in _TODAY_RECONCILIATION_OUTCOMES:
            continue
        evidence_source = evidence_sources.get(target_id, "live_chat_audit")
        if evidence_source not in _TODAY_RECONCILIATION_EVIDENCE_SOURCES:
            continue
        facts[target_id] = outcome
        recorded_evidence[target_id] = evidence_source
        if outcome == TodayOutgoingStatus.CONFIRMED_SENT.value:
            daily["confirmation_results"][target_id] = DeliveryStatus.CONFIRMED.value
            daily["confirmation_provenance"][target_id] = evidence_source
            daily["target_failures"].pop(target_id, None)
            if name_counts[target.name] == 1:
                if target.name not in daily["succeeded"]:
                    daily["succeeded"].append(target.name)
                daily["failures"].pop(target.name, None)
        elif outcome == TodayOutgoingStatus.CONFIRMED_MISSING.value:
            daily["confirmation_results"].pop(target_id, None)
            daily["confirmation_provenance"].pop(target_id, None)
            daily["target_failures"].pop(target_id, None)
            if name_counts[target.name] == 1:
                daily["succeeded"] = [
                    item for item in daily["succeeded"] if item != target.name
                ]
                daily["failures"].pop(target.name, None)

    statuses = effective_daily_target_statuses(config, state, today)
    complete = bool(targets) and all(
        statuses.get(target_identity(target)) == "success" for target in targets
    )
    if not complete:
        daily["consumed"] = False
    elif not daily.get("consumed"):
        daily["consumed"] = True
        if daily.get("message"):
            MessageRotation(_selection_rng(today, "daily")).consume(
                daily["message"], state.rotation
            )
    store.save(state)
    return statuses


def apply_today_human_verified_delivery_reconciliation(
    config: AppConfig,
    *,
    confirmed_sent_ids: set[str],
    confirmed_missing_ids: set[str],
    today: date,
) -> dict[str, str]:
    """Persist a one-time migration verification for legacy current-day data.

    This is deliberately not a normal product workflow or a fabricated AutoDy
    post-send observation. Normal sender safety and post-send provenance remain
    required for every future send.
    """
    overlap = confirmed_sent_ids & confirmed_missing_ids
    if overlap:
        raise ValueError("a target cannot be both confirmed sent and missing")
    outcomes = {
        **{
            target_id: TodayOutgoingStatus.CONFIRMED_SENT.value
            for target_id in confirmed_sent_ids
        },
        **{
            target_id: TodayOutgoingStatus.CONFIRMED_MISSING.value
            for target_id in confirmed_missing_ids
        },
    }
    return apply_today_delivery_reconciliation(
        config,
        outcomes,
        today,
        evidence_sources={
            target_id: "human_verified_today" for target_id in outcomes
        },
    )


def plan_today_delivery_reconciliation(
    config: AppConfig,
    today: date,
) -> TodayDeliveryReconciliationPlan:
    """Use strong AutoDy confirmation before considering a live chat audit.

    A prior ``confirmed_missing`` fact conflicts with an older confirmation,
    so it deliberately remains eligible for a fresh live audit.  Weak legacy
    success/consumed state never appears in the strong set and is audited.
    """
    targets = enabled_daily_targets(config)
    state = StateStore(config.state_file).load()
    confirmed_ids = same_day_confirmed_target_ids(config, state, today)
    daily = state.daily.get(today.isoformat(), {})
    reconciliation = daily.get("delivery_reconciliation", {})
    reconciliation_evidence = daily.get("delivery_reconciliation_evidence", {})
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    reconciliation_evidence = (
        reconciliation_evidence
        if isinstance(reconciliation_evidence, dict)
        else {}
    )
    conflicting_ids = {
        target_id
        for target_id in confirmed_ids
        if reconciliation.get(target_id) == TodayOutgoingStatus.CONFIRMED_MISSING.value
    }
    post_send_confirmation_ids = confirmed_ids - conflicting_ids
    live_audit_sent_ids = {
        target_id
        for target_id, outcome in reconciliation.items()
        if outcome == TodayOutgoingStatus.CONFIRMED_SENT.value
        and reconciliation_evidence.get(target_id) == "live_chat_audit"
    }
    live_audit_missing_ids = {
        target_id
        for target_id, outcome in reconciliation.items()
        if outcome == TodayOutgoingStatus.CONFIRMED_MISSING.value
        and reconciliation_evidence.get(target_id) == "live_chat_audit"
    }
    direct_confirmation_ids = post_send_confirmation_ids | live_audit_sent_ids
    outcomes = {
        target_id: TodayOutgoingStatus.CONFIRMED_SENT.value
        for target_id in direct_confirmation_ids
    }
    evidence_sources = {
        target_id: (
            "same_day_delivery_confirmation"
            if target_id in post_send_confirmation_ids
            else "live_chat_audit"
        )
        for target_id in direct_confirmation_ids
    }
    outcomes.update(
        {
            target_id: TodayOutgoingStatus.CONFIRMED_MISSING.value
            for target_id in live_audit_missing_ids
        }
    )
    evidence_sources.update(
        {target_id: "live_chat_audit" for target_id in live_audit_missing_ids}
    )
    settled_ids = direct_confirmation_ids | live_audit_missing_ids
    live_audit_target_ids = tuple(
        target_identity(target)
        for target in targets
        if target_identity(target) not in settled_ids
    )
    return TodayDeliveryReconciliationPlan(
        outcomes,
        evidence_sources,
        live_audit_target_ids,
        tuple(sorted(live_audit_missing_ids)),
    )


def reconcile_today_delivery(
    config: AppConfig,
    chat,
    today: date,
    *,
    plan: TodayDeliveryReconciliationPlan | None = None,
    supplement: bool = True,
) -> TodayDeliveryReconciliation:
    """Reuse the canonical daily target pipeline for repair and recovery."""
    targets = enabled_daily_targets(config)
    plan = plan or plan_today_delivery_reconciliation(config, today)
    outcomes = dict(plan.outcomes)
    evidence_sources = dict(plan.evidence_sources)
    statuses = apply_today_delivery_reconciliation(
        config,
        outcomes,
        today,
        evidence_sources=evidence_sources,
    )
    pre_supplement_success_count = sum(
        status == "success" for status in statuses.values()
    )
    pre_supplement_complete = bool(targets) and all(
        statuses.get(target_identity(target)) == "success" for target in targets
    )
    supplement_result = None
    target_ids = set(plan.live_audit_target_ids) | set(
        plan.confirmed_missing_target_ids
    )
    if chat is not None and target_ids:
        execution_result = run_daily(
            config,
            chat,
            today,
            trigger_source="manual",
            target_ids=target_ids,
            audit_only=not supplement,
        )
        if supplement and execution_result.sent_count:
            supplement_result = execution_result
        outcomes.update(execution_result.today_audit_outcomes)
        evidence_sources.update(
            {
                target_id: "live_chat_audit"
                for target_id in execution_result.today_audit_outcomes
            }
        )
        daily = StateStore(config.state_file).load().daily.get(today.isoformat(), {})
        reconciliation = daily.get("delivery_reconciliation", {})
        evidence = daily.get("delivery_reconciliation_evidence", {})
        if isinstance(reconciliation, dict):
            outcomes.update(
                {
                    target_id: outcome
                    for target_id, outcome in reconciliation.items()
                    if target_id in target_ids
                }
            )
        if isinstance(evidence, dict):
            evidence_sources.update(
                {
                    target_id: source
                    for target_id, source in evidence.items()
                    if target_id in target_ids
                }
            )
    return TodayDeliveryReconciliation(
        outcomes,
        pre_supplement_success_count,
        pre_supplement_complete,
        supplement_result,
        len(plan.live_audit_target_ids),
        evidence_sources,
    )


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
            confirmation_provenance=result.confirmation_provenance,
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
    audit_only: bool = False,
) -> RunResult:
    if trigger_source not in RUN_TRIGGER_SOURCES:
        raise ValueError(f"unsupported daily-send trigger source: {trigger_source}")
    started = now or datetime.now()
    today = today or started.date()
    # Tests and explicit callers may supply a historical ``today``
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
            "confirmation_provenance": {},
            "consumed": False,
        },
    )
    daily.setdefault("confirmation_results", {})
    daily.setdefault("confirmation_provenance", {})
    daily.setdefault("target_failures", {})
    daily.setdefault("delivery_reconciliation", {})
    daily.setdefault("delivery_reconciliation_evidence", {})
    run_id = daily.setdefault(
        "task_run_id",
        _daily_run_id(today),
    )
    required_targets = enabled_daily_targets(config)
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
    binding_resolutions = _binding_resolutions(config, required_targets)
    conversation_ids = {
        target_id: resolution.conversation_id
        for target_id, resolution in binding_resolutions.items()
        if resolution.valid and resolution.conversation_id
    }
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
        stored_failures = _stored_target_failures(daily)
        for target in selected_targets:
            target_id = target_identity(target)
            if effective_before_run.get(target_id) == "success":
                continue
            if _supports_today_target_pipeline(chat):
                resolution = binding_resolutions.get(target.stable_id or "")
                if resolution is not None and not resolution.valid:
                    detail = _binding_failure_detail(
                        target,
                        resolution,
                        run_id=run_id,
                        account_scope=discovered.account_scope if discovered else None,
                    )
                    targeted_blocking_details[target_id] = detail
                    daily["failures"][target.name] = detail.reason_code
                    daily["target_failures"][target_id] = detail.model_dump(mode="json")
                # A live audit is now the only authority that can decide
                # whether a previous target-level failure remains resendable.
                continue
            if (
                daily.get("delivery_reconciliation", {}).get(target_id)
                == TodayOutgoingStatus.CONFIRMED_MISSING.value
            ):
                # Browser history has already proven that this exact today's
                # message is absent.  It is a safe supplemental send even if
                # an old local record had claimed success rather than failure.
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
            resolution = binding_resolutions.get(target.stable_id or "")
            account_diagnostics = {}
            if resolution and resolution.account_comparison:
                account_diagnostics["account_comparison"] = (
                    resolution.account_comparison
                )
            if resolution is not None and not resolution.valid:
                detail = _binding_failure_detail(
                    target,
                    resolution,
                    run_id=run_id,
                    account_scope=discovered.account_scope if discovered else None,
                )
                targeted_blocking_details[target_id] = detail
                daily["failures"][target.name] = detail.reason_code
                daily["target_failures"][target_id] = detail.model_dump(mode="json")
                continue
            if previous is not None:
                refreshed = previous.model_copy(
                    update={
                        "binding_valid": True,
                        "account_scope_matches": True,
                        "account_scope": discovered.account_scope if discovered else None,
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
                        account_scope=discovered.account_scope if discovered else None,
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
                    account_scope=discovered.account_scope if discovered else None,
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
                len(required_targets),
                0,
                len(required_targets) - len(targeted_blocking_details),
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
    binding_blocked: dict[str, FailureDetail] = {}
    if any(target.candidate_id for target in config.targets):
        discovered = load_discovered_friends(
            config.state_file.parent / "discovered_friends.json"
        )
        for target in required_targets:
            target_id = target_identity(target)
            resolution = binding_resolutions.get(target.stable_id or "")
            if effective_before_run.get(target_id) == "success":
                continue
            if resolution is not None and resolution.valid:
                continue
            detail = (
                _binding_failure_detail(
                    target,
                    resolution,
                    run_id=run_id,
                    account_scope=discovered.account_scope if discovered else None,
                )
                if resolution is not None
                else failure_detail(
                    "binding_missing",
                    stage="target_binding_resolved",
                    run_id=run_id,
                    target_stable_id=target_id,
                    binding_valid=False,
                    account_scope_matches=True,
                )
            )
            binding_blocked[target_id] = detail
            daily["failures"][target.name] = detail.reason_code
            daily["target_failures"][target_id] = detail.model_dump(mode="json")
    pending = [
        target for target in selected_targets
        if effective_before_run.get(target_identity(target)) != "success"
        and " ".join(target.name.split()).casefold() not in ambiguous_names
        and target_identity(target) not in binding_blocked
    ]
    total = len(required_targets)
    skipped = total - len(pending)
    if not pending:
        status = (
            RunStatus.UNCERTAIN
            if ambiguous_targets
            or any(
                effective_before_run.get(target_identity(target)) == "unknown"
                for target in selected_targets
            )
            else RunStatus.FINAL_FAILED
            if binding_blocked
            else RunStatus.ALREADY_DONE
        )
        details = _stored_target_failures(daily)
        result = RunResult(
            status,
            total,
            0,
            skipped,
            len(ambiguous_targets) + len(binding_blocked),
            "blocked_ambiguous_target"
            if ambiguous_targets
            else next(iter(binding_blocked.values())).reason_code
            if binding_blocked
            else None,
            run_id=run_id,
            target_failures=details,
        )
        store.save(state)
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
    can_reaudit = _supports_today_target_pipeline(chat) and bool(pending)
    reopened_terminal = can_reaudit and outcome.outcome in terminal_statuses
    if (
        outcome.outcome in terminal_statuses
        and requested_target_ids is None
        and not can_reaudit
    ):
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
    if not audit_only:
        outcome_store.start(run_id, started)

    messages = read_messages(config.messages_file)
    daily.setdefault("messages_by_target", {})

    sent = 0
    retries = outcome.retry_attempts
    confirmation_results: dict[str, str] = {}
    confirmation_provenance: dict[str, str] = {}
    today_audit_outcomes: dict[str, str] = {}
    audit_missing_ids: set[str] = set()
    safe_failure_reason: str | None = None
    unsafe_failure_reason: str | None = "blocked_ambiguous_target" if ambiguous_targets else None
    blocking_failure_reason: str | None = next(
        (detail.reason_code for detail in binding_blocked.values()),
        None,
    )
    if requested_target_ids is not None:
        for target_id, detail in _stored_target_failures(daily).items():
            if target_id in requested_target_ids:
                continue
            peer = next(
                (target for target in targets if target_identity(target) == target_id),
                None,
            )
            if (
                peer is None
                or effective_before_run.get(target_identity(peer)) == "success"
            ):
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
        try:
            target_message = _resolve_target_message(
                target,
                config,
                today,
                daily,
                messages,
                state.rotation,
            )
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
            daily["target_failures"][target_id] = detail.model_dump(mode="json")
            blocking_failure_reason = blocking_failure_reason or detail.reason_code
            logger.warning("目标文案包不可用：%s：%s", target_name, exc)
            store.save(state)
            continue
        store.save(state)
        target_id = target_identity(target)
        try:
            execution = _execute_today_target(
                chat,
                target,
                target_message,
                today,
                expected_conversation_id=conversation_ids.get(
                    target.stable_id or ""
                ),
                allow_send=not audit_only,
            )
        except FatalChatError as exc:
            execution = (
                TodayTargetExecution(
                    audit=TodayOutgoingAudit(
                        TodayOutgoingStatus.UNKNOWN,
                        reason="live_chat_audit_unavailable",
                    )
                )
                if _supports_today_target_pipeline(chat)
                else TodayTargetExecution(
                    delivery=DeliveryResult(DeliveryStatus.BLOCKED, error=str(exc))
                )
            )
        except RuntimeError as exc:
            execution = (
                TodayTargetExecution(
                    audit=TodayOutgoingAudit(
                        TodayOutgoingStatus.UNKNOWN,
                        reason="live_chat_audit_unavailable",
                    )
                )
                if _supports_today_target_pipeline(chat)
                else TodayTargetExecution(
                    delivery=DeliveryResult(DeliveryStatus.SEND_FAILED, error=str(exc))
                )
            )
        audit = execution.audit
        if audit is not None:
            today_audit_outcomes[target_id] = audit.status.value
            if audit.status is TodayOutgoingStatus.CONFIRMED_SENT:
                daily["delivery_reconciliation"][target_id] = audit.status.value
                daily["delivery_reconciliation_evidence"][target_id] = "live_chat_audit"
                confirmation_results[target_id] = DeliveryStatus.CONFIRMED.value
                confirmation_provenance[target_id] = "live_chat_audit"
                daily["confirmation_results"][target_id] = DeliveryStatus.CONFIRMED.value
                daily["confirmation_provenance"][target_id] = "live_chat_audit"
                if target_name not in daily["succeeded"]:
                    daily["succeeded"].append(target_name)
                daily["failures"].pop(target_name, None)
                daily["target_failures"].pop(target_id, None)
                store.save(state)
                continue
            daily["delivery_reconciliation"][target_id] = audit.status.value
            daily["delivery_reconciliation_evidence"][target_id] = "live_chat_audit"
            if audit.status is TodayOutgoingStatus.UNKNOWN:
                unsafe_failure_reason = unsafe_failure_reason or "today_delivery_unknown"
                store.save(state)
                continue
            daily["confirmation_results"].pop(target_id, None)
            daily["confirmation_provenance"].pop(target_id, None)
            daily["succeeded"] = [
                name for name in daily["succeeded"] if name != target_name
            ]
            daily["failures"].pop(target_name, None)
            daily["target_failures"].pop(target_id, None)
            if audit_only:
                audit_missing_ids.add(target_id)
                store.save(state)
                continue
            # MISSING is only this invocation's send predicate. Do not leave it
            # as a long-lived truth after proceeding to the composer boundary.
            daily["delivery_reconciliation"].pop(target_id, None)
            daily["delivery_reconciliation_evidence"].pop(target_id, None)
        delivery = execution.delivery
        if delivery is None:
            unsafe_failure_reason = unsafe_failure_reason or "today_delivery_unknown"
            store.save(state)
            continue
        confirmation_results[target_id] = delivery.status.value
        daily["confirmation_results"][target_id] = delivery.status.value
        provenance = delivery.confirmation_provenance.value
        confirmation_provenance[target_id] = provenance
        daily["confirmation_provenance"][target_id] = provenance
        retries += max(0, delivery.confirmation_attempts - 1)
        error = delivery.error or delivery.status.value
        if delivery.successful:
            daily["succeeded"].append(target_name)
            daily["failures"].pop(target_name, None)
            daily["target_failures"].pop(target_id, None)
            daily.get("delivery_reconciliation", {}).pop(target_id, None)
            daily.get("delivery_reconciliation_evidence", {}).pop(target_id, None)
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

    effective_after_run = effective_daily_target_statuses(
        config,
        state,
        today,
    )
    complete = all(
        effective_after_run.get(target_identity(target)) == "success"
        for target in required_targets
    )
    if complete and not daily.get("consumed"):
        rotated_messages = [
            message
            for message in [daily.get("message")]
            if message
        ]
        rotated_messages.extend(
            message
            for key, message in daily.get("messages_by_target", {}).items()
            if key.startswith("pack:one-for-all:") and message
        )
        for message in dict.fromkeys(rotated_messages):
            rotation.consume(message, state.rotation)
        daily["consumed"] = True
        store.save(state)
    failed_targets = [
        target
        for target in required_targets
        if effective_after_run.get(target_identity(target)) != "success"
    ]
    if audit_only and audit_missing_ids:
        resumed = outcome_store.resume_confirmed_missing(run_id, started)
        status = (
            RunStatus.RETRY_PENDING
            if resumed.outcome is TaskOutcome.RETRY_PENDING
            else RunStatus.FINAL_FAILED
        )
    elif complete:
        status = (
            RunStatus.RECOVERED
            if outcome.retry_attempts or reopened_terminal
            else RunStatus.COMPLETED
        )
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
        confirmation_results=confirmation_results,
        target_failures={
            target_id: detail
            for target_id, detail in _stored_target_failures(daily).items()
            if target_id in {target_identity(target) for target in failed_targets}
        },
        confirmation_provenance=confirmation_provenance,
        today_audit_outcomes=today_audit_outcomes,
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

"""Authoritative effective daily target status decisions.

History records remain immutable facts.  This module projects those facts,
legacy daily state, and the current configured target set into today's
effective status without rewriting any source record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from autody.config import (
    AppConfig,
    enabled_daily_targets,
    stable_target_id,
    target_identity,
)
from autody.history import TaskHistoryStore, TaskRunRecord
from autody.state import AppState


SUCCESS_FINAL_STATUSES = {"completed", "already_done", "recovered"}
SUCCESS_CONFIRMATIONS = {"confirmed", "retry_confirmed"}
POST_SEND_CONFIRMATION_PROVENANCE = {"post_send_observed"}


def is_post_send_confirmation(status: object, provenance: object) -> bool:
    return (
        status in SUCCESS_CONFIRMATIONS
        and provenance in POST_SEND_CONFIRMATION_PROVENANCE
    )


def _daily_send_records_by_day(
    records: Sequence[TaskRunRecord],
) -> dict[str, list[TaskRunRecord]]:
    grouped: dict[str, list[TaskRunRecord]] = {}
    for record in records:
        if record.task_type == "daily_send":
            grouped.setdefault(record.date, []).append(record)
    return grouped


def _current_target_aliases(config: AppConfig) -> dict[str, str]:
    """Map unambiguous historical target identities to current identities."""
    aliases: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for target in enabled_daily_targets(config):
        identity = target_identity(target)
        for alias in {
            target.stable_id,
            target.candidate_id,
            stable_target_id(target.name),
        } - {None}:
            previous = aliases.get(alias)
            if previous is not None and previous != identity:
                ambiguous_aliases.add(alias)
                continue
            aliases[alias] = identity
    for alias in ambiguous_aliases:
        aliases.pop(alias, None)
    return aliases


def same_day_confirmed_target_ids(
    config: AppConfig,
    state: AppState,
    day: date,
    records: Sequence[TaskRunRecord] | None = None,
) -> set[str]:
    """Return targets proven sent by AutoDy's normal same-day confirmation.

    A strong confirmation must retain the post-send observation provenance.
    Bare legacy status strings, ``succeeded`` names and consumed flags never
    prove that this invocation actually crossed the send boundary.
    """
    aliases = _current_target_aliases(config)
    confirmed: set[str] = set()
    daily = state.daily.get(day.isoformat(), {})
    provenance = daily.get("confirmation_provenance", {})
    for historical_id, result in daily.get("confirmation_results", {}).items():
        identity = aliases.get(historical_id)
        if identity is not None and is_post_send_confirmation(
            result,
            provenance.get(historical_id) if isinstance(provenance, Mapping) else None,
        ):
            confirmed.add(identity)

    if records is None:
        records = TaskHistoryStore(
            config.state_file.parent / "history" / "task-runs.jsonl"
        ).query(start_date=day, end_date=day, page_size=100).items
    for record in records:
        if record.task_type != "daily_send" or record.date != day.isoformat():
            continue
        for historical_id, result in record.confirmation_results.items():
            identity = aliases.get(historical_id)
            if identity is not None and is_post_send_confirmation(
                result,
                record.confirmation_provenance.get(historical_id),
            ):
                confirmed.add(identity)
    return confirmed


def _day_is_success(
    daily_fact: Mapping[str, object] | None,
    records: Sequence[TaskRunRecord],
) -> bool:
    if daily_fact is not None and daily_fact.get("consumed") is True:
        return True
    if any(record.final_status in SUCCESS_FINAL_STATUSES for record in records):
        return True
    required_targets = max(
        (record.total_targets for record in records),
        default=0,
    )
    if required_targets <= 0:
        return False
    confirmed_target_ids = {
        target_id
        for record in records
        for target_id, result in record.confirmation_results.items()
        if is_post_send_confirmation(
            result,
            record.confirmation_provenance.get(target_id),
        )
    }
    return len(confirmed_target_ids) >= required_targets


def dashboard_statistics(
    daily_facts: Mapping[str, Mapping[str, object]],
    records: Sequence[TaskRunRecord],
    today: date | None = None,
) -> dict[str, float | int | str | None]:
    """Project historical day facts into Dashboard statistics."""
    today = today or date.today()
    grouped_records = _daily_send_records_by_day(records)
    candidate_days = set(daily_facts) | set(grouped_records)

    def in_window(day_key: str, days: int) -> bool:
        try:
            candidate = date.fromisoformat(day_key)
        except ValueError:
            return False
        return today - timedelta(days=days - 1) <= candidate <= today

    def is_success(day_key: str) -> bool:
        fact = daily_facts.get(day_key)
        return _day_is_success(fact, grouped_records.get(day_key, ()))

    def success_rate(days: int) -> float:
        selected = [day for day in candidate_days if in_window(day, days)]
        if not selected:
            return 0.0
        successful = sum(1 for day in selected if is_success(day))
        return round(successful * 100 / len(selected), 1)

    streak = 0
    cursor = today if today.isoformat() in candidate_days else today - timedelta(days=1)
    while cursor.isoformat() in candidate_days and is_success(cursor.isoformat()):
        streak += 1
        cursor -= timedelta(days=1)

    last_completed = next(
        (
            record.end_time
            for record in sorted(records, key=lambda item: item.end_time, reverse=True)
            if record.task_type == "daily_send"
            and record.final_status in SUCCESS_FINAL_STATUSES
        ),
        None,
    )
    return {
        "last_completed_run": last_completed,
        "consecutive_successful_days": streak,
        "success_rate_7d": success_rate(7),
        "success_rate_30d": success_rate(30),
    }


def effective_daily_target_statuses(
    config: AppConfig,
    state: AppState,
    day: date,
    records: list[TaskRunRecord] | None = None,
) -> dict[str, str]:
    """Return daily status while keeping confirmed success terminal.

    Historical execution identity, current persistent binding identity, and
    live DOM identity are separate concepts. Stored events are associated only
    through explicit current aliases.  Current-day chat reconciliation is the
    final authority when it proves a local record stale; inconclusive evidence
    remains explicitly unknown rather than being treated as a send failure.
    """
    targets = enabled_daily_targets(config)
    statuses = {target_identity(target): "pending" for target in targets}
    aliases = _current_target_aliases(config)

    daily = state.daily.get(day.isoformat(), {})
    failed_names = set(daily.get("failures", {}))
    if records is None:
        records = TaskHistoryStore(
            config.state_file.parent / "history" / "task-runs.jsonl"
        ).query(start_date=day, end_date=day, page_size=100).items
    strong_confirmations = same_day_confirmed_target_ids(
        config, state, day, records
    )
    for target in targets:
        identity = target_identity(target)
        if target.name in failed_names:
            statuses[identity] = "failed"
    current_provenance = daily.get("confirmation_provenance", {})
    for historical_id, result in daily.get("confirmation_results", {}).items():
        identity = aliases.get(historical_id)
        if identity is None:
            continue
        if is_post_send_confirmation(
            result,
            current_provenance.get(historical_id)
            if isinstance(current_provenance, Mapping)
            else None,
        ):
            statuses[identity] = "success"
        elif result not in SUCCESS_CONFIRMATIONS and statuses[identity] != "success":
            statuses[identity] = "failed"

    for record in sorted(records, key=lambda item: item.end_time):
        if record.task_type != "daily_send" or record.date != day.isoformat():
            continue
        failed_target_ids = set(record.failed_target_ids) | set(
            record.target_failures
        )
        for historical_id in failed_target_ids:
            identity = aliases.get(historical_id)
            if identity is not None and statuses[identity] != "success":
                statuses[identity] = "failed"
        for historical_id, result in record.confirmation_results.items():
            identity = aliases.get(historical_id)
            if identity is not None and is_post_send_confirmation(
                result,
                record.confirmation_provenance.get(historical_id),
            ):
                statuses[identity] = "success"
    current_failures = daily.get("target_failures", {})
    if isinstance(current_failures, Mapping):
        for target in targets:
            identity = target_identity(target)
            detail = current_failures.get(identity)
            if (
                identity not in strong_confirmations
                and isinstance(detail, Mapping)
                and bool(detail.get("uncertain_send"))
                and int(detail.get("send_attempts", 0) or 0) > 0
            ):
                statuses[identity] = "unknown"
    reconciliation = daily.get("delivery_reconciliation", {})
    if isinstance(reconciliation, dict):
        for target in targets:
            outcome = reconciliation.get(target_identity(target))
            if outcome == "confirmed_sent":
                statuses[target_identity(target)] = "success"
            elif outcome == "confirmed_missing":
                statuses[target_identity(target)] = "pending"
            elif (
                outcome == "unknown"
                and target_identity(target) not in strong_confirmations
            ):
                statuses[target_identity(target)] = "unknown"
    return statuses

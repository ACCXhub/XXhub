from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
import uuid

from pydantic import BaseModel, Field

from autody.config import AppConfig, enabled_execution_targets
from autody.failures import FailureDetail
from autody.state import AppState


TaskType = Literal["daily_send", "health_check", "login", "friend_scan", "system"]
TriggerSource = Literal["scheduled", "manual", "startup_recovery", "retry"]


class TaskRunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    date: str
    task_type: TaskType
    trigger_source: TriggerSource
    start_time: str
    end_time: str
    duration: float = 0.0
    total_targets: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    retry_count: int = 0
    final_status: str
    base_message_id: str | None = None
    message_pack: str | None = None
    error_summary: str | None = None
    failed_target_ids: list[str] = Field(default_factory=list)
    confirmation_results: dict[str, str] = Field(default_factory=dict)
    target_failures: dict[str, FailureDetail] = Field(default_factory=dict)


class HistoryPage(BaseModel):
    items: list[TaskRunRecord]
    total: int
    page: int
    page_size: int


class TaskHistoryStore:
    def __init__(self, path: Path):
        self.path = path

    def append(self, record: TaskRunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n"
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)

    def _records(self) -> tuple[list[TaskRunRecord], int]:
        if not self.path.exists():
            return [], 0
        records: list[TaskRunRecord] = []
        invalid = 0
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                records.append(TaskRunRecord.model_validate_json(line))
            except Exception:
                invalid += 1
        return records, invalid

    def query(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        status: str | None = None,
        task_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> HistoryPage:
        records, _ = self._records()
        filtered = [
            item
            for item in records
            if (start_date is None or item.date >= start_date.isoformat())
            and (end_date is None or item.date <= end_date.isoformat())
            and (status is None or item.final_status == status)
            and (task_type is None or item.task_type == task_type)
        ]
        filtered.reverse()
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        start = (page - 1) * page_size
        return HistoryPage(
            items=filtered[start : start + page_size],
            total=len(filtered),
            page=page,
            page_size=page_size,
        )

    def integrity(self) -> dict[str, int | bool]:
        records, invalid = self._records()
        return {"valid": invalid == 0, "record_count": len(records), "invalid_count": invalid}


def stable_target_id(name: str) -> str:
    return f"friend-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}"


def target_identity(target) -> str:
    return target.stable_id or target.candidate_id or stable_target_id(target.name)


def effective_daily_target_statuses(
    config: AppConfig,
    state: AppState,
    day: date,
    records: list[TaskRunRecord] | None = None,
) -> dict[str, str]:
    """Return daily status while keeping confirmed success terminal.

    Historical execution identity, current persistent binding identity, and
    live DOM identity are separate concepts.  Stored events are associated
    only through explicit current aliases; historical records are never
    rewritten, and a later failure can never downgrade a confirmed success.
    """
    targets = enabled_execution_targets(config)
    statuses = {target_identity(target): "pending" for target in targets}
    aliases: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for target in targets:
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

    daily = state.daily.get(day.isoformat(), {})
    succeeded_names = set(daily.get("succeeded", []))
    failed_names = set(daily.get("failures", {}))
    for target in targets:
        identity = target_identity(target)
        if target.name in succeeded_names:
            statuses[identity] = "success"
        elif target.name in failed_names:
            statuses[identity] = "failed"
    for historical_id, result in daily.get("confirmation_results", {}).items():
        identity = aliases.get(historical_id)
        if identity is None:
            continue
        if result in {"confirmed", "retry_confirmed"}:
            statuses[identity] = "success"
        elif statuses[identity] != "success":
            statuses[identity] = "failed"

    if records is None:
        records = TaskHistoryStore(
            config.state_file.parent / "history" / "task-runs.jsonl"
        ).query(start_date=day, end_date=day, page_size=100).items
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
            if identity is not None and result in {
                "confirmed",
                "retry_confirmed",
            }:
                statuses[identity] = "success"
    return statuses


def bootstrap_legacy_daily_history(
    store: TaskHistoryStore,
    daily: dict[str, dict],
    total_targets: int,
) -> int:
    if store.integrity()["record_count"] or not daily:
        return 0
    written = 0
    for day, value in sorted(daily.items()):
        succeeded = len(value.get("succeeded", []))
        failed_names = list(value.get("failures", {}))
        complete = bool(value.get("consumed"))
        base_message = str(value.get("message", ""))
        store.append(
            TaskRunRecord(
                run_id=f"legacy-{hashlib.sha256(day.encode()).hexdigest()[:16]}",
                date=day,
                task_type="daily_send",
                trigger_source="scheduled",
                start_time=f"{day}T07:30:00",
                end_time=f"{day}T07:30:00",
                total_targets=total_targets,
                success_count=succeeded,
                failed_count=len(failed_names),
                skipped_count=max(0, total_targets - succeeded - len(failed_names)) if complete else 0,
                final_status="completed" if complete else "partial_failed",
                base_message_id=hashlib.sha256(base_message.encode("utf-8")).hexdigest()[:16] if base_message else None,
                message_pack="global",
                failed_target_ids=[stable_target_id(name) for name in failed_names],
            )
        )
        written += 1
    return written


def dashboard_statistics(records: list[TaskRunRecord], today: date | None = None) -> dict:
    today = today or date.today()
    latest: dict[str, TaskRunRecord] = {}
    totals: dict[str, int] = {}
    confirmed: dict[str, int] = {}
    for record in records:
        if record.task_type == "daily_send":
            latest[record.date] = record
            totals[record.date] = max(totals.get(record.date, 0), record.total_targets)
            confirmed[record.date] = min(
                totals[record.date],
                confirmed.get(record.date, 0) + record.success_count,
            )

    def success_rate(days: int) -> float:
        cutoff = (today - timedelta(days=days - 1)).isoformat()
        selected_days = [
            day for day in latest if cutoff <= day <= today.isoformat()
        ]
        total = sum(totals[day] for day in selected_days)
        successful = sum(confirmed[day] for day in selected_days)
        return round(successful * 100 / total, 1) if total else 0.0

    streak = 0
    cursor = today if today.isoformat() in latest else today - timedelta(days=1)
    while (
        (day_key := cursor.isoformat()) in latest
        and totals[day_key] > 0
        and confirmed[day_key] >= totals[day_key]
    ):
        streak += 1
        cursor -= timedelta(days=1)
    last_completed = next(
        (
            item.end_time
            for item in reversed(records)
            if item.task_type == "daily_send" and item.final_status in {"completed", "already_done"}
        ),
        None,
    )
    cutoff_7 = (today - timedelta(days=6)).isoformat()
    return {
        "last_completed_run": last_completed,
        "consecutive_successful_days": streak,
        "success_rate_7d": success_rate(7),
        "success_rate_30d": success_rate(30),
        "retries_7d": sum(item.retry_count for item in records if item.date >= cutoff_7),
    }

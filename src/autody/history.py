from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
import uuid

from pydantic import BaseModel, Field

from autody.config import stable_target_id as _stable_target_id
from autody.failures import FailureDetail


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
                failed_target_ids=[_stable_target_id(name) for name in failed_names],
            )
        )
        written += 1
    return written

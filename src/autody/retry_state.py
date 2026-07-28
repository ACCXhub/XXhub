"""Durable, conservative state for delayed daily-send retries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import os
from pathlib import Path


RETRY_DELAYS = (timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=10))


class TaskOutcome(str, Enum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    RETRY_PENDING = "retry_pending"
    RECOVERED = "recovered"
    COMPLETED = "completed"
    FINAL_FAILED = "final_failed"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"


@dataclass
class TaskRunState:
    run_id: str
    date: str
    completion_deadline: datetime
    outcome: TaskOutcome = TaskOutcome.SCHEDULED
    retry_attempts: int = 0
    next_attempt_at: datetime | None = None
    updated_at: datetime | None = None
    reason: str | None = None
    notified_outcomes: set[TaskOutcome] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "date": self.date,
            "completion_deadline": self.completion_deadline.isoformat(),
            "outcome": self.outcome.value,
            "retry_attempts": self.retry_attempts,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "reason": self.reason,
            "notified_outcomes": sorted(item.value for item in self.notified_outcomes),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "TaskRunState":
        return cls(
            run_id=str(value["run_id"]),
            date=str(value["date"]),
            completion_deadline=datetime.fromisoformat(value["completion_deadline"]),
            outcome=TaskOutcome(value.get("outcome", TaskOutcome.SCHEDULED.value)),
            retry_attempts=int(value.get("retry_attempts", 0)),
            next_attempt_at=datetime.fromisoformat(value["next_attempt_at"]) if value.get("next_attempt_at") else None,
            updated_at=datetime.fromisoformat(value["updated_at"]) if value.get("updated_at") else None,
            reason=value.get("reason"),
            notified_outcomes={TaskOutcome(item) for item in value.get("notified_outcomes", [])},
        )


class TaskOutcomeStore:
    """Atomic on-disk task outcome storage, safe to reopen after a restart."""

    def __init__(self, path: Path):
        self.path = path

    def _all(self) -> dict[str, TaskRunState]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            records = raw.get("runs", {}) if isinstance(raw, dict) else {}
            return {run_id: TaskRunState.from_dict(value) for run_id, value in records.items()}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save(self, records: dict[str, TaskRunState]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"runs": {run_id: record.as_dict() for run_id, record in records.items()}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def _update(self, run_id: str, **changes) -> TaskRunState:
        records = self._all()
        if run_id not in records:
            raise KeyError(run_id)
        record = records[run_id]
        for key, value in changes.items():
            setattr(record, key, value)
        records[run_id] = record
        self._save(records)
        return record

    def get(self, run_id: str) -> TaskRunState | None:
        return self._all().get(run_id)

    def schedule(self, run_id: str, day: str, completion_deadline: datetime) -> TaskRunState:
        records = self._all()
        if run_id in records:
            return records[run_id]
        record = TaskRunState(run_id=run_id, date=day, completion_deadline=completion_deadline)
        records[run_id] = record
        self._save(records)
        return record

    def start(self, run_id: str, now: datetime) -> TaskRunState:
        return self._update(run_id, outcome=TaskOutcome.RUNNING, next_attempt_at=None, updated_at=now, reason=None)

    def safe_failure(self, run_id: str, now: datetime, reason: str, *, max_retries: int | None = None) -> TaskRunState:
        record = self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        retry_limit = min(len(RETRY_DELAYS), max_retries if max_retries is not None else len(RETRY_DELAYS))
        if record.retry_attempts >= retry_limit:
            return self._update(run_id, outcome=TaskOutcome.FINAL_FAILED, next_attempt_at=None, updated_at=now, reason=reason)
        delay = RETRY_DELAYS[record.retry_attempts]
        retry_at = now + delay
        if retry_at > record.completion_deadline:
            return self._update(run_id, outcome=TaskOutcome.FINAL_FAILED, next_attempt_at=None, updated_at=now, reason=reason)
        return self._update(
            run_id,
            outcome=TaskOutcome.RETRY_PENDING,
            retry_attempts=record.retry_attempts + 1,
            next_attempt_at=retry_at,
            updated_at=now,
            reason=reason,
        )

    def recover(self, run_id: str, now: datetime) -> TaskRunState:
        return self._update(run_id, outcome=TaskOutcome.RECOVERED, next_attempt_at=None, updated_at=now, reason=None)

    def complete(self, run_id: str, now: datetime) -> TaskRunState:
        return self._update(run_id, outcome=TaskOutcome.COMPLETED, next_attempt_at=None, updated_at=now, reason=None)

    def uncertain(self, run_id: str, now: datetime, reason: str) -> TaskRunState:
        return self._update(run_id, outcome=TaskOutcome.UNCERTAIN, next_attempt_at=None, updated_at=now, reason=reason)

    def cancel(self, run_id: str, now: datetime, reason: str | None = None) -> TaskRunState:
        return self._update(run_id, outcome=TaskOutcome.CANCELLED, next_attempt_at=None, updated_at=now, reason=reason)

    def notification_due(self, run_id: str) -> bool:
        record = self.get(run_id)
        return bool(record and record.outcome in {TaskOutcome.FINAL_FAILED, TaskOutcome.UNCERTAIN} and record.outcome not in record.notified_outcomes)

    def mark_notified(self, run_id: str) -> TaskRunState:
        record = self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        notified = set(record.notified_outcomes)
        notified.add(record.outcome)
        return self._update(run_id, notified_outcomes=notified)

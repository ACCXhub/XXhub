from __future__ import annotations

from datetime import datetime, time

from autody.config import AppConfig
from autody.history import effective_daily_target_statuses
from autody.state import AppState


def recovery_due(config: AppConfig, state: AppState, now: datetime) -> bool:
    if not config.targets:
        return False
    send_at = time.fromisoformat(config.daily_send_time)
    deadline = time.fromisoformat(config.recovery_deadline)
    if now.time() < send_at or now.time() > deadline:
        return False
    statuses = effective_daily_target_statuses(config, state, now.date())
    return any(status != "success" for status in statuses.values())

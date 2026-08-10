from datetime import datetime, timedelta

from autody.retry_state import (
    RECOVERY_RETRY_DELAY,
    RETRY_DELAYS,
    TaskOutcome,
    TaskOutcomeStore,
)


def test_safe_failure_is_persisted_then_recovered_without_a_failure_notification(tmp_path):
    now = datetime(2026, 7, 28, 7, 30)
    store = TaskOutcomeStore(tmp_path / "task-outcomes.json")
    run = store.schedule("run-1", "2026-07-28", now + timedelta(hours=1))

    running = store.start(run.run_id, now)
    pending = store.safe_failure(running.run_id, now, "browser_unavailable")

    assert pending.outcome is TaskOutcome.RETRY_PENDING
    assert pending.next_attempt_at == now + RETRY_DELAYS[0]
    assert store.notification_due(run.run_id) is False

    reloaded = TaskOutcomeStore(tmp_path / "task-outcomes.json")
    assert reloaded.get(run.run_id).outcome is TaskOutcome.RETRY_PENDING

    reloaded.start(run.run_id, pending.next_attempt_at)
    recovered = reloaded.recover(run.run_id, pending.next_attempt_at)

    assert recovered.outcome is TaskOutcome.RECOVERED
    assert reloaded.notification_due(run.run_id) is False


def test_exhausted_short_retries_continue_safely_until_recovery_deadline(tmp_path):
    now = datetime(2026, 7, 28, 7, 30)
    store = TaskOutcomeStore(tmp_path / "task-outcomes.json")
    deadline = now + timedelta(hours=2)
    final = store.schedule("run-final", "2026-07-28", deadline)
    store.start(final.run_id, now)
    for _ in range(len(RETRY_DELAYS) + 1):
        state = store.safe_failure(final.run_id, now, "browser_unavailable")
        if state.outcome is TaskOutcome.RETRY_PENDING:
            store.start(final.run_id, state.next_attempt_at)
            now = state.next_attempt_at

    assert state.outcome is TaskOutcome.RETRY_PENDING
    assert state.next_attempt_at == now
    assert state.next_attempt_at - state.updated_at == RECOVERY_RETRY_DELAY
    final_state = store.safe_failure(
        final.run_id,
        deadline,
        "browser_unavailable",
    )
    assert final_state.outcome is TaskOutcome.FINAL_FAILED
    assert store.notification_due(final.run_id) is True
    store.mark_notified(final.run_id)
    assert store.notification_due(final.run_id) is False

    uncertain = store.schedule("run-uncertain", "2026-07-28", deadline)
    store.start(uncertain.run_id, now)
    uncertain = store.uncertain(uncertain.run_id, now, "confirmation_failed")
    preserved = store.safe_failure(
        uncertain.run_id,
        now + timedelta(minutes=1),
        "browser_unavailable",
    )

    assert uncertain.outcome is TaskOutcome.UNCERTAIN
    assert uncertain.next_attempt_at is None
    assert preserved.outcome is TaskOutcome.UNCERTAIN
    assert store.notification_due(uncertain.run_id) is True

from datetime import date, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from autody.config import AppConfig, Target
from autody.chat import (
    DeliveryConfirmationProvenance,
    DeliveryResult,
    DeliveryStatus,
    FatalChatError,
    conversation_candidate_id,
    TodayOutgoingStatus,
)
from autody import runner as runner_module
from autody.failures import failure_detail
from autody.logging_setup import DailyAppendFileHandler
from autody.message_packs import MessagePackService
from autody.runner import (
    RunStatus,
    TodayDeliveryReconciliationPlan,
    apply_today_delivery_reconciliation,
    apply_today_human_verified_delivery_reconciliation,
    automatic_daily_run_gate,
    plan_today_delivery_reconciliation,
    reconcile_today_delivery,
    record_safe_pre_send_failure,
    run_daily,
)
from autody.retry_state import TaskOutcome, TaskOutcomeStore
from autody.state import StateStore


class FakeChat:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.sent = []

    def send(self, target, message):
        if target in self.failures:
            raise RuntimeError("network timeout")
        self.sent.append((target, message))


def make_config(tmp_path: Path):
    messages = tmp_path / "messages.txt"
    messages.write_text("早安\n晚安\n", encoding="utf-8")
    return AppConfig(
        targets=[Target(name="小明"), Target(name="小红")],
        messages_file=messages,
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "run.lock",
        artifact_dir=tmp_path / "artifacts",
        retry_count=1,
        default_message_pack=None,
    )


def write_verified_retry_scope(
    tmp_path: Path,
    candidate_ids: list[str],
    *,
    account_scope: str | None = None,
) -> None:
    data = tmp_path
    profile_data = tmp_path / "data"
    (profile_data / "account-avatar").mkdir(parents=True, exist_ok=True)
    (profile_data / "account-avatar" / "profile.png").write_bytes(b"avatar")
    (profile_data / "account-profile.json").write_text(
        json.dumps(
            {
                "account_profile_id": "account-" + "a" * 24,
                "account_id_digest": "a" * 64,
                "display_name": "测试账号",
                "avatar_cache_key": "profile",
                "avatar_version": "v1",
                "is_self": True,
                "verification_source": "test",
                "profile_status": "verified",
                "verified_at": "2026-07-30T07:00:00",
                "last_updated_at": "2026-07-30T07:00:00",
                "switched": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data / "discovered_friends.json").write_text(
        json.dumps(
            {
                "scanned_at": "2026-07-30T07:15:00",
                "account_scope": account_scope or "account-" + "a" * 24,
                "last_result": {
                    "status": "completed_bottom_reached",
                    "completed_bottom_reached": True,
                    "partial": False,
                },
                "candidates": [
                    {
                        "candidate_id": candidate_id,
                        "display_name": "候选",
                        "avatar_status": "missing",
                        "discovered_at": "2026-07-30T07:15:00",
                        "match_status": "configured",
                        "presence_status": "current",
                        "identity_key": f"row:{candidate_id}",
                        "identity_source": "row_attribute",
                    }
                    for candidate_id in candidate_ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def bind_authoritatively(config: AppConfig, *, account_scope: str | None = None) -> None:
    scope = account_scope or "account-" + "a" * 24
    for target in config.targets:
        if not target.candidate_id:
            continue
        target.binding_identity_key = f"row:{target.candidate_id}"
        target.binding_identity_source = "row_attribute"
        target.binding_account_scope = scope


def test_second_run_same_day_sends_nothing(tmp_path: Path):
    config, chat = make_config(tmp_path), FakeChat()
    first = run_daily(config, chat, date(2026, 6, 18))
    second = run_daily(config, chat, date(2026, 6, 18))
    assert first.status is RunStatus.COMPLETED
    assert first.sent_count == 2
    assert first.skipped_count == 0
    assert second.status is RunStatus.ALREADY_DONE
    assert second.sent_count == 0
    assert second.skipped_count == 2
    assert len(chat.sent) == 2
    assert len({message for _, message in chat.sent}) == 1
    assert chat.sent[0][1].endswith(" —— gpt小助手")


def test_automatic_gate_stops_before_browser_when_all_targets_succeeded(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(
            name="当前有效目标",
            stable_id="target-current",
            candidate_id="candidate-current",
        )
    ]
    store = StateStore(config.state_file)
    state = store.load()
    state.daily["2026-08-10"] = {
        "message": "",
        "succeeded": ["当前有效目标"],
        "failures": {},
        "confirmation_results": {"target-current": "confirmed"},
        "confirmation_provenance": {"target-current": "post_send_observed"},
        "consumed": True,
    }
    store.save(state)

    result = automatic_daily_run_gate(
        config,
        now=datetime(2026, 8, 10, 8, 0),
    )

    assert result is not None
    assert result.status is RunStatus.ALREADY_DONE
    assert result.skipped_count == 1


def test_automatic_gate_reopens_only_proven_safe_legacy_final_failure(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(
            name="当前有效目标",
            stable_id="target-current",
            candidate_id="candidate-current",
        )
    ]
    write_verified_retry_scope(tmp_path, ["candidate-current"])
    bind_authoritatively(config)

    run_id = "legacy-safe-final"
    detail = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        send_attempts=0,
        run_id=run_id,
        target_stable_id="target-current",
        binding_valid=True,
        account_scope_matches=True,
    )
    store = StateStore(config.state_file)
    state = store.load()
    state.daily["2026-08-10"] = {
        "message": "",
        "succeeded": [],
        "failures": {"当前有效目标": "conversation_not_found"},
        "confirmation_results": {},
        "target_failures": {
            "target-current": detail.model_dump(mode="json")
        },
        "task_run_id": run_id,
        "consumed": False,
    }
    store.save(state)
    outcomes = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json")
    outcomes.schedule(
        run_id,
        "2026-08-10",
        datetime(2026, 8, 10, 23, 59),
    )
    outcomes.safe_failure(
        run_id,
        datetime(2026, 8, 10, 7, 40),
        "conversation_not_found",
        max_retries=0,
    )

    result = automatic_daily_run_gate(
        config,
        now=datetime(2026, 8, 10, 8, 0),
    )

    assert result is None
    resumed = outcomes.get(run_id)
    assert resumed and resumed.outcome is TaskOutcome.RETRY_PENDING
    assert resumed.next_attempt_at == datetime(2026, 8, 10, 8, 0)


def test_modern_run_keeps_enabled_records_without_bindings_in_daily_denominator(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(
            name="当前有效目标",
            stable_id="target-current",
            candidate_id="candidate-current",
        ),
        Target(name="缺少绑定目标"),
    ]
    chat = FakeChat()
    write_verified_retry_scope(tmp_path, ["candidate-current"])
    bind_authoritatively(config)

    result = run_daily(config, chat, date(2026, 7, 30))

    assert result.status is RunStatus.FINAL_FAILED
    assert result.total_targets == 2
    assert result.sent_count == 1
    assert result.failed_count == 1
    assert [name for name, _message in chat.sent] == ["当前有效目标"]


def test_binding_blocked_targets_prevent_false_daily_completion(tmp_path: Path):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    executable = [
        Target(
            name=f"可执行目标 {index}",
            stable_id=f"target-{index}",
            candidate_id=f"candidate-{index}",
        )
        for index in range(3)
    ]
    blocked = [Target(name=f"缺少绑定目标 {index}") for index in range(6)]
    config.targets = executable + blocked
    write_verified_retry_scope(
        tmp_path,
        [target.candidate_id for target in executable if target.candidate_id],
    )
    bind_authoritatively(config)

    chat = FakeChat()
    result = run_daily(config, chat, date(2026, 8, 10))
    state = StateStore(config.state_file).load()
    gated = automatic_daily_run_gate(
        config,
        now=datetime(2026, 8, 10, 8, 0),
    )

    assert result.status is RunStatus.FINAL_FAILED
    assert result.total_targets == 9
    assert result.sent_count == 3
    assert result.failed_count == 6
    assert [name for name, _message in chat.sent] == [target.name for target in executable]
    assert state.daily["2026-08-10"]["consumed"] is False
    assert gated is not None
    assert gated.status is RunStatus.FINAL_FAILED
    assert gated.total_targets == 9


def test_reconciliation_missing_overrides_stale_success_and_keeps_full_denominator(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [
        Target(name=f"目标 {index}", stable_id=f"target-{index}")
        for index in range(9)
    ]
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [target.name for target in config.targets],
        "failures": {},
        "confirmation_results": {},
        "consumed": True,
    }
    StateStore(config.state_file).save(state)

    statuses = apply_today_delivery_reconciliation(
        config,
        {
            **{
                target.stable_id: TodayOutgoingStatus.CONFIRMED_SENT.value
                for target in config.targets[:3]
            },
            **{
                target.stable_id: TodayOutgoingStatus.CONFIRMED_MISSING.value
                for target in config.targets[3:]
            },
        },
        day,
    )
    stored = StateStore(config.state_file).load().daily[day.isoformat()]

    assert sum(status == "success" for status in statuses.values()) == 3
    assert sum(status == "pending" for status in statuses.values()) == 6
    assert len(statuses) == 9
    assert stored["consumed"] is False


def test_human_verified_today_is_explicit_reconciliation_evidence_not_post_send(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(name=f"目标 {index}", stable_id=f"target-{index}")
        for index in range(9)
    ]
    day = date(2026, 8, 30)

    statuses = apply_today_human_verified_delivery_reconciliation(
        config,
        confirmed_sent_ids={"target-0", "target-1", "target-2"},
        confirmed_missing_ids={
            "target-3", "target-4", "target-5", "target-6", "target-7", "target-8"
        },
        today=day,
    )
    stored = StateStore(config.state_file).load().daily[day.isoformat()]

    assert sum(status == "success" for status in statuses.values()) == 3
    assert sum(status == "pending" for status in statuses.values()) == 6
    assert stored["consumed"] is False
    assert set(stored["delivery_reconciliation"].values()) == {
        "confirmed_sent",
        "confirmed_missing",
    }
    assert set(stored["delivery_reconciliation_evidence"].values()) == {
        "human_verified_today"
    }
    assert set(stored["confirmation_provenance"].values()) == {
        "human_verified_today"
    }


def test_reconciliation_sent_corrects_stale_failure_without_resend(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="目标", stable_id="target-a")]
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [],
        "failures": {"目标": "conversation_not_found"},
        "confirmation_results": {"target-a": "send_failed"},
        "consumed": False,
    }
    StateStore(config.state_file).save(state)

    statuses = apply_today_delivery_reconciliation(
        config,
        {"target-a": TodayOutgoingStatus.CONFIRMED_SENT.value},
        day,
    )
    stored = StateStore(config.state_file).load().daily[day.isoformat()]

    assert statuses == {"target-a": "success"}
    assert "目标" not in stored["failures"]
    assert stored["confirmation_results"]["target-a"] == "confirmed"


def test_reconciliation_unknown_preserves_same_day_confirmed_delivery(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="目标", stable_id="target-a")]
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [],
        "failures": {},
        "confirmation_results": {"target-a": "confirmed"},
        "confirmation_provenance": {"target-a": "post_send_observed"},
        "consumed": True,
    }
    StateStore(config.state_file).save(state)

    statuses = apply_today_delivery_reconciliation(
        config,
        {"target-a": TodayOutgoingStatus.UNKNOWN.value},
        day,
    )

    assert statuses == {"target-a": "success"}


def test_reconciliation_unknown_does_not_trust_legacy_success(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="目标", stable_id="target-a")]
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": ["目标"],
        "failures": {},
        "confirmation_results": {},
        "consumed": True,
    }
    StateStore(config.state_file).save(state)

    statuses = apply_today_delivery_reconciliation(
        config,
        {"target-a": TodayOutgoingStatus.UNKNOWN.value},
        day,
    )
    stored = StateStore(config.state_file).load().daily[day.isoformat()]

    assert statuses == {"target-a": "unknown"}
    assert stored["consumed"] is False


def test_reconciliation_unknown_never_enters_the_sender(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a")
    ]
    write_verified_retry_scope(tmp_path, ["candidate-a"])
    bind_authoritatively(config)

    class UnknownChat:
        sent = []

        def open_conversation_identity(self, *_args, **_kwargs):
            return SimpleNamespace(identity_match=True)

        def audit_today_outgoing(self, *_args, **_kwargs):
            return SimpleNamespace(status=TodayOutgoingStatus.UNKNOWN)

        def send(self, *_args, **_kwargs):
            raise AssertionError("unknown history must never be resent")

    result = reconcile_today_delivery(config, UnknownChat(), date(2026, 8, 30))

    assert result.outcomes == {"target-a": "unknown"}
    assert result.supplement_result is None
    assert automatic_daily_run_gate(
        config, now=datetime(2026, 8, 30, 8, 0)
    ).status is RunStatus.UNCERTAIN


def test_reconciliation_uses_same_day_confirmations_without_live_chat_audit(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(name=f"目标 {index}", stable_id=f"target-{index}")
        for index in range(9)
    ]
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [],
        "failures": {},
        "confirmation_results": {
            target.stable_id: "confirmed" if index % 2 else "retry_confirmed"
            for index, target in enumerate(config.targets)
        },
        "confirmation_provenance": {
            target.stable_id: "post_send_observed"
            for target in config.targets
        },
        "consumed": True,
    }
    StateStore(config.state_file).save(state)

    class NoLiveChat:
        def __getattr__(self, name):
            raise AssertionError(f"strong confirmation must not access chat.{name}")

    result = reconcile_today_delivery(config, NoLiveChat(), day)

    assert set(result.outcomes) == {target.stable_id for target in config.targets}
    assert set(result.outcomes.values()) == {"confirmed_sent"}
    assert result.live_audit_required == 0
    assert set(result.evidence_sources.values()) == {
        "same_day_delivery_confirmation"
    }
    assert result.supplement_result is None


def test_reconciliation_does_not_treat_unproven_same_text_confirmations_as_sent(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(name=f"目标 {index}", stable_id=f"target-{index}")
        for index in range(9)
    ]
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [target.name for target in config.targets],
        "failures": {},
        "confirmation_results": {
            target.stable_id: "confirmed" for target in config.targets
        },
        "consumed": True,
    }
    StateStore(config.state_file).save(state)

    plan = plan_today_delivery_reconciliation(config, day)

    assert plan.outcomes == {}
    assert plan.evidence_sources == {}
    assert set(plan.live_audit_target_ids) == {
        target.stable_id for target in config.targets
    }


def test_unproven_pre_send_match_cannot_complete_nine_daily_targets(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name=f"目标 {index}") for index in range(9)]

    class PreSendMatchChat:
        def send(self, _target, _message):
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=0)

    result = run_daily(config, PreSendMatchChat(), date(2026, 8, 30))
    state = StateStore(config.state_file).load()
    statuses = runner_module.effective_daily_target_statuses(
        config, state, date(2026, 8, 30)
    )

    assert result.sent_count == 0
    assert result.status is not RunStatus.COMPLETED
    assert sum(status == "success" for status in statuses.values()) == 0
    assert state.daily["2026-08-30"]["consumed"] is False


def test_reconciliation_only_live_audits_targets_without_strong_confirmation(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(name="已确认", stable_id="target-confirmed"),
        Target(name="待核实", stable_id="target-live", candidate_id="candidate-live"),
    ]
    write_verified_retry_scope(tmp_path, ["candidate-live"])
    bind_authoritatively(config)
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [],
        "failures": {},
        "confirmation_results": {"target-confirmed": "confirmed"},
        "confirmation_provenance": {
            "target-confirmed": "post_send_observed"
        },
        "consumed": False,
    }
    StateStore(config.state_file).save(state)

    class TargetedAuditChat:
        def __init__(self):
            self.opened: list[str] = []

        def open_conversation_identity(self, target_id, *_args, **_kwargs):
            self.opened.append(target_id)
            return SimpleNamespace(identity_match=True)

        def audit_today_outgoing(self, *_args, **_kwargs):
            return SimpleNamespace(status=TodayOutgoingStatus.UNKNOWN)

        def send(self, *_args, **_kwargs):
            raise AssertionError("unknown history must never be resent")

    chat = TargetedAuditChat()
    result = reconcile_today_delivery(config, chat, day)

    assert chat.opened == ["target-live"]
    assert result.outcomes == {
        "target-confirmed": "confirmed_sent",
        "target-live": "unknown",
    }
    assert result.live_audit_required == 1
    assert result.evidence_sources == {
        "target-confirmed": "same_day_delivery_confirmation",
        "target-live": "live_chat_audit",
    }
    assert result.supplement_result is None


def test_reconciliation_supplement_uses_normal_confirmed_send_path(tmp_path: Path):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a")
    ]
    write_verified_retry_scope(tmp_path, ["candidate-a"])
    bind_authoritatively(config)

    class MissingChat:
        def __init__(self):
            self.sent = []

        def open_conversation_identity(self, *_args, **_kwargs):
            return SimpleNamespace(identity_match=True)

        def audit_today_outgoing(self, *_args, **_kwargs):
            return SimpleNamespace(status=TodayOutgoingStatus.CONFIRMED_MISSING)

        def send(self, target, message, **_kwargs):
            self.sent.append((target, message))
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = MissingChat()
    result = reconcile_today_delivery(config, chat, date(2026, 8, 30))
    statuses = runner_module.effective_daily_target_statuses(
        config, StateStore(config.state_file).load(), date(2026, 8, 30)
    )

    assert result.outcomes == {"target-a": "confirmed_missing"}
    assert result.supplement_result is not None
    assert result.supplement_result.status is RunStatus.COMPLETED
    assert len(chat.sent) == 1
    assert statuses == {"target-a": "success"}
    stored = StateStore(config.state_file).load().daily["2026-08-30"]
    assert "target-a" not in stored["delivery_reconciliation"]
    assert "target-a" not in stored["delivery_reconciliation_evidence"]


def test_live_audit_missing_reopens_uncertain_and_only_resends_missing_target(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name=f"目标 {index}",
            stable_id=f"target-{index}",
            candidate_id=f"candidate-{index}",
        )
        for index in range(9)
    ]
    write_verified_retry_scope(
        tmp_path,
        [target.candidate_id for target in config.targets],
    )
    bind_authoritatively(config)
    day = date(2026, 8, 30)
    missing = config.targets[-1]
    run_id = runner_module._daily_run_id(day)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [target.name for target in config.targets[:-1]],
        "failures": {missing.name: "binding_stale"},
        "confirmation_results": {
            target.stable_id: "confirmed" for target in config.targets[:-1]
        },
        "confirmation_provenance": {
            target.stable_id: "post_send_observed"
            for target in config.targets[:-1]
        },
        "target_failures": {
            missing.stable_id: failure_detail(
                "binding_stale",
                stage="target_binding_resolved",
                run_id=run_id,
                target_stable_id=missing.stable_id,
            ).model_dump(mode="json")
        },
        "task_run_id": run_id,
        "consumed": False,
    }
    StateStore(config.state_file).save(state)
    outcomes = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json")
    outcomes.schedule(
        run_id,
        day.isoformat(),
        datetime(2026, 8, 30, 23, 59),
    )
    outcomes.uncertain(run_id, datetime(2026, 8, 30, 19, 0), "legacy_uncertain")

    class MissingAuditChat:
        def __init__(self):
            self.sent = []

        def open_conversation_identity(self, *_args, **_kwargs):
            return SimpleNamespace(identity_match=True)

        def audit_today_outgoing(self, *_args, **_kwargs):
            return SimpleNamespace(status=TodayOutgoingStatus.CONFIRMED_MISSING)

        def send(self, target, message, **_kwargs):
            self.sent.append((target, message))
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = MissingAuditChat()
    audit_only = reconcile_today_delivery(
        config,
        chat,
        day,
        plan=TodayDeliveryReconciliationPlan({}, {}, (missing.stable_id,)),
        supplement=False,
    )
    stored = StateStore(config.state_file).load().daily[day.isoformat()]
    statuses = runner_module.effective_daily_target_statuses(
        config, StateStore(config.state_file).load(), day
    )

    assert audit_only.supplement_result is None
    assert chat.sent == []
    assert statuses == {
        **{target.stable_id: "success" for target in config.targets[:-1]},
        missing.stable_id: "pending",
    }
    assert stored["delivery_reconciliation"][missing.stable_id] == "confirmed_missing"
    assert stored["delivery_reconciliation_evidence"][missing.stable_id] == "live_chat_audit"
    assert missing.stable_id not in stored["target_failures"]
    assert outcomes.get(run_id).outcome is TaskOutcome.RETRY_PENDING
    recovered_plan = plan_today_delivery_reconciliation(config, day)
    assert recovered_plan.live_audit_target_ids == ()
    assert recovered_plan.confirmed_missing_target_ids == (missing.stable_id,)

    resent = run_daily(
        config,
        chat,
        day,
        now=datetime.now() + timedelta(seconds=1),
    )

    assert resent.sent_count == 1
    assert [target for target, _message in chat.sent] == [missing.name]
    assert resent.total_targets == 9
    assert resent.status in {RunStatus.COMPLETED, RunStatus.RECOVERED}
    assert StateStore(config.state_file).load().daily[day.isoformat()]["consumed"] is True


def test_live_audit_sent_remains_success_and_is_not_selected_for_resend(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a")
    ]
    write_verified_retry_scope(tmp_path, ["candidate-a"])
    bind_authoritatively(config)

    class SentAuditChat:
        def __init__(self):
            self.sent = []

        def open_conversation_identity(self, *_args, **_kwargs):
            return SimpleNamespace(identity_match=True)

        def audit_today_outgoing(self, *_args, **_kwargs):
            return SimpleNamespace(status=TodayOutgoingStatus.CONFIRMED_SENT)

        def send(self, *_args, **_kwargs):
            raise AssertionError("live-audited sent target must not be resent")

    chat = SentAuditChat()
    result = reconcile_today_delivery(config, chat, date(2026, 8, 30))

    assert result.outcomes == {"target-a": "confirmed_sent"}
    assert result.supplement_result is None
    assert chat.sent == []
    assert runner_module.effective_daily_target_statuses(
        config, StateStore(config.state_file).load(), date(2026, 8, 30)
    ) == {"target-a": "success"}


def test_daily_pipeline_skips_eight_sent_targets_and_audits_then_sends_once(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name=f"目标 {index}",
            stable_id=f"target-{index}",
            candidate_id=f"candidate-{index}",
        )
        for index in range(9)
    ]
    write_verified_retry_scope(tmp_path, [target.candidate_id for target in config.targets])
    bind_authoritatively(config)
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [target.name for target in config.targets[:-1]],
        "failures": {},
        "confirmation_results": {
            target.stable_id: "confirmed" for target in config.targets[:-1]
        },
        "confirmation_provenance": {
            target.stable_id: "post_send_observed" for target in config.targets[:-1]
        },
        "consumed": False,
    }
    StateStore(config.state_file).save(state)

    class PipelineChat:
        def __init__(self):
            self.navigations = []
            self.audits = 0
            self.sent = []

        def open_conversation_identity(self, target_id, *_args, **_kwargs):
            self.navigations.append(target_id)
            return SimpleNamespace(identity_match=True)

        def audit_today_outgoing(self, _today):
            self.audits += 1
            return SimpleNamespace(status=TodayOutgoingStatus.CONFIRMED_MISSING)

        def send(self, target, message, *, conversation_verified=False, **_kwargs):
            assert conversation_verified is True
            self.sent.append((target, message))
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = PipelineChat()
    result = run_daily(config, chat, day)

    assert result.status is RunStatus.COMPLETED
    assert result.sent_count == 1
    assert len(chat.navigations) == 1
    assert chat.audits == 1
    assert len(chat.sent) == 1
    assert StateStore(config.state_file).load().daily[day.isoformat()]["consumed"] is True


def test_daily_pipeline_recovers_stale_conversation_locators_in_one_targeted_refresh(
    tmp_path: Path,
    monkeypatch,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name=f"目标 {index}",
            stable_id=f"target-{index}",
            candidate_id=f"candidate-{index}",
        )
        for index in range(9)
    ]
    write_verified_retry_scope(
        tmp_path, [target.candidate_id for target in config.targets]
    )
    bind_authoritatively(config)
    day = date(2026, 8, 30)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [target.name for target in config.targets[:5]],
        "failures": {},
        "confirmation_results": {
            target.stable_id: "confirmed" for target in config.targets[:5]
        },
        "confirmation_provenance": {
            target.stable_id: "post_send_observed"
            for target in config.targets[:5]
        },
        "consumed": False,
    }
    StateStore(config.state_file).save(state)

    refreshes: list[set[str]] = []

    def refresh_locators(_config, _page, _selectors, output_path, *, target_ids, **_kwargs):
        refreshes.append(set(target_ids))
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        for candidate in payload["candidates"]:
            candidate["conversation_id"] = f"conversation-{candidate['candidate_id']}"
        payload["target_refresh"] = {
            "status": "completed",
            "account_scope": "account-" + "a" * 24,
            "requested_target_ids": sorted(target_ids),
            "found_target_ids": sorted(target_ids),
            "missing_target_ids": [],
            "unresolved_target_ids": [],
            "partial": False,
        }
        output_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(runner_module, "refresh_configured_targets", refresh_locators)

    class LocatorRecoveryChat:
        page = object()
        selectors = object()
        friend_search_timeout_ms = 1

        def __init__(self):
            self.open_attempts: dict[str, int] = {}
            self.verified_targets: list[str] = []
            self.audits = 0

        def open_conversation_identity(self, target_id, expected_id, *_args, **_kwargs):
            self.open_attempts[target_id] = self.open_attempts.get(target_id, 0) + 1
            if expected_id == f"conversation-candidate-{target_id.split('-')[-1]}":
                self.verified_targets.append(target_id)
                return SimpleNamespace(identity_match=True)
            return SimpleNamespace(
                identity_match=False,
                identity_match_reason="conversation_not_found",
            )

        def audit_today_outgoing(self, _today):
            self.audits += 1
            return SimpleNamespace(status=TodayOutgoingStatus.CONFIRMED_SENT)

        def send(self, *_args, **_kwargs):
            raise AssertionError("live-audited target must not be sent")

    chat = LocatorRecoveryChat()
    missed_ids = {f"target-{index}" for index in range(5, 9)}
    result = run_daily(config, chat, day, target_ids=missed_ids)

    assert refreshes == [missed_ids]
    assert set(chat.verified_targets) == missed_ids
    assert chat.audits == 4
    assert all(chat.open_attempts[target_id] == 2 for target_id in missed_ids)
    assert all(f"target-{index}" not in chat.open_attempts for index in range(5))
    assert result.sent_count == 0
    assert result.status is RunStatus.COMPLETED


def test_unknown_outcome_is_reaudited_before_any_future_send(tmp_path: Path):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a")
    ]
    write_verified_retry_scope(tmp_path, ["candidate-a"])
    bind_authoritatively(config)
    day = date(2026, 8, 30)
    run_id = runner_module._daily_run_id(day)
    state = StateStore(config.state_file).load()
    state.daily[day.isoformat()] = {
        "message": "早安",
        "succeeded": [],
        "failures": {},
        "confirmation_results": {},
        "confirmation_provenance": {},
        "delivery_reconciliation": {"target-a": "unknown"},
        "delivery_reconciliation_evidence": {"target-a": "live_chat_audit"},
        "task_run_id": run_id,
        "consumed": False,
    }
    StateStore(config.state_file).save(state)
    outcomes = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json")
    outcomes.schedule(run_id, day.isoformat(), datetime(2026, 8, 30, 23, 59))
    outcomes.uncertain(run_id, datetime(2026, 8, 30, 19, 0), "legacy_uncertain")

    class ReauditChat:
        navigations = 0
        audits = 0
        sent = 0

        def open_conversation_identity(self, *_args, **_kwargs):
            self.navigations += 1
            return SimpleNamespace(identity_match=True)

        def audit_today_outgoing(self, _today):
            self.audits += 1
            return SimpleNamespace(status=TodayOutgoingStatus.CONFIRMED_MISSING)

        def send(self, *_args, conversation_verified=False, **_kwargs):
            assert conversation_verified is True
            self.sent += 1
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = ReauditChat()
    result = run_daily(config, chat, day, now=datetime(2026, 8, 30, 20, 0))

    assert result.status is RunStatus.RECOVERED
    assert (chat.navigations, chat.audits, chat.sent) == (1, 1, 1)


def test_running_denominator_is_immutable_and_next_run_uses_latest_targets(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [Target(name="目标一")]
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0

    class MutatingChat(FakeChat):
        def send(self, target, message):
            super().send(target, message)
            config.targets.append(Target(name="目标二"))

    first_chat = MutatingChat()
    first = run_daily(config, first_chat, date(2026, 7, 30))
    second_chat = FakeChat()
    second = run_daily(config, second_chat, date(2026, 7, 30))

    assert first.total_targets == 1
    assert first.sent_count == 1
    assert second.total_targets == 2
    assert second.skipped_count == 1
    assert [target for target, _message in second_chat.sent] == ["目标二"]


def test_retry_only_processes_failed_target_with_same_message(tmp_path: Path):
    config, first = make_config(tmp_path), FakeChat({"小红"})
    first_result = run_daily(config, first, date(2026, 6, 18))
    assert first_result.status is RunStatus.RETRY_PENDING
    assert first_result.sent_count == 1
    assert first_result.failed_count == 1
    second = FakeChat()
    second_result = run_daily(config, second, date(2026, 6, 18), now=datetime(2026, 6, 18, 7, 32))
    assert second_result.status is RunStatus.RECOVERED
    assert second_result.sent_count == 1
    assert second_result.skipped_count == 1
    assert [target for target, _ in second.sent] == ["小红"]
    assert second.sent[0][1] == first.sent[0][1]


def test_partial_pre_send_failure_retries_only_the_unconfirmed_target(tmp_path: Path):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(name=f"目标{index}", stable_id=f"target-{index}")
        for index in range(9)
    ]

    class PartialChat:
        def __init__(self, failing_target=None):
            self.failing_target = failing_target
            self.calls = []

        def send(self, target, _message):
            self.calls.append(target)
            if target == self.failing_target:
                return DeliveryResult(
                    DeliveryStatus.SEND_FAILED,
                    send_attempts=0,
                    error="target not found",
                )
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    first_chat = PartialChat("目标8")
    first = run_daily(
        config,
        first_chat,
        date(2026, 7, 29),
        now=datetime(2026, 7, 29, 7, 30),
        trigger_source="scheduled",
    )
    assert first.status is RunStatus.RETRY_PENDING
    assert first.sent_count == 8
    assert first.failed_count == 1

    pending = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(first.run_id)
    retry_chat = PartialChat()
    recovered = run_daily(
        config,
        retry_chat,
        date(2026, 7, 29),
        now=pending.next_attempt_at,
        trigger_source="retry",
    )

    assert recovered.status is RunStatus.RECOVERED
    assert retry_chat.calls == ["目标8"]
    assert recovered.skipped_count == 8


def test_runner_passes_current_stable_binding_to_navigation(tmp_path: Path):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name="显示名称",
            stable_id="target-current",
            candidate_id="candidate-current",
        )
    ]
    write_verified_retry_scope(tmp_path, ["candidate-current"])
    bind_authoritatively(config)

    class BindingAwareChat:
        def __init__(self):
            self.calls = []

        def send(
            self,
            target,
            _message,
            *,
            selected_target_id=None,
            expected_conversation_id=None,
        ):
            self.calls.append(
                (target, selected_target_id, expected_conversation_id)
            )
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = BindingAwareChat()
    result = run_daily(config, chat, date(2026, 7, 30))

    assert result.status is RunStatus.COMPLETED
    assert chat.calls == [
        (
            "显示名称",
            "target-current",
            conversation_candidate_id("row:candidate-current"),
        )
    ]


def test_runner_uses_authoritative_identity_when_candidate_cache_key_is_stale(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name="缓存键已变更",
            stable_id="target-current",
            candidate_id="candidate-stale",
            binding_identity_key="row:candidate-current",
            binding_identity_source="row_attribute",
            binding_account_scope="account-" + "a" * 24,
        )
    ]
    write_verified_retry_scope(tmp_path, ["candidate-current"])

    class BindingAwareChat:
        def __init__(self):
            self.expected_conversation_ids = []

        def send(self, _target, _message, *, expected_conversation_id=None, **_kwargs):
            self.expected_conversation_ids.append(expected_conversation_id)
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = BindingAwareChat()
    result = run_daily(config, chat, date(2026, 7, 30))

    assert result.status is RunStatus.COMPLETED
    assert chat.expected_conversation_ids == [
        conversation_candidate_id("row:candidate-current")
    ]


def test_runner_blocks_avatar_only_candidate_before_the_send_boundary(tmp_path: Path):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name="仅头像候选",
            stable_id="target-current",
            candidate_id="candidate-current",
        )
    ]
    write_verified_retry_scope(tmp_path, ["candidate-current"])
    discovery_path = tmp_path / "discovered_friends.json"
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    discovery["last_result"] = {
        "status": "completed_bottom_reached",
        "completed_bottom_reached": True,
        "partial": False,
    }
    discovery["candidates"][0].update(
        identity_key="avatar:unproven",
        identity_source="avatar_source",
    )
    discovery_path.write_text(json.dumps(discovery, ensure_ascii=False), encoding="utf-8")

    class NoSendChat:
        def __init__(self):
            self.calls = []

        def send(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = NoSendChat()
    result = run_daily(config, chat, date(2026, 8, 9), now=datetime(2026, 8, 9, 7, 30))

    assert chat.calls == []
    assert result.target_failures["target-current"].reason_code == "binding_missing"


def test_runner_rejects_retired_startup_recovery_trigger(tmp_path: Path):
    config = make_config(tmp_path)

    with pytest.raises(ValueError, match="unsupported daily-send trigger source"):
        run_daily(
            config,
            FakeChat(),
            date(2026, 7, 30),
            trigger_source="startup_recovery",
        )


@pytest.mark.parametrize("trigger_source", ["scheduled", "retry", "manual"])
def test_runner_resolves_account_scoped_bindings_to_current_conversation_identity(
    tmp_path: Path,
    trigger_source: str,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    account_scope = "account-" + "a" * 24
    identities = [f"row:{index:064x}" for index in range(1, 9)]
    persistent_ids = [
        "candidate-"
        + hashlib.sha256(f"{account_scope}\0{identity}".encode()).hexdigest()[:32]
        for identity in identities
    ]
    conversation_ids = [
        "candidate-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        for identity in identities
    ]
    config.targets = [
        Target(
            name=f"当前有效目标 {index}",
            stable_id=f"target-{index}",
            candidate_id=persistent_id,
            binding_identity_key=identity,
            binding_identity_source="row_attribute",
            binding_account_scope=account_scope,
        )
        for index, (persistent_id, identity) in enumerate(
            zip(persistent_ids, identities, strict=True), start=1
        )
    ] + [Target(name="缺少绑定目标", stable_id="target-missing")]
    write_verified_retry_scope(
        tmp_path,
        persistent_ids,
        account_scope=account_scope,
    )
    discovered_path = tmp_path / "discovered_friends.json"
    discovered = json.loads(discovered_path.read_text(encoding="utf-8"))
    discovered["last_result"] = {
        "status": "completed_bottom_reached",
        "completed_bottom_reached": True,
        "partial": False,
    }
    for index, (candidate, identity) in enumerate(
        zip(discovered["candidates"], identities, strict=True),
        start=1,
    ):
        candidate["identity_key"] = identity
        candidate["identity_source"] = "row_attribute"
        candidate["match_status"] = "unconfigured"
        candidate["configured_target_id"] = None
    discovered_path.write_text(
        json.dumps(discovered, ensure_ascii=False),
        encoding="utf-8",
    )

    class NavigationOnlyChat:
        def __init__(self):
            self.calls = []

        def send(
            self,
            target,
            _message,
            *,
            selected_target_id=None,
            expected_conversation_id=None,
        ):
            self.calls.append(
                (target, selected_target_id, expected_conversation_id)
            )
            if expected_conversation_id not in conversation_ids:
                return DeliveryResult(
                    DeliveryStatus.BLOCKED,
                    send_attempts=0,
                    error="conversation_not_found",
                    failure_stage="conversation_located",
                    reason_code="conversation_not_found",
                )
            return DeliveryResult(
                DeliveryStatus.SEND_FAILED,
                send_attempts=0,
                error="test_stop_before_send",
                failure_stage="composer_verified",
                reason_code="send_failed_before_action",
            )

    chat = NavigationOnlyChat()
    result = run_daily(
        config,
        chat,
        date(2026, 8, 9),
        now=datetime(2026, 8, 9, 7, 30),
        trigger_source=trigger_source,
    )

    assert result.total_targets == 9
    assert result.sent_count == 0
    assert len(chat.calls) == 8
    assert [call[1] for call in chat.calls] == [
        f"target-{index}" for index in range(1, 9)
    ]
    assert [call[2] for call in chat.calls] == conversation_ids
    assert all(
        detail.reason_code != "conversation_not_found"
        for detail in result.target_failures.values()
    )


def test_pre_send_failure_records_target_level_chinese_reason_and_exact_stage(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name="目标",
            stable_id="target-current",
            candidate_id="candidate-current",
        )
    ]
    write_verified_retry_scope(tmp_path, ["candidate-current"])
    bind_authoritatively(config)

    class MissingConversation:
        def send(self, *_args, **_kwargs):
            return DeliveryResult(
                DeliveryStatus.BLOCKED,
                send_attempts=0,
                error="conversation_not_found",
                failure_stage="conversation_located",
                reason_code="conversation_not_found",
            )

    result = run_daily(
        config,
        MissingConversation(),
        date(2026, 7, 30),
        now=datetime(2026, 7, 30, 7, 30),
    )

    detail = result.target_failures["target-current"]
    assert result.status is RunStatus.RETRY_PENDING
    assert detail.stage == "conversation_located"
    assert detail.user_summary_zh == "无法在当前会话列表中找到目标"
    assert detail.send_attempts == 0
    assert detail.uncertain_send is False
    assert detail.target_stable_id == "target-current"

    daily = json.loads(config.state_file.read_text(encoding="utf-8"))["daily"][
        "2026-07-30"
    ]
    assert daily["target_failures"]["target-current"]["reason_code"] == (
        "conversation_not_found"
    )


def test_history_uses_configured_stable_id_and_keeps_target_failure_detail(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(
            name="显示名称",
            stable_id="target-authoritative",
            candidate_id="candidate-current",
        )
    ]
    write_verified_retry_scope(tmp_path, ["candidate-current"])
    bind_authoritatively(config)

    class MissingConversation:
        def send(self, *_args, **_kwargs):
            return DeliveryResult(
                DeliveryStatus.BLOCKED,
                send_attempts=0,
                failure_stage="conversation_located",
                reason_code="conversation_not_found",
            )

    run_daily(
        config,
        MissingConversation(),
        date(2026, 7, 30),
        now=datetime(2026, 7, 30, 7, 30),
    )

    history = [
        json.loads(line)
        for line in (tmp_path / "history" / "task-runs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ][-1]
    assert history["failed_target_ids"] == ["target-authoritative"]
    assert history["target_failures"]["target-authoritative"]["stage"] == (
        "conversation_located"
    )


def test_logger_failure_does_not_change_completed_outcome_or_create_retry(
    tmp_path: Path, monkeypatch
):
    config, chat = make_config(tmp_path), FakeChat()
    handler = DailyAppendFileHandler(tmp_path / "logs", fixed_date=date(2026, 7, 29))
    monkeypatch.setattr(handler, "_append", lambda *_args: (_ for _ in ()).throw(PermissionError(32, "locked")))
    previous_handlers = list(runner_module.logger.handlers)
    previous_propagate = runner_module.logger.propagate
    runner_module.logger.handlers[:] = [handler]
    runner_module.logger.propagate = False
    runner_module.logger.setLevel(logging.INFO)
    try:
        result = run_daily(config, chat, date(2026, 7, 29))
    finally:
        runner_module.logger.handlers[:] = previous_handlers
        runner_module.logger.propagate = previous_propagate

    assert result.status is RunStatus.COMPLETED
    outcome = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(result.run_id)
    assert outcome and outcome.outcome is TaskOutcome.COMPLETED


def test_safe_failure_persists_retry_pending_then_recovers_without_a_final_failure(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="小明")]
    first = run_daily(config, FakeChat({"小明"}), date(2026, 7, 28), now=datetime(2026, 7, 28, 7, 30), trigger_source="scheduled")

    assert first.status is RunStatus.RETRY_PENDING
    pending = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(first.run_id)
    assert pending and pending.outcome is TaskOutcome.RETRY_PENDING

    recovered = run_daily(config, FakeChat(), date(2026, 7, 28), now=pending.next_attempt_at, trigger_source="retry")

    assert recovered.status is RunStatus.RECOVERED
    assert TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(first.run_id).outcome is TaskOutcome.RECOVERED


def test_exhausted_short_retries_remain_pending_for_scheduler_recovery(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="小明")]
    config.retry_count = 1
    first = run_daily(config, FakeChat({"小明"}), date(2026, 7, 28), now=datetime(2026, 7, 28, 7, 30))
    pending = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(first.run_id)

    later = run_daily(config, FakeChat({"小明"}), date(2026, 7, 28), now=pending.next_attempt_at, trigger_source="retry")

    assert later.status is RunStatus.RETRY_PENDING
    persisted = TaskOutcomeStore(
        tmp_path / "history" / "task-outcomes.json"
    ).get(first.run_id)
    assert persisted and persisted.next_attempt_at > pending.next_attempt_at


def test_possible_send_is_uncertain_and_never_retried(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="小明")]

    class UncertainChat:
        def send(self, _target, _message):
            return DeliveryResult(DeliveryStatus.CONFIRMATION_FAILED, send_attempts=1, error="not visible")

    result = run_daily(config, UncertainChat(), date(2026, 7, 28), now=datetime(2026, 7, 28, 7, 30))

    assert result.status is RunStatus.UNCERTAIN
    state = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(result.run_id)
    assert state and state.outcome is TaskOutcome.UNCERTAIN and state.next_attempt_at is None


def test_pre_send_browser_failure_is_persisted_as_retry_pending(tmp_path: Path):
    config = make_config(tmp_path)

    result = record_safe_pre_send_failure(config, "browser_startup_failed", now=datetime(2026, 7, 28, 7, 30))

    assert result.status is RunStatus.RETRY_PENDING
    assert TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(result.run_id).outcome is TaskOutcome.RETRY_PENDING


def test_fatal_chat_error_returns_blocked_result(tmp_path: Path):
    config = make_config(tmp_path)

    class BlockedChat:
        def send(self, _target, _message):
            raise FatalChatError("需要安全验证")

    result = run_daily(config, BlockedChat(), date(2026, 6, 18))

    assert result.status is RunStatus.RETRY_PENDING
    assert result.sent_count == 0
    assert result.error == "需要安全验证"


def test_duplicate_enabled_names_are_blocked_without_sending_ambiguous_targets(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="同名"), Target(name="同名"), Target(name="唯一")]
    chat = FakeChat()

    result = run_daily(config, chat, date(2026, 7, 14))

    assert result.status is RunStatus.UNCERTAIN
    assert [target for target, _ in chat.sent] == ["唯一"]
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["daily"]["2026-07-14"]["failures"]["同名"] == "blocked_ambiguous_target"


def test_unique_names_continue_to_send_normally(tmp_path: Path):
    config, chat = make_config(tmp_path), FakeChat()

    result = run_daily(config, chat, date(2026, 7, 14))

    assert result.status is RunStatus.COMPLETED
    assert [target for target, _ in chat.sent] == ["小明", "小红"]


def test_suffix_is_send_only_and_state_tracks_base_message(tmp_path: Path):
    config, chat = make_config(tmp_path), FakeChat()
    original = config.messages_file.read_text(encoding="utf-8")

    run_daily(config, chat, date(2026, 7, 4))

    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    base = state["daily"]["2026-07-04"]["message"]
    assert base in {"早安", "晚安"}
    assert chat.sent[0][1] == f"{base} —— gpt小助手"
    assert config.messages_file.read_text(encoding="utf-8") == original


def test_target_overrides_apply_pack_and_explicit_suffix_without_changing_global_defaults(tmp_path: Path):
    config, chat = make_config(tmp_path), FakeChat()
    pack_dir = tmp_path / "message-packs"
    pack_dir.mkdir()
    (pack_dir / "special.txt").write_text("专属问候\n", encoding="utf-8")
    (pack_dir / "index.json").write_text(
        '{"packs":[{"id":"special","name":"测试包","description":"","version":"1","file":"special.txt","count":1,"category":"test"}]}',
        encoding="utf-8",
    )
    config.targets[0].message_pack = "special"
    config.targets[0].suffix_mode = "custom"
    config.targets[0].suffix_override = "专属后缀"
    config.targets[1].suffix_mode = "disabled"

    result = run_daily(config, chat, date(2026, 7, 15))

    assert result.status is RunStatus.COMPLETED
    assert chat.sent[0] == ("小明", "专属问候 —— 专属后缀")
    assert chat.sent[1][1] in {"早安", "晚安"}
    assert config.message_suffix.text == "gpt小助手"


def test_target_pack_uses_installed_program_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config, chat = make_config(tmp_path), FakeChat()
    program_root = tmp_path / "program"
    pack_dir = program_root / "message-packs"
    pack_dir.mkdir(parents=True)
    (pack_dir / "special.txt").write_text("安装内置问候\n", encoding="utf-8")
    (pack_dir / "index.json").write_text(
        '{"packs":[{"id":"special","name":"测试包","description":"","version":"1","file":"special.txt","count":1,"category":"test"}]}',
        encoding="utf-8",
    )
    config.targets[0].message_pack = "special"
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(program_root))

    result = run_daily(config, chat, date(2026, 7, 15))

    assert result.status is RunStatus.COMPLETED
    assert chat.sent[0][1].startswith("安装内置问候")


def test_default_message_pack_uses_the_canonical_stable_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config, chat = make_config(tmp_path), FakeChat()
    program_root = tmp_path / "program"
    pack_dir = program_root / "message-packs"
    pack_dir.mkdir(parents=True)
    (pack_dir / "daily-greeting.txt").write_text("默认晨间问候\n", encoding="utf-8")
    (pack_dir / "index.json").write_text(
        '{"packs":[{"id":"daily-greeting","name":"日常问候","description":"","version":"1","file":"daily-greeting.txt","count":1,"category":"daily"}]}',
        encoding="utf-8",
    )
    config.default_message_pack = "daily-greeting"
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(program_root))

    result = run_daily(config, chat, date(2026, 7, 15))

    assert result.status is RunStatus.COMPLETED
    assert [message for _target, message in chat.sent] == [
        "默认晨间问候 —— gpt小助手",
        "默认晨间问候 —— gpt小助手",
    ]


def test_one_for_all_message_pack_reuses_today_then_advances_after_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = make_config(tmp_path)
    program_root = tmp_path / "program"
    pack_dir = program_root / "message-packs"
    pack_dir.mkdir(parents=True)
    (pack_dir / "daily-greeting.txt").write_text("早呀\n早上好\n", encoding="utf-8")
    (pack_dir / "index.json").write_text(
        '{"packs":[{"id":"daily-greeting","name":"日常问候","description":"","version":"1","file":"daily-greeting.txt","count":2,"category":"daily"}]}',
        encoding="utf-8",
    )
    config.default_message_pack = "daily-greeting"
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(program_root))

    class PartialChat:
        def __init__(self):
            self.sent = []

        def send(self, target, message):
            self.sent.append((target, message))
            if target == "小红":
                return DeliveryResult(
                    DeliveryStatus.SEND_FAILED,
                    send_attempts=0,
                    failure_stage="conversation_located",
                    reason_code="conversation_not_found",
                    error="target not found",
                )

    first_day_chat = PartialChat()
    first_day = run_daily(config, first_day_chat, date(2026, 8, 30))
    retry_chat = FakeChat()
    same_day_retry = run_daily(
        config,
        retry_chat,
        date(2026, 8, 30),
        trigger_source="retry",
        now=datetime(2026, 8, 30, 7, 31),
        target_ids={runner_module.target_identity(config.targets[1])},
    )
    second_day_chat = FakeChat()
    second_day = run_daily(config, second_day_chat, date(2026, 8, 31))

    assert first_day.status is RunStatus.RETRY_PENDING
    assert same_day_retry.status is RunStatus.RECOVERED
    first_day_messages = [message for _target, message in first_day_chat.sent]
    retry_messages = [message for _target, message in retry_chat.sent]
    second_day_messages = [message for _target, message in second_day_chat.sent]
    assert len(set(first_day_messages)) == 1
    assert retry_messages == [first_day_messages[0]]
    assert len(set(second_day_messages)) == 1
    assert first_day_messages[0] != second_day_messages[0]
    assert second_day.status is RunStatus.COMPLETED


def test_target_pack_uses_managed_catalog_without_changing_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config, chat = make_config(tmp_path), FakeChat()
    program_root = tmp_path / "program"
    pack_dir = program_root / "message-packs"
    pack_dir.mkdir(parents=True)
    (pack_dir / "special.txt").write_text("内置原文\n", encoding="utf-8")
    (pack_dir / "index.json").write_text(
        '{"packs":[{"id":"special","name":"测试包","description":"","version":"1","file":"special.txt","count":1,"category":"test"}]}',
        encoding="utf-8",
    )
    service = MessagePackService(program_root, tmp_path)
    entry = service.preview("special").entries[0]
    service.update_message(
        "special",
        entry.id,
        "用户编辑后的文案",
        service.catalog().revision,
    )
    config.targets[0].message_pack = "special"
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(program_root))

    result = run_daily(config, chat, date(2026, 7, 16))

    assert result.status is RunStatus.COMPLETED
    assert chat.sent[0][1].startswith("用户编辑后的文案")


def test_today_message_preview_matches_production_resolution_without_mutating_state_or_history(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="小明", stable_id="target-one", message_selection="per_friend")]
    before_messages = config.messages_file.read_bytes()

    preview = runner_module.preview_today_target_message(
        config,
        config.targets[0],
        date(2026, 7, 28),
    )

    assert preview.text.endswith(" —— gpt小助手")
    assert not config.state_file.exists()
    assert not (tmp_path / "history").exists()
    assert config.messages_file.read_bytes() == before_messages

    chat = FakeChat()
    run_daily(config, chat, date(2026, 7, 28))
    assert chat.sent == [("小明", preview.text)]


def test_today_message_preview_uses_target_pack_and_custom_suffix_without_persisting_plaintext(tmp_path: Path):
    config = make_config(tmp_path)
    pack_dir = tmp_path / "message-packs"
    pack_dir.mkdir()
    (pack_dir / "special.txt").write_text("专属问候\n", encoding="utf-8")
    (pack_dir / "index.json").write_text(
        '{"packs":[{"id":"special","name":"测试包","description":"","version":"1","file":"special.txt","count":1,"category":"test"}]}',
        encoding="utf-8",
    )
    config.targets = [
        Target(
            name="小明",
            stable_id="target-one",
            message_pack="special",
            suffix_mode="custom",
            suffix_override="专属后缀",
        )
    ]

    preview = runner_module.preview_today_target_message(
        config,
        config.targets[0],
        date(2026, 7, 28),
    )

    assert preview.text == "专属问候 —— 专属后缀"
    assert not config.state_file.exists()
    assert not (tmp_path / "history").exists()


def test_confirmation_failure_is_not_recorded_as_success_and_retry_does_not_duplicate(tmp_path: Path):
    config = make_config(tmp_path)
    config.retry_count = 2
    config.targets = [Target(name="小明")]

    class UnconfirmedChat:
        def __init__(self):
            self.calls = 0

        def send(self, _target, _message):
            self.calls += 1
            return DeliveryResult(DeliveryStatus.CONFIRMATION_FAILED, confirmation_attempts=3, error="not visible")

    first_chat = UnconfirmedChat()
    first = run_daily(config, first_chat, date(2026, 7, 13))

    class ExistingBubbleChat:
        def __init__(self):
            self.calls = 0

        def send(self, _target, _message):
            self.calls += 1
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=0)

    second_chat = ExistingBubbleChat()
    second = run_daily(config, second_chat, date(2026, 7, 13))

    assert first.status is RunStatus.UNCERTAIN
    assert first_chat.calls == 1
    assert second.status is RunStatus.UNCERTAIN
    assert second_chat.calls == 0


def test_targeted_retry_can_reopen_safe_target_without_retrying_uncertain_peer(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(name="不确定目标", stable_id="target-a", candidate_id="candidate-a"),
        Target(name="安全失败目标", stable_id="target-b", candidate_id="candidate-b"),
        Target(name="已确认目标", stable_id="target-c", candidate_id="candidate-c"),
    ]
    write_verified_retry_scope(
        tmp_path,
        ["candidate-a", "candidate-b", "candidate-c"],
    )
    bind_authoritatively(config)

    class FirstChat:
        def send(self, target, _message, **_kwargs):
            if target == "不确定目标":
                return DeliveryResult(
                    DeliveryStatus.CONFIRMATION_FAILED,
                    send_attempts=1,
                    error="confirmation missing",
                )
            if target == "安全失败目标":
                return DeliveryResult(
                    DeliveryStatus.SEND_FAILED,
                    send_attempts=0,
                    failure_stage="conversation_located",
                    reason_code="conversation_not_found",
                    error="target not found",
                )
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    first = run_daily(config, FirstChat(), date(2026, 7, 30))
    assert first.status is RunStatus.UNCERTAIN
    class RetryChat:
        def __init__(self):
            self.calls = []

        def send(self, target, _message, **_kwargs):
            self.calls.append(target)
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    retry_chat = RetryChat()
    retried = run_daily(
        config,
        retry_chat,
        date(2026, 7, 30),
        trigger_source="retry",
        target_ids={"target-b"},
    )

    assert retry_chat.calls == ["安全失败目标"]
    assert retried.status is RunStatus.UNCERTAIN
    assert retried.skipped_count == 2
    assert "target-b" not in retried.target_failures
    assert retried.target_failures["target-a"].uncertain_send is True


def test_targeted_retry_rechecks_current_binding_and_requires_reassociation(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a")
    ]
    write_verified_retry_scope(tmp_path, ["candidate-a"])
    bind_authoritatively(config)

    class FailingChat:
        def send(self, _target, _message, **_kwargs):
            return DeliveryResult(
                DeliveryStatus.SEND_FAILED,
                send_attempts=0,
                failure_stage="conversation_located",
                reason_code="conversation_not_found",
                error="target not found",
            )

    run_daily(config, FailingChat(), date(2026, 7, 30))
    config.targets[0].candidate_id = None

    class ForbiddenChat:
        def send(self, *_args, **_kwargs):
            raise AssertionError("invalid binding must stop before browser navigation")

    result = run_daily(
        config,
        ForbiddenChat(),
        date(2026, 7, 30),
        target_ids={"target-a"},
        trigger_source="retry",
    )

    assert result.status is RunStatus.FINAL_FAILED
    assert result.target_failures["target-a"].reason_code == "binding_missing"
    assert result.target_failures["target-a"].suggested_action == "reassociate"


def test_targeted_retry_accepts_current_platform_digest_scope(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.min_delay_seconds = 0
    config.max_delay_seconds = 0
    config.targets = [
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a")
    ]
    write_verified_retry_scope(
        tmp_path,
        ["candidate-a"],
        account_scope="a" * 64,
    )
    bind_authoritatively(config, account_scope="a" * 64)

    class PreSendFailure:
        def send(self, _target, _message, **_kwargs):
            return DeliveryResult(
                DeliveryStatus.SEND_FAILED,
                send_attempts=0,
                failure_stage="conversation_located",
                reason_code="conversation_not_found",
                error="target not found",
            )

    run_daily(config, PreSendFailure(), date(2026, 7, 30))

    class RetryChat:
        def __init__(self):
            self.calls = []

        def send(self, target, _message, **_kwargs):
            self.calls.append(target)
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = RetryChat()
    result = run_daily(
        config,
        chat,
        date(2026, 7, 30),
        target_ids={"target-a"},
        trigger_source="retry",
    )

    assert chat.calls == ["目标"]
    assert result.status is RunStatus.RECOVERED


def test_targeted_retry_stops_before_chat_for_genuine_account_mismatch(
    tmp_path: Path,
):
    config = make_config(tmp_path)
    config.targets = [
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a")
    ]
    write_verified_retry_scope(
        tmp_path,
        ["candidate-a"],
        account_scope="account-" + "b" * 24,
    )
    bind_authoritatively(config, account_scope="account-" + "b" * 24)

    class ForbiddenChat:
        def send(self, *_args, **_kwargs):
            raise AssertionError("account mismatch must stop before chat access")

    result = run_daily(
        config,
        ForbiddenChat(),
        date(2026, 7, 30),
        target_ids={"target-a"},
        trigger_source="retry",
    )

    detail = result.target_failures["target-a"]
    assert detail.reason_code == "account_scope_mismatch"
    assert detail.suggested_action == "switch_account"
    assert detail.diagnostic_details["account_comparison"] == (
        "binding_scope_matches_neither_current_namespace"
    )


def test_structured_history_contains_ids_not_friend_names(tmp_path: Path):
    config, chat = make_config(tmp_path), FakeChat()

    result = run_daily(config, chat, date(2026, 7, 13), trigger_source="scheduled")

    lines = (config.state_file.parent / "history" / "task-runs.jsonl").read_text(encoding="utf-8")
    assert result.run_id in lines
    assert '"trigger_source": "scheduled"' in lines
    assert "小明" not in lines
    assert "小红" not in lines

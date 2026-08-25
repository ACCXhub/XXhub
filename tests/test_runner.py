from datetime import date, datetime
import hashlib
import json
import logging
from pathlib import Path

import pytest

from autody.config import AppConfig, Target
from autody.chat import (
    DeliveryResult,
    DeliveryStatus,
    FatalChatError,
    conversation_candidate_id,
)
from autody import runner as runner_module
from autody.failures import failure_detail
from autody.logging_setup import DailyAppendFileHandler
from autody.message_packs import MessagePackService
from autody.runner import (
    RunStatus,
    automatic_daily_run_gate,
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
        "confirmation_results": {},
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


def test_modern_run_excludes_enabled_records_without_an_executable_binding(
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

    assert result.total_targets == 1
    assert result.sent_count == 1
    assert [name for name, _message in chat.sent] == ["当前有效目标"]


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
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1)

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
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1)

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
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1)

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
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1)

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

    assert result.total_targets == 8
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
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1)

    first = run_daily(config, FirstChat(), date(2026, 7, 30))
    assert first.status is RunStatus.UNCERTAIN
    class RetryChat:
        def __init__(self):
            self.calls = []

        def send(self, target, _message, **_kwargs):
            self.calls.append(target)
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1)

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
            return DeliveryResult(DeliveryStatus.CONFIRMED, send_attempts=1)

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

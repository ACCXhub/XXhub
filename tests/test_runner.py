from datetime import date
import json
from datetime import datetime
import logging
from pathlib import Path

from autody.config import AppConfig, Target
from autody.chat import DeliveryResult, DeliveryStatus, FatalChatError
from autody import runner as runner_module
from autody.logging_setup import DailyAppendFileHandler
from autody.runner import RunStatus, record_safe_pre_send_failure, run_daily
from autody.retry_state import TaskOutcome, TaskOutcomeStore


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
                "candidates": [
                    {
                        "candidate_id": candidate_id,
                        "display_name": "候选",
                        "avatar_status": "missing",
                        "discovered_at": "2026-07-30T07:15:00",
                        "match_status": "configured",
                        "presence_status": "current",
                    }
                    for candidate_id in candidate_ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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
        ("显示名称", "target-current", "candidate-current")
    ]


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


def test_exhausted_safe_retries_become_final_failed(tmp_path: Path):
    config = make_config(tmp_path)
    config.targets = [Target(name="小明")]
    config.retry_count = 1
    first = run_daily(config, FakeChat({"小明"}), date(2026, 7, 28), now=datetime(2026, 7, 28, 7, 30))
    pending = TaskOutcomeStore(tmp_path / "history" / "task-outcomes.json").get(first.run_id)

    final = run_daily(config, FakeChat({"小明"}), date(2026, 7, 28), now=pending.next_attempt_at, trigger_source="retry")

    assert final.status is RunStatus.FINAL_FAILED


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
        '{"packs":[{"id":"special","name":"测试包","description":"","version":"1","file":"special.txt","relative_url":"special.txt","raw_url":null,"count":1,"category":"test"}]}',
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
        '{"packs":[{"id":"special","name":"测试包","description":"","version":"1","file":"special.txt","relative_url":"special.txt","raw_url":null,"count":1,"category":"test"}]}',
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
    write_verified_retry_scope(
        tmp_path,
        ["candidate-a", "candidate-b", "candidate-c"],
    )

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
    write_verified_retry_scope(tmp_path, ["candidate-a"])
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
    write_verified_retry_scope(
        tmp_path,
        ["candidate-a"],
        account_scope="a" * 64,
    )

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

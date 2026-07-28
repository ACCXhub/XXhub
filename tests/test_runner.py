from datetime import date
import json
from datetime import datetime
from pathlib import Path

from autody.config import AppConfig, Target
from autody.chat import DeliveryResult, DeliveryStatus, FatalChatError
from autody import runner as runner_module
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


def test_structured_history_contains_ids_not_friend_names(tmp_path: Path):
    config, chat = make_config(tmp_path), FakeChat()

    result = run_daily(config, chat, date(2026, 7, 13), trigger_source="scheduled")

    lines = (config.state_file.parent / "history" / "task-runs.jsonl").read_text(encoding="utf-8")
    assert result.run_id in lines
    assert '"trigger_source": "scheduled"' in lines
    assert "小明" not in lines
    assert "小红" not in lines

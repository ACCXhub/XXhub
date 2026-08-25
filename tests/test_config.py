from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from autody.config import AppConfig, MessageSuffixStyle, load_config, save_config


def test_loads_valid_config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "targets:\n  - name: 小明\nmessages_file: messages.txt\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert [target.name for target in config.targets] == ["小明"]
    assert config.messages_file == tmp_path / "messages.txt"
    assert config.retry_count == 3
    assert config.message_suffix.enabled is True
    assert config.message_suffix.text == "gpt小助手"
    assert config.message_suffix.style is MessageSuffixStyle.DASH
    assert config.default_message_pack == "daily-greeting"
    assert config.min_delay_seconds == 0
    assert config.max_delay_seconds == 0


def test_allows_empty_targets_for_first_run_and_discovery(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("targets: []\n", encoding="utf-8")

    assert load_config(path).targets == []


def test_allows_duplicate_target_names_when_their_stable_ids_differ(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "targets:\n  - name: 小明\n    stable_id: candidate-a\n"
        "  - name: 小明\n    stable_id: candidate-b\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert [target.stable_id for target in config.targets] == ["candidate-a", "candidate-b"]


def test_rejects_duplicate_stable_target_ids(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "targets:\n  - name: 小明\n    stable_id: candidate-a\n"
        "  - name: 小红\n    stable_id: candidate-a\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)


def test_save_config_persists_suffix_and_removes_legacy_remote_pack_url(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "targets: []\nmessage_pack_index_url: https://example.com/index.json\n",
        encoding="utf-8",
    )
    config = load_config(path)
    config.message_suffix.text = "每日问候"
    config.message_suffix.style = MessageSuffixStyle.BRACKET

    save_config(path, config)
    restored = load_config(path)

    assert restored.message_suffix.text == "每日问候"
    assert restored.message_suffix.style is MessageSuffixStyle.BRACKET
    assert not hasattr(restored, "message_pack_index_url")
    assert "message_pack_index_url" not in path.read_text(encoding="utf-8")


def test_concurrent_config_updates_each_commit_one_complete_document(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("targets: []\n", encoding="utf-8")
    send_times = [f"07:{minute:02d}" for minute in range(32)]

    def write(send_time: str) -> None:
        save_config(path, AppConfig(daily_send_time=send_time))

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(write, send_times))

    assert load_config(path).daily_send_time in send_times
    assert list(tmp_path.glob("config.yaml.*.tmp")) == []


def test_new_recovery_and_log_defaults_keep_old_config_compatible(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("targets:\n  - name: 小明\n", encoding="utf-8")

    config = load_config(path)

    assert config.daily_send_time == "07:30"
    assert config.recovery_deadline == "23:59"
    assert config.mask_log_friend_names is True


def test_config_validates_practical_sending_defaults_and_delay_range():
    config = AppConfig(min_delay_seconds=1, max_delay_seconds=3, confirmation_timeout_ms=12_000)

    assert config.friend_order == "configured"
    assert config.message_selection == "one_for_all"
    with pytest.raises(ValidationError, match="minimum delay"):
        AppConfig(min_delay_seconds=4, max_delay_seconds=1)

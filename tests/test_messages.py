import random
from pathlib import Path

import pytest

from autody.config import MessageSuffixConfig, MessageSuffixStyle
from autody.messages import MessageRotation, format_message_with_suffix, read_messages
from autody.state import RotationState


def test_read_messages_ignores_blank_lines(tmp_path: Path):
    path = tmp_path / "messages.txt"
    path.write_text("早安\n\n今天也要开心\n", encoding="utf-8")
    assert read_messages(path) == ["早安", "今天也要开心"]


def test_empty_library_is_rejected(tmp_path: Path):
    path = tmp_path / "messages.txt"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        read_messages(path)


def test_empty_text_projection_can_be_loaded_for_a_sticker_only_global_pool(
    tmp_path: Path,
):
    path = tmp_path / "messages.txt"
    path.write_text("\n", encoding="utf-8")

    assert read_messages(path, allow_empty=True) == []


def test_rotation_uses_every_message_before_repeating():
    rotation = MessageRotation(random.Random(7))
    state = RotationState()
    values = []
    for _ in range(3):
        value = rotation.peek(["a", "b", "c"], state)
        values.append(value)
        rotation.consume(value, state)
    assert set(values) == {"a", "b", "c"}
    assert rotation.peek(["a", "b", "c"], state) in {"a", "b", "c"}


def test_rotation_recovers_when_library_changes():
    rotation = MessageRotation(random.Random(1))
    state = RotationState(order=["removed", "keep"], consumed=["done"])
    assert rotation.peek(["keep", "new"], state) in {"keep", "new"}
    assert "removed" not in state.order
    assert "done" not in state.consumed


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        (MessageSuffixStyle.DASH, "早安 —— gpt小助手"),
        (MessageSuffixStyle.BRACKET, "早安【gpt小助手】"),
        (MessageSuffixStyle.NEWLINE, "早安\ngpt小助手"),
        (MessageSuffixStyle.NONE, "早安 gpt小助手"),
    ],
)
def test_formats_enabled_suffix_styles(style: MessageSuffixStyle, expected: str):
    suffix = MessageSuffixConfig(enabled=True, text="gpt小助手", style=style)

    assert format_message_with_suffix("早安", suffix) == expected


def test_disabled_or_blank_suffix_keeps_base_message():
    assert format_message_with_suffix(
        "早安", MessageSuffixConfig(enabled=False, text="gpt小助手")
    ) == "早安"
    assert format_message_with_suffix(
        "早安", MessageSuffixConfig(enabled=True, text="  ")
    ) == "早安"

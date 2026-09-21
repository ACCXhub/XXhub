from pathlib import Path
import random

from autody.state import RotationState
from autody.config import MessageSuffixConfig, MessageSuffixStyle


def read_messages(path: Path, *, allow_empty: bool = False) -> list[str]:
    messages = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not messages and not allow_empty:
        raise ValueError("message library is empty")
    return list(dict.fromkeys(messages))


def format_message_with_suffix(message: str, suffix: MessageSuffixConfig) -> str:
    text = suffix.text.strip()
    if not suffix.enabled or not text:
        return message
    if suffix.style is MessageSuffixStyle.DASH:
        return f"{message} —— {text}"
    if suffix.style is MessageSuffixStyle.BRACKET:
        return f"{message}【{text}】"
    if suffix.style is MessageSuffixStyle.NEWLINE:
        return f"{message}\n{text}"
    return f"{message} {text}"


class MessageRotation:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.SystemRandom()

    def _sync(self, messages: list[str], state: RotationState) -> None:
        state.consumed = [item for item in state.consumed if item in messages]
        pending = [
            item
            for item in state.order
            if item in messages and item not in state.consumed
        ]
        new_items = [
            item
            for item in messages
            if item not in state.consumed and item not in pending
        ]
        self.rng.shuffle(new_items)
        pending.extend(new_items)
        if not pending:
            state.consumed = []
            pending = list(messages)
            self.rng.shuffle(pending)
        state.order = pending

    def peek(self, messages: list[str], state: RotationState) -> str:
        self._sync(messages, state)
        return state.order[0]

    def consume(self, message: str, state: RotationState) -> None:
        if state.order and state.order[0] == message:
            state.order.pop(0)
        if message not in state.consumed:
            state.consumed.append(message)

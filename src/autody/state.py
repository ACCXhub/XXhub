from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path


@dataclass
class RotationState:
    order: list[str] = field(default_factory=list)
    consumed: list[str] = field(default_factory=list)


@dataclass
class AppState:
    rotation: RotationState = field(default_factory=RotationState)
    daily: dict[str, dict] = field(default_factory=dict)


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AppState:
        if not self.path.exists():
            return AppState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("state root must be a mapping")
            rotation = raw.get("rotation", {})
            if not isinstance(rotation, dict):
                rotation = {}
            order = rotation.get("order", [])
            consumed = rotation.get("consumed", [])
            daily = raw.get("daily", {})
            if not isinstance(daily, dict):
                daily = {}
            return AppState(
                rotation=RotationState(
                    order=(
                        list(order)
                        if isinstance(order, list)
                        and all(isinstance(item, str) for item in order)
                        else []
                    ),
                    consumed=(
                        list(consumed)
                        if isinstance(consumed, list)
                        and all(isinstance(item, str) for item in consumed)
                        else []
                    ),
                ),
                daily={
                    str(day): dict(value)
                    for day, value in daily.items()
                    if isinstance(day, str) and isinstance(value, dict)
                },
            )
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise ValueError(f"state file is corrupt: {self.path}") from exc

    def save(self, state: AppState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

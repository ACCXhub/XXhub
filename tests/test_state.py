import hashlib
import json
from pathlib import Path

import pytest

from autody.state import AppState, StateStore


def test_state_round_trip_is_atomic(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    state = AppState()
    state.daily["2026-06-18"] = {"message": "早安", "succeeded": ["小明"]}
    store.save(state)
    assert store.load() == state
    assert not (tmp_path / "state.json.tmp").exists()


def test_corrupt_state_is_preserved_and_rejected(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="state file is corrupt"):
        StateStore(path).load()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_legacy_rotation_fields_are_normalized_without_rewriting(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "rotation": {
                    "next_index": 3,
                    "order": ["message-a"],
                    "consumed": ["message-b"],
                    "future_optional_field": {"enabled": True},
                },
                "daily": {
                    "2026-07-31": {
                        "message": "fixture",
                        "succeeded": [],
                        "future_optional_field": True,
                    },
                    "malformed-record": "not-a-mapping",
                },
                "future_top_level_field": True,
            }
        ),
        encoding="utf-8",
    )
    before = (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        path.stat().st_mtime_ns,
    )

    state = StateStore(path).load()

    assert state.rotation.order == ["message-a"]
    assert state.rotation.consumed == ["message-b"]
    assert state.daily == {
        "2026-07-31": {
            "message": "fixture",
            "succeeded": [],
            "future_optional_field": True,
        }
    }
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        path.stat().st_mtime_ns,
    ) == before

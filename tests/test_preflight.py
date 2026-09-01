from datetime import datetime
from pathlib import Path
import re

import pytest

from autody.chat import ChatPageConditionError, DOUYIN_SELECTORS
from autody.config import AppConfig, Target
from autody.preflight import (
    PlaywrightPreflightInspector,
    PreflightStore,
    ReadOnlyPage,
    Readiness,
    run_preflight,
)


class ReadOnlyInspector:
    """The fake intentionally has no send, fill, type, or press API."""
    def __init__(self, results: dict[str, Readiness]):
        self.results = results
        self.checked: list[str] = []

    def inspect_target(self, target: Target) -> Readiness:
        self.checked.append(target.stable_id or "")
        return self.results[target.stable_id or ""]


def config(tmp_path: Path, targets: list[Target]) -> AppConfig:
    return AppConfig(
        targets=targets,
        state_file=tmp_path / "data" / "state.json",
        lock_file=tmp_path / "data" / "locks" / "autody.lock",
    )


def test_preflight_records_ready_target_without_any_send_capability(tmp_path: Path):
    target = Target(name="测试目标", stable_id="target-a", candidate_id="candidate-a")
    inspector = ReadOnlyInspector({"target-a": Readiness.ready()})

    result = run_preflight(config(tmp_path, [target]), inspector, trigger_source="manual")

    assert result["global_status"] == "ready"
    assert result["ready_count"] == 1
    assert result["targets"][0]["target_status"] == "ready"
    assert inspector.checked == ["target-a"]
    assert "display_name" not in result["targets"][0]
    assert result["action_counts"] == {
        "fill": 0, "type": 0, "press": 0, "keyboard_input": 0,
        "send_click": 0, "send_message": 0,
    }


def test_preflight_blocks_ambiguous_names_and_continues_unique_targets(tmp_path: Path):
    targets = [
        Target(name="同名", stable_id="target-a", candidate_id="candidate-a"),
        Target(name="同名", stable_id="target-b", candidate_id="candidate-b"),
        Target(name="唯一", stable_id="target-c", candidate_id="candidate-c"),
    ]
    inspector = ReadOnlyInspector({"target-c": Readiness.ready()})

    result = run_preflight(config(tmp_path, targets), inspector)

    assert result["blocked_count"] == 2
    assert result["ready_count"] == 1
    assert {item["target_status"] for item in result["targets"]} == {"blocked_ambiguous_target", "ready"}
    assert inspector.checked == ["target-c"]


def test_preflight_cancellation_does_not_inspect_or_mutate_runtime_state(tmp_path: Path):
    target = Target(name="测试目标", stable_id="target-a", candidate_id="candidate-a")
    settings = config(tmp_path, [target])
    state = settings.state_file; state.parent.mkdir(parents=True); state.write_text('{"daily": {}}', encoding="utf-8")
    inspector = ReadOnlyInspector({"target-a": Readiness.ready()})

    result = run_preflight(settings, inspector, cancelled=lambda: True)

    assert result["cancelled"] is True
    assert inspector.checked == []
    assert state.read_text(encoding="utf-8") == '{"daily": {}}'


def test_preflight_reports_read_only_progress_without_exposing_names(tmp_path: Path):
    targets = [
        Target(name="甲", stable_id="target-1", candidate_id="candidate-1"),
        Target(name="乙", stable_id="target-2", candidate_id="candidate-2"),
    ]
    updates: list[dict] = []
    inspector = ReadOnlyInspector({"target-1": Readiness.ready(), "target-2": Readiness.ready()})

    run_preflight(config(tmp_path, targets), inspector, on_progress=updates.append)

    assert [update["completed_targets"] for update in updates] == [1, 2]
    assert all(update["total_targets"] == 2 for update in updates)
    assert "甲" not in str(updates)


def test_store_preserves_previous_latest_when_atomic_write_fails(tmp_path: Path, monkeypatch):
    store = PreflightStore(tmp_path / "data" / "preflight")
    original = {"check_id": "old", "completed_at": datetime.now().isoformat(), "targets": []}
    store.save(original)
    monkeypatch.setattr(store, "_atomic_write", lambda *_: (_ for _ in ()).throw(OSError("disk locked")))

    try:
        store.save({"check_id": "new", "targets": []})
    except OSError:
        pass

    assert store.load_latest()["check_id"] == "old"


def test_preflight_module_has_no_message_input_or_send_operations():
    source = (Path(__file__).parents[1] / "src" / "autody" / "preflight.py").read_text(encoding="utf-8")

    for forbidden in (r"\.fill\(", r"\.type\(", r"\.press\(", r"keyboard\.", r"send_message\s*\(", r"\.send\("):
        assert re.search(forbidden, source) is None


def test_read_only_page_exposes_no_input_or_generic_click_methods():
    class FakeLocator:
        def __init__(self): self.clicked = 0
        @property
        def first(self): return self
        def count(self): return 1
        def filter(self, **_kwargs): return self
        def evaluate(self, *_args): return None
        def wait_for(self, **_kwargs): return None
        def click(self): self.clicked += 1

    raw = FakeLocator()
    page = ReadOnlyPage(type("FakePage", (), {"locator": lambda *_args: raw, "wait_for_timeout": lambda *_args: None})())
    locator = page.locator(DOUYIN_SELECTORS.conversation).first

    assert not hasattr(locator, "fill")
    assert not hasattr(locator, "type")
    assert not hasattr(locator, "press")
    assert not hasattr(locator, "click")
    page.open_conversation(locator)
    assert raw.clicked == 1
    assert page.action_counts["send_click"] == 0
    with pytest.raises(TypeError):
        page.open_conversation(page.locator(".composer"))


def test_preflight_uses_the_chat_page_condition_for_explicit_risk_control():
    class Locator:
        def __init__(self, present: bool):
            self.present = present

        @property
        def first(self):
            return self

        def count(self):
            return int(self.present)

        def is_visible(self):
            return self.present

    class Page:
        def locator(self, selector, **_kwargs):
            return Locator(selector == DOUYIN_SELECTORS.risk_control_marker)

        def wait_for_timeout(self, _timeout):
            return None

    inspector = PlaywrightPreflightInspector(Page())

    with pytest.raises(ChatPageConditionError) as captured:
        inspector.chat_ready()

    assert captured.value.reason_code == "risk_control_required"
    assert captured.value.marker_id == "verification"

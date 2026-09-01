import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from autody.failures import failure_detail
from autody.failure_evidence import capture_failure_evidence


def test_failure_bundle_writes_one_sanitized_manifest_and_references_existing_screenshot(
    tmp_path: Path,
):
    artifact_dir = tmp_path / "artifacts"
    screenshot = artifact_dir / "confirmation-failed.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"sensitive-local-screenshot")
    detail = failure_detail(
        "confirmation_failed_uncertain",
        stage="confirmation_observed",
        run_id="run-opaque-123",
        target_stable_id="stable-target-should-not-persist",
        send_attempts=1,
    )

    captured = capture_failure_evidence(
        detail,
        artifact_dir,
        screenshot_path=screenshot,
        source_component="runner",
        structural_snapshot_provider=lambda: {
            "page": {
                "url": "https://www.douyin.com/chat?token=secret-token",
                "chat_body": "绝不应保存的聊天正文",
            },
            "conversation_list": {"present": True, "row_count": 3},
            "composer": {
                "present": True,
                "visible": True,
                "value": "绝不应保存的输入内容",
            },
            "markers": {"login": False, "verification": False},
            "cookies": "session-secret",
        },
    )

    manifest = json.loads(captured.manifest_path.read_text(encoding="utf-8"))
    serialized = json.dumps(manifest, ensure_ascii=False)

    assert captured.manifest_reference.startswith("failure-evidence/")
    assert manifest["run_id"] == "run-opaque-123"
    assert manifest["stage"] == "confirmation_observed"
    assert manifest["reason_code"] == "confirmation_failed_uncertain"
    assert manifest["artifacts"] == [
        {
            "kind": "screenshot",
            "reference": (captured.manifest_path.parent / "confirmation-failed.png")
            .relative_to(artifact_dir)
            .as_posix(),
        }
    ]
    assert manifest["structural_snapshot"] == {
        "page": {"url": "https://www.douyin.com/chat"},
        "conversation_list": {"present": True, "row_count": 3},
        "composer": {"present": True, "visible": True},
        "markers": {"login": False, "verification": False},
    }
    assert manifest["trace"] == {"enabled": False, "reference": None}
    assert "绝不应保存" not in serialized
    assert "secret-token" not in serialized
    assert "stable-target-should-not-persist" not in serialized
    assert "session-secret" not in serialized
    assert not screenshot.exists()
    assert (captured.manifest_path.parent / "confirmation-failed.png").exists()


def test_failure_bundle_retention_removes_only_expired_bundle_directories(
    tmp_path: Path,
):
    artifact_dir = tmp_path / "artifacts"
    expired = artifact_dir / "failure-evidence" / "expired-bundle"
    expired.mkdir(parents=True)
    (expired / "manifest.json").write_text("{}", encoding="utf-8")
    (expired / "screenshot.png").write_bytes(b"sensitive")
    expired_timestamp = datetime(2026, 8, 1, 8, 0).timestamp()
    os.utime(expired, (expired_timestamp, expired_timestamp))
    current = artifact_dir / "failure-evidence" / "current-bundle"
    current.mkdir()
    (current / "manifest.json").write_text("{}", encoding="utf-8")

    captured = capture_failure_evidence(
        failure_detail(
            "conversation_not_found",
            stage="conversation_located",
            run_id="run-retention",
        ),
        artifact_dir,
        source_component="runner",
        now=datetime(2026, 9, 1, 8, 0),
    )

    assert not expired.exists()
    assert current.exists()
    assert captured.manifest_path.exists()


def test_manifest_write_failure_restores_the_existing_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact_dir = tmp_path / "artifacts"
    screenshot = artifact_dir / "send-error.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"local-only")
    original_write_text = Path.write_text

    def fail_manifest_write(path: Path, *args, **kwargs):
        if path.name == "manifest.tmp":
            raise OSError("disk unavailable")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_manifest_write)

    with pytest.raises(OSError, match="disk unavailable"):
        capture_failure_evidence(
            failure_detail(
                "conversation_not_found",
                stage="conversation_located",
                run_id="run-restore",
            ),
            artifact_dir,
            screenshot_path=screenshot,
            source_component="runner",
        )

    assert screenshot.exists()

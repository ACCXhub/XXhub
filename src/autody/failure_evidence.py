"""Local, privacy-bounded evidence manifests for existing failures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


_SCHEMA_VERSION = 1
_EVIDENCE_DIRECTORY = "failure-evidence"
_RETENTION_DAYS = 14


@dataclass(frozen=True)
class CapturedFailureEvidence:
    manifest_path: Path
    manifest_reference: str
    capture_warnings: tuple[str, ...] = ()


def _safe_page_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def _safe_boolean(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _safe_structure(snapshot: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Keep only the explicit structural allowlist; never serialize DOM text."""
    if not isinstance(snapshot, Mapping):
        return {}

    result: dict[str, dict[str, Any]] = {}
    page = snapshot.get("page")
    if isinstance(page, Mapping):
        url = _safe_page_url(page.get("url"))
        if url:
            result["page"] = {"url": url}

    sections = {
        "conversation_list": (
            "present", "visible", "row_count", "scroll_top", "scroll_height",
            "client_height", "at_origin",
        ),
        "conversation": (
            "visible_conversation_present", "header_present", "identity_match",
            "expected_locator_present",
        ),
        "composer": ("present", "visible", "enabled", "contenteditable"),
        "history": (
            "present", "visible", "outgoing_count", "scroll_top", "scroll_height",
            "client_height", "date_evidence_available",
        ),
        "markers": ("login", "verification"),
    }
    count_fields = {
        "row_count", "scroll_top", "scroll_height", "client_height", "outgoing_count",
    }
    for name, fields in sections.items():
        source = snapshot.get(name)
        if not isinstance(source, Mapping):
            continue
        sanitized: dict[str, Any] = {}
        for field in fields:
            value = _safe_count(source.get(field)) if field in count_fields else _safe_boolean(source.get(field))
            if value is not None:
                sanitized[field] = value
        if sanitized:
            result[name] = sanitized
    return result


def _artifact_reference(
    artifact_dir: Path,
    bundle: Path,
    screenshot_path: Path | None,
) -> tuple[dict[str, str] | None, str | None]:
    if screenshot_path is None:
        return None, None
    try:
        reference = screenshot_path.resolve().relative_to(artifact_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return None, "screenshot_outside_artifact_root"
    if not screenshot_path.is_file():
        return None, "screenshot_missing"
    destination = bundle / screenshot_path.name
    try:
        screenshot_path.replace(destination)
    except OSError:
        return None, "screenshot_move_failed"
    return {
        "kind": "screenshot",
        "reference": destination.relative_to(artifact_dir).as_posix(),
    }, None


def _cleanup_expired_bundles(evidence_root: Path, *, now: datetime) -> None:
    """Delete only aged directories owned by this evidence module."""
    if not evidence_root.is_dir():
        return
    cutoff = now - timedelta(days=_RETENTION_DAYS)
    for bundle in evidence_root.iterdir():
        if not bundle.is_dir() or bundle.is_symlink():
            continue
        try:
            modified = datetime.fromtimestamp(bundle.stat().st_mtime)
            if modified >= cutoff:
                continue
            if evidence_root.resolve() not in bundle.resolve().parents:
                continue
            shutil.rmtree(bundle)
        except OSError:
            continue


def capture_failure_evidence(
    detail,
    artifact_dir: Path,
    *,
    screenshot_path: Path | None = None,
    source_component: str,
    structural_snapshot_provider: Callable[[], Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> CapturedFailureEvidence:
    """Write one manifest without allowing diagnostic data to affect a failure."""
    captured_at = now or datetime.now()
    artifact_dir = artifact_dir.resolve()
    evidence_root = artifact_dir / _EVIDENCE_DIRECTORY
    evidence_root.mkdir(parents=True, exist_ok=True)
    _cleanup_expired_bundles(evidence_root, now=captured_at)

    warnings: list[str] = []
    snapshot: Mapping[str, Any] | None = None
    if structural_snapshot_provider is not None:
        try:
            candidate = structural_snapshot_provider()
            snapshot = candidate if isinstance(candidate, Mapping) else None
            if candidate is not None and snapshot is None:
                warnings.append("structural_snapshot_invalid")
        except Exception:
            warnings.append("structural_snapshot_unavailable")

    bundle = evidence_root / f"{captured_at:%Y%m%d-%H%M%S}-{uuid4().hex[:12]}"
    bundle.mkdir()
    screenshot, screenshot_warning = _artifact_reference(
        artifact_dir, bundle, screenshot_path
    )
    if screenshot_warning:
        warnings.append(screenshot_warning)

    manifest_path = bundle / "manifest.json"
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": detail.run_id,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "stage": detail.stage,
        "reason_code": detail.reason_code,
        "failure_category": detail.category,
        "source_component": source_component,
        "artifacts": [screenshot] if screenshot else [],
        "structural_snapshot": _safe_structure(snapshot),
        "capture_warnings": warnings,
        "trace": {"enabled": False, "reference": None},
    }
    temporary = manifest_path.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(manifest_path)
    except OSError:
        if screenshot_path is not None and screenshot is not None:
            try:
                moved_screenshot = artifact_dir / screenshot["reference"]
                if moved_screenshot.is_file():
                    moved_screenshot.replace(screenshot_path)
            except OSError:
                pass
        raise
    return CapturedFailureEvidence(
        manifest_path=manifest_path,
        manifest_reference=manifest_path.relative_to(artifact_dir).as_posix(),
        capture_warnings=tuple(warnings),
    )

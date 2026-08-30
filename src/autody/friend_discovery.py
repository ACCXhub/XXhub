from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
from typing import Callable
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

from PIL import Image, UnidentifiedImageError

from autody.chat import (
    CONVERSATION_ID_ATTRIBUTES,
    ChatSelectors,
    conversation_candidate_id,
    conversation_row_identity,
    conversation_row_locator,
    normalized_avatar_source,
    opaque_conversation_identity,
)
from autody.account_profile import load_account_profile
from autody.config import AppConfig, Target
from autody.failures import failure_detail


_SAFE_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
DISCOVERY_CACHE_TTL = timedelta(hours=24)
AVATAR_CACHE_TTL = timedelta(days=7)
_VIRTUAL_LIST_GROWTH_WINDOW_MS = 4_000
_VIRTUAL_LIST_STABLE_MS = 750
_VIRTUAL_LIST_POLL_MS = 250
_VIRTUAL_LIST_BOTTOM_GROWTH_MS = 3_000
_AVATAR_FETCH_WORKERS = 4
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FriendCandidate:
    candidate_id: str
    display_name: str
    avatar_cache_path: str | None
    avatar_status: str
    discovered_at: str
    match_status: str
    configured_target_id: str | None = None
    configured_enabled: bool | None = None
    avatar_cache_key: str | None = None
    avatar_updated_at: str | None = None
    first_discovered_at: str | None = None
    last_seen_at: str | None = None
    last_scan_id: str | None = None
    presence_status: str = "current"
    identity_key: str | None = None
    identity_source: str | None = None
    conversation_id: str | None = None

    @property
    def name(self) -> str:
        """Compatibility alias for the original discovery payload."""
        return self.display_name

    @property
    def already_configured(self) -> bool:
        return self.match_status == "configured"


@dataclass(frozen=True)
class FriendDiscoveryResult:
    scanned_at: str | None
    candidates: list[FriendCandidate]
    output_path: Path
    config_changed: bool = False
    scan_id: str | None = None
    last_result: dict[str, object] = field(default_factory=dict)
    account_scope: str | None = None
    target_refresh: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AvatarRefreshResult:
    updated: int
    missing: int
    ambiguous: int
    config_changed: bool


class ScanProgress:
    """Small on-disk status record for the existing dashboard polling loop."""

    _LABELS = {
        "waiting_browser": "正在等待浏览器",
        "launching_chromium": "正在启动浏览器",
        "loading_chat_page": "正在加载抖音聊天页",
        "locating_chat_list": "正在读取聊天列表",
        "scanning_rows": "正在读取聊天列表",
        "updating_avatars": "正在更新头像",
        "writing_cache": "正在保存候选缓存",
        "releasing_browser_lock": "正在释放浏览器锁",
    }

    def __init__(self, output_path: Path, monotonic: Callable[[], float] = time.monotonic):
        self.path = output_path.with_name("friend_scan_progress.json")
        self.monotonic = monotonic
        self.started = monotonic()
        self.stage_started = self.started
        self.stage = "waiting_browser"
        self.timings: dict[str, float] = {}
        self.current = 0
        self.total: int | None = None
        self._write(running=True)

    def update(self, stage: str, current: int = 0, total: int | None = None) -> None:
        if stage == self.stage:
            self.current, self.total = current, total
            self._write(running=True)
            return
        now = self.monotonic()
        self.timings[self.stage] = self.timings.get(self.stage, 0.0) + now - self.stage_started
        self.stage, self.stage_started = stage, now
        self.current, self.total = current, total
        self._write(running=True)

    def finish(self, status: str, **details: object) -> dict[str, object]:
        now = self.monotonic()
        self.timings[self.stage] = self.timings.get(self.stage, 0.0) + now - self.stage_started
        payload: dict[str, object] = {
            "running": False,
            "stage": self.stage,
            "message": self._LABELS.get(self.stage, self.stage),
            "status": status,
            "current": self.current,
            "total": self.total,
            "timings": {key: round(value, 3) for key, value in self.timings.items()},
            "total_seconds": round(now - self.started, 3),
            **details,
        }
        self._write_payload(payload)
        return payload

    def _write(self, *, running: bool) -> None:
        self._write_payload({
            "running": running,
            "stage": self.stage,
            "message": self._LABELS.get(self.stage, self.stage),
            "current": self.current,
            "total": self.total,
            "timings": {key: round(value, 3) for key, value in self.timings.items()},
        })

    def _write_payload(self, payload: dict[str, object]) -> None:
        _write_discovery_payload(self.path, payload)


@dataclass(frozen=True)
class _ScannedItem:
    name: str
    temporary_avatar: Path | None
    avatar_hash: str | None
    identity_key: str | None
    identity_source: str | None
    conversation_id: str | None
    row_index: int
    capture_attempted: bool = True
    avatar_capture_failed: bool = False
    association_uncertain: bool = False
    avatar_source: str | None = None


@dataclass(frozen=True)
class _VisibleRowSnapshot:
    """Immutable browser-row data safe to use after the virtual list moves."""

    name: str
    identity_key: str | None
    identity_source: str | None
    conversation_id: str | None
    avatar_source: str | None
    row_index: int


_VISIBLE_ROWS_SNAPSHOT_SCRIPT = r"""
(rows, options) => {
    const conversationFromFiber = start => {
        let fiber = start;
        for (let level = 0; fiber && level < 18; level += 1, fiber = fiber.return) {
            for (const props of [fiber.memoizedProps, fiber.pendingProps]) {
                const candidates = [
                    props?.conversation,
                    props?.children?.props?.conversation,
                    props?.children?.[0]?.props?.children?.props?.conversation,
                ];
                const conversation = candidates.find(value => value != null);
                if (conversation) return conversation;
            }
        }
        return null;
    };
    const runtimeFields = element => {
        const nodes = [element, ...element.querySelectorAll('*')];
        for (const node of nodes) {
            const fiberKey = Object.getOwnPropertyNames(node).find(
                key => key.startsWith('__reactFiber$')
            );
            const conversation = fiberKey
                ? conversationFromFiber(node[fiberKey])
                : null;
            if (!conversation) continue;
            const text = value => value == null ? null : String(value);
            return {
                participant: text(conversation.toParticipantSecUserId),
                conversation: text(conversation.id || conversation.shortId),
            };
        }
        return { participant: null, conversation: null };
    };
    return rows.map((row, rowIndex) => {
        const nameNode = row.querySelector(options.nameSelector);
        const avatar = row.querySelector('img');
        const attributes = {};
        for (const attribute of options.identityAttributes) {
            attributes[attribute] = row.getAttribute(attribute);
        }
        return {
            name: (nameNode?.innerText || '').trim(),
            avatarSource: avatar?.getAttribute('src') || null,
            attributes,
            rowIndex,
            ...runtimeFields(row),
        };
    });
}
"""


def _new_local_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _ensure_target_id(target: Target) -> bool:
    if target.stable_id and _SAFE_LOCAL_ID.fullmatch(target.stable_id):
        return False
    target.stable_id = _new_local_id("friend")
    return True


def _avatar_path(cache_dir: Path, identifier: str) -> Path:
    return cache_dir / f"{identifier}.png"


def _capture_temporary_avatar(
    page, item, cache_dir: Path, timeout_ms: int
) -> tuple[Path | None, str | None, bool]:
    """Capture one row avatar, preferring its browser-context image response."""
    temporary = cache_dir / f".scan-{uuid.uuid4().hex}.png"
    try:
        avatar = item.locator("img").first
        source = avatar.get_attribute("src")
        request = getattr(getattr(page, "context", None), "request", None)
        if source and request is not None:
            try:
                response = request.get(source, timeout=timeout_ms)
                content = response.body() if response.ok else b""
                with Image.open(BytesIO(content)) as image:
                    image.save(temporary, format="PNG")
                saved = temporary.read_bytes()
                if saved:
                    return temporary, hashlib.sha256(saved).hexdigest(), False
            except (OSError, UnidentifiedImageError, ValueError):
                temporary.unlink(missing_ok=True)
            except Exception:
                temporary.unlink(missing_ok=True)
        try:
            avatar.screenshot(path=str(temporary), timeout=timeout_ms)
        except TypeError:  # test doubles and older Playwright bindings
            avatar.screenshot(path=str(temporary))
        content = temporary.read_bytes()
        if not content:
            raise OSError("captured avatar was empty")
        return temporary, hashlib.sha256(content).hexdigest(), False
    except Exception:
        temporary.unlink(missing_ok=True)
        return None, None, True


def _opaque_identity(source: str, value: str) -> str:
    return opaque_conversation_identity(source, value)


def _row_identity_hint(item) -> tuple[str | None, str | None]:
    return conversation_row_identity(item)


def _candidate_id(identity_key: str | None) -> str:
    return conversation_candidate_id(identity_key) or _new_local_id("candidate")


def _snapshot_visible_rows(
    conversations,
    selectors: ChatSelectors,
) -> list[_VisibleRowSnapshot] | None:
    """Snapshot a viewport in one evaluation, with no retained row Locators.

    Test doubles and older locator adapters may not support ``evaluate_all``;
    callers retain the conservative one-row fallback for those cases.
    """
    try:
        payload = conversations.evaluate_all(
            _VISIBLE_ROWS_SNAPSHOT_SCRIPT,
            {
                "nameSelector": selectors.conversation_name,
                "identityAttributes": list(CONVERSATION_ID_ATTRIBUTES),
            },
        )
    except (AttributeError, TypeError):
        return None
    except Exception:
        logger.debug("批量会话行快照不可用，回退逐行安全读取。", exc_info=True)
        return None
    if not isinstance(payload, list):
        return None

    snapshots: list[_VisibleRowSnapshot] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        avatar_source = raw.get("avatarSource")
        avatar_source = str(avatar_source) if avatar_source else None
        participant = raw.get("participant")
        conversation = raw.get("conversation")
        attributes = raw.get("attributes")
        identity_key: str | None = None
        identity_source: str | None = None
        if participant:
            identity_key = _opaque_identity("participant", str(participant))
            identity_source = "participant_sec_user_id"
        elif isinstance(attributes, dict):
            for attribute in CONVERSATION_ID_ATTRIBUTES:
                value = attributes.get(attribute)
                if value:
                    identity_key = _opaque_identity("row", str(value))
                    identity_source = "row_attribute"
                    break
        if identity_key is None and avatar_source:
            identity_key = _opaque_identity(
                "avatar", normalized_avatar_source(avatar_source)
            )
            identity_source = "avatar_source"
        conversation_id = (
            conversation_candidate_id(
                _opaque_identity("conversation", str(conversation))
            )
            if conversation
            else conversation_candidate_id(identity_key)
        )
        try:
            row_index = int(raw.get("rowIndex", index))
        except (TypeError, ValueError):
            row_index = index
        snapshots.append(
            _VisibleRowSnapshot(
                name=name,
                identity_key=identity_key,
                identity_source=identity_source,
                conversation_id=conversation_id,
                avatar_source=avatar_source,
                row_index=row_index,
            )
        )
    return snapshots


def _fetch_avatar_from_source(
    source: str,
    cache_dir: Path,
    timeout_ms: int,
) -> tuple[Path | None, str | None, bool]:
    """Fetch one immutable avatar URL without accessing Playwright objects."""
    try:
        parsed = urlsplit(source)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("unsupported avatar URL scheme")
        request = Request(source, headers={"User-Agent": "AutoDy/1"})
        with urlopen(request, timeout=max(0.1, timeout_ms / 1000)) as response:
            content = response.read()
        temporary = cache_dir / f".scan-{uuid.uuid4().hex}.png"
        with Image.open(BytesIO(content)) as image:
            image.save(temporary, format="PNG")
        saved = temporary.read_bytes()
        if not saved:
            raise OSError("downloaded avatar was empty")
        return temporary, hashlib.sha256(saved).hexdigest(), False
    except (OSError, URLError, UnidentifiedImageError, ValueError):
        return None, None, True
    except Exception:
        return None, None, True


def _scroll_metrics(scrollable) -> dict[str, int | float]:
    return scrollable.first.evaluate(
        """el => ({
            before: el.scrollTop,
            maximum: Math.max(0, el.scrollHeight - el.clientHeight),
            step: Math.max(200, Math.floor(el.clientHeight * 0.7))
        })"""
    )


def _wait_for_initial_virtual_list_expansion(
    page,
    scrollable,
    *,
    timeout_ms: int,
) -> None:
    """Let Douyin finish replacing its temporary short virtual-list model."""
    maximum = _scroll_metrics(scrollable)["maximum"]
    elapsed_ms = 0
    last_growth_ms: int | None = None
    hard_limit_ms = min(
        timeout_ms,
        _VIRTUAL_LIST_GROWTH_WINDOW_MS + _VIRTUAL_LIST_STABLE_MS,
    )

    while elapsed_ms < hard_limit_ms:
        if last_growth_ms is None and elapsed_ms >= _VIRTUAL_LIST_GROWTH_WINDOW_MS:
            break
        if (
            last_growth_ms is not None
            and elapsed_ms - last_growth_ms >= _VIRTUAL_LIST_STABLE_MS
        ):
            break

        wait_ms = min(_VIRTUAL_LIST_POLL_MS, hard_limit_ms - elapsed_ms)
        page.wait_for_timeout(wait_ms)
        elapsed_ms += wait_ms

        current_maximum = _scroll_metrics(scrollable)["maximum"]
        if current_maximum > maximum:
            maximum = current_maximum
            last_growth_ms = elapsed_ms


def _wait_for_virtual_list_growth_at_bottom(
    page,
    scrollable,
    *,
    previous_maximum: int | float,
    timeout_ms: int,
) -> bool:
    """Wait briefly for Douyin to append the next lazy-loaded page."""
    elapsed_ms = 0
    hard_limit_ms = min(timeout_ms, _VIRTUAL_LIST_BOTTOM_GROWTH_MS)
    while elapsed_ms < hard_limit_ms:
        wait_ms = min(_VIRTUAL_LIST_POLL_MS, hard_limit_ms - elapsed_ms)
        page.wait_for_timeout(wait_ms)
        elapsed_ms += wait_ms
        if _scroll_metrics(scrollable)["maximum"] > previous_maximum:
            return True
    return False


def _scan_items(
    page,
    selectors: ChatSelectors,
    cache_dir: Path,
    max_scrolls: int = 20,
    capture_avatar: Callable[[str | None], bool] | None = None,
    avatar_timeout_ms: int = 2_000,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    progress: Callable[[str, int, int | None], None] | None = None,
    initial_virtual_list_timeout_ms: int = (
        _VIRTUAL_LIST_GROWTH_WINDOW_MS + _VIRTUAL_LIST_STABLE_MS
    ),
    stop_after_identities: set[str] | None = None,
    wait_for_bottom_growth: bool = True,
) -> tuple[list[_ScannedItem], bool, bool, str, int]:
    """Read visible conversation rows while keeping avatar failures non-fatal."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    conversations = page.locator(selectors.conversation)
    scrollable = page.locator(selectors.conversation_list)
    items: list[_ScannedItem] = []
    seen: set[str] = set()
    missing_counts: Counter[str] = Counter()
    rows_inspected = 0
    avatar_futures: list[tuple[int, Future[tuple[Path | None, str | None, bool]]]] = []
    avatar_executor = ThreadPoolExecutor(max_workers=_AVATAR_FETCH_WORKERS)

    partial_timeout = False
    completed_bottom_reached = False
    ended_by = "scroll_limit"
    try:
        scrollable.first.wait_for(state="visible", timeout=avatar_timeout_ms)
    except AttributeError:  # test doubles do not implement wait_for
        pass
    if scrollable.count():
        scrollable.first.evaluate(
            "(el) => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }"
        )
        _wait_for_initial_virtual_list_expansion(
            page,
            scrollable,
            timeout_ms=initial_virtual_list_timeout_ms,
        )
    if progress:
        progress("locating_chat_list", 0, None)
    try:
        for _ in range(max_scrolls + 1):
            if deadline is not None and monotonic() >= deadline:
                partial_timeout = True
                ended_by = "timeout"
                break
            if progress:
                progress("scanning_rows", rows_inspected, None)
            visible_snapshots = _snapshot_visible_rows(conversations, selectors)
            if visible_snapshots is not None:
                rows_inspected += len(visible_snapshots)
                for snapshot in visible_snapshots:
                    identity_key = snapshot.identity_key
                    identity_source = snapshot.identity_source
                    if identity_key is None:
                        missing_counts[snapshot.name] += 1
                        identity_key = _opaque_identity(
                            "unresolved",
                            f"{snapshot.name}\0{missing_counts[snapshot.name]}\0{uuid.uuid4().hex}",
                        )
                        identity_source = "unresolved"
                    if identity_key in seen:
                        continue
                    seen.add(identity_key)
                    should_capture = capture_avatar is None or capture_avatar(identity_key)
                    item_index = len(items)
                    items.append(
                        _ScannedItem(
                            snapshot.name,
                            None,
                            None,
                            identity_key,
                            identity_source,
                            snapshot.conversation_id,
                            snapshot.row_index,
                            should_capture,
                            should_capture and not snapshot.avatar_source,
                            False,
                            snapshot.avatar_source,
                        )
                    )
                    if should_capture and snapshot.avatar_source:
                        if progress:
                            progress("updating_avatars", rows_inspected, None)
                        avatar_futures.append(
                            (
                                item_index,
                                avatar_executor.submit(
                                    _fetch_avatar_from_source,
                                    snapshot.avatar_source,
                                    cache_dir,
                                    avatar_timeout_ms,
                                ),
                            )
                        )

            for index in range(conversations.count() if visible_snapshots is None else 0):
                rows_inspected += 1
                # Locators follow a virtualized DOM node.  Snapshot and capture a
                # single row in one operation; never retain row locators while
                # collecting names for the rest of the viewport.
                accepted = None
                last_snapshot: tuple[
                    str, str | None, str | None, str | None
                ] | None = None
                for _attempt in range(2):
                    item = conversations.nth(index)
                    try:
                        name = item.locator(selectors.conversation_name).inner_text().strip()
                    except Exception:
                        break
                    if not name:
                        break
                    identity_key, identity_source = _row_identity_hint(item)
                    conversation_id = conversation_row_locator(item)
                    last_snapshot = (
                        name,
                        identity_key,
                        identity_source,
                        conversation_id,
                    )
                    should_capture = capture_avatar is None or capture_avatar(identity_key)
                    if should_capture:
                        if progress:
                            progress("updating_avatars", rows_inspected, None)
                        remaining_avatar_timeout = avatar_timeout_ms
                        if deadline is not None:
                            remaining_avatar_timeout = max(
                                1, min(avatar_timeout_ms, int((deadline - monotonic()) * 1000))
                            )
                            if remaining_avatar_timeout <= 1:
                                partial_timeout = True
                                ended_by = "timeout"
                                break
                        temporary, avatar_hash, avatar_capture_failed = _capture_temporary_avatar(
                            page, item, cache_dir, remaining_avatar_timeout
                        )
                    else:
                        temporary, avatar_hash, avatar_capture_failed = None, None, False
                    try:
                        verified_name = item.locator(selectors.conversation_name).inner_text().strip()
                    except Exception:
                        verified_name = ""
                    verified_identity_key, _ = _row_identity_hint(item)
                    verified_conversation_id = conversation_row_locator(item)
                    if (
                        name == verified_name
                        and identity_key == verified_identity_key
                        and conversation_id == verified_conversation_id
                    ):
                        accepted = (
                            name,
                            identity_key,
                            identity_source,
                            conversation_id,
                            temporary,
                            avatar_hash,
                            avatar_capture_failed,
                            should_capture,
                            False,
                        )
                        break
                    if temporary:
                        temporary.unlink(missing_ok=True)
                if partial_timeout:
                    break
                if accepted is None:
                    # A reused DOM node is unsafe evidence.  Keep a current row
                    # record with a fallback avatar rather than showing an image
                    # captured for a different nickname.
                    if last_snapshot is None:
                        continue
                    accepted = (*last_snapshot, None, None, True, True, True)
                (
                    name,
                    identity_key,
                    identity_source,
                    conversation_id,
                    temporary,
                    avatar_hash,
                    avatar_capture_failed,
                    should_capture,
                    association_uncertain,
                ) = accepted
                if identity_key is None and avatar_hash is not None:
                    identity_key = _opaque_identity("pixels", f"{name}\0{avatar_hash}")
                    identity_source = "avatar_pixels"
                if identity_key is None:
                    # There is no safe durable identity, so keep this row distinct
                    # instead of merging it with another same-name conversation.
                    missing_counts[name] += 1
                    identity_key = _opaque_identity(
                        "unresolved", f"{name}\0{missing_counts[name]}\0{uuid.uuid4().hex}"
                    )
                    identity_source = "unresolved"
                if deadline is not None and monotonic() >= deadline:
                    if temporary:
                        temporary.unlink(missing_ok=True)
                    partial_timeout = True
                    ended_by = "timeout"
                    break
                if identity_key in seen:
                    if temporary:
                        temporary.unlink(missing_ok=True)
                    continue
                seen.add(identity_key)
                items.append(
                    _ScannedItem(
                        name,
                        temporary,
                        avatar_hash,
                        identity_key,
                        identity_source,
                        conversation_id,
                        index,
                        should_capture,
                        avatar_capture_failed,
                        association_uncertain,
                    )
                )

            if partial_timeout:
                break

            if stop_after_identities and stop_after_identities.issubset(seen):
                ended_by = "targets_found"
                break

            if not scrollable.count():
                completed_bottom_reached = True
                ended_by = "bottom_reached"
                break
            metrics = _scroll_metrics(scrollable)
            if metrics["before"] >= metrics["maximum"]:
                if not wait_for_bottom_growth:
                    completed_bottom_reached = True
                    ended_by = "bottom_reached"
                    break
                remaining_ms = (
                    max(0, int((deadline - monotonic()) * 1000))
                    if deadline is not None
                    else _VIRTUAL_LIST_BOTTOM_GROWTH_MS
                )
                if _wait_for_virtual_list_growth_at_bottom(
                    page,
                    scrollable,
                    previous_maximum=metrics["maximum"],
                    timeout_ms=remaining_ms,
                ):
                    continue
                completed_bottom_reached = True
                ended_by = "bottom_reached"
                break
            scrollable.first.evaluate(
                "(el, step) => { el.scrollTop += step; el.dispatchEvent(new Event('scroll')); }",
                metrics["step"],
            )
            page.wait_for_timeout(250)
    finally:
        for index, future in avatar_futures:
            try:
                temporary, avatar_hash, avatar_capture_failed = future.result()
            except Exception:
                temporary, avatar_hash, avatar_capture_failed = None, None, True
            items[index] = replace(
                items[index],
                temporary_avatar=temporary,
                avatar_hash=avatar_hash,
                avatar_capture_failed=avatar_capture_failed,
            )
        avatar_executor.shutdown(wait=True)
    return items, partial_timeout, completed_bottom_reached, ended_by, rows_inspected


def _avatar_needs_refresh(path: Path, now: datetime) -> bool:
    if not path.is_file():
        return True
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return True
    return now - modified >= AVATAR_CACHE_TTL


def _publish_avatar(
    temporary: Path | None,
    cache_dir: Path,
    identifier: str,
    now: datetime,
    force: bool = False,
) -> tuple[str | None, str, bool]:
    destination = _avatar_path(cache_dir, identifier)
    refresh_needed = force or _avatar_needs_refresh(destination, now)
    if temporary is not None and refresh_needed:
        try:
            if destination.is_file() and temporary.read_bytes() == destination.read_bytes():
                temporary.unlink(missing_ok=True)
                return destination.name, "cached", False
            os.replace(temporary, destination)
            return destination.name, "cached", True
        except OSError:
            temporary.unlink(missing_ok=True)
    elif temporary is not None:
        temporary.unlink(missing_ok=True)
    if destination.is_file():
        return destination.name, "cached", False
    return None, "missing", False


def _discard_temporary(items: list[_ScannedItem]) -> None:
    for item in items:
        if item.temporary_avatar is not None:
            item.temporary_avatar.unlink(missing_ok=True)


def scan_friend_names(
    page,
    selectors: ChatSelectors,
    max_scrolls: int = 20,
) -> list[str]:
    """Compatibility projection that reuses the sole conversation-list lane."""
    with TemporaryDirectory(prefix="autody-scan-names-") as temporary:
        items, _, _, _, _ = _scan_items(
            page,
            selectors,
            Path(temporary),
            max_scrolls=max_scrolls,
            capture_avatar=lambda _identity: False,
            initial_virtual_list_timeout_ms=0,
            wait_for_bottom_growth=False,
        )
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.name not in seen:
            seen.add(item.name)
            names.append(item.name)
    return names


def _fresh_avatar_identities(
    previous: FriendDiscoveryResult | None,
    cache_dir: Path,
    now: datetime,
) -> set[str]:
    fresh: set[str] = set()
    for candidate in previous.candidates if previous else []:
        cache_key = candidate.avatar_cache_key or candidate.configured_target_id or candidate.candidate_id
        if candidate.identity_key and _SAFE_LOCAL_ID.fullmatch(cache_key):
            if not _avatar_needs_refresh(_avatar_path(cache_dir, cache_key), now):
                fresh.add(candidate.identity_key)
    return fresh


def discover_friends(
    config: AppConfig,
    page,
    selectors: ChatSelectors,
    output_path: Path,
    now: Callable[[], datetime] | None = None,
    avatar_cache_dir: Path | None = None,
    force_avatar_refresh: bool = False,
    overall_timeout_ms: int = 90_000,
    max_scrolls: int = 20,
    avatar_timeout_ms: int = 2_000,
    monotonic: Callable[[], float] = time.monotonic,
    progress: Callable[[str, int, int | None], None] | None = None,
) -> FriendDiscoveryResult:
    cache_dir = avatar_cache_dir or output_path.parent / "avatar-cache"
    previous_cache = load_discovered_friends(output_path)
    profile = load_account_profile(output_path.parent.parent)
    account_scope = profile.account_profile_id if profile else None
    previous = previous_cache
    if (
        previous_cache
        and previous_cache.account_scope
        and account_scope
        and previous_cache.account_scope != account_scope
    ):
        previous = None
    scanned_now = (now or datetime.now)()
    fresh_avatar_identities = (
        set()
        if force_avatar_refresh
        else _fresh_avatar_identities(previous, cache_dir, scanned_now)
    )
    def should_capture(identity_key: str | None) -> bool:
        if force_avatar_refresh:
            # A correction run repairs every current candidate association.
            return True
        return identity_key not in fresh_avatar_identities
    started = monotonic()
    scanned, partial_timeout, completed_bottom_reached, ended_by, rows_inspected = _scan_items(
        page,
        selectors,
        cache_dir,
        capture_avatar=should_capture,
        max_scrolls=max_scrolls,
        avatar_timeout_ms=avatar_timeout_ms,
        deadline=started + overall_timeout_ms / 1000,
        monotonic=monotonic,
        progress=progress,
        initial_virtual_list_timeout_ms=overall_timeout_ms,
    )
    scanned_at = scanned_now.isoformat(timespec="seconds")
    scan_id = _new_local_id("scan")
    targets_by_id = {
        target.stable_id: target
        for target in config.targets
        if target.stable_id and _SAFE_LOCAL_ID.fullmatch(target.stable_id)
    }
    targets_by_candidate_id = {
        target.candidate_id: target
        for target in config.targets
        if target.candidate_id and _SAFE_LOCAL_ID.fullmatch(target.candidate_id)
    }
    previous_by_identity = {
        candidate.identity_key: candidate
        for candidate in (previous.candidates if previous else [])
        if candidate.identity_key
    }
    previous_by_conversation: dict[str, list[FriendCandidate]] = defaultdict(list)
    for candidate in (previous.candidates if previous else []):
        if candidate.conversation_id:
            previous_by_conversation[candidate.conversation_id].append(candidate)
    config_changed = False
    candidates: list[FriendCandidate] = []
    seen_previous_ids: set[str] = set()
    avatars_updated = avatars_reused = avatars_failed = configured_matched = new_candidates = 0

    try:
        for item in scanned:
            prior = previous_by_identity.get(item.identity_key)
            if prior is None and item.conversation_id:
                locator_matches = previous_by_conversation.get(
                    item.conversation_id, []
                )
                if (
                    len(locator_matches) == 1
                    and locator_matches[0].candidate_id not in seen_previous_ids
                ):
                    prior = locator_matches[0]
            candidate_id = (
                prior.candidate_id
                if prior and _SAFE_LOCAL_ID.fullmatch(prior.candidate_id)
                else _candidate_id(
                    f"{account_scope}\0{item.identity_key}"
                    if account_scope and item.identity_key
                    else item.identity_key
                )
            )
            target = targets_by_candidate_id.get(candidate_id)
            legacy_target = targets_by_id.get(candidate_id)
            if target is None and legacy_target is not None and not legacy_target.candidate_id:
                candidate_id = _candidate_id(
                    f"{account_scope}\0{item.identity_key}"
                    if account_scope and item.identity_key
                    else item.identity_key
                )
                legacy_target.candidate_id = candidate_id
                targets_by_candidate_id[candidate_id] = legacy_target
                target = legacy_target
                config_changed = True
            configured_target_id = target.stable_id if target else None
            configured_enabled = target.enabled if target else None
            # Cache ownership belongs to the candidate row, never to a
            # permanent target.  Targets resolve through their candidate_id.
            cache_id = candidate_id
            # Nicknames and avatars may suggest a relink in the API, but a scan
            # never silently changes a stable target binding.
            match_status = "configured" if target else "unconfigured"
            if target:
                configured_matched += 1
            if item.association_uncertain:
                avatar_cache_path, avatar_status, avatar_updated = None, "missing", False
            else:
                avatar_cache_path, avatar_status, avatar_updated = _publish_avatar(
                    item.temporary_avatar,
                    cache_dir,
                    cache_id,
                    scanned_now,
                    force_avatar_refresh,
                )
            if avatar_updated:
                avatars_updated += 1
            elif avatar_status == "cached":
                avatars_reused += 1
            elif item.avatar_capture_failed:
                avatars_failed += 1
            if prior is None:
                new_candidates += 1
            else:
                seen_previous_ids.add(prior.candidate_id)
            logger.debug(
                "friend discovery row candidate=%s name=%s row=%s avatar_key=%s source=%s association=%s",
                candidate_id,
                f"{item.name[:1]}***",
                item.row_index,
                cache_id,
                item.identity_source,
                match_status,
            )
            candidates.append(
                FriendCandidate(
                    candidate_id=candidate_id,
                    display_name=item.name,
                    avatar_cache_path=avatar_cache_path,
                    avatar_status=avatar_status,
                    discovered_at=scanned_at,
                    match_status=match_status,
                    configured_target_id=configured_target_id,
                    configured_enabled=configured_enabled,
                    avatar_cache_key=cache_id,
                    avatar_updated_at=scanned_at if avatar_updated else (prior.avatar_updated_at if prior else None),
                    first_discovered_at=prior.first_discovered_at if prior else scanned_at,
                    last_seen_at=scanned_at,
                    last_scan_id=scan_id,
                    presence_status="current",
                    identity_key=item.identity_key,
                    identity_source=item.identity_source,
                    conversation_id=item.conversation_id,
                )
            )

        for candidate in previous.candidates if previous else []:
            if candidate.candidate_id in seen_previous_ids:
                continue
            target = targets_by_candidate_id.get(candidate.candidate_id)
            configured_target_id = target.stable_id if target else None
            configured_enabled = target.enabled if target else None
            match_status = "configured" if target else "unconfigured"
            candidates.append(
                replace(
                    candidate,
                    match_status=match_status,
                    configured_target_id=configured_target_id,
                    configured_enabled=configured_enabled,
                    last_scan_id=scan_id,
                    presence_status="stale",
                )
            )
    finally:
        _discard_temporary(scanned)

    removed_stale_candidates = 0
    if completed_bottom_reached and not partial_timeout:
        kept: list[FriendCandidate] = []
        for candidate in candidates:
            if candidate.presence_status == "stale":
                removed_stale_candidates += 1
                continue
            kept.append(candidate)
        candidates = kept
        referenced_avatar_keys = {
            candidate.avatar_cache_key or candidate.candidate_id
            for candidate in candidates
            if candidate.avatar_cache_key or candidate.candidate_id
        }
        for candidate in previous_cache.candidates if previous_cache else []:
            key = candidate.avatar_cache_key or candidate.candidate_id
            if candidate.candidate_id not in {item.candidate_id for item in candidates} and key not in referenced_avatar_keys:
                _avatar_path(cache_dir, key).unlink(missing_ok=True)

    last_result: dict[str, object] = {
        "status": "partial_timeout" if partial_timeout else "completed_with_avatar_failures" if avatars_failed else "completed_bottom_reached" if completed_bottom_reached else "partial_scroll_limit",
        "finished_at": scanned_at,
        "scan_id": scan_id,
        "candidates_found": len(scanned),
        "rows_inspected": rows_inspected,
        "ended_by": ended_by,
        "new_candidates": new_candidates,
        "configured_matched": configured_matched,
        "avatars_updated": avatars_updated,
        "avatars_reused": avatars_reused,
        "avatars_failed": avatars_failed,
        "stale_candidates": sum(item.presence_status == "stale" for item in candidates),
        "partial": partial_timeout,
        "completed_bottom_reached": completed_bottom_reached,
        "removed_stale_candidates": removed_stale_candidates,
    }
    if progress:
        progress("writing_cache", len(scanned), len(scanned))
    _write_discovery_payload(
        output_path,
        {
            "version": 2,
            "scanned_at": scanned_at,
            "scan_id": scan_id,
            "last_result": last_result,
            "account_scope": account_scope,
            "target_refresh": {},
            "candidates": [asdict(candidate) for candidate in candidates],
        },
    )
    return FriendDiscoveryResult(
        scanned_at,
        candidates,
        output_path,
        config_changed,
        scan_id,
        last_result,
        account_scope,
        {},
    )


def refresh_configured_targets(
    config: AppConfig,
    page,
    selectors: ChatSelectors,
    output_path: Path,
    now: Callable[[], datetime] | None = None,
    avatar_cache_dir: Path | None = None,
    overall_timeout_ms: int = 30_000,
    max_scrolls: int = 20,
    avatar_timeout_ms: int = 2_000,
    monotonic: Callable[[], float] = time.monotonic,
    progress: Callable[[str, int, int | None], None] | None = None,
) -> FriendDiscoveryResult:
    """Refresh only configured targets without claiming a full account scan.

    The full-cache ``scanned_at`` and ``last_result`` remain owned by
    :func:`discover_friends`.  This operation stores per-target freshness in
    the same cache document and only updates candidates proven by a target's
    persistent account-scoped identity.
    """
    previous = load_discovered_friends(output_path)
    profile = load_account_profile(output_path.parent.parent)
    account_scope = profile.account_profile_id if profile else None
    refreshed_now = (now or datetime.now)()
    refreshed_at = refreshed_now.isoformat(timespec="seconds")
    cache_dir = avatar_cache_dir or output_path.parent / "avatar-cache"
    authoritative_sources = {"participant_sec_user_id", "row_attribute"}
    requested_targets = [target for target in config.targets if target.enabled]
    eligible_candidates = [
        target
        for target in requested_targets
        if target.stable_id
        and target.binding_identity_key
        and target.binding_identity_source in authoritative_sources
        and target.binding_account_scope == account_scope
    ]
    identity_counts = Counter(
        target.binding_identity_key for target in eligible_candidates
    )
    eligible = {
        target.binding_identity_key: target
        for target in eligible_candidates
        if identity_counts[target.binding_identity_key] == 1
    }
    requested_ids = [target.stable_id for target in requested_targets if target.stable_id]
    unresolved_ids = [
        target.stable_id
        for target in requested_targets
        if target.stable_id
        and eligible.get(target.binding_identity_key) is not target
    ]
    started = monotonic()
    scanned: list[_ScannedItem] = []
    partial_timeout = False
    completed_bottom_reached = False
    if eligible:
        fresh_avatar_identities = _fresh_avatar_identities(
            previous,
            cache_dir,
            refreshed_now,
        )
        scanned, partial_timeout, completed_bottom_reached, ended_by, rows_inspected = _scan_items(
            page,
            selectors,
            cache_dir,
            max_scrolls=max_scrolls,
            capture_avatar=lambda identity: (
                identity in eligible and identity not in fresh_avatar_identities
            ),
            avatar_timeout_ms=avatar_timeout_ms,
            deadline=started + overall_timeout_ms / 1000,
            monotonic=monotonic,
            progress=progress,
            # Targeted refresh can inspect the current rows immediately.  If
            # the list is still growing, the normal bottom-growth wait keeps
            # scrolling until the proven identities appear.
            initial_virtual_list_timeout_ms=0,
            stop_after_identities=set(eligible),
        )

    candidates = list(previous.candidates if previous else [])
    by_identity = {
        candidate.identity_key: index
        for index, candidate in enumerate(candidates)
        if candidate.identity_key
    }
    found_ids: list[str] = []
    try:
        for item in scanned:
            target = eligible.get(item.identity_key)
            if target is None or not target.stable_id or item.association_uncertain:
                continue
            prior_index = by_identity.get(item.identity_key)
            prior = candidates[prior_index] if prior_index is not None else None
            candidate_id = (
                prior.candidate_id
                if prior is not None
                else _candidate_id(f"{account_scope}\0{item.identity_key}")
            )
            avatar_cache_path, avatar_status, avatar_updated = _publish_avatar(
                item.temporary_avatar,
                cache_dir,
                candidate_id,
                datetime.fromisoformat(refreshed_at),
            )
            if avatar_cache_path is None and prior is not None:
                avatar_cache_path = prior.avatar_cache_path
                avatar_status = prior.avatar_status
            updated = FriendCandidate(
                candidate_id=candidate_id,
                display_name=item.name,
                avatar_cache_path=avatar_cache_path,
                avatar_status=avatar_status,
                discovered_at=prior.discovered_at if prior else refreshed_at,
                match_status="configured",
                configured_target_id=target.stable_id,
                configured_enabled=target.enabled,
                avatar_cache_key=candidate_id,
                avatar_updated_at=(
                    refreshed_at
                    if avatar_updated
                    else (prior.avatar_updated_at if prior else None)
                ),
                first_discovered_at=(
                    prior.first_discovered_at if prior else refreshed_at
                ),
                last_seen_at=refreshed_at,
                last_scan_id=prior.last_scan_id if prior else None,
                presence_status="current",
                identity_key=item.identity_key,
                identity_source=item.identity_source,
                conversation_id=item.conversation_id,
            )
            if prior_index is None:
                by_identity[item.identity_key] = len(candidates)
                candidates.append(updated)
            else:
                candidates[prior_index] = updated
            found_ids.append(target.stable_id)
    finally:
        _discard_temporary(scanned)

    eligible_ids = {
        target.stable_id
        for target in eligible.values()
        if target.stable_id
    }
    missing_ids = sorted(eligible_ids - set(found_ids))
    refresh_status = (
        "completed"
        if len(found_ids) == len(eligible)
        else "partial_timeout"
        if partial_timeout
        else "completed_with_missing"
        if completed_bottom_reached
        else "partial_scroll_limit"
    )
    target_refresh: dict[str, object] = {
        "status": refresh_status,
        "completed_at": refreshed_at,
        "account_scope": account_scope,
        "requested_target_ids": requested_ids,
        "found_target_ids": sorted(found_ids),
        "missing_target_ids": missing_ids,
        "unresolved_target_ids": sorted(unresolved_ids),
        "rows_examined": rows_inspected,
        "rows_inspected": rows_inspected,
        "ended_by": ended_by if eligible else "no_authoritative_targets",
        "completed_bottom_reached": completed_bottom_reached,
        "partial": partial_timeout,
    }
    if progress:
        progress("writing_cache", len(found_ids), len(requested_ids))
    _write_discovery_payload(
        output_path,
        {
            "version": 3,
            "scanned_at": previous.scanned_at if previous else None,
            "scan_id": previous.scan_id if previous else None,
            "last_result": previous.last_result if previous else {},
            "account_scope": account_scope,
            "target_refresh": target_refresh,
            "candidates": [asdict(candidate) for candidate in candidates],
        },
    )
    return FriendDiscoveryResult(
        previous.scanned_at if previous else None,
        candidates,
        output_path,
        scan_id=previous.scan_id if previous else None,
        last_result=previous.last_result if previous else {},
        account_scope=account_scope,
        target_refresh=target_refresh,
    )


def refresh_configured_avatars(
    config: AppConfig,
    page,
    selectors: ChatSelectors,
    avatar_cache_dir: Path,
) -> AvatarRefreshResult:
    """Refresh only unambiguous configured-avatar associations; never edit names."""
    scanned, _, _, _, _ = _scan_items(page, selectors, avatar_cache_dir)
    by_name: dict[str, list[_ScannedItem]] = defaultdict(list)
    for item in scanned:
        by_name[item.name].append(item)
    updated = missing = ambiguous = 0
    config_changed = False
    try:
        for target in config.targets:
            matches = by_name.get(target.name, [])
            if len(matches) > 1:
                ambiguous += 1
                continue
            if not matches:
                missing += 1
                continue
            config_changed = _ensure_target_id(target) or config_changed
            avatar_cache_path, avatar_status, avatar_updated = _publish_avatar(
                matches[0].temporary_avatar,
                avatar_cache_dir,
                target.stable_id or _new_local_id("friend"),
                datetime.now(),
            )
            if avatar_status == "cached" and avatar_updated:
                updated += 1
            elif avatar_cache_path is None:
                missing += 1
    finally:
        _discard_temporary(scanned)
    return AvatarRefreshResult(updated, missing, ambiguous, config_changed)


def _candidate_from_payload(item: object, scanned_at: str) -> FriendCandidate:
    if not isinstance(item, dict):
        raise ValueError("candidate must be an object")
    display_name = str(item.get("display_name", item.get("name", ""))).strip()
    if not display_name:
        raise ValueError("candidate name is required")
    match_status = str(item.get("match_status", ""))
    if not match_status:
        match_status = "configured" if item.get("already_configured") else "unconfigured"
    first_discovered_at = str(item.get("first_discovered_at", item.get("discovered_at", scanned_at)))
    return FriendCandidate(
        candidate_id=str(item.get("candidate_id") or _new_local_id("legacy-candidate")),
        display_name=display_name,
        avatar_cache_path=(str(item["avatar_cache_path"]) if item.get("avatar_cache_path") else None),
        avatar_status=str(item.get("avatar_status", "missing")),
        discovered_at=str(item.get("discovered_at", scanned_at)),
        match_status=match_status,
        configured_target_id=(str(item["configured_target_id"]) if item.get("configured_target_id") else None),
        configured_enabled=(bool(item["configured_enabled"]) if item.get("configured_enabled") is not None else None),
        avatar_cache_key=(str(item["avatar_cache_key"]) if item.get("avatar_cache_key") else None),
        avatar_updated_at=(str(item["avatar_updated_at"]) if item.get("avatar_updated_at") else None),
        first_discovered_at=first_discovered_at,
        last_seen_at=str(item.get("last_seen_at", scanned_at)),
        last_scan_id=(str(item["last_scan_id"]) if item.get("last_scan_id") else None),
        presence_status=str(item.get("presence_status", "current")),
        identity_key=(str(item["identity_key"]) if item.get("identity_key") else None),
        identity_source=(str(item["identity_source"]) if item.get("identity_source") else None),
        conversation_id=(str(item["conversation_id"]) if item.get("conversation_id") else None),
    )


def load_discovered_friends(path: Path) -> FriendDiscoveryResult | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_scanned_at = payload.get("scanned_at")
        scanned_at = str(raw_scanned_at) if raw_scanned_at else None
        candidates = [_candidate_from_payload(item, scanned_at or "") for item in payload["candidates"]]
        last_result = payload.get("last_result", {})
        return FriendDiscoveryResult(
            scanned_at,
            candidates,
            path,
            scan_id=str(payload["scan_id"]) if payload.get("scan_id") else None,
            last_result=last_result if isinstance(last_result, dict) else {},
            account_scope=str(payload["account_scope"]) if payload.get("account_scope") else None,
            target_refresh=(
                payload.get("target_refresh", {})
                if isinstance(payload.get("target_refresh", {}), dict)
                else {}
            ),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def is_discovery_stale(scanned_at: str | None, now: datetime | None = None) -> bool:
    if not scanned_at:
        return True
    try:
        scanned = datetime.fromisoformat(scanned_at)
    except ValueError:
        return True
    current = now or datetime.now()
    if scanned.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=scanned.tzinfo)
    return current - scanned > DISCOVERY_CACHE_TTL


def targeted_refresh_cache_is_usable(
    config: AppConfig,
    output_path: Path,
    now: datetime | None = None,
) -> FriendDiscoveryResult | None:
    """Return a fresh, account-compatible targeted snapshot without scanning.

    A targeted snapshot is complete only when every enabled stable target has a
    recorded terminal lookup result.  It intentionally does not infer that an
    unbound target can be found by scrolling a conversation list.
    """
    cached = load_discovered_friends(output_path)
    profile = load_account_profile(output_path.parent.parent)
    if cached is None or profile is None or cached.account_scope != profile.account_profile_id:
        return None
    expected = {
        target.stable_id: target
        for target in config.targets
        if target.enabled and target.stable_id
    }
    last_result = cached.last_result
    full_snapshot_complete = (
        isinstance(last_result, dict)
        and last_result.get("completed_bottom_reached") is True
        and last_result.get("partial") is not True
        and not is_discovery_stale(cached.scanned_at, now)
    )
    if full_snapshot_complete:
        current_identities = {
            candidate.identity_key
            for candidate in cached.candidates
            if candidate.presence_status == "current" and candidate.identity_key
        }
        if all(
            target.binding_identity_key
            and target.binding_account_scope == profile.account_profile_id
            and target.binding_identity_key in current_identities
            for target in expected.values()
        ):
            return replace(
                cached,
                target_refresh={
                    "status": "cached",
                    "completed_at": cached.scanned_at,
                    "account_scope": cached.account_scope,
                    "requested_target_ids": sorted(expected),
                    "found_target_ids": sorted(expected),
                    "missing_target_ids": [],
                    "unresolved_target_ids": [],
                    "rows_inspected": 0,
                    "ended_by": "cache",
                    "partial": False,
                },
            )

    refresh = cached.target_refresh
    completed_at = refresh.get("completed_at") if isinstance(refresh, dict) else None
    if not isinstance(completed_at, str) or is_discovery_stale(completed_at, now):
        return None
    if refresh.get("partial") is True:
        return None
    expected_ids = set(expected)
    requested = set(refresh.get("requested_target_ids", []))
    accounted = set(refresh.get("found_target_ids", []))
    accounted.update(refresh.get("missing_target_ids", []))
    accounted.update(refresh.get("unresolved_target_ids", []))
    if not expected_ids.issubset(requested) or not expected_ids.issubset(accounted):
        return None
    return cached


def _write_discovery_payload(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    json.loads(serialized)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_discovery_failure(
    path: Path,
    error: str,
    now: Callable[[], datetime] | None = None,
    status: str = "failed",
    details: dict[str, object] | None = None,
) -> FriendDiscoveryResult:
    previous = load_discovered_friends(path)
    finished_at = (now or datetime.now)().isoformat(timespec="seconds")
    candidates = previous.candidates if previous else []
    scanned_at = previous.scanned_at if previous else None
    scan_id = previous.scan_id if previous else None
    reason_code, stage = {
        "lock_busy": ("browser_busy", "browser_opened"),
        "login_unavailable": ("login_required", "account_verified"),
        "page_load_failed": ("page_load_timeout", "browser_opened"),
        "partial_timeout": ("friend_discovery_failed", "conversation_located"),
        "cancelled": ("operation_cancelled", "conversation_located"),
    }.get(status, ("friend_discovery_failed", "conversation_located"))
    failure = failure_detail(
        reason_code,
        stage=stage,
        account_scope=previous.account_scope if previous else None,
        diagnostic_details={"discovery_status": status},
    )
    last_result: dict[str, object] = {
        "status": status,
        "finished_at": finished_at,
        "error": failure.user_summary_zh,
        "failure": failure.model_dump(mode="json"),
        **(details or {}),
    }
    _write_discovery_payload(
        path,
        {
            "version": 2,
            "scanned_at": scanned_at,
            "scan_id": scan_id,
            "last_result": last_result,
            "account_scope": previous.account_scope if previous else None,
            "target_refresh": previous.target_refresh if previous else {},
            "candidates": [asdict(candidate) for candidate in candidates],
        },
    )
    return FriendDiscoveryResult(
        scanned_at,
        candidates,
        path,
        scan_id=scan_id,
        last_result=last_result,
        account_scope=previous.account_scope if previous else None,
        target_refresh=previous.target_refresh if previous else {},
    )


def record_target_refresh_failure(
    path: Path,
    error: Exception,
    now: Callable[[], datetime] | None = None,
) -> FriendDiscoveryResult:
    """Record startup refresh failure without invalidating full-scan evidence."""
    previous = load_discovered_friends(path)
    finished_at = (now or datetime.now)().isoformat(timespec="seconds")
    target_refresh: dict[str, object] = {
        "status": "failed",
        "completed_at": finished_at,
        "account_scope": previous.account_scope if previous else None,
        "requested_target_ids": [],
        "found_target_ids": [],
        "missing_target_ids": [],
        "unresolved_target_ids": [],
        "partial": True,
        "error_type": type(error).__name__,
    }
    candidates = previous.candidates if previous else []
    _write_discovery_payload(
        path,
        {
            "version": 3,
            "scanned_at": previous.scanned_at if previous else None,
            "scan_id": previous.scan_id if previous else None,
            "last_result": previous.last_result if previous else {},
            "account_scope": previous.account_scope if previous else None,
            "target_refresh": target_refresh,
            "candidates": [asdict(candidate) for candidate in candidates],
        },
    )
    return FriendDiscoveryResult(
        previous.scanned_at if previous else None,
        candidates,
        path,
        scan_id=previous.scan_id if previous else None,
        last_result=previous.last_result if previous else {},
        account_scope=previous.account_scope if previous else None,
        target_refresh=target_refresh,
    )

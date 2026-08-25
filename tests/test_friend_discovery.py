from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from autody.chat import ChatSelectors
from autody.config import AppConfig, Target
from autody.friend_discovery import (
    ScanProgress,
    discover_friends,
    is_discovery_stale,
    load_discovered_friends,
    record_discovery_failure,
    refresh_configured_targets,
    refresh_configured_avatars,
    scan_friend_names,
)


def write_account_profile(root: Path, scope: str) -> None:
    (root / "data" / "account-avatar").mkdir(parents=True, exist_ok=True)
    (root / "data" / "account-avatar" / "profile.png").write_bytes(b"avatar")
    (root / "data" / "account-profile.json").write_text(
        json.dumps(
            {
                "account_profile_id": scope,
                "account_id_digest": f"digest-{scope}",
                "display_name": "测试账号",
                "avatar_cache_key": "profile",
                "avatar_version": "v1",
                "is_self": True,
                "verification_source": "test_fixture",
                "profile_status": "verified",
                "verified_at": "2026-07-30T08:00:00",
                "last_updated_at": "2026-07-30T08:00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class FakeNamesLocator:
    def __init__(self, page):
        self.page = page

    def all_inner_texts(self):
        pages = [
            ["测试好友甲", "测试好友乙", "  测试好友甲  "],
            ["测试好友丙", "测试好友乙"],
            ["测试好友丁", ""],
        ]
        return pages[min(self.page.position, len(pages) - 1)]


class FakeScrollLocator:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def evaluate(self, expression, *_args):
        if "scrollTop = 0" in expression:
            self.page.position = 0
            return None
        if "before" in expression:
            return {
                "before": self.page.position,
                "maximum": 2,
                "step": 1,
            }
        self.page.position += 1


class FakeTextLocator:
    def __init__(self, value: str):
        self.value = value

    def inner_text(self):
        return self.value


class FakeAvatarLocator:
    def __init__(self, content: bytes | None, source: str | None = None):
        self.content = content
        self.source = source

    @property
    def first(self):
        return self

    def screenshot(self, path: str):
        if self.content is None:
            raise RuntimeError("avatar unavailable")
        Path(path).write_bytes(self.content)

    def get_attribute(self, attribute: str):
        return self.source if attribute == "src" else None


class FakeConversationItem:
    def __init__(
        self,
        selectors: ChatSelectors,
        name: str,
        avatar: bytes | None,
        row_id: str | None = None,
        avatar_source: str | None = None,
    ):
        self.selectors = selectors
        self.name = name
        self.avatar = avatar
        self.row_id = row_id
        self.avatar_source = avatar_source

    def get_attribute(self, attribute: str):
        if attribute in {"data-conversation-id", "data-id", "data-key"}:
            return self.row_id
        return None

    def inner_text(self):
        return self.name

    def locator(self, selector: str):
        if selector == self.selectors.conversation_name:
            return FakeTextLocator(self.name)
        if selector == "img":
            return FakeAvatarLocator(self.avatar, self.avatar_source)
        raise AssertionError(f"unexpected item selector: {selector}")


class RuntimeConversationItem(FakeConversationItem):
    def __init__(
        self,
        selectors: ChatSelectors,
        name: str,
        avatar: bytes | None,
        *,
        participant: str | None,
        conversation: str,
        avatar_source: str | None = None,
    ):
        super().__init__(
            selectors,
            name,
            avatar,
            avatar_source=avatar_source,
        )
        self.participant = participant
        self.conversation = conversation

    def evaluate(self, _expression: str):
        return {
            "participantSecUserId": self.participant,
            "conversationId": self.conversation,
            "conversationShortId": None,
        }


class FakeConversationLocator:
    def __init__(self, page):
        self.page = page

    def count(self):
        return len(self.page.rows[min(self.page.position, len(self.page.rows) - 1)])

    def nth(self, index: int):
        return self.page.rows[min(self.page.position, len(self.page.rows) - 1)][index]


class FakePage:
    def __init__(self, selectors: ChatSelectors, rows: list[list[FakeConversationItem]] | None = None):
        self.position = 0
        self.selectors = selectors
        self.waits = []
        self.rows = rows or [
            [
                FakeConversationItem(selectors, "测试好友甲", b"avatar-jiang"),
                FakeConversationItem(selectors, "测试好友乙", b"avatar-gege"),
            ],
            [
                FakeConversationItem(selectors, "测试好友丙", b"avatar-ning"),
                FakeConversationItem(selectors, "测试好友乙", b"avatar-gege"),
            ],
            [FakeConversationItem(selectors, "测试好友丁", b"avatar-chen")],
        ]

    def locator(self, selector):
        if selector == self.selectors.conversation:
            return FakeConversationLocator(self)
        if selector == self.selectors.conversation_name:
            return FakeNamesLocator(self)
        if selector == self.selectors.conversation_list:
            return FakeScrollLocator(self)
        raise AssertionError(f"unexpected selector: {selector}")

    def wait_for_timeout(self, delay):
        self.waits.append(delay)


class DelayedExpansionScrollLocator(FakeScrollLocator):
    def evaluate(self, expression, *_args):
        if "scrollTop = 0" in expression:
            self.page.position = 0
            return None
        if "before" in expression:
            return {
                "before": self.page.position,
                "maximum": 2 if self.page.elapsed_ms >= 1_250 else 1,
                "step": 1,
            }
        self.page.position += 1


class DelayedExpansionPage(FakePage):
    def __init__(self, selectors: ChatSelectors):
        super().__init__(selectors)
        self.elapsed_ms = 0

    def locator(self, selector):
        if selector == self.selectors.conversation_list:
            return DelayedExpansionScrollLocator(self)
        return super().locator(selector)

    def wait_for_timeout(self, delay):
        super().wait_for_timeout(delay)
        self.elapsed_ms += delay


class LazyBottomScrollLocator(FakeScrollLocator):
    def evaluate(self, expression, *_args):
        if "scrollTop = 0" in expression:
            self.page.position = 0
            return None
        if "before" in expression:
            maximum = (
                2
                if self.page.position >= 1
                and self.page.bottom_wait_ms >= self.page.growth_after_ms
                else 1
            )
            return {
                "before": self.page.position,
                "maximum": maximum,
                "step": 1,
            }
        self.page.position += 1


class LazyBottomPage(FakePage):
    def __init__(self, selectors: ChatSelectors):
        super().__init__(selectors)
        self.bottom_wait_ms = 0
        self.growth_after_ms = 750

    def locator(self, selector):
        if selector == self.selectors.conversation_list:
            return LazyBottomScrollLocator(self)
        return super().locator(selector)

    def wait_for_timeout(self, delay):
        super().wait_for_timeout(delay)
        if self.position >= 1:
            self.bottom_wait_ms += delay


def test_scan_friend_names_scrolls_and_deduplicates():
    selectors = ChatSelectors.test_defaults()
    page = FakePage(selectors)

    names = scan_friend_names(page, selectors, max_scrolls=5)

    assert names == ["测试好友甲", "测试好友乙", "测试好友丙", "测试好友丁"]
    assert page.position == 2
    assert len(page.waits) == 2


def test_discovery_persists_candidates_without_overwriting_config(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    config = AppConfig(targets=[Target(name="测试好友乙")])
    output = tmp_path / "data" / "discovered_friends.json"

    result = discover_friends(
        config,
        FakePage(selectors),
        selectors,
        output,
        now=lambda: datetime(2026, 7, 4, 12, 30, 0),
    )

    assert [target.name for target in config.targets] == ["测试好友乙"]
    assert result.candidates[1].name == "测试好友乙"
    assert result.candidates[1].already_configured is False
    assert result.candidates[0].already_configured is False
    assert config.targets[0].candidate_id is None
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["scanned_at"] == "2026-07-04T12:30:00"
    assert len(saved["candidates"]) == 4


def test_targeted_refresh_updates_only_proven_targets_and_preserves_full_scan_freshness(
    tmp_path: Path,
):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    write_account_profile(tmp_path, "account-a")
    rows = [
        [
            FakeConversationItem(selectors, "测试好友甲", b"avatar-a", row_id="row-a"),
            FakeConversationItem(selectors, "测试好友乙", b"avatar-b", row_id="row-b"),
        ]
    ]
    full = discover_friends(
        AppConfig(),
        FakePage(selectors, rows=rows),
        selectors,
        output,
        now=lambda: datetime(2026, 8, 24, 8, 0, 0),
    )
    candidate = next(item for item in full.candidates if item.display_name == "测试好友甲")
    config = AppConfig(
        targets=[
            Target(
                name="旧显示名",
                stable_id="target-a",
                candidate_id="candidate-stale",
                binding_identity_key=candidate.identity_key,
                binding_identity_source=candidate.identity_source,
                binding_account_scope="account-a",
            )
        ]
    )
    targeted_page = FakePage(selectors, rows=rows)

    refreshed = refresh_configured_targets(
        config,
        targeted_page,
        selectors,
        output,
        now=lambda: datetime(2026, 8, 25, 8, 0, 0),
    )
    persisted = load_discovered_friends(output)

    assert refreshed.scanned_at == full.scanned_at
    assert refreshed.last_result == full.last_result
    assert refreshed.target_refresh["found_target_ids"] == ["target-a"]
    assert refreshed.target_refresh["rows_examined"] == 2
    assert targeted_page.position == 0
    assert targeted_page.waits == []
    current = next(
        item for item in persisted.candidates if item.identity_key == candidate.identity_key
    )
    assert current.configured_target_id == "target-a"
    assert current.last_seen_at == "2026-08-25T08:00:00"


def test_targeted_refresh_skips_fresh_cached_target_avatar(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    write_account_profile(tmp_path, "account-a")
    first_rows = [[
        FakeConversationItem(
            selectors,
            "测试好友甲",
            b"avatar-a",
            row_id="row-a",
        )
    ]]
    full = discover_friends(
        AppConfig(),
        FakePage(selectors, rows=first_rows),
        selectors,
        output,
        now=lambda: datetime(2026, 8, 24, 8, 0, 0),
    )
    candidate = full.candidates[0]
    captures = []

    class CountingAvatar(FakeAvatarLocator):
        def screenshot(self, path: str):
            captures.append(path)
            super().screenshot(path)

    class CountingItem(FakeConversationItem):
        def locator(self, selector: str):
            if selector == "img":
                return CountingAvatar(self.avatar, self.avatar_source)
            return super().locator(selector)

    config = AppConfig(targets=[Target(
        name="测试好友甲",
        stable_id="target-a",
        candidate_id=candidate.candidate_id,
        binding_identity_key=candidate.identity_key,
        binding_identity_source=candidate.identity_source,
        binding_account_scope="account-a",
    )])
    refresh_configured_targets(
        config,
        FakePage(
            selectors,
            rows=[[CountingItem(
                selectors,
                "测试好友甲",
                b"new-avatar",
                row_id="row-a",
            )]],
        ),
        selectors,
        output,
        now=lambda: datetime(2026, 8, 25, 8, 0, 0),
    )

    assert captures == []


def test_discovery_resets_a_reused_conversation_list_before_full_scan(
    tmp_path: Path,
):
    selectors = ChatSelectors.test_defaults()
    page = FakePage(selectors)
    page.position = 1

    result = discover_friends(
        AppConfig(),
        page,
        selectors,
        tmp_path / "data" / "discovered_friends.json",
    )

    assert [candidate.display_name for candidate in result.candidates] == [
        "测试好友甲",
        "测试好友乙",
        "测试好友丙",
        "测试好友丁",
    ]
    assert result.last_result["candidates_found"] == 4
    assert result.last_result["completed_bottom_reached"] is True


def test_discovery_waits_for_delayed_virtual_list_expansion_before_scanning(
    tmp_path: Path,
):
    selectors = ChatSelectors.test_defaults()

    result = discover_friends(
        AppConfig(),
        DelayedExpansionPage(selectors),
        selectors,
        tmp_path / "data" / "discovered_friends.json",
    )

    assert [candidate.display_name for candidate in result.candidates] == [
        "测试好友甲",
        "测试好友乙",
        "测试好友丙",
        "测试好友丁",
    ]
    assert result.last_result["candidates_found"] == 4
    assert result.last_result["completed_bottom_reached"] is True


def test_discovery_waits_for_lazy_page_growth_before_declaring_bottom(
    tmp_path: Path,
):
    selectors = ChatSelectors.test_defaults()

    result = discover_friends(
        AppConfig(),
        LazyBottomPage(selectors),
        selectors,
        tmp_path / "data" / "discovered_friends.json",
    )

    assert [candidate.display_name for candidate in result.candidates] == [
        "测试好友甲",
        "测试好友乙",
        "测试好友丙",
        "测试好友丁",
    ]
    assert result.last_result["candidates_found"] == 4
    assert result.last_result["completed_bottom_reached"] is True


def test_discovery_does_not_misclassify_slow_lazy_growth_as_the_final_bottom(
    tmp_path: Path,
):
    selectors = ChatSelectors.test_defaults()
    page = LazyBottomPage(selectors)
    page.growth_after_ms = 2_500

    result = discover_friends(
        AppConfig(),
        page,
        selectors,
        tmp_path / "data" / "discovered_friends.json",
    )

    assert [candidate.display_name for candidate in result.candidates] == [
        "测试好友甲",
        "测试好友乙",
        "测试好友丙",
        "测试好友丁",
    ]
    assert result.last_result["completed_bottom_reached"] is True


def test_discovery_captures_avatar_without_binding_target_by_display_name(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    config = AppConfig(targets=[Target(name="测试好友乙", enabled=False, stable_id="friend-gege")])
    output = tmp_path / "data" / "discovered_friends.json"
    cache = tmp_path / "data" / "avatar-cache"

    result = discover_friends(
        config,
        FakePage(selectors),
        selectors,
        output,
        avatar_cache_dir=cache,
        now=lambda: datetime(2026, 7, 4, 12, 30, 0),
    )

    candidate = next(item for item in result.candidates if item.display_name == "测试好友乙")
    assert candidate.match_status == "unconfigured"
    assert candidate.configured_target_id is None
    assert candidate.configured_enabled is None
    assert config.targets[0].candidate_id is None
    assert candidate.avatar_status == "cached"
    assert candidate.avatar_cache_path == f"{candidate.candidate_id}.png"
    assert (cache / f"{candidate.candidate_id}.png").read_bytes() == b"avatar-gege"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert "avatar_cache_path" in saved["candidates"][1]
    assert "http" not in json.dumps(saved, ensure_ascii=False)


def test_avatar_capture_failure_does_not_fail_discovery(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    page = FakePage(
        selectors,
        [[FakeConversationItem(selectors, "无头像", None)]],
    )

    result = discover_friends(
        AppConfig(),
        page,
        selectors,
        tmp_path / "data" / "discovered_friends.json",
        avatar_cache_dir=tmp_path / "data" / "avatar-cache",
    )

    assert result.candidates[0].avatar_status == "missing"
    assert result.candidates[0].avatar_cache_path is None


def test_duplicate_nickname_does_not_overwrite_configured_avatar(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    config = AppConfig(targets=[Target(name="测试好友乙", stable_id="friend-gege")])
    cache = tmp_path / "data" / "avatar-cache"
    cache.mkdir(parents=True)
    avatar_path = cache / "friend-gege.png"
    avatar_path.write_bytes(b"original")
    page = FakePage(
        selectors,
        [[
            FakeConversationItem(selectors, "测试好友乙", b"first-avatar"),
            FakeConversationItem(selectors, "测试好友乙", b"second-avatar"),
        ]],
    )

    result = refresh_configured_avatars(config, page, selectors, cache)

    assert result.updated == 0
    assert result.ambiguous == 1
    assert config.targets[0].name == "测试好友乙"
    assert config.targets[0].stable_id == "friend-gege"
    assert avatar_path.read_bytes() == b"original"


def test_duplicate_nickname_rows_keep_stable_ids_and_avatars_after_reorder(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    cache = tmp_path / "data" / "avatar-cache"
    first = discover_friends(
        AppConfig(),
        FakePage(
            selectors,
            [[
                FakeConversationItem(selectors, "同名", b"avatar-a", "conversation-a"),
                FakeConversationItem(selectors, "同名", b"avatar-b", "conversation-b"),
            ]],
        ),
        selectors,
        output,
        avatar_cache_dir=cache,
        now=lambda: datetime(2026, 7, 4, 8, 0, 0),
    )
    first_ids = [item.candidate_id for item in first.candidates]

    second = discover_friends(
        AppConfig(),
        FakePage(
            selectors,
            [[
                FakeConversationItem(selectors, "同名", b"avatar-b-new", "conversation-b"),
                FakeConversationItem(selectors, "同名", b"avatar-a-new", "conversation-a"),
            ]],
        ),
        selectors,
        output,
        avatar_cache_dir=cache,
        force_avatar_refresh=True,
        now=lambda: datetime(2026, 7, 5, 8, 0, 0),
    )
    assert len(set(first_ids)) == 2
    assert [item.candidate_id for item in second.candidates] == list(reversed(first_ids))
    assert (cache / f"{second.candidates[0].avatar_cache_key}.png").read_bytes() == b"avatar-b-new"
    assert (cache / f"{second.candidates[1].avatar_cache_key}.png").read_bytes() == b"avatar-a-new"


def test_avatar_source_identity_is_preferred_when_rows_lack_conversation_ids(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    first = discover_friends(
        AppConfig(),
        FakePage(
            selectors,
            [[
                FakeConversationItem(selectors, "同名", b"avatar-a", avatar_source="https://avatar/a"),
                FakeConversationItem(selectors, "同名", b"avatar-b", avatar_source="https://avatar/b"),
            ]],
        ),
        selectors,
        output,
    )
    second = discover_friends(
        AppConfig(),
        FakePage(
            selectors,
            [[
                FakeConversationItem(selectors, "同名", b"avatar-b-new", avatar_source="https://avatar/b"),
                FakeConversationItem(selectors, "同名", b"avatar-a-new", avatar_source="https://avatar/a"),
            ]],
        ),
        selectors,
        output,
        force_avatar_refresh=True,
    )

    assert [candidate.candidate_id for candidate in second.candidates] == list(
        reversed([candidate.candidate_id for candidate in first.candidates])
    )
    assert {candidate.identity_source for candidate in second.candidates} == {"avatar_source"}


def test_avatar_url_query_changes_keep_candidate_and_target_ids_stable(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    config = AppConfig(targets=[Target(name="小明", stable_id="target-permanent")])
    first = discover_friends(
        config,
        FakePage(selectors, [[FakeConversationItem(selectors, "小明", b"old", avatar_source="https://cdn/avatar/a.png?x-expires=1&x-signature=old")]]),
        selectors,
        output,
    )
    original_candidate = first.candidates[0].candidate_id
    original_target = config.targets[0].stable_id
    config.targets[0].candidate_id = original_candidate
    second = discover_friends(
        config,
        FakePage(selectors, [[FakeConversationItem(selectors, "小明", b"new", avatar_source="https://cdn/avatar/a.png?x-expires=2&x-signature=new")]]),
        selectors,
        output,
        force_avatar_refresh=True,
    )

    assert second.candidates[0].candidate_id == original_candidate
    assert second.candidates[0].configured_target_id == original_target
    assert config.targets[0].stable_id == original_target
    assert config.targets[0].candidate_id == original_candidate


def test_authoritative_identity_upgrade_requires_locator_continuity(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    config = AppConfig(targets=[Target(name="旧名称", stable_id="target-old")])
    first = discover_friends(
        config,
        FakePage(
            selectors,
            [[RuntimeConversationItem(
                selectors,
                "旧名称",
                b"old",
                participant=None,
                conversation="conversation-stable",
                avatar_source="https://avatar/legacy",
            )]],
        ),
        selectors,
        output,
    )
    config.targets[0].candidate_id = first.candidates[0].candidate_id

    second = discover_friends(
        config,
        FakePage(
            selectors,
            [[RuntimeConversationItem(
                selectors,
                "新名称",
                b"new",
                participant="participant-stable",
                conversation="conversation-stable",
            )]],
        ),
        selectors,
        output,
        force_avatar_refresh=True,
    )

    assert second.candidates[0].candidate_id == first.candidates[0].candidate_id
    assert second.candidates[0].configured_target_id == "target-old"
    assert second.candidates[0].identity_source == "participant_sec_user_id"


def test_unique_name_does_not_upgrade_unproven_legacy_target(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    config = AppConfig(targets=[Target(name="唯一同名", stable_id="target-old")])
    first = discover_friends(
        config,
        FakePage(
            selectors,
            [[FakeConversationItem(selectors, "唯一同名", None)]],
        ),
        selectors,
        output,
    )
    config.targets[0].candidate_id = first.candidates[0].candidate_id

    second = discover_friends(
        config,
        FakePage(
            selectors,
            [[RuntimeConversationItem(
                selectors,
                "唯一同名",
                b"new",
                participant="different-participant",
                conversation="different-conversation",
            )]],
        ),
        selectors,
        output,
        force_avatar_refresh=True,
    )

    current = next(
        item for item in second.candidates
        if item.identity_source == "participant_sec_user_id"
    )
    assert current.candidate_id != config.targets[0].candidate_id
    assert current.match_status == "unconfigured"
    assert current.configured_target_id is None


def test_deadline_saves_partial_scan_with_a_clear_status(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    ticks = iter([0.0, 0.0, 0.0, 2.0])

    result = discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "小明", b"avatar")]]),
        selectors,
        output,
        overall_timeout_ms=1,
        monotonic=lambda: next(ticks),
    )

    assert result.last_result["status"] == "partial_timeout"
    assert result.last_result["partial"] is True
    assert load_discovered_friends(output) is not None


def test_partial_scan_preserves_previous_candidates_and_avatars(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    cache = tmp_path / "data" / "avatar-cache"
    first = discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "历史候选", b"keep", "keep-row")]]),
        selectors,
        output,
        avatar_cache_dir=cache,
        now=lambda: datetime(2026, 7, 4, 8, 0, 0),
    )
    avatar = cache / f"{first.candidates[0].avatar_cache_key}.png"
    ticks = iter([0.0, 0.0, 0.0, 2.0])

    result = discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "不完整新候选", b"new", "new-row")]]),
        selectors,
        output,
        avatar_cache_dir=cache,
        overall_timeout_ms=1,
        monotonic=lambda: next(ticks),
        now=lambda: datetime(2026, 7, 5, 8, 0, 0),
    )

    assert [candidate.display_name for candidate in result.candidates] == ["历史候选"]
    assert avatar.read_bytes() == b"keep"
    assert result.last_result["status"] == "partial_timeout"


def test_avatar_capture_failure_does_not_block_later_rows(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()

    class SlowAvatar(FakeAvatarLocator):
        def screenshot(self, path: str, timeout=None):
            raise TimeoutError(f"timed out after {timeout}")

    class SlowItem(FakeConversationItem):
        def locator(self, selector: str):
            if selector == "img":
                return SlowAvatar(self.avatar, self.avatar_source)
            return super().locator(selector)

    result = discover_friends(
        AppConfig(),
        FakePage(selectors, [[
            SlowItem(selectors, "慢头像", b"slow", "slow-row"),
            FakeConversationItem(selectors, "正常头像", b"fast", "fast-row"),
        ]]),
        selectors,
        tmp_path / "data" / "discovered_friends.json",
        avatar_timeout_ms=500,
    )

    assert result.last_result["status"] == "completed_with_avatar_failures"
    assert result.last_result["avatars_failed"] == 1
    assert next(item for item in result.candidates if item.display_name == "正常头像").avatar_status == "cached"


def test_virtual_scan_honors_the_maximum_round_count(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    result = discover_friends(
        AppConfig(),
        FakePage(selectors, [
            [FakeConversationItem(selectors, "第一行", b"first", "first-row")],
            [FakeConversationItem(selectors, "第二行", b"second", "second-row")],
        ]),
        selectors,
        tmp_path / "data" / "discovered_friends.json",
        max_scrolls=0,
    )

    assert [candidate.display_name for candidate in result.candidates] == ["第一行"]


def test_discovery_preserves_candidate_identity_and_marks_missed_rows_stale(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"

    first = discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "旧候选", b"old")]]),
        selectors,
        output,
        now=lambda: datetime(2026, 7, 4, 8, 0, 0),
    )
    second = discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "新候选", b"new")]]),
        selectors,
        output,
        now=lambda: datetime(2026, 7, 5, 8, 0, 0),
    )

    current = next(item for item in second.candidates if item.display_name == "新候选")
    assert all(item.display_name != "旧候选" for item in second.candidates)
    assert current.presence_status == "current"
    assert second.last_result["status"] == "completed_bottom_reached"
    assert second.last_result["removed_stale_candidates"] == 1
    assert second.last_result["candidates_found"] == 1


def test_failed_scan_records_failure_without_erasing_previous_candidates(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "保留候选", b"keep")]]),
        selectors,
        output,
        now=lambda: datetime(2026, 7, 4, 8, 0, 0),
    )

    record_discovery_failure(
        output,
        "chat list unavailable",
        now=lambda: datetime(2026, 7, 5, 8, 0, 0),
    )

    cached = load_discovered_friends(output)
    assert cached is not None
    assert [item.display_name for item in cached.candidates] == ["保留候选"]
    assert cached.scanned_at == "2026-07-04T08:00:00"
    assert cached.last_result["status"] == "failed"
    assert cached.last_result["finished_at"] == "2026-07-05T08:00:00"
    assert cached.last_result["error"] == "好友候选刷新未能完成"
    assert cached.last_result["failure"]["reason_code"] == "friend_discovery_failed"
    assert cached.last_result["failure"]["stage"] == "conversation_located"
    assert cached.last_result["failure"]["suggested_action_zh"] == "重新刷新候选"
    assert "chat list unavailable" not in output.read_text(encoding="utf-8")


def test_discovery_cache_freshness_uses_a_24_hour_window():
    now = datetime(2026, 7, 5, 8, 0, 0)

    assert is_discovery_stale("2026-07-04T08:00:00", now) is False
    assert is_discovery_stale("2026-07-04T07:59:59", now) is True
    assert is_discovery_stale("not-a-date", now) is True


def test_fresh_cached_avatar_is_reused_without_overwriting_it(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    cache = tmp_path / "data" / "avatar-cache"
    current = datetime.now()
    first = discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "缓存头像", b"first-avatar", "cached-row")]]),
        selectors,
        output,
        avatar_cache_dir=cache,
        now=lambda: current,
    )
    avatar = cache / f"{first.candidates[0].candidate_id}.png"
    second = discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "缓存头像", b"new-avatar", "cached-row")]]),
        selectors,
        output,
        avatar_cache_dir=cache,
        now=lambda: current,
    )

    assert avatar.read_bytes() == b"first-avatar"
    assert second.last_result["avatars_updated"] == 0
    assert second.last_result["avatars_reused"] == 1


def test_automatic_scan_does_not_recapture_a_fresh_cached_avatar(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    cache = tmp_path / "data" / "avatar-cache"
    current = datetime.now()
    discover_friends(
        AppConfig(),
        FakePage(selectors, [[FakeConversationItem(selectors, "免重抓", b"avatar", "fresh-row")]]),
        selectors,
        output,
        avatar_cache_dir=cache,
        now=lambda: current,
    )
    screenshot_calls = []

    class CountingAvatar:
        @property
        def first(self):
            return self

        def screenshot(self, path: str):
            screenshot_calls.append(path)
            Path(path).write_bytes(b"unexpected")

    class CachedItem(FakeConversationItem):
        def locator(self, selector: str):
            if selector == "img":
                return CountingAvatar()
            return super().locator(selector)

    discover_friends(
        AppConfig(),
        FakePage(selectors, [[CachedItem(selectors, "免重抓", b"unused", "fresh-row")]]),
        selectors,
        output,
        avatar_cache_dir=cache,
        now=lambda: current,
    )

    assert screenshot_calls == []


def test_completed_scan_replaces_candidate_snapshot_but_preserves_target(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    config = AppConfig(targets=[Target(name="已配置目标")])
    output = tmp_path / "data" / "discovered_friends.json"
    discover_friends(
        config,
        FakePage(selectors, [[FakeConversationItem(selectors, "已配置目标", b"avatar")]]),
        selectors,
        output,
        now=lambda: datetime(2026, 7, 4, 8, 0, 0),
    )
    result = discover_friends(
        config,
        FakePage(selectors, [[]]),
        selectors,
        output,
        now=lambda: datetime(2026, 7, 5, 8, 0, 0),
    )

    assert [target.name for target in config.targets] == ["已配置目标"]
    assert result.candidates == []
    assert result.last_result["removed_stale_candidates"] == 1


def test_bound_current_candidate_wins_and_stale_same_name_identity_is_removed(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    config = AppConfig(targets=[Target(name="同名目标")])
    output = tmp_path / "data" / "discovered_friends.json"
    first = discover_friends(
        config,
        FakePage(
            selectors,
            [[
                FakeConversationItem(selectors, "同名目标", b"a", "stable-a"),
                FakeConversationItem(selectors, "同名目标", b"b", "stable-b"),
            ]],
        ),
        selectors,
        output,
    )
    config.targets[0].candidate_id = first.candidates[0].candidate_id

    current = discover_friends(
        config,
        FakePage(
            selectors,
            [[FakeConversationItem(selectors, "同名目标", b"a2", "stable-a")]],
        ),
        selectors,
        output,
        force_avatar_refresh=True,
    )

    assert len(current.candidates) == 1
    assert current.candidates[0].candidate_id == config.targets[0].candidate_id
    assert current.candidates[0].match_status == "configured"


def test_account_switch_replaces_snapshot_and_scopes_candidate_identity(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    output = tmp_path / "data" / "discovered_friends.json"
    write_account_profile(tmp_path, "account-scope-a")
    first = discover_friends(
        AppConfig(),
        FakePage(
            selectors,
            [[FakeConversationItem(selectors, "候选", b"a", "same-row-id")]],
        ),
        selectors,
        output,
    )
    write_account_profile(tmp_path, "account-scope-b")
    second = discover_friends(
        AppConfig(),
        FakePage(
            selectors,
            [[FakeConversationItem(selectors, "候选", b"b", "same-row-id")]],
        ),
        selectors,
        output,
        force_avatar_refresh=True,
    )

    assert first.account_scope == "account-scope-a"
    assert second.account_scope == "account-scope-b"
    assert len(second.candidates) == 1
    assert second.candidates[0].candidate_id != first.candidates[0].candidate_id


def test_scan_progress_records_the_browser_lock_release_stage(tmp_path: Path):
    ticks = iter([0.0, 1.0, 1.5])
    progress = ScanProgress(
        tmp_path / "data" / "discovered_friends.json",
        monotonic=lambda: next(ticks),
    )

    progress.update("releasing_browser_lock")
    payload = progress.finish("completed")

    assert payload["timings"] == {
        "waiting_browser": 1.0,
        "releasing_browser_lock": 0.5,
    }


def test_virtualized_row_reuse_keeps_each_avatar_with_its_atomic_row_snapshot(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()

    class NoScroll:
        def count(self):
            return 0

        @property
        def first(self):
            return self

        def wait_for(self, **_kwargs):
            return None

    class ReusedAvatar:
        def __init__(self, page, index):
            self.page = page
            self.index = index

        @property
        def first(self):
            return self

        def get_attribute(self, attribute):
            return self.page.rows[self.page.phase][self.index]["source"] if attribute == "src" else None

        def screenshot(self, path: str, timeout=None):
            row = self.page.rows[self.page.phase][self.index]
            Path(path).write_bytes(row["avatar"])
            if self.index == 0:
                self.page.phase = 1

    class ReusedRow:
        def __init__(self, page, index):
            self.page = page
            self.index = index

        def get_attribute(self, _attribute):
            return None

        def locator(self, selector):
            row = self.page.rows[self.page.phase][self.index]
            if selector == self.page.selectors.conversation_name:
                return FakeTextLocator(row["name"])
            if selector == "img":
                return ReusedAvatar(self.page, self.index)
            raise AssertionError(f"unexpected selector: {selector}")

    class ReusedConversationList:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 2

        def nth(self, index):
            return ReusedRow(self.page, index)

    class ReusedPage:
        def __init__(self):
            self.selectors = selectors
            self.phase = 0
            self.rows = [
                [
                    {"name": "甲", "source": "https://avatar/alpha", "avatar": b"avatar-alpha"},
                    {"name": "乙", "source": "https://avatar/bravo", "avatar": b"avatar-bravo"},
                ],
                [
                    {"name": "丙", "source": "https://avatar/charlie", "avatar": b"avatar-charlie"},
                    {"name": "丁", "source": "https://avatar/delta", "avatar": b"avatar-delta"},
                ],
            ]

        def locator(self, selector):
            if selector == self.selectors.conversation:
                return ReusedConversationList(self)
            if selector == self.selectors.conversation_list:
                return NoScroll()
            raise AssertionError(f"unexpected selector: {selector}")

    cache = tmp_path / "data" / "avatar-cache"
    result = discover_friends(
        AppConfig(),
        ReusedPage(),
        selectors,
        tmp_path / "data" / "discovered_friends.json",
        avatar_cache_dir=cache,
        max_scrolls=0,
    )

    candidates = {candidate.display_name: candidate for candidate in result.candidates}
    assert set(candidates) == {"丙", "丁"}
    assert (cache / f"{candidates['丙'].avatar_cache_key}.png").read_bytes() == b"avatar-charlie"
    assert (cache / f"{candidates['丁'].avatar_cache_key}.png").read_bytes() == b"avatar-delta"


def test_avatar_capture_uses_the_same_browser_context_image_response_before_screenshot(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()
    image = Image.new("RGB", (2, 2), "red")
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    requested = []

    class Response:
        ok = True

        def body(self):
            return image_bytes.getvalue()

    class Request:
        def get(self, source, timeout):
            requested.append((source, timeout))
            return Response()

    class NoScreenshotAvatar(FakeAvatarLocator):
        def screenshot(self, *_args, **_kwargs):
            raise AssertionError("the image response should be used before screenshot fallback")

    class DirectImageItem(FakeConversationItem):
        def locator(self, selector: str):
            if selector == "img":
                return NoScreenshotAvatar(self.avatar, self.avatar_source)
            return super().locator(selector)

    page = FakePage(
        selectors,
        [[DirectImageItem(selectors, "直连头像", b"unused", "row-direct", "https://avatar/direct")]],
    )
    page.context = SimpleNamespace(request=Request())
    cache = tmp_path / "data" / "avatar-cache"

    result = discover_friends(
        AppConfig(),
        page,
        selectors,
        tmp_path / "data" / "discovered_friends.json",
        avatar_cache_dir=cache,
        max_scrolls=0,
    )

    candidate = result.candidates[0]
    assert requested == [("https://avatar/direct", 2000)]
    assert candidate.avatar_status == "cached"
    assert Image.open(cache / f"{candidate.avatar_cache_key}.png").format == "PNG"


def test_unstable_virtual_row_uses_fallback_instead_of_another_rows_avatar(tmp_path: Path):
    selectors = ChatSelectors.test_defaults()

    class NoScroll:
        def count(self): return 0
        @property
        def first(self): return self
        def wait_for(self, **_kwargs): return None

    class FlippingRow:
        def __init__(self, page): self.page = page
        def get_attribute(self, _attribute): return None
        def locator(self, selector):
            current = self.page.rows[self.page.phase]
            if selector == self.page.selectors.conversation_name:
                return FakeTextLocator(current["name"])
            if selector == "img":
                return self
            raise AssertionError(selector)
        @property
        def first(self): return self
        def get_attribute(self, attribute):
            return self.page.rows[self.page.phase]["source"] if attribute == "src" else None
        def screenshot(self, path: str, timeout=None):
            Path(path).write_bytes(self.page.rows[self.page.phase]["avatar"])
            self.page.phase = 1 - self.page.phase

    class Page:
        def __init__(self):
            self.selectors = selectors
            self.phase = 0
            self.rows = [
                {"name": "甲", "source": "https://avatar/a", "avatar": b"a"},
                {"name": "乙", "source": "https://avatar/b", "avatar": b"b"},
            ]
        def locator(self, selector):
            if selector == self.selectors.conversation:
                return self
            if selector == self.selectors.conversation_list:
                return NoScroll()
            raise AssertionError(selector)
        def count(self): return 1
        def nth(self, _index): return FlippingRow(self)

    result = discover_friends(
        AppConfig(), Page(), selectors,
        tmp_path / "data" / "discovered_friends.json",
        avatar_cache_dir=tmp_path / "data" / "avatar-cache", max_scrolls=0,
    )

    assert result.candidates[0].avatar_status == "missing"
    assert result.candidates[0].avatar_cache_path is None

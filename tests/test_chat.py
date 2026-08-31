from dataclasses import replace
from pathlib import Path
from datetime import date

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from autody.chat import (
    ChatPageLoadError,
    ChatNavigationInterrupted,
    ChatSelectors,
    DOUYIN_CONFIRMATION_SELECTORS,
    DOUYIN_SELECTORS,
    DeliveryStatus,
    DouyinChat,
    TodayOutgoingStatus,
    conversation_candidate_id,
    conversation_row_candidate_id,
    conversation_row_identity,
    conversation_row_locator,
    normalize_message_text,
    open_chat,
)


@pytest.fixture
def fake_chat(page, tmp_path: Path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    return DouyinChat(
        page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0
    )


def test_send_confirms_exact_target_and_message(page, fake_chat):
    result = fake_chat.send("小明", "早安")
    assert result.status is DeliveryStatus.CONFIRMED
    assert result.confirmation_provenance == "post_send_observed"
    assert page.locator('[data-e2e="message-text"]', has_text="早安").count() == 1


def test_pre_send_same_text_does_not_confirm_delivery(page, fake_chat):
    page.locator('[data-e2e="message-list"]').evaluate(
        "(el) => { const p=document.createElement('p'); p.dataset.e2e='message-text'; p.textContent='早安'; el.append(p); }"
    )
    result = fake_chat.send("小明", "早安")
    assert result.status is DeliveryStatus.CONFIRMED
    assert result.send_attempts == 1
    assert result.confirmation_provenance == "post_send_observed"
    assert page.locator('[data-e2e="message-text"]', has_text="早安").count() == 2


def test_duplicate_names_are_rejected(page, fake_chat):
    page.locator('[data-e2e="conversation-item"]').evaluate(
        "el => el.parentNode.appendChild(el.cloneNode(true))"
    )
    result = fake_chat.send("小明", "早安")
    assert result.status is DeliveryStatus.SEND_FAILED
    assert result.send_attempts == 0
    assert "ambiguous" in (result.error or "")


def test_navigation_failure_is_not_recorded_as_a_send_attempt(monkeypatch, fake_chat):
    composer_accessed = False

    def fail_before_composer(_target):
        raise RuntimeError("target not found")

    def composer_editor():
        nonlocal composer_accessed
        composer_accessed = True
        raise AssertionError("composer must not be accessed after navigation failure")

    monkeypatch.setattr(fake_chat, "open_verified_conversation", fail_before_composer)
    monkeypatch.setattr(fake_chat, "composer_editor", composer_editor)

    result = fake_chat.send("小明", "早安")

    assert result.status is DeliveryStatus.SEND_FAILED
    assert result.send_attempts == 0
    assert composer_accessed is False


def test_stable_binding_navigation_is_used_before_composer(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)

    result = fake_chat.send(
        "小明",
        "早安",
        selected_target_id="target-current",
        expected_conversation_id=expected,
    )

    assert result.successful


def test_runtime_model_separates_friend_identity_from_conversation_locator(
    page, fake_chat
):
    row = page.locator('[data-e2e="conversation-item"]').first
    row.evaluate(
        """element => {
            for (const attribute of ['data-conversation-id', 'data-id', 'data-key']) {
                element.removeAttribute(attribute);
            }
            element.__reactFiber$autody = {
                memoizedProps: {
                    conversation: {
                        toParticipantSecUserId: 'durable-friend-proof',
                        id: 'current-conversation-locator'
                    }
                },
                pendingProps: null,
                return: null
            };
        }"""
    )

    identity_key, identity_source = conversation_row_identity(row)
    locator = conversation_row_locator(row)

    assert identity_source == "participant_sec_user_id"
    assert identity_key is not None
    assert locator is not None
    assert locator != conversation_candidate_id(identity_key)


def test_authoritative_ids_allow_a_lagging_title(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    page.locator('[data-e2e="chat-header-name"]').evaluate(
        """element => {
            element.textContent = '';
            setTimeout(() => { element.textContent = '小明 · 在线'; }, 250);
        }"""
    )

    identity = fake_chat.open_conversation_identity(
        "target-current",
        expected,
        "小明",
        timeout_ms=600,
    )

    assert identity.identity_match is True
    assert identity.identity_match_reason == "stable_id_match_title_warning"


def test_title_mismatch_alone_does_not_override_authoritative_ids(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    page.locator('[data-e2e="chat-header-name"]').evaluate(
        "element => { element.textContent = '仍在更新'; }"
    )

    identity = fake_chat.open_conversation_identity(
        "target-current",
        expected,
        "小明",
        timeout_ms=500,
    )

    assert identity.identity_match is True
    assert identity.identity_match_reason == "stable_id_match_title_warning"


def test_conflicting_visible_stable_id_stops_before_composer(
    page, monkeypatch, fake_chat
):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    composer_accessed = False
    page.locator('[data-e2e="visible-conversation"]').evaluate(
        "element => { element.dataset.conversationId = 'conversation-other'; }"
    )

    def composer_editor():
        nonlocal composer_accessed
        composer_accessed = True
        raise AssertionError("visible identity conflict must stop before composer")

    monkeypatch.setattr(fake_chat, "composer_editor", composer_editor)
    result = fake_chat.send(
        "小明",
        "早安",
        selected_target_id="target-current",
        expected_conversation_id=expected,
    )

    assert result.status is DeliveryStatus.BLOCKED
    assert result.send_attempts == 0
    assert result.reason_code == "binding_stale"
    assert composer_accessed is False


def test_selected_row_identity_change_fails_verification(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    row.evaluate(
        """element => {
            element.addEventListener('click', () => {
                setTimeout(() => {
                    element.dataset.conversationId = 'conversation-other';
                }, 150);
            }, { once: true });
        }"""
    )

    identity = fake_chat.open_conversation_identity(
        "target-current",
        expected,
        "小明",
        timeout_ms=500,
    )

    assert identity.identity_match is False
    assert identity.identity_match_reason == "stable_id_mismatch"


def test_virtualized_row_replacement_keeps_stable_identity(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    row.evaluate(
        """element => {
            element.addEventListener('click', () => {
                setTimeout(() => {
                    element.replaceWith(element.cloneNode(true));
                }, 50);
            }, { once: true });
        }"""
    )

    identity = fake_chat.open_conversation_identity(
        "target-current",
        expected,
        "小明",
        timeout_ms=600,
    )

    assert identity.identity_match is True


def test_first_transient_read_is_not_counted_as_stable(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    row.evaluate(
        """element => {
            element.addEventListener('click', () => {
                setTimeout(() => {
                    document.querySelector('[data-e2e="visible-conversation"]')
                        .dataset.conversationId = 'conversation-other';
                }, 150);
            }, { once: true });
        }"""
    )

    identity = fake_chat.open_conversation_identity(
        "target-current",
        expected,
        "小明",
        timeout_ms=500,
    )

    assert identity.identity_match is False
    assert identity.identity_match_reason == "stable_id_mismatch"


def test_stability_window_waits_until_expected_row_is_selected(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    row.evaluate(
        """element => {
            element.addEventListener('click', () => {
                element.setAttribute('aria-selected', 'false');
                setTimeout(() => {
                    element.setAttribute('aria-selected', 'true');
                }, 150);
            }, { once: true });
        }"""
    )

    identity = fake_chat.open_conversation_identity(
        "target-current",
        expected,
        "小明",
        timeout_ms=600,
    )

    assert identity.identity_match is True


def test_navigation_only_fixture_never_accesses_composer(
    page, monkeypatch, fake_chat
):
    row = page.locator('[data-e2e="conversation-item"]').first
    expected = conversation_row_candidate_id(row)
    composer_accessed = False

    def composer_editor():
        nonlocal composer_accessed
        composer_accessed = True
        raise AssertionError("navigation-only verification cannot inspect composer")

    monkeypatch.setattr(fake_chat, "composer_editor", composer_editor)
    identity = fake_chat.open_conversation_identity(
        "target-current",
        expected,
        "小明",
        timeout_ms=500,
    )

    assert identity.identity_match is True
    assert composer_accessed is False


def test_stale_stable_binding_stops_before_composer(page, monkeypatch, fake_chat):
    composer_accessed = False

    def composer_editor():
        nonlocal composer_accessed
        composer_accessed = True
        raise AssertionError("identity failure must stop before composer access")

    monkeypatch.setattr(fake_chat, "composer_editor", composer_editor)

    result = fake_chat.send(
        "小明",
        "早安",
        selected_target_id="target-current",
        expected_conversation_id="candidate-stale",
    )

    assert result.status is DeliveryStatus.BLOCKED
    assert result.send_attempts == 0
    assert result.reason_code == "binding_stale"
    assert composer_accessed is False


def test_header_mismatch_is_blocked(page, fake_chat):
    page.set_default_timeout(300)
    page.locator('[data-e2e="chat-header-name"]').evaluate("el => el.textContent='小红'")
    result = fake_chat.send("小明", "早安")
    assert result.status is DeliveryStatus.BLOCKED


def test_send_waits_for_header_to_switch_to_target(page, fake_chat):
    page.locator('[data-e2e="chat-header-name"]').evaluate(
        "el => { el.textContent='小红'; setTimeout(() => el.textContent='小明', 100); }"
    )
    assert fake_chat.send("小明", "早安").successful


def test_production_confirmation_selector_is_isolated_and_scoped():
    assert DOUYIN_SELECTORS.header_name == ".RightPanelHeadertitle"
    assert DOUYIN_CONFIRMATION_SELECTORS.outgoing_message_text.startswith(
        ".componentsRightPanelwrapper .MessageBoxContentactiveClickArea"
    )
    assert DOUYIN_CONFIRMATION_SELECTORS.history_container == (
        ".componentsRightPanelwrapper .messageMessageListlist"
    )
    assert not hasattr(DOUYIN_SELECTORS, "message_text")


def test_conversation_preview_is_not_accepted_as_sent_message(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator("body").evaluate(
        "el => { const preview=document.createElement('pre'); preview.textContent='早安'; el.append(preview); }"
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)
    assert chat.send("小明", "早安").successful
    assert page.locator('[data-e2e="message-text"]', has_text="早安").count() == 1


def test_editor_container_uses_contenteditable_descendant(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="chat-input"]').evaluate(
        "el => { const wrapper=document.createElement('div'); wrapper.className='editor-wrapper'; el.parentNode.insertBefore(wrapper, el); wrapper.append(el); }"
    )
    selectors = replace(ChatSelectors.test_defaults(), input=".editor-wrapper")
    chat = DouyinChat(page, selectors, tmp_path, confirmation_delay_ms=0)
    assert chat.send("小明", "早安").successful


def test_douyin_current_conversation_class_is_recognized_as_selected(page, fake_chat):
    row = page.locator('[data-e2e="conversation-item"]')
    row.evaluate(
        """element => {
            element.removeAttribute('aria-selected');
            element.className = 'conversationConversationItemwrapper conversationConversationItemcurConversation';
        }"""
    )

    assert fake_chat._row_is_selected(row) is True


def test_identity_navigation_can_be_interrupted_before_composer_access(page, fake_chat):
    checks = 0

    def interrupt_requested():
        nonlocal checks
        checks += 1
        return "pause"

    with pytest.raises(ChatNavigationInterrupted) as interrupted:
        fake_chat.open_conversation_identity(
            "target-a",
            "conversation-a",
            "小明",
            timeout_ms=1000,
            interrupt_requested=interrupt_requested,
        )

    assert interrupted.value.kind == "pause"
    assert checks == 1


def test_send_rejects_optimistic_bubble_that_disappears(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="chat-input"]').evaluate(
        "el => el.addEventListener('keydown', event => { if (event.key === 'Enter') setTimeout(() => document.querySelector('.MessageItemTextisFromMe')?.remove(), 50); })"
    )
    chat = DouyinChat(
        page,
        ChatSelectors.test_defaults(),
        tmp_path,
        confirmation_delay_ms=150,
        confirmation_retries=1,
    )
    result = chat.send("小明", "早安")
    assert result.status is DeliveryStatus.CONFIRMATION_FAILED
    assert result.screenshot_path is not None


def test_find_target_scrolls_conversation_list(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="conversation-item"]').evaluate("el => el.remove()")
    page.locator('[data-e2e="chat-app"]').evaluate(
        """el => {
          el.style.height='100px'; el.style.overflow='auto';
          const spacer=document.createElement('div'); spacer.style.height='500px'; el.prepend(spacer);
          el.addEventListener('scroll', () => {
            if (el.querySelector('[data-late-target]')) return;
            const button=document.createElement('button');
            button.dataset.e2e='conversation-item'; button.dataset.lateTarget='1';
            button.innerHTML='<span data-e2e="conversation-name">小明</span>';
            el.append(button);
          });
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)
    assert chat.send("小明", "早安").successful


def test_confirmation_normalizes_whitespace_and_line_endings(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        "(el) => { const p=document.createElement('p'); p.dataset.e2e='message-text'; p.textContent='你好\\n  gpt小助手'; el.append(p); }"
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)
    assert chat._latest_matches("你好\r\ngpt小助手")
    assert chat._matching_outgoing_count("你好\r\ngpt小助手") == 1
    assert normalize_message_text("你好\r\n gpt小助手") == "你好 gpt小助手"


def test_latest_outgoing_uses_visual_order_for_reversed_douyin_dom(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
          el.style.position='relative'; el.style.height='240px';
          const latest=document.createElement('p'); latest.dataset.e2e='message-text'; latest.textContent='最新消息'; latest.style.position='absolute'; latest.style.top='180px';
          const old=document.createElement('p'); old.dataset.e2e='message-text'; old.textContent='旧消息'; old.style.position='absolute'; old.style.top='20px';
          el.append(latest, old);
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    assert chat._latest_outgoing_text() == "最新消息"


def test_today_history_audit_confirms_any_current_day_outgoing(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const time = document.createElement('time');
            time.textContent = '今天 08:00';
            const message = document.createElement('p');
            message.dataset.e2e = 'message-text'; message.textContent = '早安';
            el.append(time, message);
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.CONFIRMED_SENT
    assert audit.reason == "today_outgoing_found"


def test_today_history_audit_ignores_text_and_stops_at_newest_snapshot(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const time = document.createElement('time'); time.textContent = '今天 08:00';
            const outgoing = document.createElement('p'); outgoing.dataset.e2e = 'message-text'; outgoing.textContent = '不同文案';
            const incoming = document.createElement('p'); incoming.textContent = '收到';
            el.append(time, outgoing, incoming);
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.CONFIRMED_SENT
    assert audit.snapshots == 1
    assert audit.scrolls == 0


def test_today_history_audit_prefers_message_timestamp_over_date_separator(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const separator = document.createElement('time'); separator.textContent = '昨天 21:00';
            const outgoing = document.createElement('p'); outgoing.dataset.e2e = 'message-text';
            outgoing.dataset.timestamp = String(Date.UTC(2026, 7, 30, 8, 0, 0)); outgoing.textContent = '不同文案';
            el.append(separator, outgoing);
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.CONFIRMED_SENT
    assert audit.boundary == "message_timestamp"


def test_today_history_audit_does_not_confirm_same_text_without_today_evidence(
    page, tmp_path
):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        "(el) => { const p=document.createElement('p'); p.dataset.e2e='message-text'; p.textContent='早安'; el.append(p); }"
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.UNKNOWN
    assert audit.reason == "date_evidence_unavailable"


def test_today_history_audit_requires_a_boundary_before_missing(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const time = document.createElement('time');
            time.textContent = '昨天 21:00';
            const message = document.createElement('p');
            message.dataset.e2e = 'message-text'; message.textContent = '早安';
            el.append(time, message);
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.CONFIRMED_MISSING
    assert audit.boundary == "prior_day_marker"
    assert audit.reason == "previous_day_boundary_reached"


def test_today_history_audit_returns_unknown_when_one_snapshot_crosses_dates(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const today = document.createElement('time'); today.textContent = '今天 08:00';
            const yesterday = document.createElement('time'); yesterday.textContent = '昨天 21:00';
            const outgoing = document.createElement('p'); outgoing.dataset.e2e = 'message-text'; outgoing.textContent = '早安';
            el.append(today, outgoing, yesterday);
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.UNKNOWN
    assert audit.reason == "date_boundary_ambiguous"


def test_today_history_audit_loads_older_history_until_a_previous_day_boundary(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            el.style.height = '80px'; el.style.overflow = 'auto';
            const spacer = document.createElement('div'); spacer.style.height = '240px'; el.append(spacer);
            let reachedBottom = false;
            el.addEventListener('scroll', () => {
              if (el.scrollTop > 1) reachedBottom = true;
              if (reachedBottom && el.scrollTop <= 1 && !el.querySelector('time')) {
                const time = document.createElement('time'); time.textContent = '昨天 21:00'; el.prepend(time);
              }
            });
        }"""
    )
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.CONFIRMED_MISSING
    assert audit.reason == "previous_day_boundary_reached"


def test_today_history_audit_accepts_the_true_history_start_as_a_missing_boundary(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.CONFIRMED_MISSING
    assert audit.boundary == "history_start"
    assert audit.reason == "history_start_reached"


def test_today_history_audit_returns_unknown_when_history_container_is_unavailable(page, tmp_path):
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator('[data-e2e="message-list"]').evaluate("el => el.remove()")
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0)

    audit = chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is TodayOutgoingStatus.UNKNOWN
    assert audit.reason == "date_evidence_unavailable"


def test_open_chat_bounds_page_load_and_closes_resources(monkeypatch, tmp_path: Path):
    state = {"context_closed": False, "playwright_stopped": False}

    class FakePage:
        def goto(self, _url, **kwargs):
            assert kwargs == {"wait_until": "domcontentloaded", "timeout": 321}
            raise PlaywrightTimeoutError("page timed out")

    class FakeContext:
        pages = [FakePage()]

        def set_default_timeout(self, timeout):
            assert timeout == 321

        def close(self):
            state["context_closed"] = True

    class FakeChromium:
        def launch_persistent_context(self, profile_dir, **kwargs):
            assert profile_dir == str(tmp_path / "profile")
            assert kwargs == {"headless": True, "timeout": 321}
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            state["playwright_stopped"] = True

    class FakePlaywrightFactory:
        def start(self):
            return FakePlaywright()

    monkeypatch.setattr("autody.chat.sync_playwright", lambda: FakePlaywrightFactory())

    with pytest.raises(ChatPageLoadError, match="page load timed out"):
        with open_chat(tmp_path / "profile", timeout_ms=321):
            pass

    assert state == {"context_closed": True, "playwright_stopped": True}

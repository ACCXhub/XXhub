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
    TodayOutgoingAudit,
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


def test_confirmation_accepts_new_message_identity_when_virtual_dom_replaces_same_text(
    page, fake_chat
):
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const historical = document.createElement('p');
            historical.dataset.e2e = 'message-text';
            historical.dataset.messageId = 'historical-same-text';
            historical.textContent = '早安';
            el.append(historical);
        }"""
    )
    page.locator('[data-e2e="chat-input"]').evaluate(
        """editor => editor.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            const messages = document.querySelector('[data-e2e="message-list"]');
            messages.querySelector('[data-message-id="historical-same-text"]')?.remove();
        })"""
    )

    result = fake_chat.send("小明", "早安")

    assert result.status is DeliveryStatus.CONFIRMED
    assert page.locator('[data-e2e="message-text"]', has_text="早安").count() == 1


def test_missing_to_sent_without_new_message_proof_never_confirms(
    page, fake_chat
):
    page.locator('[data-e2e="chat-input"]').evaluate(
        """editor => editor.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            const message = document.querySelector('[data-e2e="message-list"] p:last-child');
            message?.removeAttribute('data-message-id');
            const marker = document.createElement('time');
            marker.textContent = '今天 08:00';
            document.querySelector('[data-e2e="message-list"]').append(marker);
        })"""
    )

    result = fake_chat.send(
        "小明",
        "早安",
        pre_send_audit=TodayOutgoingAudit(TodayOutgoingStatus.CONFIRMED_MISSING),
        delivery_day=date(2026, 8, 30),
    )

    assert result.status is DeliveryStatus.CONFIRMATION_FAILED
    assert result.confirmation_provenance.value == "none"


def test_historical_same_text_without_a_new_identity_never_confirms(page, fake_chat):
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const historical = document.createElement('p');
            historical.dataset.e2e = 'message-text';
            historical.dataset.messageId = 'historical-same-text';
            historical.textContent = '早安';
            el.append(historical);
        }"""
    )

    status, _attempts = fake_chat._confirm_delivery(
        "早安",
        pre_send_identities=fake_chat._outgoing_message_identities(),
    )

    assert status is None


def test_rerendered_weak_react_key_never_proves_a_new_outgoing(page, fake_chat):
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const item = document.createElement('p');
            const text = document.createElement('span');
            text.dataset.e2e = 'message-text';
            text.textContent = '早安';
            Object.defineProperty(item, '__reactFiber$test', {
              configurable: true, value: { key: 'old-render-key' }
            });
            item.append(text); el.append(item);
        }"""
    )
    page.locator('[data-e2e="chat-input"]').evaluate(
        """editor => editor.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            event.preventDefault(); event.stopImmediatePropagation();
            const item = document.querySelector('[data-e2e="message-list"] p');
            Object.defineProperty(item, '__reactFiber$test', {
              configurable: true, value: { key: 'new-render-key' }
            });
            editor.textContent = '';
        }, true)"""
    )

    result = fake_chat.send("小明", "早安")

    assert result.status is DeliveryStatus.CONFIRMATION_FAILED
    assert result.confirmation_provenance.value == "none"


def test_generic_today_marker_does_not_date_an_unrelated_old_outgoing(page, fake_chat):
    page.locator('[data-e2e="message-list"]').evaluate(
        """el => {
            const marker = document.createElement('time');
            marker.textContent = '今天 08:00';
            const old = document.createElement('p');
            old.dataset.e2e = 'message-text'; old.textContent = '昨天的消息';
            el.append(marker, old);
        }"""
    )

    audit = fake_chat.audit_today_outgoing(date(2026, 8, 30))

    assert audit.status is not TodayOutgoingStatus.CONFIRMED_SENT


def test_composer_clear_without_new_outgoing_never_confirms(page, fake_chat):
    page.locator('[data-e2e="chat-input"]').evaluate(
        """editor => editor.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            event.preventDefault(); event.stopImmediatePropagation();
            editor.textContent = '';
        }, true)"""
    )

    result = fake_chat.send("小明", "早安")

    assert result.status is DeliveryStatus.CONFIRMATION_FAILED


def test_matching_new_direct_timestamp_identity_confirms(page, fake_chat):
    page.locator('[data-e2e="chat-input"]').evaluate(
        """editor => editor.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            event.preventDefault(); event.stopImmediatePropagation();
            const text = document.createElement('span');
            text.dataset.e2e = 'message-text';
            text.dataset.timestamp = '2026-08-30T08:00:01';
            text.textContent = editor.textContent;
            document.querySelector('[data-e2e="message-list"]').append(text);
            editor.textContent = '';
        }, true)"""
    )

    result = fake_chat.send("小明", "早安")

    assert result.status is DeliveryStatus.CONFIRMED
    assert result.confirmation_provenance.value == "post_send_observed"


def test_post_enter_bounded_observation_can_find_a_later_durable_message(
    page, fake_chat, monkeypatch
):
    page.locator('[data-e2e="chat-input"]').evaluate(
        """editor => editor.addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); event.stopImmediatePropagation(); }
        }, true)"""
    )
    original = fake_chat._outgoing_message_identities
    calls = 0

    def delayed_observation():
        nonlocal calls
        calls += 1
        if calls == 3:
            page.locator('[data-e2e="message-list"]').evaluate(
                """el => {
                    const item = document.createElement('p');
                    item.dataset.messageId = 'post-observation-id';
                    const text = document.createElement('span');
                    text.dataset.e2e = 'message-text'; text.textContent = '早安';
                    item.append(text); el.append(item);
                }"""
            )
        return original()

    fake_chat.confirmation_retries = 1
    monkeypatch.setattr(fake_chat, "_outgoing_message_identities", delayed_observation)

    result = fake_chat.send("小明", "早安")

    assert result.status is DeliveryStatus.RETRY_CONFIRMED
    assert result.confirmation_provenance.value == "post_send_observed"


def test_post_enter_live_audit_cannot_manufacture_post_send_provenance(
    page, fake_chat, monkeypatch
):
    page.locator('[data-e2e="chat-input"]').evaluate(
        """editor => editor.addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); event.stopImmediatePropagation(); }
        }, true)"""
    )
    audits = []

    def sent_audit(_day):
        audits.append(_day)
        return TodayOutgoingAudit(TodayOutgoingStatus.CONFIRMED_SENT)

    monkeypatch.setattr(fake_chat, "audit_today_outgoing", sent_audit)

    result = fake_chat.send(
        "小明",
        "早安",
        pre_send_audit=TodayOutgoingAudit(TodayOutgoingStatus.CONFIRMED_MISSING),
        delivery_day=date(2026, 8, 30),
    )

    assert result.status is DeliveryStatus.CONFIRMATION_FAILED
    assert result.confirmation_provenance.value == "none"
    assert audits == [date(2026, 8, 30)]


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


def test_locator_open_waits_for_lazy_conversation_list_growth(fake_chat):
    class Row:
        def __init__(self, participant: str, conversation: str):
            self.participant = participant
            self.conversation = conversation

        def evaluate(self, _script):
            return {
                "participantSecUserId": self.participant,
                "conversationId": self.conversation,
                "conversationShortId": None,
            }

        def get_attribute(self, _attribute):
            return None

    class Conversations:
        def __init__(self, page):
            self.page = page

        def count(self):
            return len(self.page.rows[self.page.position])

        def nth(self, index):
            return self.page.rows[self.page.position][index]

    class ConversationList:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 1

        @property
        def first(self):
            return self

        def evaluate(self, script, *_args):
            if "scrollTop = 0" in script:
                self.page.position = 0
                return None
            if "atOrigin" in script:
                return {"scrollTop": self.page.position, "atOrigin": self.page.position == 0}
            if "before" in script:
                return {
                    "before": self.page.position,
                    "maximum": 2 if self.page.waited_ms >= 500 else 1,
                    "step": 1,
                }
            if "maximum" in script:
                return {"maximum": 2 if self.page.waited_ms >= 500 else 1}
            self.page.position = min(self.page.position + 1, 2)
            return None

    class LazyPage:
        def __init__(self, selectors, target):
            self.selectors = selectors
            self.position = 0
            self.waited_ms = 0
            other = Row("other-proof", "other-conversation")
            self.rows = [[other], [other], [target]]
            self.conversations = Conversations(self)
            self.conversation_list = ConversationList(self)

        def locator(self, selector):
            if selector == self.selectors.conversation:
                return self.conversations
            if selector == self.selectors.conversation_list:
                return self.conversation_list
            raise AssertionError(f"unexpected selector: {selector}")

        def wait_for_timeout(self, delay):
            self.waited_ms += delay

    target = Row("target-proof", "fresh-conversation")
    page = LazyPage(fake_chat.selectors, target)
    fake_chat.page = page
    expected = conversation_row_candidate_id(target)

    found, count = fake_chat._find_conversation("target", expected)

    assert count == 1
    assert found is target
    assert page.waited_ms >= 500


def test_locator_waits_for_top_virtual_rows_to_settle_before_search(fake_chat):
    class Row:
        def __init__(self, participant: str, conversation: str):
            self.participant = participant
            self.conversation = conversation

        def evaluate(self, _script):
            return {
                "participantSecUserId": self.participant,
                "conversationId": self.conversation,
                "conversationShortId": None,
            }

        def get_attribute(self, _attribute):
            return None

    class Conversations:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 1

        def nth(self, _index):
            return self.page.target if self.page.settled else self.page.other

    class ConversationList:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 1

        @property
        def first(self):
            return self

        def evaluate(self, script, *_args):
            if "scrollTop = 0" in script:
                return None
            if "atOrigin" in script:
                return {"scrollTop": 0, "atOrigin": True}
            if "before" in script:
                return {"before": 0, "maximum": 0, "step": 1}
            if "maximum" in script:
                return {"maximum": 0}
            return None

    class SettlingPage:
        def __init__(self, selectors):
            self.selectors = selectors
            self.settled = False
            self.other = Row("other-proof", "other-conversation")
            self.target = Row("target-proof", "fresh-conversation")
            self.conversations = Conversations(self)
            self.conversation_list = ConversationList(self)

        def locator(self, selector):
            if selector == self.selectors.conversation:
                return self.conversations
            if selector == self.selectors.conversation_list:
                return self.conversation_list
            raise AssertionError(f"unexpected selector: {selector}")

        def wait_for_timeout(self, delay):
            if delay == 50:
                self.settled = True

    page = SettlingPage(fake_chat.selectors)
    fake_chat.page = page
    fake_chat.friend_search_timeout_ms = 200
    expected = conversation_row_candidate_id(page.target)

    found, count = fake_chat._find_conversation("target", expected)

    assert count == 1
    assert found is page.target


def test_sequential_lookup_restarts_from_fresh_virtual_list_origin(fake_chat):
    class Row:
        def __init__(self, page, name: str, participant: str, conversation: str):
            self.page = page
            self.name = name
            self.participant = participant
            self.conversation = conversation

        def evaluate(self, _script):
            return {
                "participantSecUserId": self.participant,
                "conversationId": self.conversation,
                "conversationShortId": None,
            }

        def get_attribute(self, _attribute):
            return None

        def click(self):
            self.page.opened.append(self.name)

    class ConversationSnapshot:
        def __init__(self, page):
            self.rows = list(page.visible_rows())

        def count(self):
            return len(self.rows)

        def nth(self, index):
            return self.rows[index]

    class ConversationList:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 1

        @property
        def first(self):
            return self

        def evaluate(self, script, *_args):
            if "scrollTop = 0" in script:
                self.page.origin_reset_pending = True
                return None
            if "atOrigin" in script:
                return {
                    "scrollTop": 0 if self.page.position == 0 else 400,
                    "atOrigin": self.page.position == 0,
                }
            if "before" in script:
                return {
                    "before": self.page.position,
                    "maximum": 1,
                    "step": 1,
                }
            self.page.position = min(1, self.page.position + 1)
            return None

    class VirtualPage:
        def __init__(self, selectors):
            self.selectors = selectors
            self.position = 0
            self.origin_reset_pending = False
            self.opened: list[str] = []
            self.rows = []
            self.conversation_list = ConversationList(self)

        def visible_rows(self):
            return self.rows[:2] if self.position == 0 else self.rows[2:]

        def locator(self, selector):
            if selector == self.selectors.conversation:
                return ConversationSnapshot(self)
            if selector == self.selectors.conversation_list:
                return self.conversation_list
            raise AssertionError(f"unexpected selector: {selector}")

        def wait_for_timeout(self, _delay):
            if self.origin_reset_pending:
                self.position = 0
                self.origin_reset_pending = False

    page = VirtualPage(fake_chat.selectors)
    rows = {
        name: Row(page, name, f"participant-{name}", f"conversation-{name}")
        for name in ("A", "B", "C", "D")
    }
    page.rows = [rows["C"], rows["A"], rows["B"], rows["D"]]
    fake_chat.page = page

    def locate_and_open(name: str):
        expected = conversation_row_candidate_id(rows[name])
        item, count = fake_chat._find_conversation(name, expected)
        assert count == 1
        item.click()

    locate_and_open("A")
    # A send moves A to the newest/top end, but the recycled list remains
    # scrolled to its older segment. B is now above that stale position.
    page.rows = [rows["A"], rows["B"], rows["C"], rows["D"]]
    page.position = 1
    locate_and_open("B")
    # The next send reorders again. C now requires a fresh origin followed by
    # the normal bounded forward search in the same chat instance.
    page.rows = [rows["B"], rows["A"], rows["C"], rows["D"]]
    page.position = 1
    locate_and_open("C")

    assert page.opened == ["A", "B", "C"]


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


def test_failure_evidence_snapshot_is_structural_and_never_reads_chat_or_composer_text(
    page, fake_chat
):
    page.locator('[data-e2e="chat-input"]').fill("私密草稿")
    page.locator('[data-e2e="message-list"]').evaluate(
        "el => { const item=document.createElement('p'); item.dataset.e2e='message-text'; item.textContent='私密聊天正文'; el.append(item); }"
    )

    snapshot = fake_chat.failure_evidence_snapshot("confirmation_observed")

    assert snapshot["page"]["url"].startswith("file:")
    assert snapshot["conversation_list"]["row_count"] == 1
    assert snapshot["composer"]["present"] is True
    assert snapshot["composer"]["contenteditable"] is True
    assert snapshot["history"]["outgoing_count"] == 1
    assert "私密草稿" not in repr(snapshot)
    assert "私密聊天正文" not in repr(snapshot)


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

    assert audit.status is TodayOutgoingStatus.UNKNOWN
    assert audit.reason == "date_evidence_unavailable"


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

    assert audit.status is TodayOutgoingStatus.UNKNOWN
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


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("authentication-required", ("login_required", "login")),
        ("risk-control-required", ("risk_control_required", "verification")),
        (None, None),
    ],
)
def test_page_failure_requires_one_explicit_visible_marker(marker, expected, tmp_path: Path):
    class Locator:
        def __init__(self, present):
            self.present = present
            self.first = self

        def count(self):
            return int(self.present)

        def is_visible(self):
            return self.present

    class Page:
        def locator(self, selector):
            return Locator(marker is not None and marker in selector)

    chat = DouyinChat(Page(), ChatSelectors.test_defaults(), tmp_path)

    assert chat.page_failure() == expected


def test_page_failure_prioritizes_explicit_verification_over_login_when_both_are_visible(
    tmp_path: Path,
):
    class Locator:
        def __init__(self, present):
            self.present = present
            self.first = self

        def count(self):
            return int(self.present)

        def is_visible(self):
            return self.present

    class Page:
        def locator(self, selector):
            return Locator(
                selector
                in {
                    ChatSelectors.test_defaults().authentication_marker,
                    ChatSelectors.test_defaults().risk_control_marker,
                }
            )

    chat = DouyinChat(Page(), ChatSelectors.test_defaults(), tmp_path)

    assert chat.page_failure() == ("risk_control_required", "verification")


def test_page_failure_keeps_a_hidden_marker_on_the_generic_safe_path(tmp_path: Path):
    class Locator:
        def __init__(self, selector):
            self.selector = selector
            self.first = self

        def count(self):
            return int(self.selector == ChatSelectors.test_defaults().risk_control_marker)

        def is_visible(self):
            return False

    class Page:
        def locator(self, selector):
            return Locator(selector)

    chat = DouyinChat(Page(), ChatSelectors.test_defaults(), tmp_path)

    assert chat.page_failure() is None


@pytest.mark.parametrize(
    ("failure", "reason_code"),
    [
        (("login_required", "login"), "login_required"),
        (("risk_control_required", "verification"), "risk_control_required"),
    ],
)
def test_explicit_page_condition_stops_send_before_the_send_boundary(
    failure, reason_code, tmp_path: Path
):
    class ClassifiedChat(DouyinChat):
        def page_failure(self):
            return failure

        def screenshot(self, _label):
            return tmp_path / "page-condition.png"

    result = ClassifiedChat(
        object(), ChatSelectors.test_defaults(), tmp_path, confirmation_delay_ms=0
    ).send("目标", "消息")

    assert result.reason_code == reason_code
    assert result.send_attempts == 0
    assert result.failure_marker == failure[1]


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

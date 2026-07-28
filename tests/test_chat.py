from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from autody.chat import (
    ChatPageLoadError,
    ChatSelectors,
    DOUYIN_CONFIRMATION_SELECTORS,
    DOUYIN_SELECTORS,
    DeliveryStatus,
    DouyinChat,
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
    assert page.locator('[data-e2e="message-text"]', has_text="早安").count() == 1


def test_existing_latest_message_is_not_sent_twice(page, fake_chat):
    page.locator('[data-e2e="message-list"]').evaluate(
        "(el) => { const p=document.createElement('p'); p.dataset.e2e='message-text'; p.textContent='早安'; el.append(p); }"
    )
    result = fake_chat.send("小明", "早安")
    assert result.send_attempts == 0
    assert page.locator('[data-e2e="message-text"]', has_text="早安").count() == 1


def test_duplicate_names_are_rejected(page, fake_chat):
    page.locator('[data-e2e="conversation-item"]').evaluate(
        "el => el.parentNode.appendChild(el.cloneNode(true))"
    )
    result = fake_chat.send("小明", "早安")
    assert result.status is DeliveryStatus.SEND_FAILED
    assert "ambiguous" in (result.error or "")


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
    result = chat.send("小明", "你好\r\ngpt小助手")
    assert result.status is DeliveryStatus.CONFIRMED
    assert result.send_attempts == 0
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

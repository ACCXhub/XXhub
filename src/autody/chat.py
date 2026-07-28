from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from autody.runtime import configure_runtime


CHAT_URL = "https://www.douyin.com/chat"
CONVERSATION_ID_ATTRIBUTES = (
    "data-conversation-id",
    "data-im-conversation-id",
    "data-id",
    "data-key",
)
_VOLATILE_AVATAR_QUERY_KEYS = {
    "auth_key", "expires", "signature", "timestamp", "ts", "x-expires",
    "x-signature", "x-tos-signature", "x-bce-date", "x-bce-expire", "x-bce-signature",
}


class FatalChatError(RuntimeError):
    """A failure for which sending to further targets is unsafe."""


class AuthenticationError(FatalChatError):
    pass


class ChatPageLoadError(FatalChatError):
    pass


class PageChangedError(FatalChatError):
    pass


@dataclass(frozen=True)
class ChatSelectors:
    conversation: str
    conversation_name: str
    conversation_list: str
    header_name: str
    input: str
    login_marker: str
    verification_marker: str

    @classmethod
    def test_defaults(cls):
        return cls(
            '[data-e2e="conversation-item"]',
            '[data-e2e="conversation-name"]',
            '[data-e2e="chat-app"]',
            '[data-e2e="chat-header-name"]',
            '[data-e2e="chat-input"]',
            '[data-e2e="conversation-item"]',
            "text=安全验证",
        )


@dataclass(frozen=True)
class ConfirmationSelectors:
    outgoing_message_text: str

    @classmethod
    def test_defaults(cls):
        return cls('[data-e2e="message-text"]')


@dataclass(frozen=True)
class ComposerState:
    visible_text: str
    normalized_text_length: int
    visible_text_present: bool
    attachment_present: bool
    mention_or_reply_present: bool
    composer_empty: bool
    reason: str


class DeliveryStatus(str, Enum):
    CONFIRMED = "confirmed"
    RETRY_CONFIRMED = "retry_confirmed"
    SEND_FAILED = "send_failed"
    CONFIRMATION_FAILED = "confirmation_failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    send_attempts: int = 0
    confirmation_attempts: int = 0
    screenshot_path: Path | None = None
    error: str | None = None

    @property
    def successful(self) -> bool:
        return self.status in {DeliveryStatus.CONFIRMED, DeliveryStatus.RETRY_CONFIRMED}


@dataclass(frozen=True)
class ConversationIdentity:
    expected_conversation_id: str | None
    visible_conversation_id: str | None
    selected_display_name: str
    visible_display_name: str | None
    identity_match: bool
    identity_match_reason: str


def opaque_conversation_identity(source: str, value: str) -> str:
    digest = hashlib.sha256(f"{source}\0{value}".encode("utf-8")).hexdigest()
    return f"{source}:{digest}"


def normalized_avatar_source(source: str) -> str:
    try:
        parsed = urlsplit(source)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _VOLATILE_AVATAR_QUERY_KEYS
        ]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    except ValueError:
        return source


def conversation_row_identity(item) -> tuple[str | None, str | None]:
    """Return the same opaque identity used by friend discovery."""
    for attribute in CONVERSATION_ID_ATTRIBUTES:
        try:
            value = item.get_attribute(attribute)
        except Exception:
            value = None
        if value:
            return opaque_conversation_identity("row", str(value)), "row_attribute"
    try:
        source = item.locator("img").first.get_attribute("src")
    except Exception:
        source = None
    if source:
        return opaque_conversation_identity("avatar", normalized_avatar_source(str(source))), "avatar_source"
    try:
        markup = item.evaluate("el => el.outerHTML")
    except Exception:
        markup = None
    if markup:
        return opaque_conversation_identity("row", str(markup)), "row_fingerprint"
    return None, None


def conversation_candidate_id(identity_key: str | None) -> str | None:
    if not identity_key:
        return None
    return f"candidate-{hashlib.sha256(identity_key.encode('utf-8')).hexdigest()[:32]}"


def conversation_row_candidate_id(item) -> str | None:
    identity_key, _ = conversation_row_identity(item)
    return conversation_candidate_id(identity_key)


# Centralized selectors based on the current douyin.com/chat page and the upstream
# DouYinSparkFlow dev branch. Adjust only this object when the page changes.
DOUYIN_SELECTORS = ChatSelectors(
    conversation=".conversationConversationItemwrapper",
    conversation_name=".conversationConversationItemtitle",
    conversation_list=".conversationConversationListwrapper",
    header_name=".RightPanelHeadertitle",
    input=".messageEditorimChatEditorContainer",
    login_marker=".conversationConversationListwrapper",
    verification_marker="text=/安全验证|扫码登录|登录后即可聊天/",
)

# Delivery confirmation selectors are deliberately isolated from navigation and
# editor selectors. A Douyin page change here must not alter friend search/send.
DOUYIN_CONFIRMATION_SELECTORS = ConfirmationSelectors(
    outgoing_message_text=".componentsRightPanelwrapper .MessageBoxContentactiveClickArea .MessageItemTextisFromMe .TextMessageTextpureText"
)


def normalize_message_text(value: str) -> str:
    return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())


class DouyinChat:
    def __init__(
        self,
        page: Page,
        selectors: ChatSelectors,
        artifact_dir: Path,
        confirmation_selectors: ConfirmationSelectors | None = None,
        confirmation_delay_ms: int = 2_000,
        confirmation_retries: int = 2,
        friend_search_timeout_ms: int = 30_000,
    ):
        self.page = page
        self.selectors = selectors
        self.artifact_dir = artifact_dir
        self.confirmation_selectors = confirmation_selectors or (
            ConfirmationSelectors.test_defaults()
            if selectors.login_marker.startswith('[data-e2e=')
            else DOUYIN_CONFIRMATION_SELECTORS
        )
        self.confirmation_delay_ms = confirmation_delay_ms
        self.confirmation_retries = confirmation_retries
        self.friend_search_timeout_ms = friend_search_timeout_ms

    def _latest_outgoing_text(self) -> str | None:
        messages = self.page.locator(self.confirmation_selectors.outgoing_message_text)
        if messages.count() == 0:
            return None
        # Douyin renders the message list in reverse DOM order. Selecting by the
        # largest visual bottom coordinate works for both normal and reversed
        # lists and therefore reflects the latest visible outgoing bubble.
        return messages.evaluate_all(
            """elements => elements
                .map(element => ({
                    text: element.innerText || element.textContent || '',
                    bottom: element.getBoundingClientRect().bottom
                }))
                .sort((left, right) => right.bottom - left.bottom)[0]?.text || null"""
        )

    def _latest_matches(self, message: str) -> bool:
        latest = self._latest_outgoing_text()
        return latest is not None and normalize_message_text(latest) == normalize_message_text(message)

    def _confirm_delivery(self, message: str) -> tuple[DeliveryStatus | None, int]:
        for attempt in range(1, self.confirmation_retries + 2):
            if self.confirmation_delay_ms:
                self.page.wait_for_timeout(self.confirmation_delay_ms)
            if self._latest_matches(message):
                status = DeliveryStatus.CONFIRMED if attempt == 1 else DeliveryStatus.RETRY_CONFIRMED
                return status, attempt
        return None, self.confirmation_retries + 1

    @staticmethod
    def _row_is_selected(item) -> bool:
        try:
            return bool(item.evaluate(
                """element => {
                    const selected = ['aria-selected', 'aria-current', 'data-selected', 'data-active']
                      .some(name => ['true', 'page'].includes(String(element.getAttribute(name)).toLowerCase()));
                    const classes = String(element.className || '');
                    return selected || classes.split(/\\s+/).some(
                      name => /(?:active|selected|current|curConversation)$/i.test(name)
                    );
                }"""
            ))
        except Exception:
            return False

    def _find_conversation(self, target: str, expected_conversation_id: str | None = None):
        conversations = self.page.locator(self.selectors.conversation)
        scrollable = self.page.locator(self.selectors.conversation_list)
        if scrollable.count():
            scrollable.first.evaluate("el => el.scrollTop = 0")
        deadline = time.monotonic() + self.friend_search_timeout_ms / 1000
        for _ in range(50):
            if expected_conversation_id:
                for index in range(conversations.count()):
                    item = conversations.nth(index)
                    if conversation_row_candidate_id(item) == expected_conversation_id:
                        return item, 1
            else:
                names = self.page.locator(
                    self.selectors.conversation_name,
                    has_text=re.compile(rf"^{re.escape(target)}$"),
                )
                matches = conversations.filter(has=names)
                count = matches.count()
                if count:
                    return matches, count
            if not scrollable.count():
                break
            position = scrollable.first.evaluate(
                """el => ({
                    before: el.scrollTop,
                    maximum: Math.max(0, el.scrollHeight - el.clientHeight),
                    step: Math.max(200, Math.floor(el.clientHeight * 0.7))
                })"""
            )
            if position["before"] >= position["maximum"]:
                break
            if time.monotonic() >= deadline:
                break
            scrollable.first.evaluate(
                "(el, step) => { el.scrollTop += step; el.dispatchEvent(new Event('scroll')); }",
                position["step"],
            )
            self.page.wait_for_timeout(250)
        if expected_conversation_id:
            return conversations, 0
        return matches, matches.count()

    def _visible_conversation(self) -> tuple[str | None, str | None]:
        header = self.page.locator(self.selectors.header_name)
        visible_name = header.first.inner_text().strip() if header.count() and header.first.is_visible() else None
        conversations = self.page.locator(self.selectors.conversation)
        for index in range(conversations.count()):
            item = conversations.nth(index)
            if self._row_is_selected(item):
                return conversation_row_candidate_id(item), visible_name
        return None, visible_name

    def open_conversation_identity(
        self,
        selected_target_id: str,
        expected_conversation_id: str | None,
        selected_display_name: str,
        *,
        timeout_ms: int,
    ) -> ConversationIdentity:
        """Open and stably verify one conversation without touching the composer."""
        if not expected_conversation_id:
            visible_id, visible_name = self._visible_conversation()
            return ConversationIdentity(
                expected_conversation_id,
                visible_id,
                selected_display_name,
                visible_name,
                False,
                "missing_expected_conversation_id",
            )
        item, count = self._find_conversation(selected_display_name, expected_conversation_id)
        if count != 1:
            visible_id, visible_name = self._visible_conversation()
            reason = "stable_id_mismatch" if visible_id else "conversation_not_found"
            return ConversationIdentity(
                expected_conversation_id,
                visible_id,
                selected_display_name,
                visible_name,
                False,
                reason,
            )

        item.click()
        header = self.page.locator(self.selectors.header_name)
        deadline = time.monotonic() + timeout_ms / 1000
        stable_reads = 0
        visible_id: str | None = None
        visible_name: str | None = None
        while time.monotonic() < deadline:
            try:
                visible_id, visible_name = self._visible_conversation()
            except Exception:
                visible_id, visible_name = None, None
            if (
                visible_id == expected_conversation_id
                and visible_name is not None
                and self._row_is_selected(item)
                and header.count()
                and header.first.is_visible()
            ):
                stable_reads += 1
                if stable_reads >= 2:
                    break
            else:
                stable_reads = 0
            self.page.wait_for_timeout(100)

        if stable_reads < 2:
            return ConversationIdentity(
                expected_conversation_id,
                visible_id,
                selected_display_name,
                visible_name,
                False,
                "navigation_not_stable",
            )
        if visible_id != expected_conversation_id:
            reason = "stable_id_mismatch"
        elif visible_name != selected_display_name:
            reason = "display_name_mismatch"
        else:
            reason = "stable_id_match"
        return ConversationIdentity(
            expected_conversation_id,
            visible_id,
            selected_display_name,
            visible_name,
            reason == "stable_id_match",
            reason,
        )

    def open_conversation(self, target: str) -> str:
        """Open one unambiguous chat and return the currently visible header.

        This navigation primitive deliberately does not inspect, write, or send
        any composer content.  It is shared by the normal sender and the
        Test Center's separately guarded dry-run operation.
        """
        matches, count = self._find_conversation(target)
        if count == 0:
            raise RuntimeError(f"target not found: {target}")
        if count > 1:
            raise RuntimeError(f"ambiguous target: {target}")
        matches.first.click()
        header = self.page.locator(self.selectors.header_name)
        header.first.wait_for(state="visible")
        return header.first.inner_text().strip()

    def open_verified_conversation(self, target: str) -> str:
        """Wait for the opened conversation header to prove the target identity."""
        self.open_conversation(target)
        header = self.page.locator(self.selectors.header_name).filter(
            has_text=re.compile(rf"^{re.escape(target)}$")
        )
        header.first.wait_for(state="visible")
        return header.first.inner_text().strip()

    def composer_editor(self):
        """Return the editable child used by the already-open conversation."""
        editor_container = self.page.locator(self.selectors.input)
        editor_container.wait_for(state="visible")
        editable_child = editor_container.locator("[contenteditable], textarea, input")
        return editable_child.last if editable_child.count() else editor_container

    def composer_state(self, editor=None) -> ComposerState:
        """Read one authoritative, visibility-aware snapshot of the composer."""
        editor = editor or self.composer_editor()
        raw = editor.evaluate(
            """element => {
                const visible = node => {
                    if (!(node instanceof Element)) return false;
                    const style = getComputedStyle(node);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                };
                const matchesVisible = selectors => Array.from(
                    element.matches(selectors) ? [element] : element.querySelectorAll(selectors)
                ).some(visible);
                const parentMatchesVisible = selectors => Array.from(
                    element.parentElement?.querySelectorAll(selectors) || []
                ).some(visible);
                const attachmentSelectors = [
                    'video', '[data-e2e="chat-attachment"]',
                    '[class*="attachment" i]', '[class*="uploadPreview" i]',
                    '[class*="filePreview" i]'
                ].join(',');
                const mentionSelectors = [
                    '[data-e2e="mention-chip"]', '[data-e2e="reply-chip"]',
                    '[class*="mention" i]', '[class*="reply" i][contenteditable="false"]'
                ].join(',');
                const emojiSelectors = [
                    'img[data-e2e="emoji"]', 'img[class*="emoji" i]'
                ].join(',');
                const text = element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement
                    ? element.value
                    : (element.innerText || '');
                const emojiText = Array.from(element.querySelectorAll(emojiSelectors))
                    .filter(visible)
                    .map(node => node.getAttribute('alt') || '')
                    .join('');
                const visibleText = `${text}${emojiText}`
                    .replace(/[\\u200b-\\u200d\\u2060\\ufeff]/g, '');
                const normalized = visibleText
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\s+/g, '');
                const attachment = matchesVisible(attachmentSelectors)
                    || parentMatchesVisible(attachmentSelectors);
                const mentionOrReply = matchesVisible(mentionSelectors);
                return {
                    visibleText,
                    normalizedTextLength: Array.from(normalized).length,
                    attachment,
                    mentionOrReply
                };
            }"""
        )
        visible_text_present = int(raw["normalizedTextLength"]) > 0
        attachment_present = bool(raw["attachment"])
        mention_or_reply_present = bool(raw["mentionOrReply"])
        if attachment_present:
            reason = "attachment"
        elif mention_or_reply_present:
            reason = "mention_or_reply"
        elif visible_text_present:
            reason = "visible_text"
        else:
            reason = "empty"
        return ComposerState(
            visible_text=str(raw["visibleText"]),
            normalized_text_length=int(raw["normalizedTextLength"]),
            visible_text_present=visible_text_present,
            attachment_present=attachment_present,
            mention_or_reply_present=mention_or_reply_present,
            composer_empty=reason == "empty",
            reason=reason,
        )

    def send(self, target: str, message: str) -> DeliveryResult:
        try:
            self.open_verified_conversation(target)
            if self._latest_matches(message):
                return DeliveryResult(
                    DeliveryStatus.CONFIRMED,
                    send_attempts=0,
                    confirmation_attempts=1,
                )
            editor = self.composer_editor()
            editor.fill(message)
            editor.press("Enter")
            status, attempts = self._confirm_delivery(message)
            if status:
                return DeliveryResult(status, send_attempts=1, confirmation_attempts=attempts)
            screenshot = self.screenshot("confirmation-failed")
            return DeliveryResult(
                DeliveryStatus.CONFIRMATION_FAILED,
                send_attempts=1,
                confirmation_attempts=attempts,
                screenshot_path=screenshot,
                error="latest outgoing message did not match the final sent message",
            )
        except RuntimeError as exc:
            screenshot = self.screenshot("send-error")
            return DeliveryResult(
                DeliveryStatus.SEND_FAILED,
                send_attempts=1,
                screenshot_path=screenshot,
                error=str(exc),
            )
        except PlaywrightTimeoutError as exc:
            screenshot = self.screenshot("page-changed")
            return DeliveryResult(
                DeliveryStatus.BLOCKED,
                screenshot_path=screenshot,
                error="Douyin chat page structure changed or timed out",
            )

    def screenshot(self, label: str) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "-", label)
        path = self.artifact_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{safe_label}.png"
        self.page.screenshot(path=path, full_page=True)
        return path


def _runtime_home(profile_dir: Path, home: Path | None) -> Path:
    if home is not None:
        return home
    resolved = profile_dir.resolve()
    return resolved.parent.parent if resolved.parent.name == "data" else resolved.parent


def login(
    profile_dir: Path,
    timeout_ms: int = 300_000,
    home: Path | None = None,
    on_ready: Callable[[Page], None] | None = None,
) -> None:
    configure_runtime(_runtime_home(profile_dir, home))
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=False
        )
        context.set_default_timeout(10_000)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            # Register account observers before navigation/QR completion.  The
            # listener is read-only and does not alter chat behavior.
            from autody.account_profile import attach_account_observer
            attach_account_observer(page)
            page.goto(CHAT_URL)
            page.locator(DOUYIN_SELECTORS.login_marker).wait_for(timeout=timeout_ms)
            if on_ready is not None:
                on_ready(page)
        finally:
            context.close()


@contextmanager
def open_chat(
    profile_dir: Path,
    timeout_ms: int = 30_000,
    headless: bool = True,
    artifact_dir: Path | None = None,
    home: Path | None = None,
    on_stage: Callable[[str], None] | None = None,
):
    configure_runtime(_runtime_home(profile_dir, home))
    playwright = None
    context = None
    try:
        if on_stage:
            on_stage("launching_chromium")
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=headless, timeout=timeout_ms
        )
        context.set_default_timeout(timeout_ms)
        page = context.pages[0] if context.pages else context.new_page()
        if on_stage:
            on_stage("loading_chat_page")
        try:
            page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ChatPageLoadError("Douyin chat page load timed out") from exc
        if page.locator(DOUYIN_SELECTORS.verification_marker).count():
            if artifact_dir:
                DouyinChat(page, DOUYIN_SELECTORS, artifact_dir, DOUYIN_CONFIRMATION_SELECTORS).screenshot("authentication")
            raise AuthenticationError("login expired or security verification required")
        try:
            page.locator(DOUYIN_SELECTORS.login_marker).wait_for()
        except PlaywrightTimeoutError as exc:
            if artifact_dir:
                DouyinChat(page, DOUYIN_SELECTORS, artifact_dir, DOUYIN_CONFIRMATION_SELECTORS).screenshot("authentication")
            raise AuthenticationError("Douyin login is unavailable; run autody login") from exc
        yield page
    finally:
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()

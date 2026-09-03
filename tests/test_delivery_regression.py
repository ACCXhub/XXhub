from datetime import date
from types import SimpleNamespace

from autody import runner as runner_module
from autody.chat import (
    DeliveryConfirmationProvenance,
    DeliveryResult,
    DeliveryStatus,
    DouyinChat,
)
from autody.config import Target


def test_post_send_confirmation_falls_back_to_new_matching_bubble_count():
    class FakeChat:
        confirmation_retries = 0
        confirmation_delay_ms = 0
        page = SimpleNamespace(wait_for_timeout=lambda *_args, **_kwargs: None)

        def _outgoing_message_identities(self):
            return {}

        def _latest_matches(self, message):
            return message == "早安"

        def _matching_outgoing_count(self, message):
            assert message == "早安"
            return 2

    status, attempts = DouyinChat._confirm_delivery(
        FakeChat(),
        "早安",
        pre_send_identities={},
        pre_send_match_count=1,
    )

    assert status is DeliveryStatus.CONFIRMED
    assert attempts == 1


def test_verified_conversation_send_does_not_reopen_by_display_name():
    class FakeEditor:
        def fill(self, _message):
            return None

        def press(self, key):
            assert key == "Enter"

    class FakeChat:
        def __init__(self):
            self.reopened = 0
            self.page = SimpleNamespace()

        def _raise_if_page_failure(self):
            return None

        def open_verified_conversation(self, _target):
            self.reopened += 1

        def composer_editor(self):
            return FakeEditor()

        def _outgoing_message_identities(self):
            return {}

        def _matching_outgoing_count(self, _message):
            return 0

        def _confirm_delivery(self, _message, **_kwargs):
            return DeliveryStatus.CONFIRMED, 1

        def page_failure(self):
            return None

    chat = FakeChat()
    result = DouyinChat.send(
        chat,
        "小明",
        "早安",
        selected_target_id="target-a",
        expected_conversation_id="conversation-a",
        conversation_verified=True,
    )

    assert chat.reopened == 0
    assert result.successful is True


def test_fresh_pending_target_can_send_without_live_history_audit():
    class FakeChat:
        friend_search_timeout_ms = 1

        def __init__(self):
            self.audits = 0
            self.sends = 0

        def open_conversation_identity(self, *_args, **_kwargs):
            return SimpleNamespace(identity_match=True, identity_match_reason="stable_id_match")

        def audit_today_outgoing(self, _today):
            self.audits += 1
            raise AssertionError("fresh pending delivery must not depend on history audit")

        def send(self, _target, _message, *, conversation_verified=False, **_kwargs):
            assert conversation_verified is True
            self.sends += 1
            return DeliveryResult(
                DeliveryStatus.CONFIRMED,
                send_attempts=1,
                confirmation_attempts=1,
                confirmation_provenance=DeliveryConfirmationProvenance.POST_SEND_OBSERVED,
            )

    chat = FakeChat()
    target = Target(name="小明", stable_id="target-a")
    execution = runner_module._execute_today_target(
        chat,
        target,
        "早安",
        date(2026, 9, 3),
        expected_conversation_id="conversation-a",
        audit_before_send=False,
    )

    assert chat.audits == 0
    assert chat.sends == 1
    assert execution.delivery is not None
    assert execution.delivery.successful is True


def test_only_fresh_pending_normal_delivery_skips_live_audit():
    assert runner_module._needs_live_audit_before_send(
        requested_target_ids=None,
        effective_status="pending",
        has_target_failure=False,
        reconciliation_status=None,
    ) is False
    assert runner_module._needs_live_audit_before_send(
        requested_target_ids={"target-a"},
        effective_status="pending",
        has_target_failure=False,
        reconciliation_status=None,
    ) is True
    assert runner_module._needs_live_audit_before_send(
        requested_target_ids=None,
        effective_status="unknown",
        has_target_failure=True,
        reconciliation_status="unknown",
    ) is True

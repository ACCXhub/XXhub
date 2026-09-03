from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    data = file_path.read_bytes()
    newline = "\r\n" if b"\r\n" in data else "\n"
    old_bytes = old.replace("\n", newline).encode("utf-8")
    new_bytes = new.replace("\n", newline).encode("utf-8")
    count = data.count(old_bytes)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement target, found {count}")
    file_path.write_bytes(data.replace(old_bytes, new_bytes, 1))


replace_once(
    "src/autody/chat.py",
    '''    def _latest_matches(self, message: str) -> bool:
        latest = self._latest_outgoing_text()
        return latest is not None and normalize_message_text(latest) == normalize_message_text(message)

    def _outgoing_message_identities(self) -> dict[str, str]:
''',
    '''    def _latest_matches(self, message: str) -> bool:
        latest = self._latest_outgoing_text()
        return latest is not None and normalize_message_text(latest) == normalize_message_text(message)

    def _matching_outgoing_count(self, message: str) -> int:
        expected = normalize_message_text(message)
        messages = self.page.locator(self.confirmation_selectors.outgoing_message_text)
        return sum(
            normalize_message_text(str(text)) == expected
            for text in messages.all_text_contents()
        )

    def _outgoing_message_identities(self) -> dict[str, str]:
''',
)

replace_once(
    "src/autody/chat.py",
    '''    def _confirm_delivery(
        self,
        message: str,
        *,
        pre_send_identities: dict[str, str],
    ) -> tuple[DeliveryStatus | None, int]:
        for attempt in range(1, self.confirmation_retries + 2):
            if self.confirmation_delay_ms:
                self.page.wait_for_timeout(self.confirmation_delay_ms)
            matching_identity_observed = any(
                identity not in pre_send_identities
                and text == normalize_message_text(message)
                for identity, text in self._outgoing_message_identities().items()
            )
            if matching_identity_observed:
                status = DeliveryStatus.CONFIRMED if attempt == 1 else DeliveryStatus.RETRY_CONFIRMED
                return status, attempt
        return None, self.confirmation_retries + 1
''',
    '''    def _confirm_delivery(
        self,
        message: str,
        *,
        pre_send_identities: dict[str, str],
        pre_send_match_count: int,
    ) -> tuple[DeliveryStatus | None, int]:
        normalized_message = normalize_message_text(message)
        for attempt in range(1, self.confirmation_retries + 2):
            if self.confirmation_delay_ms:
                self.page.wait_for_timeout(self.confirmation_delay_ms)
            matching_identity_observed = any(
                identity not in pre_send_identities and text == normalized_message
                for identity, text in self._outgoing_message_identities().items()
            )
            matching_count_observed = (
                self._latest_matches(message)
                and self._matching_outgoing_count(message) > pre_send_match_count
            )
            if matching_identity_observed or matching_count_observed:
                status = (
                    DeliveryStatus.CONFIRMED
                    if attempt == 1
                    else DeliveryStatus.RETRY_CONFIRMED
                )
                return status, attempt
        return None, self.confirmation_retries + 1
''',
)

replace_once(
    "src/autody/chat.py",
    '''            else:
                self.open_verified_conversation(target)
            self._raise_if_page_failure()
''',
    '''            elif expected_conversation_id is None:
                self.open_verified_conversation(target)
            self._raise_if_page_failure()
''',
)

replace_once(
    "src/autody/chat.py",
    '''            editor = self.composer_editor()
            editor.fill(message)
            pre_send_identities = self._outgoing_message_identities()
''',
    '''            editor = self.composer_editor()
            pre_send_match_count = self._matching_outgoing_count(message)
            editor.fill(message)
            pre_send_identities = self._outgoing_message_identities()
''',
)

replace_once(
    "src/autody/chat.py",
    '''            status, attempts = self._confirm_delivery(
                message,
                pre_send_identities=pre_send_identities,
            )
''',
    '''            status, attempts = self._confirm_delivery(
                message,
                pre_send_identities=pre_send_identities,
                pre_send_match_count=pre_send_match_count,
            )
''',
)

replace_once(
    "src/autody/runner.py",
    '''def _supports_today_target_pipeline(chat) -> bool:
    return callable(getattr(chat, "open_conversation_identity", None)) and callable(
        getattr(chat, "audit_today_outgoing", None)
    )


def _fatal_chat_execution(exc: FatalChatError) -> TodayTargetExecution:
''',
    '''def _supports_today_target_pipeline(chat) -> bool:
    return callable(getattr(chat, "open_conversation_identity", None)) and callable(
        getattr(chat, "audit_today_outgoing", None)
    )


def _needs_live_audit_before_send(
    *,
    requested_target_ids: set[str] | None,
    effective_status: str | None,
    has_target_failure: bool,
    reconciliation_status: object,
) -> bool:
    """Keep live history audit on recovery paths, not fresh normal delivery."""
    return (
        requested_target_ids is not None
        or effective_status != "pending"
        or has_target_failure
        or reconciliation_status is not None
    )


def _fatal_chat_execution(exc: FatalChatError) -> TodayTargetExecution:
''',
)

replace_once(
    "src/autody/runner.py",
    '''    expected_conversation_id: str | None,
    allow_send: bool = True,
) -> TodayTargetExecution:
''',
    '''    expected_conversation_id: str | None,
    allow_send: bool = True,
    audit_before_send: bool = True,
) -> TodayTargetExecution:
''',
)

replace_once(
    "src/autody/runner.py",
    '''    try:
        audit = chat.audit_today_outgoing(today)
    except FatalChatError as exc:
        return _fatal_chat_execution(exc)
''',
    '''    if allow_send and not audit_before_send:
        if message is None:
            raise ValueError("sending requires a prepared message")
        return TodayTargetExecution(
            delivery=_send_target(
                chat,
                target,
                message,
                expected_conversation_id=expected_conversation_id,
                conversation_verified=True,
                delivery_day=today,
            )
        )
    try:
        audit = chat.audit_today_outgoing(today)
    except FatalChatError as exc:
        return _fatal_chat_execution(exc)
''',
)

replace_once(
    "src/autody/runner.py",
    '''    def execute_target(target: Target, message: str, expected_conversation_id: str | None):
        try:
            return _execute_today_target(
                chat,
                target,
                message,
                today,
                expected_conversation_id=expected_conversation_id,
                allow_send=not audit_only,
            )
''',
    '''    def execute_target(target: Target, message: str, expected_conversation_id: str | None):
        target_id = target_identity(target)
        target_failures = _stored_target_failures(daily)
        reconciliation = daily.get("delivery_reconciliation", {})
        reconciliation_status = (
            reconciliation.get(target_id)
            if isinstance(reconciliation, dict)
            else None
        )
        audit_before_send = audit_only or _needs_live_audit_before_send(
            requested_target_ids=requested_target_ids,
            effective_status=effective_before_run.get(target_id),
            has_target_failure=target_id in target_failures,
            reconciliation_status=reconciliation_status,
        )
        try:
            return _execute_today_target(
                chat,
                target,
                message,
                today,
                expected_conversation_id=expected_conversation_id,
                allow_send=not audit_only,
                audit_before_send=audit_before_send,
            )
''',
)

print("delivery regression repair applied")

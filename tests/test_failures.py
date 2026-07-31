from autody.failures import FailureDetail, failure_detail


def test_unknown_failure_has_stage_chinese_fallback_and_safe_action():
    detail = failure_detail(
        "unknown_exception",
        stage="conversation_selected",
        diagnostic_details={"exception_type": "RuntimeError"},
    )

    assert detail.stage == "conversation_selected"
    assert detail.reason_code == "unknown_exception"
    assert detail.user_summary_zh == "操作未能完成"
    assert "会话已选中" in detail.user_detail_zh
    assert detail.suggested_action_zh
    assert detail.send_attempts == 0
    assert detail.uncertain_send is False


def test_safe_retry_requires_current_binding_and_matching_account_scope():
    detail = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        binding_valid=True,
        account_scope_matches=True,
    )

    assert detail.retryable is True
    assert detail.safe_retry_available is True

    assert detail.model_copy(update={"binding_valid": False}).safe_retry_available is False
    assert (
        detail.model_copy(update={"account_scope_matches": False}).safe_retry_available
        is False
    )


def test_uncertain_confirmation_can_never_be_retried():
    detail = failure_detail(
        "confirmation_failed_uncertain",
        stage="confirmation_observed",
        send_attempts=1,
        binding_valid=True,
        account_scope_matches=True,
    )

    assert detail.send_attempted is True
    assert detail.uncertain_send is True
    assert detail.safe_retry_available is False
    assert detail.suggested_action_zh == "查看详情并人工确认，禁止自动重试"


def test_binding_and_account_failures_choose_condition_aware_actions():
    stale = failure_detail(
        "binding_stale",
        stage="target_binding_resolved",
        binding_valid=False,
        account_scope_matches=True,
    )
    mismatch = failure_detail(
        "account_scope_mismatch",
        stage="account_verified",
        binding_valid=True,
        account_scope_matches=False,
    )

    assert stale.suggested_action == "reassociate"
    assert stale.suggested_action_zh == "重新关联"
    assert mismatch.suggested_action == "switch_account"
    assert mismatch.suggested_action_zh == "切换或登录账号"


def test_failure_detail_round_trips_through_json():
    detail = failure_detail(
        "conversation_not_found",
        stage="conversation_located",
        run_id="run-local",
        target_stable_id="target-local",
        account_scope="account-local",
        diagnostic_details={"exception_type": "RuntimeError"},
    )

    restored = FailureDetail.model_validate_json(detail.model_dump_json())

    assert restored == detail


def test_account_discovery_and_test_center_failures_have_chinese_actions():
    cases = [
        ("account_operation_busy", "account_verified"),
        ("account_profile_unavailable", "account_verified"),
        ("account_switch_failed", "account_verified"),
        ("account_logout_failed", "account_verified"),
        ("friend_discovery_failed", "conversation_located"),
        ("test_center_busy", "browser_opened"),
        ("operation_cancelled", "browser_opened"),
    ]

    for reason_code, stage in cases:
        detail = failure_detail(reason_code, stage=stage)
        assert detail.reason_code == reason_code
        assert detail.stage == stage
        assert detail.user_summary_zh
        assert detail.user_detail_zh
        assert detail.suggested_action_zh

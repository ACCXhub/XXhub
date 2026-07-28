import json
from pathlib import Path
import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import autody.test_center_dry_run as dry_run_module
from autody.chat import ChatSelectors, ComposerState, ConversationIdentity, DouyinChat
from autody.config import AppConfig, Target, save_config
from autody.module_assets import TEST_CENTER_JS
from autody.test_center_dry_run import (
    DryRunController,
    DryRunResult,
    DryRunSettings,
    DryRunStore,
    TestCenterDryRun,
    eligible_batch_targets,
    empty_counters,
)


CONVERSATION_A_ID = "candidate-7d3593e740346f441e165bfb3f55513a"
CONVERSATION_B_ID = "candidate-3c5b2b936f9a72396a6105f6c89845f9"


def _open_fixture(page) -> None:
    page.goto((Path("tests/fixtures/chat.html").resolve()).as_uri())
    page.locator("body").evaluate(
        """body => {
            window.__dryRunKeys = [];
            document.querySelector('[data-e2e="chat-input"]').addEventListener(
                'keydown', event => window.__dryRunKeys.push(event.key)
            );
        }"""
    )


def _frontend_state(
    *,
    selected_target_id: str,
    request_revision: int,
    run_id: str | None,
    visible_name: str | None,
    result: str | None,
) -> dict:
    return {
        "targets": [
            {"target_id": "target-a", "display_name": "好友甲", "conversation_id": CONVERSATION_A_ID, "batch_eligible": True, "batch_exclusion_reason": None},
            {"target_id": "target-b", "display_name": "好友乙", "conversation_id": CONVERSATION_B_ID, "batch_eligible": True, "batch_exclusion_reason": None},
        ],
        "settings": {
            **DryRunSettings().model_dump(),
            "selected_batch_target_ids": ["target-a", "target-b"],
        },
        "counters": {
            "real_composer_writes": 0,
            "real_composer_clears": 0,
            "send_button_clicks": 0,
            "enter_key_presses": 0,
            "send_pipeline_calls": 0,
            "send_attempts": 0,
            "existing_drafts_preserved": 0,
            "cleanup_failures": 0,
        },
        "running": False,
        "paused": False,
        "stage": "completed" if result else "waiting",
        "selected_target_id": selected_target_id,
        "expected_conversation_id": CONVERSATION_A_ID if selected_target_id == "target-a" else CONVERSATION_B_ID,
        "visible_conversation_id": CONVERSATION_A_ID if visible_name else None,
        "selected_display_name": "好友甲" if selected_target_id == "target-a" else "好友乙",
        "visible_display_name": visible_name,
        "identity_match": True if visible_name else None,
        "identity_match_reason": "stable_id_match" if visible_name else None,
        "composer_status": "empty" if visible_name else "unknown",
        "result": result,
        "message": None,
        "elapsed_seconds": 0,
        "run_id": run_id,
        "request_revision": request_revision,
        "recovery_warning": None,
        "eligible_target_count": 2,
        "mode": "single",
        "total_targets": 0,
        "current_position": 0,
        "completed_targets": 0,
        "passed_targets": 0,
        "skipped_targets": 0,
        "failed_targets": 0,
        "remaining_targets": 0,
        "results": [],
        "resolved_test_text": None,
    }


def _open_mocked_frontend(page, initial_status: dict, previews: dict[str, str] | None = None) -> None:
    page.set_content(
        """<main id="root"></main><script>
        window.__requests = [];
        window.__previewByTarget = {};
        window.postMessage = () => {};
        window.fetch = (url, options = {}) => {
          const parsed = new URL(String(url), 'http://local.test');
          if (parsed.pathname.endsWith('/message-preview')) {
            const targetId = parsed.searchParams.get('target_id');
            const text = window.__previewByTarget[targetId];
            return Promise.resolve({
              ok: text !== undefined,
              json: async () => text === undefined ? {detail:'无可用文案'} : {available:true, text, mode:'today'},
              text: async () => text === undefined ? '无可用文案' : JSON.stringify({available:true, text, mode:'today'})
            });
          }
          return new Promise(resolve => {
          window.__requests.push({url:String(url), options, resolve});
          });
        };
        window.__resolveRequest = (index, payload) => {
          window.__requests[index].resolve({
            ok: true,
            json: async () => payload,
            text: async () => JSON.stringify(payload)
          });
        };
        </script>"""
    )
    page.evaluate("value => window.__previewByTarget = value", previews or {})
    page.add_script_tag(content=TEST_CENTER_JS)
    page.wait_for_function("window.__requests.length === 2")
    page.evaluate(
        """status => {
          window.__resolveRequest(0, status);
          window.__resolveRequest(1, {items:[]});
        }""",
        initial_status,
    )
    page.wait_for_selector("#target")


def test_empty_textarea_loads_today_message_and_manual_edit_switches_to_custom(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id=None,
        visible_name=None,
        result=None,
    )
    _open_mocked_frontend(page, status, {"target-a": "今日文案甲"})

    page.wait_for_function("document.querySelector('#test-text').value === '今日文案甲'")
    assert page.locator("#use-today-message").is_checked()
    assert "使用今日文案" in page.locator(".control-column").inner_text()

    page.locator("#test-text").fill("自定义内容")

    assert page.locator("#use-custom-message").is_checked()
    assert "自定义测试文本" in page.locator(".control-column").inner_text()

    page.get_by_role("button", name="重新载入今日文案").click()
    page.wait_for_function("document.querySelector('#test-text').value === '今日文案甲'")
    assert page.locator("#use-today-message").is_checked()


def test_target_change_loads_that_targets_today_message(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id=None,
        visible_name=None,
        result=None,
    )
    _open_mocked_frontend(
        page,
        status,
        {"target-a": "今日文案甲", "target-b": "今日文案乙"},
    )
    page.wait_for_function("document.querySelector('#test-text').value === '今日文案甲'")

    page.select_option("#target", "target-b")
    page.wait_for_function("window.__requests.length === 3")
    selected_b = _frontend_state(
        selected_target_id="target-b",
        request_revision=2,
        run_id=None,
        visible_name=None,
        result=None,
    )
    page.evaluate("payload => window.__resolveRequest(2, payload)", selected_b)

    page.wait_for_function("document.querySelector('#test-text').value === '今日文案乙'")
    assert page.locator("#use-today-message").is_checked()


def test_primary_status_is_chinese_and_internal_codes_are_collapsed(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=7,
        run_id="run-private",
        visible_name="好友甲",
        result="skipped_existing_draft",
    )
    status["composer_status"] = "existing_draft_preserved"
    status["message"] = "检测到该聊天已有文字、附件或回复内容，未进行测试输入。"
    _open_mocked_frontend(page, status, {"target-a": "今日文案甲"})

    primary = page.locator(".status-summary").inner_text()
    assert "好友甲" in primary
    assert "已跳过：存在草稿" in primary
    assert "检测到该聊天已有文字、附件或回复内容，未进行测试输入。" in primary
    for internal in (
        "target-a",
        CONVERSATION_A_ID,
        "stable_id_match",
        "existing_draft_preserved",
        "skipped_existing_draft",
        "run-private",
        "请求版本",
    ):
        assert internal not in primary

    diagnostics = page.get_by_text("诊断详情", exact=True)
    assert diagnostics.count() == 1
    assert not diagnostics.locator("xpath=..").get_attribute("open")


def test_test_center_exposes_managed_browser_focus_without_internal_primary_ids(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id=None,
        visible_name=None,
        result=None,
    )
    _open_mocked_frontend(page, status, {"target-a": "今日文案甲"})

    assert page.get_by_role("button", name="打开受管浏览器").count() == 1
    assert "选择目标 ID" not in page.locator(".status-summary").inner_text()


def test_batch_mode_shows_eligible_count_and_sends_one_pass_request(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id=None,
        visible_name=None,
        result=None,
    )
    _open_mocked_frontend(page, status, {"target-a": "今日文案甲"})
    page.wait_for_function("document.querySelector('#test-text').value === '今日文案甲'")

    assert "可安全批量测试 2 个目标" in page.locator(".mode-selector").inner_text()
    page.get_by_role("button", name="开始批量测试").click()
    page.wait_for_function("window.__requests.length === 3")
    payload = page.evaluate(
        "() => JSON.parse(window.__requests[2].options.body)"
    )

    assert payload["automatic"] is True
    assert payload["use_today_message"] is True
    assert payload["navigation_only"] is False
    assert payload["batch_target_ids"] == ["target-a", "target-b"]


def test_batch_target_dialog_selects_subset_and_preserves_configured_order(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id=None,
        visible_name=None,
        result=None,
    )
    status["targets"].append({
        "target_id": "target-disabled",
        "display_name": "停用目标",
        "conversation_id": "candidate-disabled",
        "batch_eligible": False,
        "batch_exclusion_reason": "已停用",
    })
    _open_mocked_frontend(page, status, {"target-a": "今日文案甲"})
    page.get_by_label("批量测试").check()
    page.get_by_role("button", name="选择目标").click()

    picker = page.locator("#batch-target-dialog")
    assert picker.is_visible()
    assert "已选择 2 / 2" in picker.inner_text()
    assert picker.get_by_label("停用目标").is_disabled()
    assert "已停用" in picker.inner_text()

    picker.get_by_role("button", name="清空").click()
    assert page.get_by_role("button", name="开始批量测试").is_disabled()
    picker.get_by_label("好友乙").check()
    picker.get_by_label("好友甲").check()
    assert "已选择 2 / 2" in picker.inner_text()
    picker.get_by_role("button", name="完成").click()

    page.wait_for_function("window.__requests.length === 3")
    page.evaluate(
        """status => window.__resolveRequest(2, status.settings)""",
        status,
    )
    page.get_by_role("button", name="开始批量测试").click()
    page.wait_for_function("window.__requests.length === 4")
    payload = page.evaluate("() => JSON.parse(window.__requests[3].options.body)")
    assert payload["batch_target_ids"] == ["target-a", "target-b"]


def test_batch_target_dialog_select_all_clear_and_invert(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id=None,
        visible_name=None,
        result=None,
    )
    _open_mocked_frontend(page, status, {"target-a": "今日文案甲"})
    page.get_by_role("button", name="选择目标").click()
    picker = page.locator("#batch-target-dialog")

    picker.get_by_role("button", name="清空").click()
    assert "已选择 0 / 2" in picker.inner_text()
    picker.get_by_role("button", name="反选").click()
    assert "已选择 2 / 2" in picker.inner_text()
    picker.get_by_role("button", name="全选").click()
    assert "已选择 2 / 2" in picker.inner_text()


def test_pause_click_shows_safe_pause_immediately_without_waiting_for_response(page):
    page.set_default_timeout(1000)
    status = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id="run-pausing",
        visible_name="好友甲",
        result=None,
    )
    status.update({"running": True, "stage": "typing", "mode": "batch"})
    _open_mocked_frontend(page, status)

    started = time.monotonic()
    page.get_by_role("button", name="暂停", exact=True).click()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert "正在安全暂停" in page.locator(".status-summary").inner_text()
    assert page.get_by_role("button", name="暂停", exact=True).is_disabled()


def test_batch_progress_and_results_are_chinese_without_raw_reason_codes(page):
    status = _frontend_state(
        selected_target_id="target-b",
        request_revision=3,
        run_id="run-batch",
        visible_name="好友乙",
        result="batch_completed",
    )
    status.update({
        "mode": "batch",
        "total_targets": 3,
        "current_position": 3,
        "completed_targets": 3,
        "passed_targets": 1,
        "skipped_targets": 1,
        "failed_targets": 1,
        "remaining_targets": 0,
        "results": [
            {
                "selected_display_name": "好友甲",
                "result": "completed",
                "identity_match": True,
                "identity_match_reason": "stable_id_match",
                "duration_seconds": 1.2,
                "message": None,
            },
            {
                "selected_display_name": "好友乙",
                "result": "skipped_existing_draft",
                "identity_match": True,
                "identity_match_reason": "stable_id_match",
                "duration_seconds": 0.8,
                "message": "检测到已有草稿，已保留",
            },
            {
                "selected_display_name": "好友丙",
                "result": "stopped",
                "identity_match": False,
                "identity_match_reason": "stable_id_mismatch",
                "duration_seconds": 0.5,
                "message": "会话不匹配，测试已停止",
            },
        ],
    })
    _open_mocked_frontend(page, status)

    primary = page.locator(".status-column").inner_text()
    assert "当前进度" in primary and "3 / 3" in primary
    assert "1 / 1 / 1" in primary
    assert "测试通过" in primary
    assert "已跳过：存在草稿" in primary
    assert "会话身份不匹配" in primary
    assert "stable_id_match" not in primary
    assert "skipped_existing_draft" not in primary


def test_real_page_dry_run_types_then_clears_without_send_or_screenshot(page, tmp_path: Path):
    _open_fixture(page)
    observed = []
    runner = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path)

    result = runner.run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID),
        "模块测试文本",
        DryRunSettings(page_ready_delay_ms=500, typing_delay_ms=30, typed_text_hold_ms=500, clear_verify_delay_ms=200),
        on_stage=lambda stage: observed.append((stage, page.locator('[data-e2e="chat-input"]').inner_text())),
    )

    assert result.result == "completed"
    assert result.visible_identity == "小明"
    assert result.identity_match is True
    assert (page.locator('[data-e2e="chat-input"]').text_content() or "").strip() == ""
    assert any(stage == "observing" and value == "模块测试文本" for stage, value in observed)
    assert result.counters == {
        "real_composer_writes": 1,
        "real_composer_clears": 1,
        "send_button_clicks": 0,
        "enter_key_presses": 0,
        "send_pipeline_calls": 0,
        "send_attempts": 0,
        "existing_drafts_preserved": 0,
        "cleanup_failures": 0,
    }
    assert "Enter" not in page.locator("body").evaluate("() => window.__dryRunKeys")
    assert not list(tmp_path.glob("*.png"))


def test_existing_draft_is_preserved_and_skipped(page, tmp_path: Path):
    _open_fixture(page)
    page.locator('[data-e2e="chat-input"]').evaluate("element => element.textContent = '用户草稿'")

    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID), "模块测试文本", DryRunSettings()
    )

    assert result.result == "skipped_existing_draft"
    assert page.locator('[data-e2e="chat-input"]').inner_text() == "用户草稿"
    assert result.counters["existing_drafts_preserved"] == 1
    assert result.counters["real_composer_writes"] == 0
    assert result.counters["real_composer_clears"] == 0


def test_existing_attachment_is_preserved_and_skipped_without_writing_or_clearing(page, tmp_path: Path):
    _open_fixture(page)
    page.locator('[data-e2e="chat-input"]').evaluate(
        """element => {
          const preview = document.createElement('img');
          preview.dataset.e2e = 'chat-attachment';
          preview.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';
          element.append(preview);
        }"""
    )

    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID),
        "模块测试文本",
        DryRunSettings(),
    )

    assert result.result == "skipped_existing_context"
    assert result.composer_status == "existing_attachment_preserved"
    assert page.locator('[data-e2e="chat-attachment"]').count() == 1
    assert result.counters["existing_drafts_preserved"] == 1
    assert result.counters["real_composer_writes"] == 0
    assert result.counters["real_composer_clears"] == 0


@pytest.mark.parametrize(
    "markup",
    [
        "",
        "   \n",
        "&nbsp;",
        "\u200b",
        "<br>",
        '<span style="display:none">隐藏占位</span>',
    ],
)
def test_authoritative_composer_state_treats_empty_editor_variants_as_empty(page, tmp_path: Path, markup: str):
    _open_fixture(page)
    page.locator('[data-e2e="chat-input"]').evaluate("(element, value) => element.innerHTML = value", markup)
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path)
    editor = chat.composer_editor()

    state = chat.composer_state(editor)

    assert state.normalized_text_length == 0
    assert state.visible_text_present is False
    assert state.attachment_present is False
    assert state.mention_or_reply_present is False
    assert state.composer_empty is True
    assert state.reason == "empty"


@pytest.mark.parametrize(
    ("markup", "reason"),
    [
        ("真实草稿", "visible_text"),
        ('<span data-e2e="mention-chip">@某人</span>', "mention_or_reply"),
        ('<span data-e2e="reply-chip">回复</span>', "mention_or_reply"),
        ('<img data-e2e="chat-attachment" src="fixture.png">', "attachment"),
        ('<img data-e2e="emoji" alt="🙂" src="fixture.png">', "visible_text"),
    ],
)
def test_authoritative_composer_state_preserves_visible_content(page, tmp_path: Path, markup: str, reason: str):
    _open_fixture(page)
    page.locator('[data-e2e="chat-input"]').evaluate("(element, value) => element.innerHTML = value", markup)
    chat = DouyinChat(page, ChatSelectors.test_defaults(), tmp_path)

    state = chat.composer_state(chat.composer_editor())

    assert state.composer_empty is False
    assert state.reason == reason


def test_zero_width_placeholder_does_not_trigger_false_existing_draft(page, tmp_path: Path):
    _open_fixture(page)
    page.locator('[data-e2e="chat-input"]').evaluate(
        """element => {
          element.setAttribute('data-placeholder', '发送消息');
          element.innerHTML = '<span>\\u200b</span>';
        }"""
    )

    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID),
        "模块测试文本",
        DryRunSettings(page_ready_delay_ms=500, typing_delay_ms=30, typed_text_hold_ms=500, clear_verify_delay_ms=200),
    )

    assert result.result == "completed"
    assert result.counters["real_composer_writes"] == 1
    assert result.counters["real_composer_clears"] == 1


def test_stop_before_composer_access_stops_without_writing(page, tmp_path: Path):
    _open_fixture(page)
    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID), "模块测试文本", DryRunSettings(), stop_requested=lambda: True
    )

    assert result.result == "stopped"
    assert result.counters["real_composer_writes"] == result.counters["real_composer_clears"] == 0
    assert (page.locator('[data-e2e="chat-input"]').text_content() or "").strip() == ""


def test_cleanup_result_is_safe_only_when_writes_and_clears_balance():
    safe = {"result": "completed", "counters": {"real_composer_writes": 1, "real_composer_clears": 1, "cleanup_failures": 0}}
    unsafe = {"result": "stopped", "counters": {"real_composer_writes": 1, "real_composer_clears": 0, "cleanup_failures": 1}}

    assert DryRunController.can_advance(safe) is True
    assert DryRunController.can_advance(unsafe) is False


def test_eligible_batch_targets_keep_configured_order_and_exclude_unsafe_targets():
    config = AppConfig(
        targets=[
            Target(name="甲", stable_id="target-a", candidate_id="candidate-a"),
            Target(name="停用", enabled=False, stable_id="target-disabled", candidate_id="candidate-disabled"),
            Target(name="过期", stable_id="target-stale", candidate_id="candidate-stale"),
            Target(name="同名", stable_id="target-duplicate-a", candidate_id="candidate-duplicate-a"),
            Target(name=" 同名 ", stable_id="target-duplicate-b", candidate_id="candidate-duplicate-b"),
            Target(name="缺少会话", stable_id="target-unresolved"),
            Target(name="乙", stable_id="target-b", candidate_id="candidate-b"),
        ]
    )
    discovery = {
        "candidate-a": "current",
        "candidate-stale": "stale",
        "candidate-duplicate-a": "current",
        "candidate-duplicate-b": "current",
        "candidate-b": "current",
    }

    eligible = eligible_batch_targets(config, discovery)

    assert [(target.stable_id, target.name) for target in eligible] == [
        ("target-a", "甲"),
        ("target-b", "乙"),
    ]


def test_today_message_is_resolved_after_composer_is_confirmed_empty(page, tmp_path: Path):
    _open_fixture(page)
    observed: list[str] = []

    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID),
        None,
        DryRunSettings(
            page_ready_delay_ms=500,
            typing_delay_ms=30,
            typed_text_hold_ms=500,
            clear_verify_delay_ms=200,
        ),
        on_stage=observed.append,
        resolve_test_text=lambda: observed.append("resolved_today_message") or "目标今日文案",
    )

    assert result.result == "completed"
    assert observed.index("checking_existing_draft") < observed.index("resolved_today_message")
    assert result.text_length == len("目标今日文案")
    assert result.counters["real_composer_writes"] == result.counters["real_composer_clears"] == 1


def _configured_batch(tmp_path: Path, count: int = 3) -> tuple[Path, list[Target]]:
    config_path = tmp_path / "config.yaml"
    targets = [
        Target(
            name=f"目标{index}",
            stable_id=f"target-{index}",
            candidate_id=f"candidate-{index}",
        )
        for index in range(count)
    ]
    save_config(config_path, AppConfig(targets=targets))
    return config_path, targets


def _install_fake_batch_runtime(monkeypatch, outcomes: dict[str, str], visited: list[str], resolved: list[str]):
    page = SimpleNamespace(bring_to_front=lambda: None)
    monkeypatch.setattr(dry_run_module, "SingleInstanceLock", lambda _path: nullcontext())
    monkeypatch.setattr(dry_run_module, "open_chat", lambda *_args, **_kwargs: nullcontext(page))
    monkeypatch.setattr(
        dry_run_module,
        "preview_today_target_message",
        lambda _config, target, _today: resolved.append(target.stable_id)
        or SimpleNamespace(text=f"今日文案-{target.stable_id}"),
    )

    def fake_run_target(
        _runner,
        target,
        _test_text,
        _settings,
        *,
        run_id,
        request_revision,
        on_stage,
        on_composer_write_started,
        resolve_test_text,
        **_kwargs,
    ):
        target_id = target.stable_id
        visited.append(target_id)
        outcome = outcomes.get(target_id, "completed")
        counters = empty_counters()
        identity_match = True
        reason = "stable_id_match"
        composer_status = "empty"
        message = None
        if outcome == "completed":
            on_stage("checking_existing_draft")
            value = resolve_test_text()
            assert value == f"今日文案-{target_id}"
            on_composer_write_started()
            counters["real_composer_writes"] = 1
            counters["real_composer_clears"] = 1
        elif outcome == "skipped_existing_draft":
            counters["existing_drafts_preserved"] = 1
            composer_status = "existing_draft_preserved"
            message = "检测到已有草稿，已保留"
        elif outcome == "identity_mismatch":
            identity_match = False
            reason = "stable_id_mismatch"
            message = "会话不匹配，测试已停止"
            outcome = "stopped"
        elif outcome == "navigation_failed":
            identity_match = False
            reason = "conversation_not_found"
            message = "无法打开聊天"
        elif outcome == "cleanup_failed":
            counters["real_composer_writes"] = 1
            counters["cleanup_failures"] = 1
            message = "清除失败，批量测试已停止"
        return DryRunResult(
            run_id=run_id,
            request_revision=request_revision,
            target_id=target_id,
            selected_target_id=target_id,
            expected_conversation_id=target.candidate_id,
            selected_display_name=target.name,
            visible_conversation_id=target.candidate_id if identity_match else None,
            visible_display_name=target.name if identity_match else None,
            identity_match=identity_match,
            identity_match_reason=reason,
            composer_status=composer_status,
            stage=outcome,
            result=outcome,
            message=message,
            counters=counters,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(TestCenterDryRun, "run_target", fake_run_target)


def test_batch_runs_one_configured_pass_resolving_each_targets_today_message(tmp_path: Path, monkeypatch):
    config_path, targets = _configured_batch(tmp_path)
    visited: list[str] = []
    resolved: list[str] = []
    _install_fake_batch_runtime(monkeypatch, {}, visited, resolved)
    controller = DryRunController(config_path, tmp_path / "module-data")
    monkeypatch.setattr(controller, "_wait_switch_interval", lambda _seconds: True)
    assert controller.select("target-0", request_revision=1)

    controller.start(
        "target-0",
        "",
        automatic=True,
        use_today_message=True,
        run_id="run-batch",
        request_revision=1,
    )
    controller._thread.join(timeout=3)
    status = controller.status()

    assert visited == resolved == [target.stable_id for target in targets]
    assert status["running"] is False
    assert status["mode"] == "batch"
    assert status["completed_targets"] == status["total_targets"] == 3
    assert status["passed_targets"] == 3
    assert status["skipped_targets"] == status["failed_targets"] == 0
    assert status["remaining_targets"] == 0
    assert status["counters"]["real_composer_writes"] == 3
    assert status["counters"]["real_composer_clears"] == 3
    assert all(status["counters"][name] == 0 for name in (
        "send_button_clicks",
        "enter_key_presses",
        "send_pipeline_calls",
        "send_attempts",
        "cleanup_failures",
    ))


def test_three_target_batch_uses_one_browser_context_and_authoritative_page(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path)
    opened = 0
    visited_pages: list[int] = []
    context = SimpleNamespace(pages=[])
    page = SimpleNamespace(bring_to_front=lambda: None, context=context)
    context.pages.append(page)

    class FakeChatSession:
        def __enter__(self):
            nonlocal opened
            opened += 1
            return page

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(dry_run_module, "SingleInstanceLock", lambda _path: nullcontext())
    monkeypatch.setattr(dry_run_module, "open_chat", lambda *_args, **_kwargs: FakeChatSession())
    monkeypatch.setattr(
        dry_run_module,
        "preview_today_target_message",
        lambda _config, target, _today: SimpleNamespace(text=f"今日文案-{target.stable_id}"),
    )

    def complete_target(
        runner,
        target,
        _test_text,
        _settings,
        *,
        run_id,
        request_revision,
        on_composer_write_started,
        resolve_test_text,
        **_kwargs,
    ):
        visited_pages.append(id(runner.page))
        resolve_test_text()
        on_composer_write_started()
        counters = empty_counters()
        counters["real_composer_writes"] = counters["real_composer_clears"] = 1
        return DryRunResult(
            run_id=run_id,
            request_revision=request_revision,
            target_id=target.stable_id,
            selected_target_id=target.stable_id,
            expected_conversation_id=target.candidate_id,
            selected_display_name=target.name,
            visible_conversation_id=target.candidate_id,
            visible_display_name=target.name,
            identity_match=True,
            identity_match_reason="stable_id_match",
            composer_status="empty",
            stage="completed",
            result="completed",
            counters=counters,
        )

    monkeypatch.setattr(TestCenterDryRun, "run_target", complete_target)
    monkeypatch.setattr(DryRunController, "_browser_process_id", staticmethod(lambda _page: 4242))
    controller = DryRunController(config_path, tmp_path / "module-data")
    monkeypatch.setattr(controller, "_wait_switch_interval", lambda _seconds: True)
    controller.select("target-0", request_revision=1)
    controller.start(
        "target-0",
        "",
        automatic=True,
        use_today_message=True,
        run_id="run-one-page",
        request_revision=1,
    )
    controller._thread.join(timeout=3)
    status = controller.status()

    assert opened == 1
    assert len(set(visited_pages)) == 1
    assert status["browser_pid"] == 4242
    assert status["context_identity"]
    assert status["page_identity"]
    assert status["page_count_before"] == status["page_count_after"] == 1


def test_batch_stops_before_target_when_context_has_ambiguous_extra_page(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path, count=1)
    page = SimpleNamespace(bring_to_front=lambda: None)
    extra = SimpleNamespace(opener=lambda: None)
    context = SimpleNamespace(pages=[page, extra])
    page.context = context
    monkeypatch.setattr(dry_run_module, "SingleInstanceLock", lambda _path: nullcontext())
    monkeypatch.setattr(dry_run_module, "open_chat", lambda *_args, **_kwargs: nullcontext(page))
    monkeypatch.setattr(
        TestCenterDryRun,
        "run_target",
        lambda *_args, **_kwargs: pytest.fail("target must not start with ambiguous pages"),
    )
    controller = DryRunController(config_path, tmp_path / "module-data")
    controller.select("target-0", request_revision=1)

    controller.start(
        "target-0",
        "测试",
        automatic=False,
        run_id="run-ambiguous-page",
        request_revision=1,
    )
    controller._thread.join(timeout=3)
    status = controller.status()

    assert status["result"] == "navigation_failed"
    assert status["page_count_before"] == status["page_count_after"] == 2
    assert status["completed_targets"] == 0


def test_confirmed_unexpected_popup_is_closed_and_recorded_before_batch_continues(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path, count=2)
    visited: list[str] = []

    class FakePopup:
        def __init__(self, opener):
            self._opener = opener
            self.closed = False

        def opener(self):
            return self._opener

        def close(self):
            self.closed = True
            context.pages.remove(self)

    context = SimpleNamespace(pages=[])
    page = SimpleNamespace(bring_to_front=lambda: None, context=context)
    popup = FakePopup(page)
    context.pages.append(page)
    monkeypatch.setattr(dry_run_module, "SingleInstanceLock", lambda _path: nullcontext())
    monkeypatch.setattr(dry_run_module, "open_chat", lambda *_args, **_kwargs: nullcontext(page))
    monkeypatch.setattr(
        dry_run_module,
        "preview_today_target_message",
        lambda _config, target, _today: SimpleNamespace(text=f"今日文案-{target.stable_id}"),
    )

    def target_with_popup(
        _runner,
        target,
        _test_text,
        _settings,
        *,
        run_id,
        request_revision,
        **_kwargs,
    ):
        visited.append(target.stable_id)
        if target.stable_id == "target-0":
            context.pages.append(popup)
        return DryRunResult(
            run_id=run_id,
            request_revision=request_revision,
            target_id=target.stable_id,
            selected_target_id=target.stable_id,
            expected_conversation_id=target.candidate_id,
            selected_display_name=target.name,
            visible_conversation_id=target.candidate_id,
            visible_display_name=target.name,
            identity_match=True,
            identity_match_reason="stable_id_match",
            composer_status="empty",
            stage="completed",
            result="completed",
        )

    monkeypatch.setattr(TestCenterDryRun, "run_target", target_with_popup)
    controller = DryRunController(config_path, tmp_path / "module-data")
    monkeypatch.setattr(controller, "_wait_switch_interval", lambda _seconds: True)
    controller.select("target-0", request_revision=1)
    controller.start(
        "target-0",
        "",
        automatic=True,
        use_today_message=True,
        run_id="run-popup",
        request_revision=1,
    )
    controller._thread.join(timeout=3)
    status = controller.status()

    assert popup.closed is True
    assert visited == ["target-0", "target-1"]
    assert status["page_count_before"] == status["page_count_after"] == 1
    assert status["unexpected_page_count"] == 1
    assert status["unexpected_page_message"] == "检测到并关闭了意外弹出页面"


def test_batch_continues_after_safe_skip_identity_and_navigation_failures(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path, count=4)
    visited: list[str] = []
    resolved: list[str] = []
    _install_fake_batch_runtime(
        monkeypatch,
        {
            "target-0": "skipped_existing_draft",
            "target-1": "identity_mismatch",
            "target-2": "navigation_failed",
        },
        visited,
        resolved,
    )
    controller = DryRunController(config_path, tmp_path / "module-data")
    monkeypatch.setattr(controller, "_wait_switch_interval", lambda _seconds: True)
    controller.select("target-0", request_revision=1)

    controller.start(
        "target-0",
        "",
        automatic=True,
        use_today_message=True,
        run_id="run-safe-failures",
        request_revision=1,
    )
    controller._thread.join(timeout=3)
    status = controller.status()

    assert visited == ["target-0", "target-1", "target-2", "target-3"]
    assert resolved == ["target-3"]
    assert status["passed_targets"] == 1
    assert status["skipped_targets"] == 1
    assert status["failed_targets"] == 2
    assert status["completed_targets"] == 4


def test_cleanup_failure_stops_batch_before_next_target(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path)
    visited: list[str] = []
    resolved: list[str] = []
    _install_fake_batch_runtime(
        monkeypatch,
        {"target-0": "cleanup_failed"},
        visited,
        resolved,
    )
    controller = DryRunController(config_path, tmp_path / "module-data")
    monkeypatch.setattr(controller, "_wait_switch_interval", lambda _seconds: True)
    controller.select("target-0", request_revision=1)

    controller.start(
        "target-0",
        "",
        automatic=True,
        use_today_message=True,
        run_id="run-cleanup-failed",
        request_revision=1,
    )
    controller._thread.join(timeout=3)
    status = controller.status()

    assert visited == ["target-0"]
    assert status["completed_targets"] == 1
    assert status["remaining_targets"] == 2
    assert status["counters"]["real_composer_writes"] == 1
    assert status["counters"]["real_composer_clears"] == 0
    assert status["counters"]["cleanup_failures"] == 1


def test_pause_becomes_visible_only_after_current_target_is_safely_cleared(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path, count=2)
    entered = threading.Event()
    release_cleanup = threading.Event()
    visited: list[str] = []
    resolved: list[str] = []
    _install_fake_batch_runtime(monkeypatch, {}, visited, resolved)
    original_run_target = TestCenterDryRun.run_target

    def blocked_first_target(*args, **kwargs):
        result = original_run_target(*args, **kwargs)
        if result.target_id == "target-0":
            entered.set()
            assert release_cleanup.wait(timeout=2)
        return result

    monkeypatch.setattr(TestCenterDryRun, "run_target", blocked_first_target)
    controller = DryRunController(config_path, tmp_path / "module-data")
    monkeypatch.setattr(
        controller,
        "_wait_switch_interval",
        lambda _seconds: controller._wait_until_resumed_or_stopped(),
    )
    controller.select("target-0", request_revision=1)
    controller.start(
        "target-0",
        "",
        automatic=True,
        use_today_message=True,
        run_id="run-pause-safe",
        request_revision=1,
    )
    assert entered.wait(timeout=2)

    controller.pause()
    assert controller.status()["paused"] is False
    release_cleanup.set()
    deadline = time.monotonic() + 2
    while not controller.status()["paused"] and time.monotonic() < deadline:
        time.sleep(0.01)

    paused = controller.status()
    assert paused["paused"] is True
    assert paused["counters"]["real_composer_writes"] == 1
    assert paused["counters"]["real_composer_clears"] == 1
    controller.resume()
    controller._thread.join(timeout=3)
    assert controller.status()["completed_targets"] == 2


def test_pause_request_sets_nonblocking_safe_pause_state_immediately(tmp_path: Path):
    controller = DryRunController(tmp_path / "config.yaml", tmp_path / "module-data")
    controller._update(running=True, stage="typing")

    started = time.monotonic()
    status = controller.pause()

    assert time.monotonic() - started < 0.2
    assert status["pause_requested"] is True
    assert status["paused"] is False
    assert status["stage"] == "pausing"


def test_pause_during_browser_lock_wait_stops_retries_until_resume(tmp_path: Path, monkeypatch):
    attempts = 0
    acquired = threading.Event()
    release = threading.Event()

    class ContendedLock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise dry_run_module.TaskAlreadyRunning("busy")
            acquired.set()
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(dry_run_module, "SingleInstanceLock", ContendedLock)
    controller = DryRunController(tmp_path / "config.yaml", tmp_path / "module-data")
    controller._update(running=True)
    controller.pause()

    def acquire():
        with controller._acquire_browser_lock(tmp_path / "browser.lock", timeout_seconds=2):
            release.wait(timeout=2)

    thread = threading.Thread(target=acquire)
    thread.start()
    deadline = time.monotonic() + 1
    while not controller.status()["paused"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert controller.status()["paused"] is True
    assert attempts == 0
    controller.resume()
    assert acquired.wait(timeout=1)
    release.set()
    thread.join(timeout=1)
    assert attempts == 2


def test_pause_during_target_interval_is_immediate(tmp_path: Path):
    controller = DryRunController(tmp_path / "config.yaml", tmp_path / "module-data")
    controller._update(running=True)
    finished = threading.Event()

    thread = threading.Thread(
        target=lambda: (controller._wait_switch_interval(0.5), finished.set())
    )
    thread.start()
    started = time.monotonic()
    controller.pause()
    deadline = started + 0.2
    while not controller.status()["paused"] and time.monotonic() < deadline:
        time.sleep(0.005)

    assert controller.status()["paused"] is True
    assert time.monotonic() - started < 0.2
    controller.resume()
    assert finished.wait(timeout=1)
    thread.join(timeout=1)


def test_pause_during_partial_typing_clears_only_the_exact_inserted_prefix(tmp_path: Path, monkeypatch):
    pause_requested = threading.Event()

    class FakeEditor:
        def __init__(self):
            self.text = ""

        def press_sequentially(self, value, *, delay):
            assert delay == 30
            self.text += value
            if len(self.text) == 2:
                pause_requested.set()

        def press(self, key):
            if key == "Backspace":
                self.text = ""

    editor = FakeEditor()

    class FakeChat:
        def __init__(self, *_args, **_kwargs):
            pass

        def open_conversation_identity(self, *_args, **_kwargs):
            return ConversationIdentity("candidate-a", "candidate-a", "目标", "目标", True, "stable_id_match")

        def composer_editor(self):
            return editor

        def composer_state(self, _editor):
            normalized = len(editor.text.replace(" ", ""))
            return ComposerState(editor.text, normalized, normalized > 0, False, False, normalized == 0, "empty" if normalized == 0 else "visible_text")

    page = SimpleNamespace(
        set_default_timeout=lambda _value: None,
        bring_to_front=lambda: None,
        wait_for_timeout=lambda _value: None,
    )
    monkeypatch.setattr(dry_run_module, "DouyinChat", FakeChat)
    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a"),
        "四字文本",
        DryRunSettings(page_ready_delay_ms=500, typing_delay_ms=30, typed_text_hold_ms=500, clear_verify_delay_ms=200),
        pause_requested=pause_requested.is_set,
    )

    assert result.result == "paused"
    assert editor.text == ""
    assert result.counters["real_composer_writes"] == 1
    assert result.counters["real_composer_clears"] == 1
    assert result.counters["cleanup_failures"] == 0


def test_pause_after_navigation_stops_before_composer_inspection(tmp_path: Path, monkeypatch):
    pause_requested = threading.Event()
    composer_inspections = 0

    class FakeChat:
        def __init__(self, *_args, **_kwargs):
            pass

        def open_conversation_identity(self, *_args, **_kwargs):
            pause_requested.set()
            return ConversationIdentity("candidate-a", "candidate-a", "目标", "目标", True, "stable_id_match")

        def composer_editor(self):
            nonlocal composer_inspections
            composer_inspections += 1
            raise AssertionError("composer must not be inspected while pausing")

    page = SimpleNamespace(
        set_default_timeout=lambda _value: None,
        bring_to_front=lambda: None,
        wait_for_timeout=lambda _value: None,
    )
    monkeypatch.setattr(dry_run_module, "DouyinChat", FakeChat)
    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a"),
        "测试文本",
        DryRunSettings(),
        pause_requested=pause_requested.is_set,
    )

    assert result.result == "paused"
    assert composer_inspections == 0
    assert all(value == 0 for value in result.counters.values())


@pytest.mark.parametrize("pause_phase", ["observation", "cleanup"])
def test_pause_during_observation_or_cleanup_finishes_exact_cleanup(
    pause_phase: str,
    tmp_path: Path,
    monkeypatch,
):
    pause_requested = threading.Event()

    class FakeEditor:
        def __init__(self):
            self.text = ""

        def press_sequentially(self, value, *, delay):
            self.text += value

        def press(self, key):
            if key == "Backspace":
                self.text = ""
                if pause_phase == "cleanup":
                    pause_requested.set()

    editor = FakeEditor()

    class FakeChat:
        def __init__(self, *_args, **_kwargs):
            pass

        def open_conversation_identity(self, *_args, **_kwargs):
            return ConversationIdentity("candidate-a", "candidate-a", "目标", "目标", True, "stable_id_match")

        def composer_editor(self):
            return editor

        def composer_state(self, _editor):
            normalized = len(editor.text.replace(" ", ""))
            return ComposerState(editor.text, normalized, normalized > 0, False, False, normalized == 0, "empty" if normalized == 0 else "visible_text")

    def wait_for_timeout(_milliseconds):
        if pause_phase == "observation" and editor.text == "测试":
            pause_requested.set()

    page = SimpleNamespace(
        set_default_timeout=lambda _value: None,
        bring_to_front=lambda: None,
        wait_for_timeout=wait_for_timeout,
    )
    monkeypatch.setattr(dry_run_module, "DouyinChat", FakeChat)

    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="目标", stable_id="target-a", candidate_id="candidate-a"),
        "测试",
        DryRunSettings(page_ready_delay_ms=500, typing_delay_ms=30, typed_text_hold_ms=500, clear_verify_delay_ms=200),
        pause_requested=pause_requested.is_set,
    )

    assert result.result == "paused"
    assert editor.text == ""
    assert result.counters["real_composer_writes"] == 1
    assert result.counters["real_composer_clears"] == 1
    assert result.counters["cleanup_failures"] == 0


def test_safe_stop_waits_for_balanced_cleanup_and_does_not_start_next_target(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path, count=2)
    entered = threading.Event()
    release_cleanup = threading.Event()
    visited: list[str] = []
    resolved: list[str] = []
    _install_fake_batch_runtime(monkeypatch, {}, visited, resolved)
    original_run_target = TestCenterDryRun.run_target

    def blocked_first_target(*args, **kwargs):
        result = original_run_target(*args, **kwargs)
        entered.set()
        assert release_cleanup.wait(timeout=2)
        return result

    monkeypatch.setattr(TestCenterDryRun, "run_target", blocked_first_target)
    controller = DryRunController(config_path, tmp_path / "module-data")
    monkeypatch.setattr(controller, "_wait_switch_interval", lambda _seconds: True)
    controller.select("target-0", request_revision=1)
    controller.start(
        "target-0",
        "",
        automatic=True,
        use_today_message=True,
        run_id="run-stop-safe",
        request_revision=1,
    )
    assert entered.wait(timeout=2)

    controller.stop()
    assert controller.status()["running"] is True
    release_cleanup.set()
    controller._thread.join(timeout=3)
    status = controller.status()

    assert visited == ["target-0"]
    assert status["result"] == "stopped"
    assert status["counters"]["real_composer_writes"] == 1
    assert status["counters"]["real_composer_clears"] == 1


def test_controller_allows_only_one_single_or_batch_run(tmp_path: Path, monkeypatch):
    config_path, _targets = _configured_batch(tmp_path, count=1)
    entered = threading.Event()
    release = threading.Event()
    controller = DryRunController(config_path, tmp_path / "module-data")
    controller.select("target-0", request_revision=1)

    def blocked_run(*_args):
        entered.set()
        assert release.wait(timeout=2)

    monkeypatch.setattr(controller, "_run", blocked_run)
    controller.start(
        "target-0",
        "测试文本",
        automatic=False,
        run_id="run-one",
        request_revision=1,
    )
    assert entered.wait(timeout=2)

    with pytest.raises(RuntimeError, match="正在运行"):
        controller.start(
            "target-0",
            "测试文本",
            automatic=False,
            run_id="run-two",
            request_revision=1,
        )

    release.set()
    controller._thread.join(timeout=3)


def test_stable_id_mismatch_stops_before_composer_even_when_display_names_match(page, tmp_path: Path, monkeypatch):
    _open_fixture(page)
    composer_inspections = 0
    composer_write_starts = 0

    def record_composer_inspection(_chat):
        nonlocal composer_inspections
        composer_inspections += 1
        raise AssertionError("composer must not be inspected")

    def record_composer_write_start():
        nonlocal composer_write_starts
        composer_write_starts += 1

    monkeypatch.setattr("autody.test_center_dry_run.DouyinChat.composer_editor", record_composer_inspection)
    result = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path).run_target(
        Target(name="小明", stable_id="target-b", candidate_id=CONVERSATION_B_ID),
        "模块测试文本",
        DryRunSettings(),
        run_id="run-stable-mismatch",
        request_revision=7,
        on_composer_write_started=record_composer_write_start,
    )

    assert result.selected_target_id == "target-b"
    assert result.expected_conversation_id == CONVERSATION_B_ID
    assert result.visible_conversation_id == CONVERSATION_A_ID
    assert result.selected_display_name == result.visible_display_name == "小明"
    assert result.identity_match is False
    assert result.identity_match_reason == "stable_id_mismatch"
    assert result.message == "会话不匹配，测试已停止"
    assert composer_inspections == 0
    assert composer_write_starts == 0
    assert all(value == 0 for value in result.counters.values())


def test_identity_or_cleanup_mismatch_stops_without_deleting_user_content(page, tmp_path: Path):
    _open_fixture(page)
    page.locator('[data-e2e="conversation-name"]').evaluate("element => element.textContent = '小红'")
    page.locator('[data-e2e="chat-header-name"]').evaluate("element => element.textContent = '小红'")
    runner = TestCenterDryRun(page, ChatSelectors.test_defaults(), artifact_dir=tmp_path)

    mismatch = runner.run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID),
        "模块测试文本",
        DryRunSettings(),
    )

    assert mismatch.result == "stopped"
    assert mismatch.message == "会话不匹配，测试已停止"
    assert mismatch.identity_match is False
    assert mismatch.counters["real_composer_writes"] == 0

    page.locator('[data-e2e="conversation-name"]').evaluate("element => element.textContent = '小明'")
    page.locator('[data-e2e="chat-header-name"]').evaluate("element => element.textContent = '小明'")
    changed = runner.run_target(
        Target(name="小明", stable_id="target-a", candidate_id=CONVERSATION_A_ID),
        "模块测试文本",
        DryRunSettings(),
        on_stage=lambda stage: page.locator('[data-e2e="chat-input"]').evaluate(
            "element => element.textContent = '用户修改后的草稿'"
        ) if stage == "observing" else None,
    )

    assert changed.result == "cleanup_failed"
    assert changed.message == "输入内容发生变化，无法确认安全清除"
    assert page.locator('[data-e2e="chat-input"]').inner_text() == "用户修改后的草稿"
    assert changed.counters["cleanup_failures"] == 1
    assert changed.counters["real_composer_clears"] == 0


def test_target_change_immediately_clears_previous_identity_draft_and_result(page):
    _open_mocked_frontend(
        page,
        _frontend_state(
            selected_target_id="target-a",
            request_revision=1,
            run_id="run-a",
            visible_name="好友甲",
            result="skipped_existing_draft",
        ),
    )

    page.select_option("#target", "target-b")

    status_text = page.locator(".status-column").inner_text()
    assert page.locator("#target").input_value() == "target-b"
    assert "好友甲" not in status_text
    assert "skipped_existing_draft" not in status_text
    assert "existing_draft_preserved" not in status_text
    assert "待核验" in status_text
    assert "尚未检查" in status_text


def test_out_of_order_older_target_response_is_ignored(page):
    _open_mocked_frontend(
        page,
        _frontend_state(
            selected_target_id="target-a",
            request_revision=1,
            run_id=None,
            visible_name=None,
            result=None,
        ),
    )
    page.select_option("#target", "target-b")
    page.wait_for_function("window.__requests.length === 3")
    selected_b = _frontend_state(
        selected_target_id="target-b",
        request_revision=2,
        run_id=None,
        visible_name=None,
        result=None,
    )
    page.evaluate("payload => window.__resolveRequest(2, payload)", selected_b)
    page.wait_for_function("window.__requests.length === 5")
    page.evaluate("void load()")
    page.wait_for_function("window.__requests.length === 7")
    newer = _frontend_state(
        selected_target_id="target-b",
        request_revision=2,
        run_id="run-b",
        visible_name="好友乙",
        result="completed",
    )
    older = _frontend_state(
        selected_target_id="target-a",
        request_revision=1,
        run_id="run-a",
        visible_name="好友甲",
        result="skipped_existing_draft",
    )
    page.evaluate(
        """payloads => {
          window.__resolveRequest(5, payloads.newer);
          window.__resolveRequest(6, {items:[]});
          window.__resolveRequest(3, payloads.older);
          window.__resolveRequest(4, {items:[]});
        }""",
        {"newer": newer, "older": older},
    )
    page.wait_for_function("document.querySelector('.status-column').innerText.includes('好友乙')")

    assert page.locator("#target").input_value() == "target-b"
    assert "好友甲" not in page.locator(".status-column").inner_text()


def test_stale_response_for_an_older_run_is_ignored(page):
    _open_mocked_frontend(
        page,
        _frontend_state(
            selected_target_id="target-b",
            request_revision=2,
            run_id="run-current",
            visible_name="好友乙",
            result="completed",
        ),
    )
    page.evaluate("void load()")
    page.wait_for_function("window.__requests.length === 4")
    stale = _frontend_state(
        selected_target_id="target-b",
        request_revision=2,
        run_id=None,
        visible_name="旧会话",
        result="skipped_existing_draft",
    )
    page.evaluate(
        """payload => {
          window.__resolveRequest(2, payload);
          window.__resolveRequest(3, {items:[]});
        }""",
        stale,
    )
    page.wait_for_timeout(50)

    status_text = page.locator(".status-column").inner_text()
    assert "好友乙" in status_text
    assert "旧会话" not in status_text


def test_controller_rejects_stale_revision_and_binds_updates_to_current_run(tmp_path: Path):
    controller = DryRunController(tmp_path / "config.yaml", tmp_path / "module-data")
    controller.select("target-a", request_revision=1)
    controller.select("target-b", request_revision=2)

    assert controller.select("target-a", request_revision=1) is False
    assert controller._update_for_run("run-old", 1, visible_display_name="好友甲") is False
    status = controller.status()
    assert status["selected_target_id"] == "target-b"
    assert status["visible_display_name"] is None
    assert status["identity_match"] is None
    assert status["stage"] == "waiting"
    assert status["composer_status"] == "unknown"
    assert status["result"] is None


def test_concurrent_target_selections_cannot_persist_an_older_revision(tmp_path: Path, monkeypatch):
    controller = DryRunController(tmp_path / "config.yaml", tmp_path / "module-data")
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    original_write = controller.store._write_json

    def delayed_write(path, value):
        if value["request_revision"] == 1:
            first_write_started.set()
            assert release_first_write.wait(timeout=2)
        original_write(path, value)

    monkeypatch.setattr(controller.store, "_write_json", delayed_write)
    first = threading.Thread(
        target=lambda: controller.select("target-a", request_revision=1),
        daemon=True,
    )
    first.start()
    assert first_write_started.wait(timeout=2)
    second = threading.Thread(
        target=lambda: controller.select("target-b", request_revision=2),
        daemon=True,
    )
    second.start()
    release_first_write.set()
    first.join(timeout=2)
    second.join(timeout=2)

    persisted = json.loads(controller.selection_path.read_text(encoding="utf-8"))
    assert persisted == {"selected_target_id": "target-b", "request_revision": 2}
    assert controller.status()["selected_target_id"] == "target-b"


def test_identity_mismatch_without_composer_writes_does_not_require_recovery():
    mismatch = {
        "result": "stopped",
        "identity_match": False,
        "counters": empty_counters(),
    }
    uncertain_write = {
        "result": "stopped",
        "identity_match": True,
        "counters": {**empty_counters(), "real_composer_writes": 1},
    }

    assert DryRunController.recovery_needed(mismatch) is False
    assert DryRunController.recovery_needed(uncertain_write) is True


@pytest.mark.parametrize(
    "payload",
    [
        {"page_ready_delay_ms": 499},
        {"typing_delay_ms": 301},
        {"typed_text_hold_ms": 10_001},
        {"clear_verify_delay_ms": 199},
        {"target_switch_interval_seconds": 61},
        {"navigation_timeout_seconds": 31},
    ],
)
def test_dry_run_settings_reject_out_of_range_values(payload):
    with pytest.raises(ValueError):
        DryRunSettings(**payload)


def test_module_store_keeps_only_timing_and_redacted_dry_run_history(tmp_path: Path):
    store = DryRunStore(tmp_path / "module-data")
    settings = store.save_settings(DryRunSettings(typing_delay_ms=120))
    store.save_result({
        "run_id": "run-1", "target_id": "target-a", "stage": "completed", "result": "completed",
        "text_length": 6, "text_hash": "a" * 64, "secret_text": "不得持久化", "counters": {"send_attempts": 0},
    })
    store.save_recovery({"run_id": "run-1", "target_id": "target-a", "text_hash": "a" * 64, "text_length": 6, "stage": "typing"})

    assert settings.typing_delay_ms == 120
    assert store.history() == [{
        "run_id": "run-1", "target_id": "target-a", "stage": "completed", "result": "completed",
        "text_length": 6, "text_hash": "a" * 64, "counters": {"send_attempts": 0},
    }]
    assert store.recovery_warning() == "检测到未完成的测试输入，请人工检查聊天输入框；系统不会自动清理。"
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "module-data").rglob("*") if path.is_file())
    assert "不得持久化" not in persisted

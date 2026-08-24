"""Canonical, privacy-bounded failure details shared by runtime and UI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field


_STAGE_LABELS = {
    "target_loaded": "目标已从配置载入",
    "target_binding_resolved": "目标绑定解析",
    "account_verified": "当前账号校验",
    "browser_opened": "浏览器打开",
    "conversation_located": "会话定位",
    "conversation_selected": "会话已选中",
    "identity_verified": "会话身份验证",
    "composer_found": "输入框定位",
    "draft_state_checked": "草稿状态检查",
    "message_prepared": "消息准备",
    "send_boundary_reached": "发送边界",
    "confirmation_observed": "发送结果确认",
    "history_written": "运行历史写入",
}

_RECOVERED_REASSOCIATION_REASONS = frozenset(
    {"binding_stale", "binding_missing", "blocked_ambiguous_target"}
)

_REASONS: dict[str, dict[str, Any]] = {
    "conversation_not_found": {
        "category": "navigation",
        "summary": "无法在当前会话列表中找到目标",
        "detail": "已搜索当前账号的完整会话列表，但没有找到稳定绑定对应的会话；尚未访问输入框。",
        "retryable": True,
        "action": "retry",
        "action_zh": "仅重试此目标",
    },
    "send_failed_before_action": {
        "category": "preparation",
        "summary": "发送前准备失败，尚未尝试发送",
        "detail": "操作在发送边界之前停止，可以在条件恢复后安全重试。",
        "retryable": True,
        "action": "retry",
        "action_zh": "仅重试此目标",
    },
    "message_pack_unavailable": {
        "category": "content",
        "summary": "目标文案包当前不可用",
        "detail": "消息准备未完成，未访问输入框，也未尝试发送。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
    "blocked_ambiguous_target": {
        "category": "identity",
        "summary": "存在同名目标，无法安全确定聊天对象",
        "detail": "系统已在会话定位前停止，不会按昵称猜测目标。",
        "retryable": False,
        "action": "reassociate",
        "action_zh": "重新关联",
    },
    "binding_stale": {
        "category": "binding",
        "summary": "目标绑定已过期，需要重新关联",
        "detail": "当前候选缓存无法证明原稳定绑定仍然有效；系统不会按相同昵称自动改绑。",
        "retryable": False,
        "action": "reassociate",
        "action_zh": "重新关联",
    },
    "binding_missing": {
        "category": "binding",
        "summary": "目标缺少稳定绑定，需要重新关联",
        "detail": "该目标没有可用于验证会话身份的稳定候选标识；尚未访问输入框。",
        "retryable": False,
        "action": "reassociate",
        "action_zh": "重新关联",
    },
    "account_scope_mismatch": {
        "category": "account",
        "summary": "当前登录账号与目标所属账号不一致",
        "detail": "已在账号校验阶段停止，未打开目标会话，也未访问输入框。",
        "retryable": False,
        "action": "switch_account",
        "action_zh": "切换或登录账号",
    },
    "account_operation_busy": {
        "category": "account",
        "summary": "当前有任务占用账号或浏览器",
        "detail": "账号操作未开始，当前发送、重试或测试任务会保持原有账号快照。",
        "retryable": True,
        "action": "retry_operation",
        "action_zh": "等待当前任务结束后重试",
    },
    "account_profile_unavailable": {
        "category": "account",
        "summary": "当前账号尚未完成权威身份验证",
        "detail": "无法建立安全的本地账号资料；现有账号数据未迁移也未删除。",
        "retryable": False,
        "action": "switch_account",
        "action_zh": "登录并验证当前账号",
    },
    "account_switch_failed": {
        "category": "account",
        "summary": "本地账号切换未能完成",
        "detail": "切换已在安全边界停止，请检查账号资料后重试。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
    "account_logout_failed": {
        "category": "account",
        "summary": "当前账号认证清理未能完成",
        "detail": "发送绑定仍保持阻止状态，其他账号和本地设置未被删除。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
    "friend_discovery_failed": {
        "category": "discovery",
        "summary": "好友候选刷新未能完成",
        "detail": "已保留上一次完整候选结果，不会用不完整扫描覆盖绑定。",
        "retryable": True,
        "action": "retry_operation",
        "action_zh": "重新刷新候选",
    },
    "test_center_busy": {
        "category": "test_center",
        "summary": "测试中心正在执行受控任务",
        "detail": "本次操作未取得测试中心所有权，未触碰发送流程。",
        "retryable": True,
        "action": "retry_operation",
        "action_zh": "等待测试结束后重试",
    },
    "operation_cancelled": {
        "category": "system",
        "summary": "操作已取消",
        "detail": "操作在当前安全阶段停止，未继续执行后续步骤。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
    "identity_verification_failed": {
        "category": "identity",
        "summary": "会话身份验证失败，未访问输入框",
        "detail": "选中行或会话稳定标识与目标绑定不一致，为避免误发已停止。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
    "navigation_not_stable": {
        "category": "navigation",
        "summary": "会话选择未能稳定确认",
        "detail": "页面中的选中行、标题或稳定会话标识未能连续一致；未访问输入框。",
        "retryable": True,
        "action": "retry",
        "action_zh": "仅重试此目标",
    },
    "page_load_timeout": {
        "category": "browser",
        "summary": "页面加载超时，尚未尝试发送",
        "detail": "聊天页未能在限定时间内进入可验证状态，发送边界尚未到达。",
        "retryable": True,
        "action": "retry",
        "action_zh": "仅重试此目标",
    },
    "browser_busy": {
        "category": "browser",
        "summary": "浏览器正在执行其他任务",
        "detail": "未取得全局浏览器锁，本次操作在打开聊天页前停止。",
        "retryable": True,
        "action": "retry",
        "action_zh": "仅重试此目标",
    },
    "login_required": {
        "category": "account",
        "summary": "当前账号未登录或需要安全验证",
        "detail": "账号身份无法验证，发送任务已在导航前停止。",
        "retryable": False,
        "action": "switch_account",
        "action_zh": "切换或登录账号",
    },
    "composer_missing": {
        "category": "composer",
        "summary": "无法识别消息输入框",
        "detail": "会话身份已验证，但页面中没有找到可用输入区域；尚未到达发送边界。",
        "retryable": True,
        "action": "retry",
        "action_zh": "仅重试此目标",
    },
    "draft_present": {
        "category": "composer",
        "summary": "输入框存在草稿，已跳过且未修改",
        "detail": "检测到已有文字、附件、提及或回复内容；系统保留原状态并停止。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
    "confirmation_failed_uncertain": {
        "category": "confirmation",
        "summary": "消息发送状态无法确认，为防止重复发送已停止",
        "detail": "发送边界可能已经到达，但未观察到权威确认；禁止自动重试。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情并人工确认，禁止自动重试",
        "uncertain": True,
    },
    "history_write_failed": {
        "category": "logging",
        "summary": "日志写入失败，但不影响发送结果",
        "detail": "业务结果已保留，仅结构化历史或日志未能写入。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
    "unknown_exception": {
        "category": "system",
        "summary": "操作未能完成",
        "detail": "发生未预期错误，系统已在当前阶段停止。",
        "retryable": False,
        "action": "details",
        "action_zh": "查看详情",
    },
}


class FailureDetail(BaseModel):
    category: str
    stage: str
    reason_code: str
    user_summary_zh: str
    user_detail_zh: str
    retryable: bool
    send_attempted: bool = False
    send_attempts: int = 0
    uncertain_send: bool = False
    suggested_action: str
    suggested_action_zh: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    run_id: str | None = None
    target_stable_id: str | None = None
    account_scope: str | None = None
    scheduler_execution_id: str | None = None
    binding_valid: bool | None = None
    account_scope_matches: bool | None = None
    diagnostic_details: dict[str, Any] = Field(default_factory=dict)

    def _reassociation_resolved(self) -> bool:
        return (
            self.reason_code in _RECOVERED_REASSOCIATION_REASONS
            and self.send_attempts == 0
            and not self.uncertain_send
            and self.binding_valid is True
            and self.account_scope_matches is True
        )

    @computed_field
    @property
    def safe_retry_available(self) -> bool:
        return (
            (self.retryable or self._reassociation_resolved())
            and self.send_attempts == 0
            and not self.uncertain_send
            and self.binding_valid is True
            and self.account_scope_matches is True
        )

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        payload = super().model_dump(*args, **kwargs)
        if self._reassociation_resolved():
            payload["suggested_action"] = "retry"
            payload["suggested_action_zh"] = "仅重试此目标"
        return payload


def failure_detail(
    reason_code: str,
    *,
    stage: str,
    send_attempts: int = 0,
    binding_valid: bool | None = None,
    account_scope_matches: bool | None = None,
    **context: Any,
) -> FailureDetail:
    definition = _REASONS.get(reason_code, _REASONS["unknown_exception"])
    resolved_code = reason_code if reason_code in _REASONS else "unknown_exception"
    stage_label = _STAGE_LABELS.get(stage, stage or "未知阶段")
    base_detail = str(definition["detail"])
    user_detail = (
        f"{stage_label}：{base_detail}"
        if not base_detail.startswith(stage_label)
        else base_detail
    )
    uncertain = bool(definition.get("uncertain", False))
    send_attempted = send_attempts > 0
    return FailureDetail(
        category=str(definition["category"]),
        stage=stage,
        reason_code=resolved_code,
        user_summary_zh=str(definition["summary"]),
        user_detail_zh=user_detail,
        retryable=bool(definition["retryable"]),
        send_attempted=send_attempted,
        send_attempts=max(0, send_attempts),
        uncertain_send=uncertain,
        suggested_action=str(definition["action"]),
        suggested_action_zh=str(definition["action_zh"]),
        binding_valid=binding_valid,
        account_scope_matches=account_scope_matches,
        **context,
    )

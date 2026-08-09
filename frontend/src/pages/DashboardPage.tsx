import { Fragment, useState } from "react";
import { AlertTriangle, ArchiveRestore, CalendarPlus2, ChevronDown, Download, Play, ScanLine, ShieldCheck, Wrench } from "lucide-react";
import { ActionButton } from "../components/ActionButton";
import { StatusRail } from "../components/StatusRail";
import type { DashboardStatus, FailureDetail } from "../types";
import type { ViewName } from "../components/Sidebar";

const deliveryStatusLabel = {
  success: "已发送",
  failed: "今日失败",
  pending: "待发送"
};

const healthLabel = {
  healthy: "正常",
  abnormal: "异常",
  unknown: "未知"
};

const healthTag = {
  healthy: "success",
  abnormal: "failed",
  unknown: "pending"
};

const triggerLabel = {
  scheduled: "定时",
  manual: "手动",
  startup_recovery: "错过恢复",
  retry: "补发"
};

const stageLabel: Record<string, string> = {
  target_loaded: "目标载入",
  target_binding_resolved: "目标绑定",
  account_verified: "账号校验",
  browser_opened: "浏览器打开",
  conversation_located: "会话定位",
  conversation_selected: "会话选择",
  identity_verified: "身份验证",
  composer_found: "输入框定位",
  draft_state_checked: "草稿检查",
  message_prepared: "消息准备",
  send_boundary_reached: "发送边界",
  confirmation_observed: "结果确认",
  history_written: "历史写入"
};

interface FailureOccurrence {
  targetId: string;
  failure: FailureDetail;
}

interface FailureGroup {
  key: string;
  label: string;
  items: FailureOccurrence[];
  resolved: boolean;
}

const reasonLabel: Record<string, string> = {
  conversation_not_found: "会话定位异常",
  binding_missing: "目标绑定异常",
  binding_stale: "目标绑定异常",
  binding_revalidation_required: "目标绑定异常",
  identity_ambiguous: "身份校验异常",
  account_scope_mismatch: "账号校验异常",
  login_required: "账号校验异常",
  confirmation_failed_uncertain: "发送结果异常",
  send_failed: "发送异常"
};

function failureLabel(failure: FailureDetail) {
  if (reasonLabel[failure.reason_code]) return reasonLabel[failure.reason_code];
  if (failure.stage === "conversation_located") return "会话定位异常";
  if (failure.stage === "target_binding_resolved") return "目标绑定异常";
  if (failure.stage === "account_verified" || failure.stage === "identity_verified") return "身份校验异常";
  if (["send_boundary_reached", "confirmation_observed"].includes(failure.stage)) return "发送异常";
  return "运行异常";
}

function failureGroupDescription(label: string, count: number) {
  if (label === "会话定位异常") {
    return `${count} 个目标在本次执行中未完成会话定位。`;
  }
  return `${count} 个目标在本次执行中出现${label}。`;
}

function groupedHistoryFailures(status: DashboardStatus): {
  historyRows: DashboardStatus["history"];
  groupsByRow: Map<number, FailureGroup[]>;
} {
  const historyRows = status.history.slice(0, 7);
  const groupsByRow = new Map<number, FailureGroup[]>();
  historyRows.forEach((row, rowIndex) => {
    const rowGroups = new Map<string, FailureGroup>();
    Object.entries(row.target_failures || {}).forEach(([targetId, failure]) => {
      const key = JSON.stringify([
        row.run_id,
        row.end_time,
        failure.category,
        failure.reason_code,
        failure.stage,
        failure.suggested_action,
        failure.resolved === true
      ]);
      const group = rowGroups.get(key) || {
        key,
        label: failureLabel(failure),
        items: [],
        resolved: failure.resolved === true
      };
      group.items.push({ targetId, failure });
      rowGroups.set(key, group);
    });
    if (rowGroups.size) groupsByRow.set(rowIndex, [...rowGroups.values()]);
  });
  return { historyRows, groupsByRow };
}

export function DashboardPage({
  status,
  busy,
  onAction,
  onNavigate,
  onRetryTargets
}: {
  status: DashboardStatus;
  busy: string | null;
  onAction: (action: string) => void;
  onNavigate: (view: ViewName) => void;
  onRetryTargets?: (targetIds: string[]) => void;
}) {
  const [expandedRun, setExpandedRun] = useState<string | null>(null);
  const { historyRows, groupsByRow } = groupedHistoryFailures(status);
  const safeRetryTargetIds = status.friends.flatMap((friend) => (
    friend.target_id
    && friend.status === "failed"
    && friend.failure
    && !friend.failure.resolved
    && (friend.failure.retry_action_available ?? friend.failure.safe_retry_available)
      ? [friend.target_id]
      : []
  ));
  const handleIssue = (action: string) => {
    if (["friends", "messages", "packs", "scheduler", "logs", "backup", "settings"].includes(action)) {
      onNavigate(action as ViewName);
    } else {
      onAction(action);
    }
  };
  const targetName = (targetId: string) => (
    status.friends.find((friend) => friend.target_id === targetId)?.name
    || "失败目标"
  );
  const toggleRun = (key: string) => {
    setExpandedRun((current) => current === key ? null : key);
  };
  return (
    <>
      <header className="page-header">
        <div><h1>运行总览</h1><p>查看今日续火状态和本机自动化健康情况。</p></div>
        <div className="header-actions">
          <ActionButton disabled={!!busy} icon={<ShieldCheck size={17} />} onClick={() => onAction("health-check")}>检查登录</ActionButton>
          <ActionButton disabled={!!busy} primary icon={<Play size={17} fill="currentColor" />} onClick={() => onAction("run")}>立即运行</ActionButton>
          <ActionButton disabled={!!busy} icon={<ScanLine size={17} />} onClick={() => onAction("login")}>扫码登录</ActionButton>
          <ActionButton disabled={!!busy} icon={<Wrench size={17} />} onClick={() => onAction("repair-playwright")}>修复运行时</ActionButton>
        </div>
      </header>
      <StatusRail status={status} />
      <section className="stats-grid" aria-label="运行统计">
        <article className="panel stat-card"><small>今日成功</small><strong>{status.statistics.successful_today}</strong><span>失败 {status.statistics.failed_today}</span></article>
        <article className="panel stat-card"><small>连续成功</small><strong>{status.statistics.consecutive_successful_days} 天</strong><span>近 7 天 {status.statistics.success_rate_7d}%</span></article>
        <article className="panel stat-card"><small>近 30 天成功率</small><strong>{status.statistics.success_rate_30d}%</strong><span>7 天重试 {status.statistics.retries_7d} 次</span></article>
        <article className="panel stat-card"><small>本机资源</small><strong>{status.statistics.enabled_friend_count} 位好友</strong><span>{status.statistics.local_message_count} 条文案 · {status.statistics.active_message_pack_count} 个文案包</span></article>
      </section>
      <div className="dashboard-grid">
        <section className="panel history-panel">
          <div className="panel-heading"><h2>结构化运行记录</h2><span className="inline-actions"><span>{status.history.length} 条记录</span>{groupsByRow.size ? <button className="action-button primary" disabled={!!busy || !safeRetryTargetIds.length || !onRetryTargets} onClick={() => onRetryTargets?.(safeRetryTargetIds)}>重试所有目标</button> : null}</span></div>
          <div className="table-wrap">
            <table aria-label="结构化运行记录">
              <thead><tr><th>结束时间</th><th>来源</th><th>确认成功</th><th>重试</th><th>状态</th></tr></thead>
              <tbody>
                {historyRows.map((row, rowIndex) => {
                  const groups = groupsByRow.get(rowIndex) || [];
                  const anomalyCount = groups.reduce((total, group) => total + group.items.length, 0);
                  const runKey = `${row.run_id}-${row.end_time}-${rowIndex}`;
                  const expanded = expandedRun === runKey;
                  const toggle = () => anomalyCount && toggleRun(runKey);
                  return (
                  <Fragment key={runKey}>
                    <tr
                      className={anomalyCount ? "history-run-row expandable" : "history-run-row"}
                      role={anomalyCount ? "button" : undefined}
                      tabIndex={anomalyCount ? 0 : undefined}
                      aria-expanded={anomalyCount ? expanded : undefined}
                      aria-label={anomalyCount ? `运行记录异常详情，${anomalyCount} 个目标` : undefined}
                      onClick={toggle}
                      onKeyDown={(event) => {
                        if (anomalyCount && (event.key === "Enter" || event.key === " ")) {
                          event.preventDefault();
                          toggleRun(runKey);
                        }
                      }}
                    >
                      <td>{new Date(row.end_time).toLocaleString("zh-CN")}</td>
                      <td>{triggerLabel[row.trigger_source]}</td>
                      <td className="history-progress-cell">
                        <span>{row.success_count}/{row.total_targets}</span>
                        {row.skipped_count ? <small>跳过 {row.skipped_count}</small> : null}
                      </td>
                      <td>{row.retry_count}</td>
                      <td className="history-run-status">
                        <span className={row.final_status === "completed" || row.final_status === "already_done" ? "tag success" : "tag warning"}>{row.final_status === "completed" || row.final_status === "already_done" ? "成功" : "部分失败"}</span>
                        {anomalyCount ? <span className="history-anomaly-count"><AlertTriangle size={14} />{anomalyCount} 项异常<ChevronDown className={expanded ? "expanded" : ""} size={15} /></span> : null}
                      </td>
                    </tr>
                    {expanded ? <tr className="history-run-detail-row"><td colSpan={5}>
                      <div className="history-run-detail">
                        {groups.map((group) => {
                          const first = group.items[0].failure;
                          const retryable = group.items.some(({ failure }) => (
                            !failure.resolved
                            && (failure.retry_action_available ?? failure.safe_retry_available)
                          ));
                          const statusText = group.resolved
                            ? "历史失败 · 已解决"
                            : retryable
                              ? "历史失败 · 当前可重试"
                              : "历史失败 · 需要人工处理";
                          return <article className={group.resolved ? "history-anomaly-group resolved" : "history-anomaly-group"} key={group.key}>
                            <div className="history-anomaly-heading">
                              <strong>{group.label} · {group.items.length} 个目标</strong>
                              <span className={group.resolved ? "tag success" : "tag warning"}>{group.resolved ? "已解决" : "异常"}</span>
                            </div>
                            <p>{failureGroupDescription(group.label, group.items.length)}</p>
                            <div className="history-failure-detail-grid">
                              <span>时间：{new Date(first.timestamp).toLocaleString("zh-CN")}</span>
                              <span>失败阶段：{stageLabel[first.stage] || first.stage}</span>
                              <span>当前状态：{statusText}</span>
                              <span>受影响目标：{group.items.length} 个</span>
                            </div>
                            <small>{group.items.map(({ targetId }) => targetName(targetId)).join(" · ")}</small>
                          </article>;
                        })}
                      </div>
                    </td></tr> : null}
                  </Fragment>
                );})}
              </tbody>
            </table>
          </div>
        </section>
        <aside className="panel quick-panel">
          <div className="panel-heading"><h2>快捷操作</h2></div>
          <button onClick={() => { window.location.href = "/api/backup"; }}><Download className="blue-text" /><span><strong>导出配置</strong><small>生成可迁移的安全备份</small></span></button>
          <button onClick={() => onNavigate("backup")}><ArchiveRestore className="green-text" /><span><strong>导入备份</strong><small>从备份恢复好友和文案</small></span></button>
          <button onClick={() => onNavigate("scheduler")}><CalendarPlus2 className="purple-text" /><span><strong>安装定时任务</strong><small>管理 07:20 与 07:30 任务</small></span></button>
        </aside>
      </div>
      <section className="panel issues-panel">
        <div className="panel-heading"><h2>需要处理</h2><span>{status.issues.length ? `${status.issues.length} 项` : "当前无异常"}</span></div>
        {status.issues.length ? <div className="issue-list">{status.issues.map((issue) => <article className={`issue-item ${issue.status}`} key={issue.id}><AlertTriangle size={19} /><div><strong>{issue.explanation}</strong><small>{issue.id}</small></div><button className="action-button" disabled={!!busy} onClick={() => handleIssue(issue.action)}>{issue.action_label}</button></article>)}</div> : <div className="empty-state compact">运行状态正常，暂无需要处理的事项。</div>}
      </section>
      <section className="panel friends-panel">
        <div className="panel-heading"><h2>好友状态</h2><span>共 {status.friends.length} 位</span></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>好友名称</th><th>当前绑定</th><th>今日发送</th><th>当前说明</th></tr></thead>
            <tbody>
              {status.friends.map((friend) => {
                const health = friend.current_health || {
                  status: "unknown" as const,
                  reason_code: "scan_unavailable",
                  summary_zh: "当前扫描不可用"
                };
                return (
                  <tr key={friend.name}>
                    <td><span className="avatar">{friend.name.slice(0, 1)}</span>{friend.name}</td>
                    <td><span className={`tag ${healthTag[health.status]}`}>{healthLabel[health.status]}</span></td>
                    <td>{deliveryStatusLabel[friend.status]}</td>
                    <td className="muted">{health.summary_zh}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

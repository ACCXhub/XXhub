import { Fragment, useState } from "react";
import { AlertTriangle, ArchiveRestore, CalendarPlus2, Download, Play, ScanLine, ShieldCheck, Wrench } from "lucide-react";
import { ActionButton } from "../components/ActionButton";
import { StatusRail } from "../components/StatusRail";
import type { DashboardStatus, FailureDetail } from "../types";
import type { ViewName } from "../components/Sidebar";

const statusLabel = {
  success: "已发送",
  failed: "异常",
  pending: "待发送"
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
  rowIndex: number;
  rowEndTime: string;
  targetId: string;
  failure: FailureDetail;
}

interface FailureGroup {
  key: string;
  items: FailureOccurrence[];
}

function groupedHistoryFailures(status: DashboardStatus): {
  historyRows: DashboardStatus["history"];
  groupByOccurrence: Map<string, FailureGroup>;
} {
  const historyRows = status.history.slice(0, 7);
  const groups = new Map<string, FailureOccurrence[]>();
  historyRows.forEach((row, rowIndex) => {
    Object.entries(row.target_failures || {}).forEach(([targetId, failure]) => {
      const key = JSON.stringify([
        row.date,
        failure.category,
        failure.reason_code,
        targetId,
        failure.suggested_action
      ]);
      const items = groups.get(key) || [];
      items.push({ rowIndex, rowEndTime: row.end_time, targetId, failure });
      groups.set(key, items);
    });
  });
  const groupByOccurrence = new Map<string, FailureGroup>();
  groups.forEach((items, key) => {
    items.sort((left, right) => (
      right.failure.timestamp.localeCompare(left.failure.timestamp)
      || right.rowEndTime.localeCompare(left.rowEndTime)
    ));
    const newest = items[0];
    groupByOccurrence.set(
      `${newest.rowIndex}\0${newest.targetId}`,
      { key, items }
    );
  });
  return { historyRows, groupByOccurrence };
}

export function DashboardPage({
  status,
  busy,
  onAction,
  onNavigate,
  onRetryTarget
}: {
  status: DashboardStatus;
  busy: string | null;
  onAction: (action: string) => void;
  onNavigate: (view: ViewName) => void;
  onRetryTarget?: (targetId: string) => void;
}) {
  const [expandedFailureGroups, setExpandedFailureGroups] = useState<Set<string>>(
    () => new Set()
  );
  const { historyRows, groupByOccurrence } = groupedHistoryFailures(status);
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
  const failureAction = (targetId: string, action: string) => {
    if (action === "retry") onRetryTarget?.(targetId);
    else if (action === "reassociate") onNavigate("friends");
    else if (action === "switch_account") onAction("login");
    else onNavigate("logs");
  };
  const toggleFailureGroup = (key: string) => {
    setExpandedFailureGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
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
          <div className="panel-heading"><h2>结构化运行记录</h2><span>{status.history.length} 条记录</span></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>结束时间</th><th>来源</th><th>成功/总数</th><th>重试</th><th>状态</th></tr></thead>
              <tbody>
                {historyRows.map((row, rowIndex) => (
                  <Fragment key={`${row.run_id}-${row.end_time}-${rowIndex}`}>
                    <tr>
                      <td>{new Date(row.end_time).toLocaleString("zh-CN")}</td>
                      <td>{triggerLabel[row.trigger_source]}</td>
                      <td>{row.success_count + row.skipped_count}/{row.total_targets}</td>
                      <td>{row.retry_count}</td>
                      <td><span className={row.final_status === "completed" || row.final_status === "already_done" ? "tag success" : "tag warning"}>{row.final_status === "completed" || row.final_status === "already_done" ? "成功" : "部分失败"}</span></td>
                    </tr>
                    {Object.keys(row.target_failures || {}).map((targetId) => {
                      const group = groupByOccurrence.get(`${rowIndex}\0${targetId}`);
                      if (!group) return null;
                      const expandable = group.items.length > 1;
                      const expanded = expandable && expandedFailureGroups.has(group.key);
                      const visibleItems = expanded ? group.items : group.items.slice(0, 1);
                      const newest = group.items[0].failure;
                      const resolved = newest.resolved === true;
                      return (
                        <tr className={resolved ? "history-failure-row resolved" : "history-failure-row"} key={`${row.run_id}-${targetId}`}>
                          <td colSpan={5}>
                            <div
                              className={[
                                "history-failure-group",
                                expandable ? "expandable" : "",
                                resolved ? "resolved" : ""
                              ].filter(Boolean).join(" ")}
                              role={expandable ? "button" : undefined}
                              tabIndex={expandable ? 0 : undefined}
                              aria-expanded={expandable ? expanded : undefined}
                              aria-label={expandable ? `${targetName(targetId)} ${newest.user_summary_zh}` : undefined}
                              onClick={expandable ? () => toggleFailureGroup(group.key) : undefined}
                              onKeyDown={expandable ? (event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                  event.preventDefault();
                                  toggleFailureGroup(group.key);
                                }
                              } : undefined}
                            >
                              {visibleItems.map(({ targetId: occurrenceTargetId, failure }, failureIndex) => {
                                const occurrenceResolved = failure.resolved === true;
                                const retryActionAvailable = (
                                  failure.retry_action_available
                                  ?? failure.safe_retry_available
                                );
                                return (
                                <div className={occurrenceResolved ? "history-failure resolved" : "history-failure"} key={`${failure.run_id}-${failure.timestamp}-${failureIndex}`}>
                                  {failureIndex === 0 && expandable ? (
                                    <span className="history-failure-badge" aria-label={`共 ${group.items.length} 条重复通知`}>
                                      {group.items.length}
                                    </span>
                                  ) : null}
                                  <div>
                                    <strong>
                                      <span>{targetName(occurrenceTargetId)} · </span>
                                      <b>{failure.user_summary_zh}</b>
                                    </strong>
                                    <small>
                                      {new Date(failure.timestamp).toLocaleString("zh-CN")}
                                      {" · "}
                                      失败阶段：{stageLabel[failure.stage] || failure.stage}
                                      {" · "}
                                      {occurrenceResolved
                                        ? failure.resolution_zh || "已解决"
                                        : retryActionAvailable
                                          ? "可安全重试"
                                          : failure.uncertain_send
                                            ? "禁止自动重试"
                                            : "需要处理后再运行"}
                                    </small>
                                    <span>{failure.user_detail_zh}</span>
                                  </div>
                                  {occurrenceResolved ? (
                                    <span className="history-failure-resolved">已解决</span>
                                  ) : (
                                    <button
                                      className="action-button"
                                      disabled={!!busy}
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        failureAction(occurrenceTargetId, failure.suggested_action);
                                      }}
                                    >
                                      {retryActionAvailable ? "仅重试此目标" : failure.suggested_action_zh}
                                    </button>
                                  )}
                                </div>
                                );
                              })}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </Fragment>
                ))}
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
            <thead><tr><th>好友名称</th><th>状态</th><th>今日发送</th><th>异常信息</th></tr></thead>
            <tbody>
              {status.friends.map((friend) => (
                <tr key={friend.name}>
                  <td><span className="avatar">{friend.name.slice(0, 1)}</span>{friend.name}</td>
                  <td><span className={`tag ${friend.status}`}>{statusLabel[friend.status]}</span></td>
                  <td>{friend.status === "success" ? "已发送" : "未完成"}</td>
                  <td className="muted">{friend.error || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

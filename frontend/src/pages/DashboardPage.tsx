import { AlertTriangle, Play, ScanLine, ShieldCheck, Wrench } from "lucide-react";
import { ActionButton } from "../components/ActionButton";
import { FriendAvatar } from "../components/FriendAvatar";
import { StatusRail } from "../components/StatusRail";
import type { DashboardIssue, DashboardStatus } from "../types";
import type { ViewName } from "../components/Sidebar";

const deliveryStatusLabel = {
  success: "已发送",
  failed: "今日失败",
  pending: "待发送",
  unknown: "待核实"
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

const deliveryTag = {
  success: "success",
  failed: "failed",
  pending: "pending",
  unknown: "pending"
};

export function DashboardPage({
  status,
  busy,
  onAction,
  onNavigate
}: {
  status: DashboardStatus;
  busy: string | null;
  onAction: (action: string) => void;
  onNavigate: (view: ViewName) => void;
}) {
  const handleIssue = (issue: DashboardIssue) => {
    const action = issue.action;
    if (action === "retry_target") {
      onAction("safe-supplement");
      return;
    }
    if (["friends", "messages", "packs", "scheduler", "logs", "backup", "settings"].includes(action)) {
      onNavigate(action as ViewName);
    } else {
      onAction(action);
    }
  };
  return (
    <div className="dashboard-page">
      <header className="page-header">
        <div><h1>运行总览</h1><p>查看今日续火状态和本机自动化健康情况。</p></div>
        <div className="header-actions">
          <ActionButton disabled={!!busy} primary icon={<Play size={17} fill="currentColor" />} onClick={() => onAction("run")}>立即运行</ActionButton>
          <ActionButton disabled={!!busy} icon={<ShieldCheck size={17} />} onClick={() => onAction("health-check")}>检查登录</ActionButton>
          <ActionButton disabled={!!busy} icon={<ScanLine size={17} />} onClick={() => onAction("login")}>扫码登录</ActionButton>
          <ActionButton disabled={!!busy} icon={<Wrench size={17} />} onClick={() => onAction("diagnose-and-repair")}>一键诊断与修复</ActionButton>
        </div>
      </header>
      <StatusRail status={status} />
      <section className={`panel issues-panel${status.issues.length ? "" : " issues-panel-clear"}`}>
        <div className="panel-heading"><h2>需要处理</h2><span>{status.issues.length ? `${status.issues.length} 项` : "当前无异常"}</span></div>
        {status.issues.length ? <div className="issue-list">{status.issues.map((issue) => <article className={`issue-item ${issue.status}`} key={issue.id}><AlertTriangle size={18} /><div><strong>{issue.explanation}</strong></div><button className="action-button" disabled={!!busy} onClick={() => handleIssue(issue)}>{issue.action_label}</button></article>)}</div> : null}
      </section>
      <section className="panel friends-panel">
        <div className="panel-heading"><h2>好友状态</h2><span>共 {status.friends.length} 位</span></div>
        <div className="table-wrap friends-table-wrap">
          <table>
            <thead><tr><th>好友名称</th><th>当前绑定</th><th>今日发送</th><th>当前说明</th></tr></thead>
            <tbody>
              {status.friends.map((friend) => {
                const health = friend.current_health || {
                  status: "unknown" as const,
                  reason_code: "scan_unavailable",
                  summary_zh: "当前扫描不可用"
                };
                const currentSummary = friend.status === "failed" && friend.failure && !friend.failure.resolved
                  ? friend.failure.user_summary_zh
                  : friend.status === "unknown" && friend.error
                    ? friend.error
                  : health.status === "healthy"
                    ? "—"
                    : health.summary_zh;
                return (
                  <tr key={friend.target_id || friend.name}>
                    <td><span className="friend-name-cell"><FriendAvatar name={friend.name} url={friend.avatar_url} />{friend.name}</span></td>
                    <td><span className={`tag ${healthTag[health.status]}`}>{healthLabel[health.status]}</span></td>
                    <td><span className={`tag ${deliveryTag[friend.status]}`}>{deliveryStatusLabel[friend.status]}</span></td>
                    <td className={friend.status === "failed" ? "failure-summary" : "muted"}>{currentSummary}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <section className="stats-grid dashboard-stats" aria-label="运行统计">
        <article className="panel stat-card"><small>今日成功</small><strong>{status.statistics.successful_today}</strong><span>失败 {status.statistics.failed_today}</span></article>
        <article className="panel stat-card"><small>连续成功</small><strong>{status.statistics.consecutive_successful_days} 天</strong><span>近 7 天 {status.statistics.success_rate_7d}%</span></article>
        <article className="panel stat-card"><small>近 30 天成功率</small><strong>{status.statistics.success_rate_30d}%</strong><span>按每日最终状态统计</span></article>
        <article className="panel stat-card"><small>本机资源</small><strong>{status.statistics.enabled_friend_count} 位好友</strong><span>{status.statistics.local_message_count} 条文案 · {status.statistics.active_message_pack_count} 个文案包</span></article>
      </section>
    </div>
  );
}

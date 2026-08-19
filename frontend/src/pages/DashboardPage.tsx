import { AlertTriangle, ArchiveRestore, CalendarPlus2, Download, Play, ScanLine, ShieldCheck, Wrench } from "lucide-react";
import { ActionButton } from "../components/ActionButton";
import { StatusRail } from "../components/StatusRail";
import type { DashboardStatus } from "../types";
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
  return (
    <>
      <header className="page-header">
        <div><h1>运行总览</h1><p>查看今日续火状态和本机自动化健康情况。</p></div>
        <div className="header-actions">
          <ActionButton disabled={!!busy} icon={<ShieldCheck size={17} />} onClick={() => onAction("health-check")}>检查登录</ActionButton>
          <ActionButton disabled={!!busy} primary icon={<Play size={17} fill="currentColor" />} onClick={() => onAction("run")}>立即运行</ActionButton>
          <ActionButton disabled={!!busy} icon={<ScanLine size={17} />} onClick={() => onAction("login")}>扫码登录</ActionButton>
          <ActionButton disabled={!!busy} icon={<Wrench size={17} />} onClick={() => onAction("diagnose-and-repair")}>一键诊断与修复</ActionButton>
        </div>
      </header>
      <StatusRail status={status} />
      <section className="stats-grid" aria-label="运行统计">
        <article className="panel stat-card"><small>今日成功</small><strong>{status.statistics.successful_today}</strong><span>失败 {status.statistics.failed_today}</span></article>
        <article className="panel stat-card"><small>连续成功</small><strong>{status.statistics.consecutive_successful_days} 天</strong><span>近 7 天 {status.statistics.success_rate_7d}%</span></article>
        <article className="panel stat-card"><small>近 30 天成功率</small><strong>{status.statistics.success_rate_30d}%</strong><span>按每日最终状态统计</span></article>
        <article className="panel stat-card"><small>本机资源</small><strong>{status.statistics.enabled_friend_count} 位好友</strong><span>{status.statistics.local_message_count} 条文案 · {status.statistics.active_message_pack_count} 个文案包</span></article>
      </section>
      {safeRetryTargetIds.length > 0 && onRetryTargets ? <section className="panel retry-panel" aria-label="今日安全补发">
        <div><h2>今日补发</h2><p>仅补发今日尚未成功且可安全重试的目标</p></div>
        <button className="action-button primary" disabled={!!busy} onClick={() => onRetryTargets(safeRetryTargetIds)}>补发</button>
      </section> : null}
      <div className="dashboard-grid">
        <aside className="panel quick-panel dashboard-quick-panel">
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

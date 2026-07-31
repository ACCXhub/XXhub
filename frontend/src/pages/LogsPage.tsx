import { Copy, Download, FolderOpen, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DashboardStatus, LogPage } from "../types";

type StorageSummary = Awaited<ReturnType<typeof api.logStorageSummary>>;
type CleanupPreview = Awaited<ReturnType<typeof api.logCleanupPreview>>;

function localDate(value: Date) { return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`; }
function megabytes(value: number) { return `${(value / 1024 / 1024).toFixed(value ? 2 : 0)} MB`; }
const taskLabels: Record<string, string> = { daily_send: "每日发送", health_check: "登录检查", login: "扫码登录", friend_scan: "好友识别", system: "系统" };
const statusLabels: Record<string, string> = { active: "当前", resolved: "已解决", historical: "历史" };

export function LogsPage({ summary }: { summary?: DashboardStatus["statistics"]["log_summary"] }) {
  const today = new Date(); const recent = new Date(today); recent.setDate(today.getDate() - 2);
  const [startDate, setStartDate] = useState(localDate(recent)); const [endDate, setEndDate] = useState(localDate(today));
  const [level, setLevel] = useState(""); const [taskType, setTaskType] = useState(""); const [status, setStatus] = useState("");
  const [logs, setLogs] = useState<LogPage | null>(null); const [tab, setTab] = useState<"scheduler" | "application">("application");
  const [storage, setStorage] = useState<StorageSummary | null>(null); const [preview, setPreview] = useState<CleanupPreview | null>(null);
  const [following, setFollowing] = useState({ application: true, scheduler: true });
  const [hasNew, setHasNew] = useState({ application: false, scheduler: false });
  const followingRef = useRef(following);
  const containers = useRef<Record<"application" | "scheduler", HTMLDivElement | null>>({ application: null, scheduler: null });
  const signatures = useRef({ application: "", scheduler: "" });
  const loadReason = useRef<"filter" | "refresh">("filter");
  const load = useCallback(async (reason: "filter" | "refresh" = "refresh") => {
    loadReason.current = reason;
    setLogs(await api.logs({ start_date: startDate, end_date: endDate, level, task_type: taskType, status }));
  }, [startDate, endDate, level, taskType, status]);
  const loadStorage = async () => setStorage(await api.logStorageSummary());
  useEffect(() => {
    void load("filter");
    const id = window.setInterval(() => { if (!document.hidden) void load("refresh"); }, 5000);
    return () => window.clearInterval(id);
  }, [load]);
  useEffect(() => { void loadStorage(); }, []);
  useEffect(() => { followingRef.current = following; }, [following]);
  const scrollToLatest = useCallback((name: "application" | "scheduler") => {
    const container = containers.current[name];
    if (container) container.scrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
    followingRef.current = { ...followingRef.current, [name]: true };
    setFollowing((current) => ({ ...current, [name]: true }));
    setHasNew((current) => ({ ...current, [name]: false }));
  }, []);
  useLayoutEffect(() => {
    if (!logs) return;
    const next = {
      application: logs.items.map((item) => `${item.timestamp}:${item.fingerprint}:${item.occurrences}`).join("|"),
      scheduler: logs.scheduler
    };
    (["application", "scheduler"] as const).forEach((name) => {
      const changed = signatures.current[name] !== next[name];
      if (!signatures.current[name] || (changed && followingRef.current[name])) {
        scrollToLatest(name);
      } else if (changed && loadReason.current === "refresh") {
        setHasNew((current) => ({ ...current, [name]: true }));
      }
      signatures.current[name] = next[name];
    });
  }, [logs, scrollToLatest]);
  useLayoutEffect(() => {
    if (logs && followingRef.current[tab]) scrollToLatest(tab);
  }, [tab, logs, scrollToLatest]);
  const handleScroll = (name: "application" | "scheduler") => {
    const container = containers.current[name];
    if (!container) return;
    const nearBottom = container.scrollHeight - container.clientHeight - container.scrollTop <= 48;
    followingRef.current = { ...followingRef.current, [name]: nearBottom };
    setFollowing((current) => ({ ...current, [name]: nearBottom }));
    if (nearBottom) setHasNew((current) => ({ ...current, [name]: false }));
  };
  const resetFilters = () => { setStartDate(localDate(recent)); setEndDate(localDate(today)); setLevel(""); setTaskType(""); setStatus(""); };
  const copyDiagnostic = async (entry: LogPage["items"][number]) => { const text = [`时间：${entry.timestamp}`, `级别：${entry.level}`, `任务：${taskLabels[entry.task_type] || "系统"}`, `摘要：${entry.summary}`, entry.detail ? `详情：${entry.detail}` : ""].filter(Boolean).join("\n"); await navigator.clipboard?.writeText(text); window.alert("已复制已脱敏的诊断信息。"); };
  const openCleanupPreview = async () => setPreview(await api.logCleanupPreview());
  const confirmCleanup = async () => { const result = await api.cleanupLogs(); setPreview(null); window.alert(`已归档 ${result.archived} 个日志文件\n已删除 ${result.deleted} 个过期归档\n释放空间 ${megabytes(result.bytes)}\n跳过 ${result.skipped} 个文件`); await Promise.all([load("refresh"), loadStorage()]); };
  const activeFilters = [level, taskType && (taskLabels[taskType] || taskType), status && (statusLabels[status] || status)].filter(Boolean);
  return <section className="editor-page"><header className="page-header"><div><h1>运行日志</h1><p>默认显示最近三天；追踪详情折叠显示，好友名称按设置脱敏。</p></div><div className="header-actions"><a className="action-button" href="/api/logs/diagnostic-export"><Download size={17} />导出脱敏诊断包</a><button className="action-button" onClick={() => void api.openLogFolder()}><FolderOpen size={17} />打开日志目录</button><button className="action-button primary" onClick={() => void openCleanupPreview()}>整理预览</button><button className="action-button" onClick={() => void Promise.all([load("refresh"), loadStorage()])}><RefreshCw size={17} />刷新</button></div></header>
    {storage ? <div className="panel log-storage"><strong>日志存储</strong><span>活跃 {storage.active_files} 个 · {megabytes(storage.active_bytes)}</span><span>归档 {storage.archived_files} 个 · {megabytes(storage.archived_bytes)}</span><span>合计 {megabytes(storage.total_bytes)}</span><span>最早保留：{storage.oldest_date || "—"}</span><span>上次整理：{storage.last_cleanup_at || "尚未执行"}</span><span>下次自动整理：{storage.cleanup_enabled ? storage.next_cleanup_date : "已关闭"}</span></div> : null}
    {preview ? <div className="cleanup-dialog" role="dialog" aria-label="日志整理确认"><div className="panel"><h2>确认整理日志</h2><p>将归档 {preview.to_archive} 个活跃日志，删除 {preview.to_delete} 个过期归档；预计释放 {megabytes(preview.bytes)}，跳过 {preview.skipped} 个文件。</p><p>预览未修改任何文件。确认后仅处理 data/logs 及其 archive 目录中可识别的 AutoDy 日志。</p><div className="header-actions"><button className="action-button" onClick={() => setPreview(null)}>取消</button><button className="action-button primary" onClick={() => void confirmCleanup()}>确认整理</button></div></div></div> : null}
    {summary ? <div className="stats-grid log-stats"><article className="panel stat-card"><small>活跃错误</small><strong>{summary.active_errors}</strong></article><article className="panel stat-card"><small>24 小时警告</small><strong>{summary.warnings_24h}</strong></article><article className="panel stat-card"><small>7 天成功任务</small><strong>{summary.successful_tasks_7d}</strong></article><article className="panel stat-card"><small>最后错误</small><strong>{summary.last_error_time || "—"}</strong></article></div> : null}
    <div className="panel log-panel"><div className="tab-list"><button className={tab === "application" ? "active" : ""} onClick={() => setTab("application")}>应用日志</button><button className={tab === "scheduler" ? "active" : ""} onClick={() => setTab("scheduler")}>调度日志</button></div>
      {tab === "application" ? <><div className="log-filters"><label>开始日期<input aria-label="日志开始日期" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>结束日期<input aria-label="日志结束日期" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><label>级别<select aria-label="日志级别" value={level} onChange={(event) => setLevel(event.target.value)}><option value="">全部</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></label><label>任务<select aria-label="日志任务" value={taskType} onChange={(event) => setTaskType(event.target.value)}><option value="">全部</option>{Object.entries(taskLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label>状态<select aria-label="日志状态" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option>{Object.entries(statusLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></div><div className="filter-summary">筛选条件：{activeFilters.length ? activeFilters.join(" · ") : "全部"}<button className="text-button" onClick={resetFilters}>重置筛选</button></div></> : null}
      <div className="log-scroll-wrap" hidden={tab !== "application"}>
        <div className="structured-logs log-scroll-container" role="log" aria-label="应用日志内容" ref={(node) => { containers.current.application = node; }} onScroll={() => handleScroll("application")}>{logs?.items.length ? [...logs.items].reverse().map((entry) => <article className={`log-entry ${entry.level.toLowerCase()}`} key={`${entry.source}:${entry.fingerprint || entry.timestamp}`}><div className="log-summary"><time>{entry.timestamp}</time><span className={`tag ${entry.level === "ERROR" ? "failed" : entry.level === "WARNING" ? "warning" : "success"}`}>{entry.level}</span><span>{taskLabels[entry.task_type] || "系统"} · {statusLabels[entry.status] || "已解决"}</span><strong>{entry.summary}{entry.occurrences > 1 ? `（相同错误重复 ${entry.occurrences} 次）` : ""}</strong></div>{entry.detail ? <details><summary>查看详情</summary><pre>{entry.detail}</pre><button className="text-button diagnostic-copy" onClick={() => void copyDiagnostic(entry)}><Copy size={14} />复制诊断信息</button></details> : null}</article>) : <div className="empty-state compact">所选范围暂无日志</div>}</div>
        {!following.application ? <button className="log-follow-button" aria-label="回到最新" onClick={() => scrollToLatest("application")}>{hasNew.application ? <span>有新日志</span> : null}<span>回到最新</span></button> : null}
      </div>
      <div className="log-scroll-wrap" hidden={tab !== "scheduler"}>
        <div className="scheduler-log log-scroll-container" role="log" aria-label="调度日志内容" ref={(node) => { containers.current.scheduler = node; }} onScroll={() => handleScroll("scheduler")}><pre>{logs?.scheduler || "暂无调度日志"}</pre></div>
        {!following.scheduler ? <button className="log-follow-button" aria-label="回到最新" onClick={() => scrollToLatest("scheduler")}>{hasNew.scheduler ? <span>有新日志</span> : null}<span>回到最新</span></button> : null}
      </div>
    </div></section>;
}

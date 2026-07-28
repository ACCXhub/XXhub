import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Sidebar, type ViewName } from "./components/Sidebar";
import { BackupPage } from "./pages/BackupPage";
import { DashboardPage } from "./pages/DashboardPage";
import { FriendsPage } from "./pages/FriendsPage";
import { LogsPage } from "./pages/LogsPage";
import { MessagesPage } from "./pages/MessagesPage";
import { MessagePacksPage } from "./pages/MessagePacksPage";
import { SchedulerPage } from "./pages/SchedulerPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ModuleHostPage } from "./pages/ModuleHostPage";
import type { AccountProfile, DashboardStatus } from "./types";

function accountRefreshErrorMessage(error: unknown): string {
  const detail = error instanceof Error ? error.message : "";
  if (/not found|接口不存在|\b404\b/i.test(detail)) return "当前账号资料接口不可用，请重启 AutoDy 管理台。";
  if (/\b401\b|\b403\b|登录/i.test(detail)) return "已登录，但暂时无法读取当前账号资料。";
  if (/\b409\b|正在运行|任务忙/i.test(detail)) return "浏览器正在执行其他任务，请稍后再试。";
  if (/\b500\b|提取|读取/i.test(detail)) return "当前账号资料读取失败，请稍后重试。";
  return "当前账号资料刷新失败，请检查 AutoDy 管理台连接。";
}

export default function App() {
  const [view, setView] = useState<ViewName | "test-center">("dashboard");
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [account, setAccount] = useState<AccountProfile | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [testCenterInstalled, setTestCenterInstalled] = useState(false);
  const load = useCallback(() => {
    void api.status().then(setStatus);
    void api.accountProfile().then(setAccount).catch(() => setAccount(null));
    void api.modules().then((result) => setTestCenterInstalled(Boolean(result.modules.find((item) => item.id === "autody-test-center")?.installed))).catch(() => setTestCenterInstalled(false));
  }, []);
  useEffect(() => {
    load();
    const refresh = () => { if (!document.hidden) load(); };
    const id = window.setInterval(refresh, 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", refresh); };
  }, [load]);
  useEffect(() => {
    void api.checkRecovery().then(async (result) => {
      if (result.started && result.job) {
        await api.waitForAction(result.job.id);
        load();
      }
    }).catch(() => undefined);
  }, [load]);
  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(""), 3200); };
  const action = async (name: string) => {
    setBusy(name);
    try {
      const job = await api.action(name);
      notify("操作已启动，可在运行日志中查看进度");
      const finished = await api.waitForAction(job.id);
      if (finished.status === "failed") {
        throw new Error(`操作失败（退出码 ${finished.exit_code ?? "未知"}）`);
      }
      notify("操作已完成");
      load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };
  const refreshAccount = async () => {
    try {
      const result = await api.refreshAccountProfile();
      setAccount(result);
      if (result.job) await api.waitForAction(result.job.id);
      setAccount(await api.accountProfile());
      notify("当前账号资料已刷新");
    } catch (error) {
      notify(accountRefreshErrorMessage(error));
    }
  };

  if (!status) return <div className="app-loading">正在连接 AutoDy 本地服务…</div>;
  return (
    <div className="app-shell">
      <Sidebar active={view === "test-center" ? "settings" : view} onChange={setView} account={account} onRefreshAccount={() => void refreshAccount()} testCenterInstalled={testCenterInstalled} onOpenTestCenter={() => setView("test-center")} />
      <main className="workspace">
        {view === "dashboard" && <DashboardPage status={status} busy={busy} onAction={action} onNavigate={setView as (view: ViewName) => void} />}
        {view === "friends" && <FriendsPage notify={notify} />}
        {view === "messages" && <MessagesPage notify={notify} onNavigate={setView} />}
        {view === "packs" && <MessagePacksPage notify={notify} />}
        {view === "scheduler" && <SchedulerPage status={status} notify={notify} onRefresh={load} />}
        {view === "logs" && <LogsPage summary={status.statistics.log_summary} />}
        {view === "backup" && <BackupPage notify={notify} />}
        {view === "settings" && <SettingsPage notify={notify} onOpenTestCenter={() => setView("test-center")} onTestCenterStateChange={setTestCenterInstalled} />}
        {view === "test-center" && testCenterInstalled && <ModuleHostPage onRemoved={() => { setTestCenterInstalled(false); setView("settings"); }} />}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

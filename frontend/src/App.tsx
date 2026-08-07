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
import type { AccountProfile, DashboardStatus, LocalAccountProfiles } from "./types";

const EMPTY_ACCOUNTS: LocalAccountProfiles = {
  active_profile_id: null,
  profiles: []
};

type LoadFailure = {
  section: string;
  reason: "请求超时" | "服务连接失败" | "服务返回错误";
};

function statusWithTimeout(): Promise<DashboardStatus> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(
      () => reject(new Error("status_request_timeout")),
      10_000
    );
    void api.status().then(resolve, reject).finally(() => {
      window.clearTimeout(timeout);
    });
  });
}

function statusLoadFailure(error: unknown): LoadFailure {
  const message = error instanceof Error ? error.message : "";
  return {
    section: "状态总览 API（/api/status）",
    reason: message === "status_request_timeout"
      ? "请求超时"
      : /\b5\d\d\b|internal server error/i.test(message)
        ? "服务返回错误"
        : "服务连接失败"
  };
}

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
  const [accounts, setAccounts] = useState<LocalAccountProfiles>(EMPTY_ACCOUNTS);
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [testCenterInstalled, setTestCenterInstalled] = useState(false);
  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      const [nextStatus, nextAccount, nextAccounts, modules] = await Promise.all([
        statusWithTimeout(),
        api.accountProfile().catch(() => null),
        api.accountProfiles().catch(() => EMPTY_ACCOUNTS),
        api.modules().catch(() => ({ modules: [] }))
      ]);
      setStatus(nextStatus);
      setAccount(nextAccount);
      setAccounts(nextAccounts);
      setTestCenterInstalled(Boolean(modules.modules.find((item) => item.id === "autody-test-center")?.installed));
      setLoadFailure(null);
      return nextStatus;
    } catch (error) {
      setLoadFailure(statusLoadFailure(error));
      throw error;
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refreshAll().catch(() => undefined);
    const refresh = () => {
      if (!document.hidden) void refreshAll().catch(() => undefined);
    };
    const id = window.setInterval(refresh, 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", refresh); };
  }, [refreshAll]);
  useEffect(() => {
    void api.checkRecovery().then(async (result) => {
      if (result.started && result.job) {
        await api.waitForAction(result.job.id);
        await refreshAll();
      }
    }).catch(() => undefined);
  }, [refreshAll]);
  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(""), 3200); };
  const action = async (name: string) => {
    setBusy(name);
    try {
      const job = await api.action(name);
      notify("操作已启动，可在运行日志中查看进度");
      const finished = await api.waitForAction(job.id);
      const nextStatus = await refreshAll();
      if (finished.status === "failed") {
        const failure = nextStatus.friends.find((friend) => friend.failure)?.failure;
        throw new Error(
          failure?.user_summary_zh
          || finished.failure?.user_summary_zh
          || `操作失败（诊断退出码 ${finished.exit_code ?? "未知"}）`
        );
      }
      notify("操作已完成");
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
      await refreshAll();
      notify("当前账号资料已刷新");
    } catch (error) {
      notify(accountRefreshErrorMessage(error));
    }
  };
  const switchAccount = async (profileId: string) => {
    setBusy("switch-account");
    try {
      await api.switchAccountProfile(profileId);
      await refreshAll();
      notify("本地账号已切换");
    } catch (error) {
      notify(error instanceof Error ? error.message : "账号切换失败");
    } finally {
      setBusy(null);
    }
  };
  const addAccount = async () => {
    setBusy("add-account");
    try {
      const result = await api.addAccountProfile();
      await api.waitForAction(result.job.id);
      await refreshAll();
      notify("新账号登录流程已完成");
    } catch (error) {
      notify(error instanceof Error ? error.message : "添加账号失败");
    } finally {
      setBusy(null);
    }
  };
  const logoutAccount = async () => {
    setBusy("logout-account");
    try {
      await api.logoutAccount();
      await refreshAll();
      notify("当前 AutoDy 账号已退出，本地设置已保留");
    } catch (error) {
      notify(error instanceof Error ? error.message : "退出当前账号失败");
    } finally {
      setBusy(null);
    }
  };
  const retryTargets = async (targetIds: string[]) => {
    if (!targetIds.length) return;
    setBusy("retry-all-targets");
    try {
      for (const targetId of targetIds) {
        const job = await api.retryFailedTarget(targetId);
        const finished = await api.waitForAction(job.id);
        const nextStatus = await refreshAll();
        if (finished.status === "failed") {
          const failure = nextStatus.friends.find(
            (friend) => friend.target_id === targetId
          )?.failure;
          throw new Error(failure?.user_summary_zh || "目标重试未完成，请查看失败详情");
        }
      }
      notify(`已完成 ${targetIds.length} 个安全目标的重试`);
    } catch (error) {
      notify(error instanceof Error ? error.message : "批量重试失败");
    } finally {
      setBusy(null);
    }
  };

  if (!status && loadFailure) {
    return (
      <div className="app-loading">
        <section className="app-load-error" role="alert">
          <h1>无法加载 AutoDy 状态</h1>
          <p>状态总览 API 暂时不可用。</p>
          <button
            className="action-button primary"
            disabled={loading}
            onClick={() => void refreshAll().catch(() => undefined)}
          >
            {loading ? "正在重试…" : "重试加载"}
          </button>
          <details>
            <summary>诊断信息</summary>
            <p>失败区段：{loadFailure.section}</p>
            <p>原因：{loadFailure.reason}</p>
          </details>
        </section>
      </div>
    );
  }
  if (!status) return <div className="app-loading">正在连接 AutoDy 本地服务…</div>;
  return (
    <div className="app-shell">
      <Sidebar
        active={view === "test-center" ? "settings" : view}
        onChange={setView}
        account={account}
        accounts={accounts}
        onRefreshAccount={() => void refreshAccount()}
        onSwitchAccount={(profileId) => void switchAccount(profileId)}
        onAddAccount={() => void addAccount()}
        onLogoutAccount={() => void logoutAccount()}
        testCenterInstalled={testCenterInstalled}
        testCenterActive={view === "test-center"}
        onOpenTestCenter={() => setView("test-center")}
      />
      <main className="workspace">
        {view === "dashboard" && <DashboardPage status={status} busy={busy} onAction={action} onNavigate={setView as (view: ViewName) => void} onRetryTargets={(targetIds) => void retryTargets(targetIds)} />}
        {view === "friends" && <FriendsPage notify={notify} onDataChanged={() => void refreshAll()} />}
        {view === "messages" && <MessagesPage notify={notify} onNavigate={setView} />}
        {view === "packs" && <MessagePacksPage notify={notify} />}
        {view === "scheduler" && <SchedulerPage status={status} notify={notify} onRefresh={() => void refreshAll()} />}
        {view === "logs" && <LogsPage summary={status.statistics.log_summary} />}
        {view === "backup" && <BackupPage notify={notify} onDataChanged={() => void refreshAll()} />}
        {view === "settings" && <SettingsPage notify={notify} onOpenTestCenter={() => setView("test-center")} onTestCenterStateChange={setTestCenterInstalled} />}
        {view === "test-center" && testCenterInstalled && <ModuleHostPage key={accounts.active_profile_id || "unscoped"} onRemoved={() => { setTestCenterInstalled(false); setView("settings"); }} />}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

import {
  ArchiveRestore,
  Check,
  ChevronDown,
  CloudDownload,
  Clock3,
  FileText,
  Flame,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Plus,
  RefreshCw,
  ScrollText,
  Settings,
  Users
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { AccountProfile, LocalAccountProfiles } from "../types";

export type ViewName =
  | "dashboard"
  | "friends"
  | "messages"
  | "packs"
  | "scheduler"
  | "logs"
  | "backup"
  | "settings";

const navigation = [
  ["dashboard", "总览", LayoutDashboard],
  ["friends", "好友管理", Users],
  ["messages", "文案库", FileText],
  ["packs", "文案包", CloudDownload],
  ["scheduler", "定时任务", Clock3],
  ["logs", "运行日志", ScrollText],
  ["backup", "备份迁移", ArchiveRestore],
  ["settings", "设置", Settings]
] as const;

export function Sidebar({
  active,
  onChange,
  account,
  accounts = { active_profile_id: null, profiles: [] },
  onRefreshAccount,
  onSwitchAccount = () => undefined,
  onAddAccount = () => undefined,
  onLogoutAccount = () => undefined,
  testCenterInstalled = false,
  testCenterActive = false,
  onOpenTestCenter = () => undefined
}: {
  active: ViewName;
  onChange: (view: ViewName) => void;
  account: AccountProfile | null;
  accounts?: LocalAccountProfiles;
  onRefreshAccount: () => void;
  onSwitchAccount?: (profileId: string) => void;
  onAddAccount?: () => void;
  onLogoutAccount?: () => void;
  testCenterInstalled?: boolean;
  testCenterActive?: boolean;
  onOpenTestCenter?: () => void;
}) {
  const [accountOpen, setAccountOpen] = useState(false);
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
  const accountTriggerRef = useRef<HTMLButtonElement>(null);
  const accountPopoverRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (
        accountTriggerRef.current?.contains(target)
        || accountPopoverRef.current?.contains(target)
      ) return;
      setAccountOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAccountOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);
  useEffect(() => {
    setAccountOpen(false);
  }, [active, testCenterActive]);
  const verified = account?.profile_status === "verified" && account.is_self;
  return (
    <aside className="sidebar">
      <button
        ref={accountTriggerRef}
        className="account-identity"
        aria-label="打开账号切换器"
        aria-expanded={accountOpen}
        onClick={() => setAccountOpen((current) => !current)}
      >
        {verified && account.avatar_url ? <img className="account-avatar" src={account.avatar_url} alt="当前账号头像" /> : <span className="brand-mark"><Flame size={24} fill="currentColor" /></span>}
        <div className="account-copy">
          <strong className="account-copy-name">{verified ? account.display_name : "未识别当前账号"}</strong>
          <small className="account-copy-meta">{verified ? (account.logged_in ? "当前抖音账号" : "上次登录账号") : "AutoDy 续火助手"}</small>
          {verified ? <em>AutoDy 续火助手</em> : null}
        </div>
        <ChevronDown className="account-chevron" size={15} />
      </button>
      <div className="account-tools">
        <button className="account-refresh" onClick={onRefreshAccount} disabled={Boolean(account?.refresh_running)}><RefreshCw size={13} />刷新当前账号资料</button>
      </div>
      {accountOpen ? <div ref={accountPopoverRef} className="account-popover" role="dialog" aria-label="账号切换器">
        <div className="account-popover-heading"><strong>当前账号</strong><small>{account?.logged_in ? "已登录" : "未登录"}</small></div>
        <div className="account-profile-list">
          {accounts.profiles.map((profile) => (
            <button
              key={profile.profile_id}
              aria-label={profile.active ? `当前账号 ${profile.display_name || "未命名账号"}` : `切换到 ${profile.display_name || "未命名账号"}`}
              className={profile.active ? "account-profile-option active" : "account-profile-option"}
              disabled={profile.active}
              onClick={() => { setAccountOpen(false); onSwitchAccount(profile.profile_id); }}
            >
              <span><strong className="account-profile-name">{profile.display_name || "未命名账号"}</strong><small className="account-profile-meta">{profile.logged_in ? "已登录" : "未登录"}</small></span>
              {profile.active ? <Check size={15} /> : null}
            </button>
          ))}
          {!accounts.profiles.length ? <small className="account-empty">暂无已保存账号</small> : null}
        </div>
        <div className="account-popover-actions">
          <button onClick={() => { setAccountOpen(false); onAddAccount(); }}><Plus size={14} />添加账号</button>
          <button onClick={() => { setAccountOpen(false); onRefreshAccount(); }}><RefreshCw size={14} />刷新账号资料</button>
          <button className="danger-link" onClick={() => setLogoutConfirmOpen(true)}><LogOut size={14} />退出当前账号</button>
        </div>
      </div> : null}
      {logoutConfirmOpen ? <div className="cleanup-dialog" role="dialog" aria-modal="true" aria-label="确认退出当前账号">
        <div className="panel account-logout-dialog">
          <h2>确认退出当前账号</h2>
          <p>只会清除当前 AutoDy 本地账号的认证状态；目标、文案分配、计划、历史和其他已保存账号都会保留。</p>
          <div className="dialog-actions">
            <button className="action-button" onClick={() => setLogoutConfirmOpen(false)}>取消</button>
            <button className="action-button danger-confirm" onClick={() => { setLogoutConfirmOpen(false); setAccountOpen(false); onLogoutAccount(); }}>确认退出</button>
          </div>
        </div>
      </div> : null}
      <nav aria-label="主导航">
        {navigation.map(([value, label, Icon]) => <div key={value} className="nav-group">
          <button className={active === value ? `nav-item ${value === "settings" && testCenterActive ? "parent-active" : "active"}` : "nav-item"} onClick={() => onChange(value)}>
            <Icon size={20} />
            <span>{label}</span>
          </button>
          {value === "settings" && testCenterInstalled && <button className={testCenterActive ? "nav-item nav-item-child test-center-active" : "nav-item nav-item-child"} onClick={onOpenTestCenter}>
            <FlaskConical size={18} />
            <span>测试中心</span>
          </button>}
        </div>)}
      </nav>
      <div className="sidebar-footer">
        <span className="service-dot" />
        <span>本地服务运行中</span>
        <small>数据仅保存在此电脑</small>
      </div>
    </aside>
  );
}

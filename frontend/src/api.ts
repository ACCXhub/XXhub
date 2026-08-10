import type {
  AppConfig,
  AccountProfile,
  LocalAccountProfile,
  LocalAccountProfiles,
  BackupPreview,
  ConfiguredFriend,
  DashboardStatus,
  FriendDiscovery,
  LogPage,
  PackCatalog,
  PackImportResult,
  PackPreview,
  SchedulePreview,
  ScheduleSettings,
  ServiceIdentity,
  TodayPlan,
  FailedTargetCenter,
  TargetEffectiveSettings
} from "./types";

type ActionJob = {
  id: string;
  action: string;
  status: "running" | "success" | "failed";
  exit_code?: number | null;
  failure?: import("./types").FailureDetail | null;
};

type ErrorResponse = {
  detail?: string | {
    user_summary_zh?: string;
    suggested_action_zh?: string;
  };
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    const raw = await response.text();
    let payload: ErrorResponse | null = null;
    try { payload = JSON.parse(raw) as ErrorResponse; } catch { payload = null; }
    if (typeof payload?.detail === "object" && payload.detail?.user_summary_zh) {
      throw new Error(
        payload.detail.suggested_action_zh
          ? `${payload.detail.user_summary_zh}；${payload.detail.suggested_action_zh}`
          : payload.detail.user_summary_zh
      );
    }
    if (typeof payload?.detail === "string") throw new Error(payload.detail);
    throw new Error(raw || `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () => request<DashboardStatus>("/api/status"),
  modules: () => request<{ modules: import("./types").OptionalModuleStatus[] }>("/api/modules"),
  installTestCenter: (file?: File) => {
    if (!file) return request<import("./types").OptionalModuleStatus>("/api/modules/autody-test-center/install", { method: "POST" });
    const data = new FormData(); data.append("file", file);
    return request<import("./types").OptionalModuleStatus>("/api/modules/autody-test-center/install", { method: "POST", body: data });
  },
  uninstallTestCenter: () => request<import("./types").OptionalModuleStatus>("/api/modules/autody-test-center/uninstall", { method: "POST", body: JSON.stringify({ confirmed: true }) }),
  serviceIdentity: () => request<ServiceIdentity>("/api/service-identity"),
  todayPlan: () => request<TodayPlan>("/api/today-plan"),
  failedTargets: () => request<FailedTargetCenter>("/api/failed-targets"),
  retryFailedTarget: (targetId: string) => request<ActionJob>(`/api/failed-targets/${encodeURIComponent(targetId)}/retry`, { method: "POST", body: JSON.stringify({}) }),
  accountProfile: () => request<AccountProfile>("/api/account-profile"),
  accountProfiles: () => request<LocalAccountProfiles>("/api/account-profiles"),
  switchAccountProfile: (profileId: string) =>
    request<LocalAccountProfiles>(`/api/account-profiles/${encodeURIComponent(profileId)}/switch`, {
      method: "POST",
      body: JSON.stringify({})
    }),
  addAccountProfile: () =>
    request<LocalAccountProfiles & { profile: LocalAccountProfile; job: ActionJob }>(
      "/api/account-profiles/add",
      { method: "POST", body: JSON.stringify({}) }
    ),
  refreshAccountProfile: () => request<AccountProfile & { job?: ActionJob }>("/api/account-profile/refresh", { method: "POST" }),
  logoutAccount: () => request<AccountProfile>("/api/account-profile/logout", {
    method: "POST",
    body: JSON.stringify({ confirmed: true })
  }),
  config: () => request<AppConfig>("/api/config"),
  saveConfig: (config: AppConfig) =>
    request<AppConfig>("/api/config", { method: "PUT", body: JSON.stringify(config) }),
  messages: () => request<{ messages: string[] }>("/api/messages"),
  saveMessages: (messages: string[]) =>
    request<{ messages: string[] }>("/api/messages", {
      method: "PUT",
      body: JSON.stringify({ messages })
    }),
  messagePacks: () => request<PackCatalog>("/api/message-packs"),
  previewMessagePack: (id: string) =>
    request<PackPreview>(`/api/message-packs/${encodeURIComponent(id)}`),
  importMessagePack: (id: string, mode: "merge" | "replace" | "preview_only") =>
    request<PackImportResult>(`/api/message-packs/${encodeURIComponent(id)}/import`, {
      method: "POST",
      body: JSON.stringify({ mode })
    }),
  logs: (filters?: { start_date?: string; end_date?: string; level?: string; task_type?: string; status?: string }) => {
    const query = new URLSearchParams();
    Object.entries(filters || {}).forEach(([key, value]) => { if (value) query.set(key, value); });
    return request<LogPage>(`/api/logs${query.size ? `?${query}` : ""}`);
  },
  archiveLogs: (before: string) =>
    request<{ archived_count: number; archive_dir: string }>(`/api/logs/archive?before=${encodeURIComponent(before)}`, { method: "POST" }),
  archiveHistoricalLogs: () => request<{ archived_count: number; archive_dir: string }>("/api/logs/archive-historical", { method: "POST" }),
  logStorageSummary: () => request<{ active_files: number; active_bytes: number; archived_files: number; archived_bytes: number; total_bytes: number; oldest_date: string | null; last_cleanup_at: string | null; last_cleanup_result: Record<string, number> | null; next_cleanup_date: string; cleanup_enabled: boolean; active_retention_days: number; archive_retention_days: number }>("/api/logs/storage-summary"),
  logCleanupPreview: () => request<{ to_archive: number; to_delete: number; bytes: number; skipped: number }>("/api/logs/cleanup-preview", { method: "POST" }),
  cleanupLogs: () => request<{ archived: number; deleted: number; bytes: number; skipped: number }>("/api/logs/cleanup", { method: "POST", body: JSON.stringify({ confirmed: true }) }),
  preflightLatest: () => request<{ result: import("./types").PreflightResult | null }>("/api/preflight/latest"),
  preflightStatus: () => request<{ running: boolean; progress: import("./types").PreflightProgress | null; result: import("./types").PreflightResult | null }>("/api/preflight/status"),
  runPreflight: (targetIds: string[] | null = null) => request<ActionJob>("/api/preflight/run", { method: "POST", body: JSON.stringify({ target_ids: targetIds }) }),
  cancelPreflight: () => request<{ cancelled: boolean }>("/api/preflight/cancel", { method: "POST" }),
  openLogFolder: () => request<{ opened: boolean }>("/api/logs/open-folder", { method: "POST" }),
  schedulerPreview: (settings: ScheduleSettings) => request<SchedulePreview>("/api/scheduler/preview", { method: "POST", body: JSON.stringify(settings) }),
  schedulerApply: (settings: ScheduleSettings) => request<{ config: AppConfig }>("/api/scheduler/apply", { method: "POST", body: JSON.stringify(settings) }),
  schedulerOperation: (operation: "install" | "update" | "repair" | "remove") => request<{ message: string }>(`/api/scheduler/${operation}`, { method: "POST" }),
  action: (name: string) =>
    request<ActionJob>(`/api/actions/${name}`, {
      method: "POST"
    }),
  waitForAction: async (id: string) => {
    for (let attempt = 0; attempt < 1200; attempt += 1) {
      const job = await request<ActionJob>(`/api/actions/${id}`);
      if (job.status !== "running") return job;
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw new Error("操作等待超时，请查看运行日志");
  },
  scanFriends: () =>
    request<ActionJob>("/api/friends/scan", { method: "POST" }),
  refreshFriendAvatars: () =>
    request<ActionJob>("/api/friends/refresh-avatars", { method: "POST" }),
  discoveredFriends: () =>
    request<FriendDiscovery>("/api/friends/discovered"),
  friends: () => request<{ friends: ConfiguredFriend[] }>("/api/friends"),
  addCandidateToTargets: (candidateId: string) =>
    request<{ created: boolean; target: { target_id: string; display_name: string; enabled: boolean } }>(`/api/friends/${encodeURIComponent(candidateId)}/add-to-targets`, {
      method: "POST"
    }),
  relinkCandidate: (candidateId: string, targetId: string) =>
    request<{ target_id: string; candidate_id: string; display_name: string }>(`/api/friends/${encodeURIComponent(candidateId)}/relink`, {
      method: "POST",
      body: JSON.stringify({ target_id: targetId })
    }),
  ignoreFriendOrphan: (targetId: string) =>
    request<{ ignored: boolean }>(`/api/friends/${encodeURIComponent(targetId)}/ignore-orphan`, { method: "POST" }),
  addDiscoveredFriends: (candidateIds: string[]) =>
    request<{ added: number; skipped: number }>("/api/friends/discovered/batch", {
      method: "POST",
      body: JSON.stringify({ candidate_ids: candidateIds })
    }),
  importBackup: (file: File, mode: "merge" | "replace" = "merge") => {
    const data = new FormData();
    data.append("file", file);
    return request<{ targets: string[]; messages: number }>(`/api/backup/import?mode=${mode}`, {
      method: "POST",
      body: data
    });
  },
  previewBackup: (file: File) => {
    const data = new FormData(); data.append("file", file);
    return request<BackupPreview>("/api/backup/preview", { method: "POST", body: data });
  },
  exportBackup: async (categories: string[]) => {
    const response = await fetch("/api/backup/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ categories }) });
    if (!response.ok) throw new Error(await response.text());
    return response.blob();
  },
  friendBatch: (targetIds: string[], action: "enable" | "disable" | "delete") => request<{ affected: number }>("/api/friends/batch", { method: "PATCH", body: JSON.stringify({ target_ids: targetIds, action }) }),
  saveTargetSettings: (targetId: string, settings: Record<string, unknown>) =>
    request<{ target_id: string; settings: TargetEffectiveSettings }>(`/api/friends/${encodeURIComponent(targetId)}/settings`, { method: "PUT", body: JSON.stringify(settings) }),
  previewMessageImport: (file: File) => upload<{ total_entries: number; valid_entries: number; exact_duplicates: number; empty_entries: number; overly_long_entries: number; entries_with_links: number }>("/api/messages/import/preview", file),
  importMessages: (file: File, mode: "merge" | "replace") => upload<{ imported: number; duplicated: number; total: number }>(`/api/messages/import?mode=${mode}`, file),
  deduplicateMessages: () => request<{ removed: number }>("/api/messages/deduplicate", { method: "POST" })
};

function upload<T>(url: string, file: File): Promise<T> {
  const data = new FormData();
  data.append("file", file);
  return request<T>(url, { method: "POST", body: data });
}

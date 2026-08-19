export type FriendStatus = "success" | "failed" | "pending";

export interface Friend {
  target_id?: string;
  name: string;
  status: FriendStatus;
  error?: string | null;
  failure?: FailureDetail | null;
  current_health?: CurrentTargetHealth;
}

export interface CurrentTargetHealth {
  status: "healthy" | "abnormal" | "unknown";
  reason_code: string;
  summary_zh: string;
}

export interface FailureDetail {
  category: string;
  stage: string;
  reason_code: string;
  user_summary_zh: string;
  user_detail_zh: string;
  retryable: boolean;
  send_attempted: boolean;
  send_attempts: number;
  uncertain_send: boolean;
  suggested_action: string;
  suggested_action_zh: string;
  timestamp: string;
  run_id: string | null;
  target_stable_id: string | null;
  account_scope: string | null;
  scheduler_execution_id?: string | null;
  binding_valid: boolean | null;
  account_scope_matches: boolean | null;
  diagnostic_details: Record<string, unknown>;
  safe_retry_available: boolean;
  resolved?: boolean;
  resolved_at?: string | null;
  resolution_zh?: string | null;
  retry_action_available?: boolean;
}

export interface HistoryRow {
  run_id: string;
  date: string;
  task_type: string;
  trigger_source: "scheduled" | "manual" | "startup_recovery" | "retry";
  success_count: number;
  failed_count: number;
  skipped_count: number;
  total_targets: number;
  retry_count: number;
  final_status: string;
  end_time: string;
  target_failures?: Record<string, FailureDetail>;
}

export interface SchedulerTask {
  name: string;
  state: string;
  next_run: string;
  last_run: string;
  last_result: number | null;
  installed: boolean;
  configured_enabled: boolean;
  configured_time: string;
  windows_time: string | null;
  target_count: number | null;
  duplicate_count: number;
  drift: boolean;
  drift_reason?: "runtime_root_mismatch" | "schedule_mismatch" | null;
}

export interface ScheduleSettings {
  daily_health_check_time: string;
  daily_send_time: string;
  weekly_health_check_enabled: boolean;
  weekly_health_check_weekday: string;
  weekly_health_check_time: string;
  recovery_deadline: string;
}

export interface SchedulePreview {
  old: ScheduleSettings;
  new: ScheduleSettings;
  affected_tasks: { name: string; action: "update" | "remove" }[];
}

export interface DashboardStatus {
  today: {
    date: string;
    message: string;
    succeeded: number;
    failed: number;
    total: number;
    complete: boolean;
  };
  friends: Friend[];
  history: HistoryRow[];
  scheduler: SchedulerTask[];
  next_run: string | null;
  login: { status: string };
  message_count: number;
  issues: DashboardIssue[];
  statistics: {
    last_completed_run: string | null;
    consecutive_successful_days: number;
    success_rate_7d: number;
    success_rate_30d: number;
    successful_today: number;
    failed_today: number;
    configured_friend_count: number;
    enabled_friend_count: number;
    local_message_count: number;
    active_message_pack_count: number;
    next_health_check: string | null;
    next_daily_send: string | null;
    most_recent_issue: string | null;
    log_summary: {
      active_errors: number;
      warnings_24h: number;
      successful_tasks_7d: number;
      last_health_check: string | null;
      last_send: string | null;
      last_error_time: string | null;
    };
  };
}

export interface DashboardIssue {
  id: string;
  status: "error" | "warning" | "info";
  explanation: string;
  action: string;
  action_label: string;
}

export interface AccountProfile {
  display_name: string | null;
  avatar_url: string | null;
  avatar_version: string | null;
  is_self: boolean;
  profile_status: "verified" | "unverified";
  verification_source: string | null;
  logged_in: boolean;
  cached: boolean;
  last_updated_at: string | null;
  refresh_running: boolean;
}

export interface LocalAccountProfile {
  profile_id: string;
  display_name: string | null;
  active: boolean;
  logged_in: boolean;
  profile_status: "verified" | "unverified";
}

export interface LocalAccountProfiles {
  active_profile_id: string | null;
  profiles: LocalAccountProfile[];
  migration_required?: boolean;
}

export type MessageSuffixStyle = "dash" | "bracket" | "newline" | "none";

export interface AppConfig {
  targets: string[];
  retry_count: number;
  timeout_ms: number;
  headless: boolean;
  message_suffix: {
    enabled: boolean;
    text: string;
    style: MessageSuffixStyle;
  };
  daily_send_time: string;
  daily_health_check_time: string;
  weekly_health_check_enabled: boolean;
  weekly_health_check_weekday: string;
  weekly_health_check_time: string;
  recovery_deadline: string;
  min_delay_seconds: number;
  max_delay_seconds: number;
  page_load_timeout_ms: number;
  friend_search_timeout_ms: number;
  confirmation_timeout_ms: number;
  friend_order: "configured" | "randomized";
  message_selection: "one_for_all" | "per_friend";
  completion_notifications_enabled: boolean;
  preflight_after_health_enabled: boolean;
  log_retention_days: number;
  log_cleanup_enabled: boolean;
  active_log_retention_days: number;
  archive_log_retention_days: number;
  mask_log_friend_names: boolean;
}

export interface LogEntry {
  timestamp: string;
  date: string;
  level: "INFO" | "WARNING" | "ERROR";
  task_type: string;
  summary: string;
  detail: string;
  source: string;
  status: "active" | "resolved" | "historical";
  fingerprint: string;
  occurrences: number;
}

export interface LogPage {
  items: LogEntry[];
  total: number;
  page: number;
  page_size: number;
  start_date: string;
  end_date: string;
  scheduler: string;
}

export interface BackupPreview {
  package_version: number;
  autody_version: string | null;
  categories: string[];
  friend_count: number;
  message_count: number;
  schedule_changes: Record<string, { old: unknown; new: unknown }>;
  suffix_change: boolean;
  conflicts: string[];
}

export interface MessagePack {
  id: string;
  name: string;
  description: string;
  version: string;
  count: number;
  category: string;
  direct_fused_sources: MessagePack[];
  fused_source_count: number;
}

export interface PackCatalog {
  packs: MessagePack[];
  revision: number;
}

export interface PackMutationResult {
  revision: number;
  pack: MessagePack | null;
  catalog: PackCatalog;
}

export interface PackEntry {
  id: string;
  text: string;
  origin_pack_id: string;
  origin_pack_name: string;
  native: boolean;
}

export interface OptionalModuleStatus {
  id: string;
  display_name: string;
  installed: boolean;
  version: string | null;
  compatible: boolean;
  bundled_available?: boolean;
  bundled_version?: string | null;
  core_version?: string | null;
  required_autody_version?: string | null;
  compatibility_reason?: string | null;
  update_available?: boolean;
  module_api_version?: string | null;
  package_sha256?: string | null;
  package_checksum?: string | null;
  load_error?: string | null;
}

export interface PackPreview {
  pack: MessagePack;
  messages: string[];
  duplicate_count: number;
  entries: PackEntry[];
}

export interface PackImportResult {
  added_count: number;
  duplicate_count: number;
  total_count: number;
  backup_path?: string | null;
  mode: "merge" | "replace" | "preview_only";
}

export interface FriendCandidate {
  candidate_id: string;
  display_name: string;
  avatar_url: string;
  avatar_status: "cached" | "missing";
  discovered_at: string;
  match_status: "configured" | "unconfigured" | "ambiguous" | "needs_reassociation" | "ignored_reassociation";
  configured?: boolean;
  target_id?: string | null;
  enabled?: boolean | null;
  stale?: boolean;
  configured_target_id: string | null;
  configured_enabled: boolean | null;
  avatar_cache_key?: string | null;
  avatar_updated_at?: string | null;
  first_discovered_at?: string | null;
  last_seen_at?: string | null;
  last_scan_id?: string | null;
  presence_status?: "current" | "stale";
  reassociation_target_id?: string | null;
}

export interface FriendDiscovery {
  scanned_at: string | null;
  stale: boolean;
  refresh_running: boolean;
  last_result: {
    status?: "completed" | "completed_with_avatar_failures" | "partial_timeout" | "lock_busy" | "login_unavailable" | "page_load_failed" | "cancelled" | "failed" | "deferred";
    finished_at?: string;
    candidates_found?: number;
    new_candidates?: number;
    avatars_updated?: number;
    avatars_failed?: number;
    removed_stale_candidates?: number;
    completed_bottom_reached?: boolean;
    error?: string;
  };
  progress?: { running?: boolean; message?: string; current?: number; total?: number | null; status?: string };
  candidates: FriendCandidate[];
  orphans?: Array<{ target_id: string; display_name: string; enabled: boolean }>;
}

export interface ConfiguredFriend {
  id: string | null;
  target_id: string | null;
  display_name: string;
  enabled: boolean;
  note: string;
  avatar_url: string;
  avatar_status: "cached" | "missing";
  today_status: "success" | "failed" | "pending";
  last_success_date: string | null;
  ambiguous_duplicate?: boolean;
  binding_status?: "verified" | "revalidation_required";
  settings?: TargetEffectiveSettings;
}

export interface TargetEffectiveSettings {
  message_source: string;
  message_source_origin: "global" | "override";
  suffix: string;
  suffix_origin: "global" | "override";
  message_selection: "one_for_all" | "per_friend";
  message_selection_origin: "global" | "override";
  delay_offset_minutes: number;
  send_order: number | null;
}

export interface TodayPlan {
  generated_at: string;
  configuration_source: "current" | "cached";
  main_scheduled_time: string;
  enabled_target_count: number;
  completed_count: number;
  pending_count: number;
  blocked_count: number;
  estimated_finish: string;
  targets: Array<TargetEffectiveSettings & {
    target_id: string;
    display_name: string;
    planned_at: string;
    status: "success" | "pending" | "failed" | "blocked";
    blocked_reason: string | null;
  }>;
}

export interface FailedTargetCenter {
  summary: { success: number; failed: number; uncertain: number; needs_attention: number };
  items: Array<{
    target_id: string;
    display_name: string;
    failure_time: string;
    trigger_source: string;
    reason_code: string;
    explanation: string;
    no_send_action_definitely_occurred: boolean;
    uncertain: boolean;
    safe_retry_available: boolean;
    latest_preflight_status: string | null;
    latest_send_status: string;
    resolved: boolean;
  }>;
}

export interface ServiceIdentity {
  application: string;
  version: string;
  git_commit: string | null;
  python_executable: string;
  package_path: string;
  project_path: string;
  frontend_build_version: string;
}

export interface PreflightTargetResult {
  target_id: string;
  display_name: string;
  target_status: string;
  user_message: string;
  checked_at: string;
  composer_found: boolean;
  send_control_found: boolean;
}

export interface PreflightResult {
  check_id: string;
  completed_at: string;
  global_status: string;
  total_targets: number;
  ready_count: number;
  failed_count: number;
  blocked_count: number;
  cancelled: boolean;
  targets: PreflightTargetResult[];
}

export interface PreflightProgress {
  running: boolean;
  completed_targets: number;
  total_targets: number;
  current_status: string;
}

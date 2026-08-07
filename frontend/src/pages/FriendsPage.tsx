import { Check, Radar, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AppConfig, ConfiguredFriend, FriendDiscovery } from "../types";

function FriendAvatar({ name, url }: { name: string; url?: string }) {
  const initial = name.trim().slice(0, 1) || "?";
  if (!url) return <span className="friend-avatar avatar-fallback" aria-label={`${name} 的默认头像`}>{initial}</span>;
  return <img key={url} className="friend-avatar" src={url} alt={`${name} 的头像`} loading="lazy" />;
}

function todayLabel(status: ConfiguredFriend["today_status"] | undefined) {
  if (status === "success") return "今日已完成";
  if (status === "failed") return "今日失败";
  return "今日待执行";
}

function candidateLabel(candidate: FriendDiscovery["candidates"][number]) {
  if (candidate.presence_status === "stale") return "历史候选 · 未在本次扫描中出现";
  const { match_status: status, enabled } = candidate;
  if (status === "ambiguous") return "可能重名，未自动关联";
  if (status === "needs_reassociation") return "需要重新关联";
  if (status === "ignored_reassociation") return "已忽略此缓存";
  if (candidate.configured || status === "configured") return enabled ? "已添加 · 已启用" : "已添加 · 已停用";
  return "点击添加到续火目标";
}

export function FriendsPage({
  notify,
  onDataChanged
}: {
  notify: (message: string) => void;
  onDataChanged?: () => void;
}) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [friends, setFriends] = useState<ConfiguredFriend[]>([]);
  const [discovery, setDiscovery] = useState<FriendDiscovery | null>(null);
  const [batchSelected, setBatchSelected] = useState<Set<string>>(() => new Set());
  const [busyAction, setBusyAction] = useState<"scan" | "avatar" | null>(null);
  const [addingCandidateId, setAddingCandidateId] = useState<string | null>(null);
  const [targetMutationId, setTargetMutationId] = useState<string | null>(null);
  const [orphanSelections, setOrphanSelections] = useState<Record<string, string>>({});
  const refreshWasRunning = useRef(false);

  const load = async () => {
    try {
      const [nextConfig, nextFriends, nextDiscovery] = await Promise.all([
        api.config(), api.friends(), api.discoveredFriends()
      ]);
      setConfig(nextConfig);
      setFriends(nextFriends.friends);
      setDiscovery(nextDiscovery.scanned_at ? nextDiscovery : null);
      return nextDiscovery;
    } catch (error) {
      notify(error instanceof Error ? error.message : "好友配置加载失败");
    }
  };

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!discovery?.refresh_running) return;
    const id = window.setInterval(() => { void load(); }, 2000);
    return () => window.clearInterval(id);
  }, [discovery?.refresh_running]);
  useEffect(() => {
    if (discovery?.refresh_running) {
      refreshWasRunning.current = true;
      return;
    }
    if (refreshWasRunning.current && discovery?.last_result?.completed_bottom_reached) {
      const result = discovery.last_result;
      notify(`扫描完成：识别 ${result.candidates_found ?? 0} 个，新增 ${result.new_candidates ?? 0} 个，更新 ${result.avatars_updated ?? 0} 个，移除过期缓存 ${result.removed_stale_candidates ?? 0} 个。`);
      refreshWasRunning.current = false;
    }
  }, [discovery?.last_result, discovery?.refresh_running, notify]);
  if (!config) return <div className="loading">加载好友配置…</div>;
  const duplicateTargets = friends.filter((friend) => friend.ambiguous_duplicate);

  const scan = async () => {
    setBusyAction("scan");
    try {
      const job = await api.scanFriends();
      const finished = await api.waitForAction(job.id);
      if (finished.status === "failed") throw new Error("好友识别失败，请查看运行日志");
      const nextDiscovery = await load();
      const result = nextDiscovery?.last_result;
      notify(result?.completed_bottom_reached ? `扫描完成：识别 ${result.candidates_found ?? 0} 个，新增 ${result.new_candidates ?? 0} 个，更新 ${result.avatars_updated ?? 0} 个，移除过期缓存 ${result.removed_stale_candidates ?? 0} 个。` : "好友识别完成，候选列表已更新");
    } catch (error) {
      notify(error instanceof Error ? error.message : "好友识别失败");
    } finally {
      setBusyAction(null);
    }
  };

  const refreshAvatars = async () => {
    setBusyAction("avatar");
    try {
      const job = await api.refreshFriendAvatars();
      const finished = await api.waitForAction(job.id);
      if (finished.status === "failed") throw new Error("头像更新失败，请查看运行日志");
      await load();
      notify("头像校正完成，未修改好友名称或续火目标");
    } catch (error) {
      notify(error instanceof Error ? error.message : "头像更新失败");
    } finally {
      setBusyAction(null);
    }
  };

  const addCandidate = async (candidate: FriendDiscovery["candidates"][number]) => {
    if (candidate.configured || candidate.presence_status === "stale" || addingCandidateId) return;
    setAddingCandidateId(candidate.candidate_id);
    try {
      const result = await api.addCandidateToTargets(candidate.candidate_id);
      setDiscovery((current) => current ? {
        ...current,
        candidates: current.candidates.map((item) => item.candidate_id === candidate.candidate_id ? {
          ...item,
          match_status: "configured",
          configured: true,
          target_id: result.target.target_id,
          configured_target_id: result.target.target_id,
          enabled: result.target.enabled,
          configured_enabled: result.target.enabled
        } : item)
      } : current);
      setFriends((current) => current.some((friend) => friend.target_id === result.target.target_id || friend.id === result.target.target_id) ? current : [
        ...current,
        {
          id: result.target.target_id,
          target_id: result.target.target_id,
          display_name: result.target.display_name,
          enabled: result.target.enabled,
          note: "",
          avatar_url: candidate.avatar_url,
          avatar_status: candidate.avatar_status,
          today_status: "pending",
          last_success_date: null
        }
      ]);
      notify(result.created ? "已添加" : "已添加");
      onDataChanged?.();
      void load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "添加候选好友失败");
    } finally {
      setAddingCandidateId(null);
    }
  };

  const relinkCandidate = async (candidate: FriendDiscovery["candidates"][number], targetId?: string | null) => {
    if (!targetId || addingCandidateId) return;
    setAddingCandidateId(candidate.candidate_id);
    try {
      await api.relinkCandidate(candidate.candidate_id, targetId);
      await load();
      onDataChanged?.();
      notify("已重新关联");
    } catch (error) {
      notify(error instanceof Error ? error.message : "重新关联失败");
    } finally {
      setAddingCandidateId(null);
    }
  };

  const ignoreOrphan = async (targetId?: string | null) => {
    if (!targetId) return;
    try {
      await api.ignoreFriendOrphan(targetId);
      await load();
      onDataChanged?.();
      notify("已忽略此缓存");
    } catch (error) {
      notify(error instanceof Error ? error.message : "忽略缓存失败");
    }
  };

  const batch = async (action: "enable" | "disable" | "delete") => {
    if (!batchSelected.size) return notify("请先勾选好友");
    if (action === "delete" && !window.confirm(`删除所选 ${batchSelected.size} 位好友？`)) return;
    try {
      const result = await api.friendBatch([...batchSelected], action);
      await load();
      onDataChanged?.();
      setBatchSelected(new Set());
      notify(`已处理 ${result.affected} 位好友`);
    } catch (error) {
      notify(error instanceof Error ? error.message : "批量操作失败");
    }
  };

  const setTargetEnabled = (targetId: string, enabled: boolean) => {
    setFriends((current) => current.map((friend) => (
      (friend.target_id || friend.id) === targetId
        ? { ...friend, enabled }
        : friend
    )));
    setDiscovery((current) => current ? {
      ...current,
      candidates: current.candidates.map((candidate) => (
        candidate.configured_target_id === targetId || candidate.target_id === targetId
          ? { ...candidate, enabled, configured_enabled: enabled }
          : candidate
      ))
    } : current);
  };

  const toggleContinuationTarget = async (
    targetId: string,
    enabled: boolean,
  ) => {
    if (targetMutationId) return;
    setTargetMutationId(targetId);
    try {
      const result = await api.friendBatch(
        [targetId],
        enabled ? "enable" : "disable",
      );
      if (result.affected !== 1) throw new Error("续火目标状态未更新");
      setTargetEnabled(targetId, enabled);
      onDataChanged?.();
      notify(enabled ? "已加入续火" : "已取消续火");
      void load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "续火目标更新失败");
    } finally {
      setTargetMutationId(null);
    }
  };

  const visibleTargetIds = friends
    .map((friend) => friend.target_id || friend.id)
    .filter((targetId): targetId is string => Boolean(targetId));
  const allTargetsSelected = visibleTargetIds.length > 0
    && visibleTargetIds.every((targetId) => batchSelected.has(targetId));

  return (
    <section className="editor-page">
      <header className="page-header">
        <div><h1>好友管理</h1><p>扫描仅读取当前聊天列表并缓存本地缩略头像；不会上传头像，也不会自动修改昵称。</p></div>
        <div className="header-actions">
          <button className="action-button" disabled={busyAction !== null} onClick={() => void scan()}><Radar size={17} />{busyAction === "scan" ? "扫描中…" : "扫描好友"}</button>
          <button className="action-button" disabled={busyAction !== null} onClick={() => void refreshAvatars()}><RefreshCw size={17} />{busyAction === "avatar" ? "校正中…" : "重新扫描并修正头像对应关系"}</button>
        </div>
      </header>

      <div className="panel form-panel">
        <div className="panel-heading"><h2>续火目标</h2><span className="inline-actions"><label className="select-all-control"><input aria-label="全选续火目标" type="checkbox" checked={allTargetsSelected} onChange={(event) => setBatchSelected(event.target.checked ? new Set(visibleTargetIds) : new Set())} />全选</label><strong className="selection-count">已选择 {batchSelected.size} 个目标</strong><button className="text-button" onClick={() => void batch("enable")}>批量启用</button><button className="text-button" onClick={() => void batch("disable")}>批量停用</button><button className="text-button" onClick={() => void batch("delete")}>批量删除记录</button></span></div>
        {duplicateTargets.length ? <p className="discovery-progress">检测到重复昵称的启用目标；为避免选错聊天，自动发送会跳过这些目标。</p> : null}
        <div className="friend-editor-list">
          {friends.map((friend) => {
            const targetId = friend.target_id || friend.id;
            if (!targetId) return null;
            const toggle = () => setBatchSelected((current) => { const next = new Set(current); if (next.has(targetId)) next.delete(targetId); else next.add(targetId); return next; });
            const selected = batchSelected.has(targetId);
            return <div className={`friend-editor-row${selected ? " selected" : ""}${friend.enabled ? "" : " disabled-target"}`} key={targetId} role="button" tabIndex={0} aria-selected={selected} onClick={toggle} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } }}>
              <input className="row-check" aria-label={`选择 ${friend.display_name}`} type="checkbox" checked={batchSelected.has(targetId)} onClick={(event) => event.stopPropagation()} onChange={(event) => { const next = new Set(batchSelected); if (event.target.checked) next.add(targetId); else next.delete(targetId); setBatchSelected(next); }} />
              <FriendAvatar name={friend.display_name} url={friend.avatar_url} />
              <div className="friend-editor-copy">
                <strong>{friend.display_name}</strong>
                <small><span className={friend.enabled ? "target-status" : "target-status paused"}>{friend.enabled ? "已启用" : "已停用"}</span>{friend.binding_status === "revalidation_required" ? <span className="target-status paused">绑定待重新验证</span> : todayLabel(friend.today_status)}{friend.binding_status !== "revalidation_required" && friend.last_success_date ? ` · 最近成功：${friend.last_success_date}` : ""}</small>
              </div>
              {selected ? <span className="selection-indicator" aria-label="已选择"><Check size={13} /></span> : null}
              <button className="text-button friend-target-toggle" aria-label={`${friend.enabled ? "取消续火" : "加入续火"} ${friend.display_name}`} disabled={targetMutationId !== null} onClick={(event) => { event.stopPropagation(); void toggleContinuationTarget(targetId, !friend.enabled); }}>{targetMutationId === targetId ? "保存中…" : friend.enabled ? "取消续火" : "加入续火"}</button>
              <button className="icon-button danger" aria-label={`删除目标 ${friend.display_name}`} title="删除目标" onClick={(event) => { event.stopPropagation(); if (window.confirm(`删除目标「${friend.display_name}」？`)) void api.friendBatch([targetId], "delete").then(async () => { await load(); onDataChanged?.(); }); }}><Trash2 size={17} /></button>
            </div>;
          })}
        </div>
      </div>

      {discovery ? <section className="panel discovery-panel">
        <div className="panel-heading"><div><h2>识别到的候选好友</h2><small className="discovery-status">候选好友来自本地缓存{discovery.scanned_at ? ` · 上次扫描：${discovery.scanned_at.replace("T", " ")}` : ""}{discovery.stale ? " · 缓存待更新" : " · 缓存当前"}</small>{discovery.refresh_running ? <small className="discovery-progress">{discovery.progress?.message ?? "正在后台更新候选好友和头像…"}{discovery.progress?.current ? `：已识别 ${discovery.progress.current}${discovery.progress.total ? ` / ${discovery.progress.total}` : ""}` : ""}</small> : null}{discovery.progress?.status === "partial_timeout" ? <small className="discovery-progress">扫描超时，已保留上次结果。</small> : null}</div></div>
        <div className="candidate-grid">{discovery.candidates.filter((candidate) => candidate.presence_status !== "stale").sort((left, right) => {
          const group = (candidate: FriendDiscovery["candidates"][number]) => candidate.presence_status === "stale" ? 2 : candidate.configured ? 1 : 0;
          return group(left) - group(right);
        }).map((candidate) => {
          const configured = candidate.configured || candidate.match_status === "configured";
          const canAdd = !configured && candidate.presence_status !== "stale" && !["ambiguous", "needs_reassociation", "ignored_reassociation"].includes(candidate.match_status);
          const adding = addingCandidateId === candidate.candidate_id;
          if (candidate.match_status === "needs_reassociation") {
            return <div className="candidate configured candidate-reassociation" key={candidate.candidate_id}>
              <FriendAvatar name={candidate.display_name} url={candidate.avatar_url} />
              <span>{candidate.display_name}</span><small>{adding ? "关联中…" : "需要重新关联"}</small>
              <span className="candidate-actions"><button className="text-button" aria-label={`重新关联 ${candidate.display_name}`} disabled={adding} onClick={() => void relinkCandidate(candidate, candidate.reassociation_target_id)}>重新关联</button><button className="text-button" aria-label={`忽略缓存 ${candidate.display_name}`} disabled={adding} onClick={() => void ignoreOrphan(candidate.reassociation_target_id)}>忽略此缓存</button></span>
            </div>;
          }
          return <button type="button" className={canAdd ? "candidate" : "candidate configured"} key={candidate.candidate_id} aria-label={`添加 ${candidate.display_name}`} disabled={!canAdd || adding} onClick={() => void addCandidate(candidate)}>
            <FriendAvatar name={candidate.display_name} url={candidate.avatar_url} />
            <span>{candidate.display_name}</span><small>{adding ? "添加中…" : configured ? "已添加" : candidateLabel(candidate)}</small>
          </button>;
        })}</div>
        {discovery.orphans?.some((orphan) => !discovery.candidates.some((candidate) => candidate.reassociation_target_id === orphan.target_id)) ? <div className="orphan-bindings">{discovery.orphans.filter((orphan) => !discovery.candidates.some((candidate) => candidate.reassociation_target_id === orphan.target_id)).map((orphan) => {
          const selectedId = orphanSelections[orphan.target_id] || "";
          const selectedCandidate = discovery.candidates.find((candidate) => candidate.candidate_id === selectedId);
          return <div className="orphan-binding" key={orphan.target_id}><span><strong>{orphan.display_name}</strong><small>当前绑定未在本次扫描中找到</small></span><select aria-label={`为 ${orphan.display_name} 选择候选`} value={selectedId} onChange={(event) => setOrphanSelections((current) => ({ ...current, [orphan.target_id]: event.target.value }))}><option value="">选择当前候选好友</option>{discovery.candidates.filter((candidate) => candidate.presence_status !== "stale" && !candidate.configured).map((candidate) => <option value={candidate.candidate_id} key={candidate.candidate_id}>{candidate.display_name}</option>)}</select><button className="text-button" disabled={!selectedCandidate} onClick={() => selectedCandidate && void relinkCandidate(selectedCandidate, orphan.target_id)}>重新关联</button><button className="text-button" onClick={() => void ignoreOrphan(orphan.target_id)}>忽略此缓存</button></div>;
        })}</div> : null}
      </section> : null}
    </section>
  );
}

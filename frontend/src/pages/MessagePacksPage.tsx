import { Download, Eye, Library, Pencil, Plus, RefreshCw, Upload } from "lucide-react";
import type { DragEvent } from "react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { PackCatalog, PackImportResult, PackMutationResult, PackPreview } from "../types";

const categoryLabels: Record<string, string> = {
  daily: "日常", cute: "可爱", funny: "趣味", care: "关心", festival: "节日", custom: "自定义"
};

type ReorderEdge = "top" | "right" | "bottom" | "left";
type DropIntent =
  | { kind: "fuse"; targetId: string }
  | { kind: "reorder"; targetId: string; position: "before" | "after"; edge: ReorderEdge };

export function MessagePacksPage({ notify }: { notify: (message: string) => void }) {
  const [catalog, setCatalog] = useState<PackCatalog | null>(null);
  const [preview, setPreview] = useState<PackPreview | null>(null);
  const [result, setResult] = useState<PackImportResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [overwritePack, setOverwritePack] = useState<{ id: string; name: string; count: number } | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropIntent, setDropIntent] = useState<DropIntent | null>(null);
  const report = (error: unknown, fallback: string) => notify(error instanceof Error ? error.message : fallback);
  const load = () => void api.messagePacks().then(setCatalog).catch((error) => report(error, "文案包加载失败"));
  useEffect(load, []);

  const acceptMutation = (mutation: PackMutationResult) => {
    setCatalog(mutation.catalog);
    if (preview && !mutation.catalog.packs.some((pack) => pack.id === preview.pack.id)) setPreview(null);
  };

  const showPreview = async (id: string) => {
    setBusy(id);
    try { setPreview(await api.previewMessagePack(id)); }
    catch (error) { report(error, "文案包预览失败"); }
    finally { setBusy(null); }
  };

  const createPack = async () => {
    if (!catalog) return;
    setBusy("create");
    try {
      const mutation = await api.createMessagePack(catalog.revision);
      acceptMutation(mutation);
      if (mutation.pack) setPreview(await api.previewMessagePack(mutation.pack.id));
    } catch (error) { report(error, "新建文案包失败"); load(); }
    finally { setBusy(null); }
  };

  const importFile = async (file: File | undefined) => {
    if (!catalog || !file) return;
    setBusy("file-import");
    try {
      const mutation = await api.importMessagePackFile(file, catalog.revision);
      acceptMutation(mutation);
      if (mutation.pack) setPreview(await api.previewMessagePack(mutation.pack.id));
    } catch (error) { report(error, "导入文案包失败"); load(); }
    finally { setBusy(null); }
  };

  const renamePack = async (id: string, currentName: string) => {
    if (!catalog) return;
    const name = window.prompt("文案包名称", currentName);
    if (name === null || name.trim() === currentName) return;
    setBusy(id);
    try { acceptMutation(await api.renameMessagePack(id, name, catalog.revision)); }
    catch (error) { report(error, "文案包重命名失败"); load(); }
    finally { setBusy(null); }
  };

  const reorderPack = async (sourceId: string, targetId: string, position: "before" | "after") => {
    if (!catalog) return;
    const currentIds = catalog.packs.map((pack) => pack.id);
    const ids = currentIds.filter((id) => id !== sourceId);
    const targetIndex = ids.indexOf(targetId);
    if (targetIndex < 0) return;
    ids.splice(targetIndex + (position === "after" ? 1 : 0), 0, sourceId);
    if (ids.every((id, index) => id === currentIds[index])) return;
    setBusy("reorder");
    try { acceptMutation(await api.reorderMessagePacks(ids, catalog.revision)); }
    catch (error) { report(error, "文案包排序失败"); load(); }
    finally { setBusy(null); }
  };

  const fusePack = async (sourceId: string, destinationId: string) => {
    if (!catalog || !destinationId) return;
    const source = catalog.packs.find((pack) => pack.id === sourceId);
    const destination = catalog.packs.find((pack) => pack.id === destinationId);
    if (!source || !destination || !window.confirm(`将「${source.name}」融合到「${destination.name}」？`)) return;
    setBusy(sourceId);
    try { acceptMutation(await api.fuseMessagePack(sourceId, destinationId, catalog.revision)); }
    catch (error) { report(error, "文案包融合失败"); load(); }
    finally { setBusy(null); }
  };

  const splitPack = async (destinationId: string, sourceId: string, sourceName: string) => {
    if (!catalog || !window.confirm(`从当前文案包拆出“${sourceName}”？`)) return;
    setBusy(destinationId);
    try { acceptMutation(await api.splitMessagePack(destinationId, sourceId, catalog.revision)); }
    catch (error) { report(error, "文案包拆分失败"); load(); }
    finally { setBusy(null); }
  };

  const importPack = async (id: string) => {
    setBusy(id);
    try {
      const imported = await api.importMessagePack(id, "merge");
      setResult(imported);
      notify(`文案包导入完成，全局文案库共 ${imported.total_count} 条`);
    } catch (error) { report(error, "文案包导入失败"); }
    finally { setBusy(null); }
  };

  const replaceGlobalMessages = async () => {
    if (!overwritePack) return;
    const pack = overwritePack;
    setOverwritePack(null);
    setBusy(pack.id);
    try {
      const imported = await api.importMessagePack(pack.id, "replace");
      setResult(imported);
      notify(`全局文案库已覆盖，共 ${imported.total_count} 条`);
    } catch (error) { report(error, "全局文案覆盖失败"); }
    finally { setBusy(null); }
  };

  const startDrag = (event: DragEvent<HTMLElement>, packId: string) => {
    if ((event.target as HTMLElement).closest("[data-no-pack-drag]")) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-autody-pack-id", packId);
    setDraggingId(packId);
  };

  const updateDropIntent = (event: DragEvent<HTMLElement>, intent: DropIntent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropIntent(intent);
  };

  const dropPack = (event: DragEvent<HTMLElement>, intent: DropIntent) => {
    event.preventDefault();
    const sourceId = draggingId || event.dataTransfer.getData("application/x-autody-pack-id");
    setDraggingId(null);
    setDropIntent(null);
    if (!sourceId || sourceId === intent.targetId) return;
    if (intent.kind === "fuse") void fusePack(sourceId, intent.targetId);
    else void reorderPack(sourceId, intent.targetId, intent.position);
  };

  const finishDrag = () => {
    setDraggingId(null);
    setDropIntent(null);
  };

  return (
    <section className="editor-page">
      <header className="page-header">
        <div><h1>文案包</h1><p>管理独立文案包；全局 messages.txt 文案库保持独立兼容。</p></div>
        <div className="header-actions">
          <button className="action-button" disabled={!catalog || busy !== null} onClick={() => void createPack()}><Plus size={17} />新建文案包</button>
          <label className="action-button file-action"><Upload size={17} />导入 TXT<input aria-label="导入 TXT 文案包" type="file" accept=".txt,text/plain" disabled={!catalog || busy !== null} onChange={(event) => { void importFile(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label>
          <button className="action-button" onClick={load}><RefreshCw size={17} />刷新列表</button>
        </div>
      </header>
      <p className="pack-drag-help">拖动卡片边缘调整顺序，拖到另一张卡片中央可融合。</p>
      <div className="pack-grid">
        {catalog?.packs.map((pack) => {
          const intent = dropIntent?.targetId === pack.id ? dropIntent : null;
          const slotClass = [
            "pack-card-slot",
            intent?.kind === "fuse" ? "drop-fuse" : "",
            intent?.kind === "reorder" ? `drop-${intent.edge}` : ""
          ].filter(Boolean).join(" ");
          return (
          <div
            className={slotClass}
            key={pack.id}
          >
          <article
            aria-label={`文案包 ${pack.name}`}
            className={`panel pack-card${draggingId === pack.id ? " is-dragging" : ""}`}
            draggable={busy === null}
            onDragStart={(event) => startDrag(event, pack.id)}
            onDragEnd={finishDrag}
          >
            <span className="pack-icon"><Library size={22} /></span>
            <div className="pack-meta"><span>{categoryLabels[pack.category] || pack.category}</span><span>{pack.count} 条</span>{pack.fused_source_count ? <span>含 {pack.fused_source_count} 个来源</span> : null}</div>
            <h2>{pack.name}</h2><p>{pack.description || "普通用户文案包"}</p>
            {pack.direct_fused_sources.length ? <p className="pack-provenance">已融合：{pack.direct_fused_sources.map((source) => source.name).join("、")}</p> : null}
            <div className="pack-actions" data-no-pack-drag onDragStart={(event) => event.preventDefault()}>
              <button disabled={busy !== null} onClick={() => void showPreview(pack.id)}><Eye size={15} />预览</button>
              <button disabled={busy !== null} onClick={() => void renamePack(pack.id, pack.name)}><Pencil size={15} />重命名</button>
              {pack.direct_fused_sources.length ? (
                <details className="pack-split-menu">
                  <summary>拆出已融合包</summary>
                  <div className="pack-split-options">
                    {pack.direct_fused_sources.map((source) => <button key={source.id} disabled={busy !== null} onClick={() => void splitPack(pack.id, source.id, source.name)}>拆出 {source.name}</button>)}
                  </div>
                </details>
              ) : null}
              <button disabled={busy !== null} onClick={() => void importPack(pack.id)}><Download size={15} />导入全局库</button>
              <button disabled={busy !== null} onClick={() => setOverwritePack({ id: pack.id, name: pack.name, count: pack.count })}><RefreshCw size={15} />覆盖全局文案</button>
            </div>
          </article>
          {draggingId && draggingId !== pack.id ? (
            <div className="pack-drop-zones" aria-hidden="true">
              {([
                ["top", "before"], ["right", "after"], ["bottom", "after"], ["left", "before"]
              ] as const).map(([edge, position]) => {
                const reorderIntent: DropIntent = { kind: "reorder", targetId: pack.id, position, edge };
                return <div
                  className={`pack-drop-zone reorder-zone ${edge}`}
                  data-drop-edge={edge}
                  data-drop-position={position}
                  key={edge}
                  onDragOver={(event) => updateDropIntent(event, reorderIntent)}
                  onDragLeave={() => { if (dropIntent?.targetId === pack.id) setDropIntent(null); }}
                  onDrop={(event) => dropPack(event, reorderIntent)}
                />;
              })}
              <div
                className="pack-drop-zone fuse-zone"
                data-drop-kind="fuse"
                onDragOver={(event) => updateDropIntent(event, { kind: "fuse", targetId: pack.id })}
                onDragLeave={() => { if (dropIntent?.targetId === pack.id) setDropIntent(null); }}
                onDrop={(event) => dropPack(event, { kind: "fuse", targetId: pack.id })}
              />
            </div>
          ) : null}
          </div>
          );
        })}
      </div>
      {result ? <div className="panel import-result"><strong>全局文案库导入结果</strong><span>新增 {result.added_count} 条</span><span>重复 {result.duplicate_count} 条</span><span>共 {result.total_count} 条</span></div> : null}
      {preview ? <section className="panel pack-preview"><div className="panel-heading"><h2>{preview.pack.name} · 预览</h2><button className="text-button" onClick={() => setPreview(null)}>关闭</button></div>{preview.messages.length ? <ol>{preview.messages.map((message, index) => <li key={preview.entries[index]?.id || index}>{message}{preview.entries[index] && !preview.entries[index].native ? <small> · 来源：{preview.entries[index].origin_pack_name}</small> : null}</li>)}</ol> : <p className="empty-list-copy">此文案包当前为空。</p>}</section> : null}
      {overwritePack ? <div className="cleanup-dialog" role="dialog" aria-modal="true" aria-label="确认覆盖全局文案">
        <div className="panel">
          <h2>确认覆盖全局文案</h2>
          <p>将用「{overwritePack.name}」的 {overwritePack.count} 条文案覆盖当前全局文案。<br />原有全局文案将被替换。</p>
          <div className="dialog-actions">
            <button className="action-button" onClick={() => setOverwritePack(null)}>取消</button>
            <button className="action-button danger-confirm" onClick={() => void replaceGlobalMessages()}>确认覆盖</button>
          </div>
        </div>
      </div> : null}
    </section>
  );
}

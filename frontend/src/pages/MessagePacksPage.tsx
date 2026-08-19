import { ArrowDown, ArrowUp, Download, Eye, Library, Pencil, Plus, RefreshCw, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { PackCatalog, PackImportResult, PackMutationResult, PackPreview } from "../types";

const categoryLabels: Record<string, string> = {
  daily: "日常", cute: "可爱", funny: "趣味", care: "关心", festival: "节日", custom: "自定义"
};

export function MessagePacksPage({ notify }: { notify: (message: string) => void }) {
  const [catalog, setCatalog] = useState<PackCatalog | null>(null);
  const [preview, setPreview] = useState<PackPreview | null>(null);
  const [result, setResult] = useState<PackImportResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
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

  const movePack = async (index: number, offset: -1 | 1) => {
    if (!catalog) return;
    const destination = index + offset;
    if (destination < 0 || destination >= catalog.packs.length) return;
    const ids = catalog.packs.map((pack) => pack.id);
    [ids[index], ids[destination]] = [ids[destination], ids[index]];
    setBusy("reorder");
    try { acceptMutation(await api.reorderMessagePacks(ids, catalog.revision)); }
    catch (error) { report(error, "文案包排序失败"); load(); }
    finally { setBusy(null); }
  };

  const fusePack = async (sourceId: string, destinationId: string) => {
    if (!catalog || !destinationId) return;
    const source = catalog.packs.find((pack) => pack.id === sourceId);
    const destination = catalog.packs.find((pack) => pack.id === destinationId);
    if (!source || !destination || !window.confirm(`将“${source.name}”融合到“${destination.name}”？`)) return;
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
      <div className="pack-grid">
        {catalog?.packs.map((pack, index) => (
          <article className="panel pack-card" key={pack.id}>
            <span className="pack-icon"><Library size={22} /></span>
            <div className="pack-meta"><span>{categoryLabels[pack.category] || pack.category}</span><span>{pack.count} 条</span>{pack.fused_source_count ? <span>含 {pack.fused_source_count} 个来源</span> : null}</div>
            <h2>{pack.name}</h2><p>{pack.description || "普通用户文案包"}</p>
            {pack.direct_fused_sources.length ? <p className="pack-provenance">已融合：{pack.direct_fused_sources.map((source) => source.name).join("、")}</p> : null}
            <div className="pack-actions">
              <button disabled={busy !== null} onClick={() => void showPreview(pack.id)}><Eye size={15} />预览</button>
              <button disabled={busy !== null} onClick={() => void renamePack(pack.id, pack.name)}><Pencil size={15} />重命名</button>
              <button aria-label={`上移 ${pack.name}`} disabled={busy !== null || index === 0} onClick={() => void movePack(index, -1)}><ArrowUp size={15} />上移</button>
              <button aria-label={`下移 ${pack.name}`} disabled={busy !== null || index === catalog.packs.length - 1} onClick={() => void movePack(index, 1)}><ArrowDown size={15} />下移</button>
              <select aria-label={`融合 ${pack.name} 到`} disabled={busy !== null || catalog.packs.length < 2} value="" onChange={(event) => void fusePack(pack.id, event.target.value)}>
                <option value="">融合到…</option>
                {catalog.packs.filter((candidate) => candidate.id !== pack.id).map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}
              </select>
              {pack.direct_fused_sources.map((source) => <button key={source.id} disabled={busy !== null} onClick={() => void splitPack(pack.id, source.id, source.name)}>拆出 {source.name}</button>)}
              <button disabled={busy !== null} onClick={() => void importPack(pack.id)}><Download size={15} />导入全局库</button>
            </div>
          </article>
        ))}
      </div>
      {result ? <div className="panel import-result"><strong>全局文案库导入结果</strong><span>新增 {result.added_count} 条</span><span>重复 {result.duplicate_count} 条</span><span>共 {result.total_count} 条</span></div> : null}
      {preview ? <section className="panel pack-preview"><div className="panel-heading"><h2>{preview.pack.name} · 预览</h2><button className="text-button" onClick={() => setPreview(null)}>关闭</button></div>{preview.messages.length ? <ol>{preview.messages.map((message, index) => <li key={preview.entries[index]?.id || index}>{message}{preview.entries[index] && !preview.entries[index].native ? <small> · 来源：{preview.entries[index].origin_pack_name}</small> : null}</li>)}</ol> : <p className="empty-list-copy">此文案包当前为空。</p>}</section> : null}
    </section>
  );
}

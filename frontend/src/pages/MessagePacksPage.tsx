import { Download, Eye, Library, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { PackCatalog, PackImportResult, PackPreview } from "../types";

const categoryLabels: Record<string, string> = {
  daily: "日常",
  cute: "可爱",
  funny: "趣味",
  care: "关心",
  festival: "节日"
};

export function MessagePacksPage({ notify }: { notify: (message: string) => void }) {
  const [catalog, setCatalog] = useState<PackCatalog | null>(null);
  const [preview, setPreview] = useState<PackPreview | null>(null);
  const [result, setResult] = useState<PackImportResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = () => void api.messagePacks().then(setCatalog).catch((error) => notify(String(error)));
  useEffect(load, []);

  const showPreview = async (id: string) => {
    setBusy(id);
    try {
      setPreview(await api.previewMessagePack(id));
    } catch (error) {
      notify(error instanceof Error ? error.message : "文案包预览失败");
    } finally {
      setBusy(null);
    }
  };

  const importPack = async (id: string, mode: "merge" | "replace") => {
    if (mode === "replace" && !window.confirm("替换会覆盖当前文案库，原文件会先自动备份。确定继续吗？")) return;
    setBusy(id);
    try {
      const imported = await api.importMessagePack(id, mode);
      setResult(imported);
      notify(`文案包导入完成，本地共 ${imported.total_count} 条`);
    } catch (error) {
      notify(error instanceof Error ? error.message : "文案包导入失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="editor-page">
      <header className="page-header">
        <div><h1>文案包</h1><p>从 AutoDy 内置示例包导入本机，您的私人文案不会上传。</p></div>
        <button className="action-button" onClick={load}><RefreshCw size={17} />刷新列表</button>
      </header>
      <div className="pack-grid">
        {catalog?.packs.map((pack) => (
          <article className="panel pack-card" key={pack.id}>
            <span className="pack-icon"><Library size={22} /></span>
            <div className="pack-meta"><span>{categoryLabels[pack.category] || pack.category}</span><span>v{pack.version}</span><span>{pack.count} 条</span></div>
            <h2>{pack.name}</h2><p>{pack.description}</p>
            <div className="pack-actions">
              <button disabled={busy === pack.id} onClick={() => void showPreview(pack.id)}><Eye size={15} />预览</button>
              <button disabled={busy === pack.id} onClick={() => void importPack(pack.id, "merge")}><Download size={15} />合并导入</button>
              <button className="danger-outline" disabled={busy === pack.id} onClick={() => void importPack(pack.id, "replace")}><Download size={15} />替换导入</button>
            </div>
          </article>
        ))}
      </div>
      {result ? <div className="panel import-result"><strong>导入结果</strong><span>新增 {result.added_count} 条</span><span>重复 {result.duplicate_count} 条</span><span>本地共 {result.total_count} 条</span><small>备份：{result.backup_path || "未生成"}</small></div> : null}
      {preview ? <section className="panel pack-preview"><div className="panel-heading"><h2>{preview.pack.name} · 预览</h2><button className="text-button" onClick={() => setPreview(null)}>关闭</button></div><ol>{preview.messages.map((message) => <li key={message}>{message}</li>)}</ol></section> : null}
    </section>
  );
}

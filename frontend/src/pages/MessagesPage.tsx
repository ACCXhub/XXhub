import { CloudDownload, Download, Plus, Save, Search, Trash2, Upload, WandSparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { GlobalMessageLibrary, NativeStickerCatalog, NativeStickerDescriptor, NativeStickerReference } from "../types";

export function MessagesPage({
  notify,
  onNavigate
}: {
  notify: (message: string) => void;
  onNavigate: (view: "packs") => void;
}) {
  const [messages, setMessages] = useState<string[]>([]);
  const [library, setLibrary] = useState<GlobalMessageLibrary | null>(null);
  const [stickerCatalog, setStickerCatalog] = useState<NativeStickerCatalog | null>(null);
  const [stickerBusy, setStickerBusy] = useState(false);
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    void api.messages().then((data) => { setLibrary(data); setMessages(data.messages); }).catch((error) => notify(error instanceof Error ? error.message : "文案库加载失败"));
    void api.nativeStickerCatalog().then(setStickerCatalog).catch(() => setStickerCatalog(null));
  }, []);
  const visible = useMemo(
    () => messages.map((text, index) => ({ text, index })).filter(({ text }) => text.includes(query)),
    [messages, query]
  );
  const save = async () => {
    try {
      const result = await api.saveMessages(messages);
      setLibrary(result);
      setMessages(result.messages);
      notify(`文案库已保存，共 ${result.text_count} 条文字、${result.native_sticker_count} 个原生表情`);
    } catch (error) { notify(error instanceof Error ? error.message : "文案库保存失败"); }
  };
  const scanStickers = async () => {
    setStickerBusy(true);
    try { setStickerCatalog(await api.scanNativeStickers()); }
    catch (error) { notify(error instanceof Error ? error.message : "原生表情扫描失败"); }
    finally { setStickerBusy(false); }
  };
  const descriptorReference = (sticker: NativeStickerDescriptor): NativeStickerReference => ({
    logical_id: sticker.logical_id,
    display_name: sticker.display_name,
    resource_key: sticker.resource_key,
    machine_id: sticker.machine_id,
    accessible_name: sticker.accessible_name,
    category: sticker.category
  });
  const toggleGlobalSticker = async (sticker: NativeStickerDescriptor) => {
    if (!library || stickerBusy) return;
    const previous = library;
    const selectedIds = new Set(previous.selected_native_stickers.map((item) => item.logical_id));
    if (selectedIds.has(sticker.logical_id)) selectedIds.delete(sticker.logical_id);
    else selectedIds.add(sticker.logical_id);
    const currentById = new Map(previous.selected_native_stickers.map((item) => [item.logical_id, item]));
    const references = [...selectedIds].map((id) => currentById.get(id) || (
      id === sticker.logical_id ? descriptorReference(sticker) : null
    )).filter((item): item is NativeStickerReference => item !== null);
    setLibrary({
      ...previous,
      selected_native_stickers: references,
      native_sticker_count: references.length,
      total_count: previous.text_count + references.length
    });
    setStickerBusy(true);
    try {
      const result = await api.saveGlobalNativeStickers([...selectedIds]);
      setLibrary(result);
    } catch (error) {
      setLibrary(previous);
      notify(error instanceof Error ? error.message : "全局原生表情保存失败");
    } finally { setStickerBusy(false); }
  };
  const importMessages = async (file?: File) => {
    if (!file) return;
    try {
      const preview = await api.previewMessageImport(file);
      const detail = `预览：有效 ${preview.valid_entries} 条，精确重复 ${preview.exact_duplicates} 条，空行 ${preview.empty_entries} 条，超长 ${preview.overly_long_entries} 条，含链接 ${preview.entries_with_links} 条。\n确定以“合并”方式导入吗？`;
      if (!window.confirm(detail)) return;
      const result = await api.importMessages(file, "merge");
      const refreshed = await api.messages();
      setLibrary(refreshed);
      setMessages(refreshed.messages);
      notify(`文案导入完成：新增 ${result.imported} 条，重复 ${result.duplicated} 条`);
    } catch (error) { notify(error instanceof Error ? error.message : "文案导入失败"); }
  };
  const deduplicate = async () => {
    try { const result = await api.deduplicateMessages(); const refreshed = await api.messages(); setLibrary(refreshed); setMessages(refreshed.messages); notify(`已移除 ${result.removed} 条精确重复文案`); }
    catch (error) { notify(error instanceof Error ? error.message : "去重失败"); }
  };
  return (
    <section className="editor-page">
      <header className="page-header"><div><h1>文案库</h1><p>导入保持原始正文，不包含动态后缀；只会去除完全相同的文案。</p></div><div className="header-actions"><a className="action-button" href="/api/messages/export?format=txt"><Download size={17} />导出 TXT</a><input ref={input} type="file" hidden accept=".txt,.csv,.json" onChange={(event) => void importMessages(event.target.files?.[0])} /><button className="action-button" onClick={() => input.current?.click()}><Upload size={17} />导入文案</button><button className="action-button" onClick={() => void deduplicate()}><WandSparkles size={17} />精确去重</button><button className="action-button" onClick={() => onNavigate("packs")}><CloudDownload size={17} />从内置文案包导入</button><button className="action-button primary" onClick={save}><Save size={17} />保存文案库</button></div></header>
      <div className="toolbar panel">
        <label className="search-box"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文案…" /></label>
        <span>{messages.length} 条文案</span>
        <button className="text-button" onClick={() => setMessages(["", ...messages])}><Plus size={16} />新增文案</button>
      </div>
      <section className="panel global-pool-panel" aria-label="全局候选">
        <div className="panel-heading"><div><h2>全局随机候选</h2><p>{(library?.text_count ?? messages.length)} 条文字 · {library?.native_sticker_count ?? 0} 个原生表情 · 共 {library?.total_count ?? messages.length} 项</p></div></div>
        {library?.default_pack_overrides_global ? <p className="notice warning">当前默认文案包「{library.default_message_pack_name || library.default_message_pack}」正在覆盖全局文案库发送来源。</p> : <p className="notice success">当前使用全局文案库作为默认 fallback 来源。</p>}
      </section>
      <section className="panel global-sticker-panel" role="region" aria-label="全局原生表情">
        <div className="panel-heading"><div><h2>原生表情</h2><p>参与全局随机发送：{library?.native_sticker_count ?? 0} 个</p></div><button className="action-button" disabled={stickerBusy} onClick={() => void scanStickers()}>刷新原生表情</button></div>
        {stickerCatalog?.stickers.length ? <div className="sticker-chooser">{stickerCatalog.stickers.map((sticker) => {
          const selected = Boolean(library?.selected_native_stickers.some((item) => item.logical_id === sticker.logical_id));
          return <button key={sticker.logical_id} className={selected ? "selected" : ""} aria-label={`选择 ${sticker.display_name}`} aria-pressed={selected} disabled={stickerBusy || !library} onClick={() => void toggleGlobalSticker(sticker)}>{sticker.preview_url ? <img src={sticker.preview_url} alt="" /> : null}<span>{sticker.display_name}</span></button>;
        })}</div> : <p className="empty-list-copy">当前账号尚未扫描原生表情。</p>}
      </section>
      <div className="message-list">
        {visible.map(({ text, index }) => (
          <article className="message-row" key={index}>
            <span className="message-index">{String(index + 1).padStart(2, "0")}</span>
            <textarea value={text} rows={2} onChange={(event) => {
              const next = [...messages]; next[index] = event.target.value; setMessages(next);
            }} />
            <button className="icon-button danger" aria-label="删除文案" onClick={() => setMessages(messages.filter((_, position) => position !== index))}><Trash2 size={17} /></button>
          </article>
        ))}
      </div>
    </section>
  );
}

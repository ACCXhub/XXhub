import { CloudDownload, Download, Plus, Save, Search, Trash2, Upload, WandSparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";

export function MessagesPage({
  notify,
  onNavigate
}: {
  notify: (message: string) => void;
  onNavigate: (view: "packs") => void;
}) {
  const [messages, setMessages] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { void api.messages().then((data) => setMessages(data.messages)); }, []);
  const visible = useMemo(
    () => messages.map((text, index) => ({ text, index })).filter(({ text }) => text.includes(query)),
    [messages, query]
  );
  const save = async () => {
    const result = await api.saveMessages(messages);
    setMessages(result.messages);
    notify(`文案库已保存，共 ${result.messages.length} 条`);
  };
  const importMessages = async (file?: File) => {
    if (!file) return;
    try {
      const preview = await api.previewMessageImport(file);
      const detail = `预览：有效 ${preview.valid_entries} 条，精确重复 ${preview.exact_duplicates} 条，空行 ${preview.empty_entries} 条，超长 ${preview.overly_long_entries} 条，含链接 ${preview.entries_with_links} 条。\n确定以“合并”方式导入吗？`;
      if (!window.confirm(detail)) return;
      const result = await api.importMessages(file, "merge");
      setMessages((await api.messages()).messages);
      notify(`文案导入完成：新增 ${result.imported} 条，重复 ${result.duplicated} 条`);
    } catch (error) { notify(error instanceof Error ? error.message : "文案导入失败"); }
  };
  const deduplicate = async () => {
    try { const result = await api.deduplicateMessages(); setMessages((await api.messages()).messages); notify(`已移除 ${result.removed} 条精确重复文案`); }
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

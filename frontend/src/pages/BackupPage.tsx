import { Download, FileArchive, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api";
import type { BackupPreview } from "../types";

const categories = [
  ["friends", "好友配置"], ["messages", "本地文案"], ["suffix", "后缀设置"], ["schedule", "定时任务设置"],
  ["sending", "发送行为设置"], ["message_packs", "文案包选择"], ["settings", "非敏感应用设置"], ["rotation_state", "当天轮换状态（可选）"]
] as const;

export function BackupPage({ notify, onDataChanged }: { notify: (message: string) => void; onDataChanged?: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set(categories.slice(0, -1).map(([key]) => key)));
  const [preview, setPreview] = useState<BackupPreview | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const download = async () => {
    try {
      const blob = await api.exportBackup([...selected]);
      const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "autody-backup.zip"; link.click(); URL.revokeObjectURL(url);
      notify("已生成选择性安全备份，不包含登录资料和日志");
    } catch (error) { notify(error instanceof Error ? error.message : "备份导出失败"); }
  };
  const inspect = async (file?: File) => {
    if (!file) return;
    try { setPendingFile(file); setPreview(await api.previewBackup(file)); }
    catch (error) { notify(error instanceof Error ? error.message : "备份预检失败"); setPendingFile(null); }
  };
  const restore = async (mode: "merge" | "replace") => {
    if (!pendingFile) return;
    try { const result = await api.importBackup(pendingFile, mode); notify(`备份已导入：${result.targets.length} 位好友，${result.messages} 条文案（${mode === "merge" ? "合并" : "替换"}）`); setPreview(null); setPendingFile(null); onDataChanged?.(); }
    catch (error) { notify(error instanceof Error ? error.message : "备份恢复失败，原数据已保留"); }
  };
  return <section className="editor-page">
    <header className="page-header"><div><h1>备份迁移</h1><p>选择要打包的本地设置；不会导出浏览器资料、Cookie、令牌、日志或截图。</p></div></header>
    <div className="backup-grid">
      <article className="panel backup-card"><FileArchive size={32} /><h2>选择性导出</h2><div className="backup-options">{categories.map(([key, label]) => <label key={key}><input type="checkbox" checked={selected.has(key)} onChange={(event) => { const next = new Set(selected); if (event.target.checked) next.add(key); else next.delete(key); setSelected(next); }} />{label}</label>)}</div><button className="action-button primary" disabled={!selected.size} onClick={() => void download()}><Download size={17} />下载 ZIP 备份</button></article>
      <article className="panel backup-card"><Upload size={32} /><h2>预检并导入</h2><p>会先校验清单、校验和、文件路径与冲突；写入前自动创建本地回滚备份。</p><input ref={input} type="file" accept=".zip" hidden onChange={(event) => void inspect(event.target.files?.[0])} /><button className="action-button" onClick={() => input.current?.click()}><Upload size={17} />选择备份文件</button></article>
    </div>
    {preview ? <section className="panel import-result"><strong>导入预览 v{preview.package_version}</strong><span>好友 {preview.friend_count} 位</span><span>文案 {preview.message_count} 条</span><span>冲突 {preview.conflicts.length} 项</span><small>计划变更 {Object.keys(preview.schedule_changes).length} 项；后缀设置{preview.suffix_change ? "将变更" : "无变化"}。确认后再执行：</small><button className="action-button" onClick={() => void restore("merge")}>合并导入</button><button className="action-button primary" onClick={() => void restore("replace")}>替换导入</button></section> : null}
  </section>;
}

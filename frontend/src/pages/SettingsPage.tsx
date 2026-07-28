import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { AppConfig, OptionalModuleStatus } from "../types";

function suffixPreview(config: AppConfig) {
  const text = config.message_suffix.text.trim();
  if (!config.message_suffix.enabled || !text) return "你好";
  if (config.message_suffix.style === "dash") return `你好 —— ${text}`;
  if (config.message_suffix.style === "bracket") return `你好【${text}】`;
  if (config.message_suffix.style === "newline") return `你好\n${text}`;
  return `你好 ${text}`;
}

export function SettingsPage({ notify, onOpenTestCenter = () => undefined, onTestCenterStateChange = () => undefined }: { notify: (message: string) => void; onOpenTestCenter?: () => void; onTestCenterStateChange?: (installed: boolean) => void }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [testCenter, setTestCenter] = useState<OptionalModuleStatus | null>(null);
  useEffect(() => {
    void api.config().then(setConfig);
    void api.modules().then((result) => setTestCenter(result.modules.find((item) => item.id === "autody-test-center") ?? null));
  }, []);
  if (!config) return <div className="loading">加载设置…</div>;
  const save = async () => {
    if (config.min_delay_seconds > config.max_delay_seconds) return notify("最小间隔不能大于最大间隔");
    if (config.active_log_retention_days < 3) return notify("活跃日志保留天数不能少于 3 天");
    if (config.archive_log_retention_days < config.active_log_retention_days) return notify("归档日志保留天数不能小于活跃日志保留天数");
    try { setConfig(await api.saveConfig(config)); notify("运行设置已保存"); }
    catch (error) { notify(error instanceof Error ? error.message : "设置保存失败"); }
  };
  const installTestCenter = async (file?: File) => {
    try {
      const result = await api.installTestCenter(file);
      setTestCenter(result);
      onTestCenterStateChange(true);
      notify("测试中心已安装，可从设置中打开。");
    } catch (error) { notify(error instanceof Error ? error.message : "测试中心安装失败"); }
  };
  const uninstallTestCenter = async () => {
    if (!window.confirm("卸载会永久删除测试中心自己的测试历史和设置，不会影响好友、文案、发送记录或浏览器资料。确定继续吗？")) return;
    try { const result = await api.uninstallTestCenter(); setTestCenter(result); onTestCenterStateChange(false); notify("测试中心已卸载。"); }
    catch (error) { notify(error instanceof Error ? error.message : "测试中心卸载失败"); }
  };
  return (
    <section className="editor-page">
      <header className="page-header"><div><h1>设置</h1><p>控制延迟、超时、文案选择和本地通知；定时任务请在“定时任务”页配置。</p></div><button className="action-button primary" onClick={save}><Save size={17} />保存设置</button></header>
      <div className="panel settings-form">
        <label><span>失败重试次数<small>单个好友失败后的最大尝试次数</small></span><input type="number" min={1} max={5} value={config.retry_count} onChange={(event) => setConfig({ ...config, retry_count: Number(event.target.value) })} /></label>
        <label><span>页面加载超时（毫秒）<small>网络较慢时可以适当增加</small></span><input type="number" min={5000} max={120000} step={5000} value={config.page_load_timeout_ms ?? config.timeout_ms} onChange={(event) => { const value = Number(event.target.value); setConfig({ ...config, timeout_ms: value, page_load_timeout_ms: value }); }} /></label>
        <label><span>后台无窗口运行<small>关闭后可以看到自动化浏览器</small></span><input className="toggle" type="checkbox" checked={config.headless} onChange={(event) => setConfig({ ...config, headless: event.target.checked })} /></label>
        <label><span>好友间最小延迟（秒）<small>避免连续过快操作</small></span><input aria-label="最小延迟" type="number" min={0} max={60} step={0.5} value={config.min_delay_seconds ?? 1} onChange={(event) => setConfig({ ...config, min_delay_seconds: Number(event.target.value) })} /></label>
        <label><span>好友间最大延迟（秒）<small>必须不小于最小延迟</small></span><input aria-label="最大延迟" type="number" min={0} max={60} step={0.5} value={config.max_delay_seconds ?? 3} onChange={(event) => setConfig({ ...config, max_delay_seconds: Number(event.target.value) })} /></label>
        <label><span>好友搜索超时（毫秒）<small>找不到聊天时的最大等待时间</small></span><input type="number" min={5000} max={120000} step={5000} value={config.friend_search_timeout_ms ?? 30000} onChange={(event) => setConfig({ ...config, friend_search_timeout_ms: Number(event.target.value) })} /></label>
        <label><span>发送确认超时（毫秒）<small>未确认不会自动再次按发送键</small></span><input type="number" min={2000} max={60000} step={1000} value={config.confirmation_timeout_ms ?? 12000} onChange={(event) => setConfig({ ...config, confirmation_timeout_ms: Number(event.target.value) })} /></label>
        <label><span>好友顺序<small>随机顺序不会影响当天已完成记录</small></span><select value={config.friend_order ?? "configured"} onChange={(event) => setConfig({ ...config, friend_order: event.target.value as AppConfig["friend_order"] })}><option value="configured">配置顺序</option><option value="randomized">随机顺序</option></select></label>
        <label><span>文案选择<small>所有好友共用当天文案，或为每位好友独立选取</small></span><select value={config.message_selection ?? "one_for_all"} onChange={(event) => setConfig({ ...config, message_selection: event.target.value as AppConfig["message_selection"] })}><option value="one_for_all">当天同一条文案</option><option value="per_friend">每位好友独立选择</option></select></label>
        <label><span>Windows 完成通知<small>任务完成或失败时显示本地通知</small></span><input className="toggle" type="checkbox" checked={config.completion_notifications_enabled ?? true} onChange={(event) => setConfig({ ...config, completion_notifications_enabled: event.target.checked })} /></label>
        <label><span>登录检查后自动执行发送前自检<small>只检查聊天准备状态，不会输入或发送消息</small></span><input className="toggle" type="checkbox" checked={config.preflight_after_health_enabled ?? true} onChange={(event) => setConfig({ ...config, preflight_after_health_enabled: event.target.checked })} /></label>
        <label><span>日志保留天数<small>只影响后续管理，不会删除现有证据</small></span><input type="number" min={7} max={3650} value={config.log_retention_days ?? 30} onChange={(event) => setConfig({ ...config, log_retention_days: Number(event.target.value) })} /></label>
        <label><span>自动整理日志<small>每天首次打开管理台时在后台检查一次</small></span><input className="toggle" type="checkbox" checked={config.log_cleanup_enabled ?? true} onChange={(event) => setConfig({ ...config, log_cleanup_enabled: event.target.checked })} /></label>
        <label><span>活跃日志保留天数<small>最少 3 天；保存设置不会立刻删除文件</small></span><input aria-label="活跃日志保留天数" type="number" min={3} max={3650} value={config.active_log_retention_days ?? 14} onChange={(event) => setConfig({ ...config, active_log_retention_days: Number(event.target.value) })} /></label>
        <label><span>归档日志保留天数<small>不能少于活跃日志保留天数</small></span><input aria-label="归档日志保留天数" type="number" min={3} max={3650} value={config.archive_log_retention_days ?? 90} onChange={(event) => setConfig({ ...config, archive_log_retention_days: Number(event.target.value) })} /></label>
        <label><span>在管理台日志中脱敏好友名称<small>默认开启，不修改本地原始日志</small></span><input className="toggle" type="checkbox" checked={config.mask_log_friend_names} onChange={(event) => setConfig({ ...config, mask_log_friend_names: event.target.checked })} /></label>
        <label><span>启用消息后缀<small>发送时动态添加，不修改文案库原文</small></span><input className="toggle" type="checkbox" checked={config.message_suffix.enabled} onChange={(event) => setConfig({ ...config, message_suffix: { ...config.message_suffix, enabled: event.target.checked } })} /></label>
        <label><span>后缀文字<small>默认：gpt小助手</small></span><input aria-label="后缀文字" value={config.message_suffix.text} onChange={(event) => setConfig({ ...config, message_suffix: { ...config.message_suffix, text: event.target.value } })} /></label>
        <label><span>后缀样式<small>选择与正文的分隔方式</small></span><select aria-label="后缀样式" value={config.message_suffix.style} onChange={(event) => setConfig({ ...config, message_suffix: { ...config.message_suffix, style: event.target.value as AppConfig["message_suffix"]["style"] } })}><option value="dash">破折号</option><option value="bracket">方括号</option><option value="newline">换行</option><option value="none">空格</option></select></label>
        <label className="preview-setting"><span>发送预览<small>状态文件仍只记录“你好”基础文案</small></span><code>{suffixPreview(config)}</code></label>
        <label><span>GitHub 文案索引 URL<small>留空时使用内置文案包</small></span><input aria-label="GitHub 文案索引 URL" placeholder="https://raw.githubusercontent.com/.../index.json" value={config.message_pack_index_url || ""} onChange={(event) => setConfig({ ...config, message_pack_index_url: event.target.value || null })} /></label>
      </div>
      <section className="panel">
        <div className="panel-heading"><div><h2>可选模块</h2><small>官方模块默认不安装；卸载会永久删除模块自己的测试历史和设置。</small></div></div>
        {testCenter ? <article className="module-card"><div className="module-card-main"><div className="module-title"><strong>{testCenter.display_name}</strong><span className={`tag ${testCenter.compatible ? "success" : "failed"}`}>{testCenter.compatible ? "兼容当前 AutoDy" : "需要更新"}</span></div><p>用于只读预检、任务诊断和安全测试；默认不会安装，也不会参与正常发送。</p><dl className="module-facts"><div><dt>AutoDy 核心</dt><dd>{testCenter.core_version || "—"}</dd></div><div><dt>官方模块版本</dt><dd>{testCenter.bundled_version || "—"}</dd></div><div><dt>当前状态</dt><dd>{testCenter.installed ? `已安装 ${testCenter.version || "未知版本"}` : "当前未安装"}</dd></div><div><dt>核心兼容范围</dt><dd>{testCenter.required_autody_version || "≥ 1.3.0，< 2.0.0"}</dd></div></dl><p className="module-notice">模块版本独立于 AutoDy 核心版本，以兼容范围为准。</p>{testCenter.update_available ? <p className="module-notice">发现官方更新，可重新安装以更新到 {testCenter.bundled_version}。</p> : null}{!testCenter.compatible ? <p className="module-error">{testCenter.compatibility_reason || "模块与当前核心版本不兼容，请安装官方兼容包。"}</p> : null}{testCenter.load_error ? <p className="module-error">加载失败：{testCenter.load_error}。可重新安装官方模块包；若问题仍在，请导出脱敏诊断信息。</p> : null}</div><div className="module-actions">{testCenter.installed ? <><button className="action-button" disabled={!testCenter.compatible} onClick={onOpenTestCenter}>打开测试中心</button><label className="action-button">重新安装<input aria-label="选择测试中心模块包" type="file" accept=".zip,.autody-module.zip" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void installTestCenter(file); event.currentTarget.value = ""; }} /></label><button className="action-button danger" onClick={() => void uninstallTestCenter()}>卸载模块</button></> : <><button className="action-button primary" disabled={!testCenter.compatible} onClick={() => void installTestCenter()}>获取与安装</button><label className="action-button">选择官方 ZIP<input aria-label="选择测试中心模块包" type="file" accept=".zip,.autody-module.zip" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void installTestCenter(file); event.currentTarget.value = ""; }} /></label></>}</div></article> : <div className="empty-state compact">正在读取可选模块状态…</div>}
      </section>
    </section>
  );
}

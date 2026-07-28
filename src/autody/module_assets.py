"""Static, isolated assets for the optional read-only Test Center."""

TEST_CENTER_INDEX = """<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><link rel=\"stylesheet\" href=\"module.css\"></head>
<body><main id=\"root\"></main><script src=\"module.js\"></script></body></html>"""

TEST_CENTER_JS = r"""
const api = '/api/modules/autody-test-center';
const root = document.getElementById('root');
const moduleId = 'autody-test-center';
const defaults = {page_ready_delay_ms:1500, typing_delay_ms:80, typed_text_hold_ms:1500, clear_verify_delay_ms:500, target_switch_interval_seconds:8, navigation_timeout_seconds:12};
let state = {targets:[], settings:defaults, counters:{}};
let testText = '';
let requestRevision = 0;
let selectedTargetId = null;
let activeRunId = null;
let selectionPending = false;
let loadSequence = 0;
let latestAppliedSequence = 0;
let previewSequence = 0;
let previewTargetId = null;
let messageMode = 'today';
let runMode = 'single';
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const stageName = value => ({waiting:'等待中',opening_conversation:'正在打开聊天',verifying_identity:'正在核对身份',identity_mismatch:'会话不匹配',navigation_verified:'会话匹配',checking_existing_draft:'检测输入框',typing:'正在输入',observing:'等待观察',clearing:'正在清除',verifying_empty:'清除完成',completed:'测试完成',skipped_existing_draft:'检测到已有草稿，已跳过',skipped_existing_context:'检测到附件或回复，已跳过',cleanup_failed:'清除失败',stopped:'测试已停止',failed:'测试失败'}[value] || '等待中');
async function request(path, payload, method = 'POST') { const response = await fetch(api + path, {method, headers:{'Content-Type':'application/json'}, body:payload === undefined ? undefined : JSON.stringify(payload)}); if (!response.ok) throw new Error(await response.text() || '请求未完成'); return response.json(); }
function emptyCounters() { return {real_composer_writes:0, real_composer_clears:0, send_button_clicks:0, enter_key_presses:0, send_pipeline_calls:0, send_attempts:0, existing_drafts_preserved:0, cleanup_failures:0}; }
function clearRunState(targetId, revision) {
  const target = state.targets.find(item => item.target_id === targetId);
  return {...state, selected_target_id:targetId, expected_conversation_id:target?.conversation_id || null, selected_display_name:target?.display_name || null, visible_conversation_id:null, visible_display_name:null, identity_match:null, identity_match_reason:null, composer_status:'unknown', stage:'waiting', result:null, message:null, run_id:null, request_revision:revision, elapsed_seconds:0, mode:runMode, total_targets:0, current_position:0, completed_targets:0, passed_targets:0, skipped_targets:0, failed_targets:0, remaining_targets:0, results:[], resolved_test_text:null, counters:emptyCounters()};
}
function canApply(status, sequence) {
  if (sequence < latestAppliedSequence) return false;
  if (Number(status.request_revision || 0) < requestRevision) return false;
  if (activeRunId) {
    if (status.run_id !== activeRunId) return false;
    if (Number(status.request_revision || 0) !== requestRevision) return false;
  } else if (selectedTargetId && status.selected_target_id !== selectedTargetId) {
    return false;
  }
  return true;
}
function applyStatus(status, history, sequence) {
  if (!canApply(status, sequence)) return false;
  latestAppliedSequence = sequence;
  requestRevision = Number(status.request_revision || 0);
  selectedTargetId = status.selected_target_id || null;
  if (status.run_id) activeRunId = status.run_id;
  state = {...status, history:history?.items || state.history || []};
  runMode = status.mode || runMode;
  if (status.resolved_test_text !== null && status.resolved_test_text !== undefined) {
    testText = status.resolved_test_text;
    messageMode = 'today';
    previewTargetId = status.selected_target_id || previewTargetId;
  }
  render();
  if (selectedTargetId && messageMode === 'today' && previewTargetId !== selectedTargetId) {
    void loadTodayMessage(selectedTargetId);
  }
  return true;
}
async function load() {
  const sequence = ++loadSequence;
  const [status, history] = await Promise.all([request('/dry-run/status', undefined, 'GET'), request('/dry-run/history', undefined, 'GET')]);
  applyStatus(status, history, sequence);
}
async function selectTarget(targetId) {
  const revision = Math.max(requestRevision, Number(state.request_revision || 0)) + 1;
  selectedTargetId = targetId;
  requestRevision = revision;
  activeRunId = null;
  previewSequence += 1;
  previewTargetId = null;
  messageMode = 'today';
  testText = '';
  selectionPending = true;
  state = clearRunState(targetId, revision);
  render();
  try {
    const response = await request('/dry-run/select', {target_id:targetId, request_revision:revision});
    if (selectedTargetId !== targetId || requestRevision !== revision) return;
    selectionPending = false;
    applyStatus(response, {items:state.history || []}, ++loadSequence);
    await load();
  } catch (error) {
    if (selectedTargetId === targetId && requestRevision === revision) {
      selectionPending = false;
      showError(error);
    }
  }
}
async function loadTodayMessage(targetId) {
  if (!targetId) return;
  const sequence = ++previewSequence;
  try {
    const preview = await request(`/dry-run/message-preview?target_id=${encodeURIComponent(targetId)}`, undefined, 'GET');
    if (sequence !== previewSequence || selectedTargetId !== targetId) return;
    previewTargetId = targetId;
    messageMode = 'today';
    testText = preview.text || '';
    render();
  } catch (error) {
    if (sequence === previewSequence && selectedTargetId === targetId) showError(error);
  }
}
function resizeHost() { const height = Math.ceil(Math.max(document.documentElement.scrollHeight, document.body.scrollHeight, 560)); window.parent.postMessage({type:'autody-test-center:resize', moduleId, height}, window.location.origin); }
new ResizeObserver(resizeHost).observe(document.documentElement);
function selected() { return state.targets.find(item => item.target_id === state.selected_target_id); }
function targetOptions() { return state.targets.map(item => `<option value="${escapeHtml(item.target_id)}" ${item.target_id === state.selected_target_id ? 'selected' : ''}>${escapeHtml(item.display_name)}</option>`).join(''); }
function timingInput(key, label, suffix) { return `<label>${label}<span><input id="setting-${key}" type="number" value="${Number(state.settings?.[key] ?? defaults[key])}" required>${suffix}</span></label>`; }
const resultName = value => ({completed:'测试通过',batch_completed:'批量测试已完成',navigation_verified:'导航验证通过',skipped_existing_draft:'已跳过：存在草稿',skipped_existing_context:'已跳过：存在附件或回复',message_unavailable:'今日文案不可用',navigation_failed:'无法打开聊天',login_required:'需要登录',browser_busy:'浏览器忙',cleanup_failed:'清除失败',uncertain_composer:'输入框状态不确定',stopped:'用户停止',failed:'测试未完成'}[value] || '待执行');
const composerName = value => ({unknown:'尚未检查',empty:'输入框为空',test_text_present:'测试文本已输入',existing_draft_preserved:'已有内容，已保留',existing_attachment_preserved:'已有附件，已保留',changed:'内容发生变化',not_empty:'未清空'}[value] || '尚未检查');
function targetResultName(item) {
  if (item.identity_match === false) {
    return ['conversation_not_found','navigation_not_stable'].includes(item.identity_match_reason) ? '无法打开聊天' : '会话身份不匹配';
  }
  return resultName(item.result);
}
function historyRows(items) {
  return items.length ? items.slice(0,6).map(item => {
    const target = state.targets.find(candidate => candidate.target_id === item.target_id);
    return `<article><strong>${escapeHtml(target?.display_name || '已移除目标')}</strong><span>${escapeHtml(stageName(item.stage))} · ${escapeHtml(resultName(item.result))}</span><small>${escapeHtml(item.completed_at || item.started_at || '')}</small></article>`;
  }).join('') : '<p class="empty">尚无测试记录。</p>';
}
function resultRows(items) {
  return items.length ? items.map(item => `<article><strong>${escapeHtml(item.selected_display_name || '目标')}</strong><span>${escapeHtml(targetResultName(item))}</span><small>${Number(item.duration_seconds || 0).toFixed(1)} 秒 · ${escapeHtml(item.message || (item.result === 'completed' ? '测试文本已安全清除' : ''))}</small></article>`).join('') : '<p class="empty">本次运行尚无结果。</p>';
}
function render() {
  const current = selected(); const counters = state.counters || {}; const running = Boolean(state.running);
  root.innerHTML = `<div class="module-shell">
    <header class="module-bar"><div><p>测试中心 · 真实页面干跑</p><small>先核验会话身份，再检查输入框；不会发送消息。</small></div><button class="link danger" id="remove">卸载测试中心</button></header>
    ${state.recovery_warning ? `<p class="warning">${escapeHtml(state.recovery_warning)}</p>` : ''}
    <div class="dry-grid">
      <section class="control-column">
        <h2>测试控制</h2>
        <div class="mode-selector">
          <label class="check"><input type="radio" id="mode-single" name="run-mode" ${runMode === 'single' ? 'checked' : ''} ${running ? 'disabled' : ''}>单个测试</label>
          <label class="check"><input type="radio" id="mode-batch" name="run-mode" ${runMode === 'batch' ? 'checked' : ''} ${running ? 'disabled' : ''}>批量测试</label>
          <span>可安全批量测试 ${Number(state.eligible_target_count || 0)} 个目标</span>
        </div>
        <label>目标<select id="target" ${running || selectionPending ? 'disabled' : ''}><option value="">请选择</option>${targetOptions()}</select></label>
        <div class="button-row"><button class="secondary" id="previous">上一个</button><button class="secondary" id="next">下一个</button><button class="secondary" id="focus-browser">打开受管浏览器</button></div>
        <div class="message-mode">
          <label class="check"><input type="radio" id="use-today-message" name="message-mode" ${messageMode === 'today' ? 'checked' : ''}>使用今日文案</label>
          <label class="check"><input type="radio" id="use-custom-message" name="message-mode" ${messageMode === 'custom' ? 'checked' : ''}>自定义测试文本</label>
          <button class="link" id="reload-today-message">重新载入今日文案</button>
        </div>
        <label>测试文本<textarea id="test-text" rows="4" placeholder="仅在本次测试内存中保留，不会写入历史或日志。">${escapeHtml(testText)}</textarea></label>
        <div class="button-row"><button id="start-single" ${!current || !testText.trim() || running || selectionPending || state.recovery_warning ? 'disabled' : ''}>开始单个测试</button><button id="start-batch" ${!current || !Number(state.eligible_target_count || 0) || running || selectionPending || state.recovery_warning ? 'disabled' : ''}>开始批量测试</button><button class="secondary" id="pause" ${!running ? 'disabled' : ''}>暂停</button><button class="secondary" id="resume" ${!running ? 'disabled' : ''}>继续</button><button class="danger-button" id="stop" ${!running ? 'disabled' : ''}>安全停止</button></div>
        <h3>时间设置</h3><div class="timing-grid">${timingInput('page_ready_delay_ms','页面就绪',' ms')}${timingInput('typing_delay_ms','逐字输入',' ms')}${timingInput('typed_text_hold_ms','保留观察',' ms')}${timingInput('clear_verify_delay_ms','清除验证',' ms')}${timingInput('navigation_timeout_seconds','导航超时',' 秒')}</div>
        <div class="button-row"><button class="secondary" id="save-settings">保存设置</button><button class="link" id="restore-defaults">恢复默认值</button></div>
      </section>
      <section class="status-column">
        <h2>当前状态</h2>
        <div class="status-summary"><dl>
          <div><dt>当前模式</dt><dd>${state.mode === 'batch' ? '批量' : '单个'}</dd></div>
          <div><dt>当前进度</dt><dd>${Number(state.current_position || 0)} / ${Number(state.total_targets || 0)}</dd></div>
          <div><dt>当前目标</dt><dd>${escapeHtml(state.selected_display_name || current?.display_name || '未选择')}</dd></div>
          <div><dt>当前会话</dt><dd>${escapeHtml(state.visible_display_name || '尚未打开')}</dd></div>
          <div><dt>身份核验</dt><dd>${state.identity_match === true ? '会话匹配' : state.identity_match === false ? '不匹配' : '待核验'}</dd></div>
          <div><dt>当前阶段</dt><dd>${escapeHtml(stageName(state.stage))}</dd></div>
          <div><dt>输入框状态</dt><dd>${escapeHtml(composerName(state.composer_status))}</dd></div>
          <div><dt>测试结果</dt><dd>${escapeHtml(resultName(state.result))}</dd></div>
          <div><dt>已通过 / 已跳过 / 已失败</dt><dd>${Number(state.passed_targets || 0)} / ${Number(state.skipped_targets || 0)} / ${Number(state.failed_targets || 0)}</dd></div>
          <div><dt>剩余数量</dt><dd>${Number(state.remaining_targets || 0)}</dd></div>
          <div><dt>已用时间</dt><dd>${Number(state.elapsed_seconds || 0)} 秒</dd></div>
        </dl>${state.message ? `<p class="warning">${escapeHtml(state.message)}</p>` : ''}</div>
        <details class="diagnostics"><summary>诊断详情</summary><dl>
          <div><dt>selected_target_id</dt><dd>${escapeHtml(state.selected_target_id || '—')}</dd></div>
          <div><dt>expected_conversation_id</dt><dd>${escapeHtml(state.expected_conversation_id || '—')}</dd></div>
          <div><dt>visible_conversation_id</dt><dd>${escapeHtml(state.visible_conversation_id || '—')}</dd></div>
          <div><dt>selected_display_name</dt><dd>${escapeHtml(state.selected_display_name || '—')}</dd></div>
          <div><dt>visible_display_name</dt><dd>${escapeHtml(state.visible_display_name || '—')}</dd></div>
          <div><dt>identity_match</dt><dd>${String(state.identity_match ?? '—')}</dd></div>
          <div><dt>identity_match_reason</dt><dd>${escapeHtml(state.identity_match_reason || '—')}</dd></div>
          <div><dt>run_id</dt><dd>${escapeHtml(state.run_id || '—')}</dd></div>
          <div><dt>request_revision</dt><dd>${Number(state.request_revision || 0)}</dd></div>
          <div><dt>result</dt><dd>${escapeHtml(state.result || '—')}</dd></div>
        </dl></details>
        <h3>安全计数</h3><div class="counter-grid"><span>输入 ${Number(counters.real_composer_writes || 0)}</span><span>清除 ${Number(counters.real_composer_clears || 0)}</span><span>发送点击 ${Number(counters.send_button_clicks || 0)}</span><span>Enter ${Number(counters.enter_key_presses || 0)}</span><span>发送链路 ${Number(counters.send_pipeline_calls || 0)}</span><span>发送尝试 ${Number(counters.send_attempts || 0)}</span><span>草稿保留 ${Number(counters.existing_drafts_preserved || 0)}</span></div>
        <h3>本次运行结果</h3><div class="rows run-results">${resultRows(state.results || [])}</div>
        <h3>最近测试</h3><div class="rows">${historyRows(state.history || [])}</div>
      </section>
    </div>
    <div class="modal" id="remove-dialog" hidden><div class="dialog"><h2>卸载测试中心？</h2><p>测试历史和设置会被删除；正常好友、文案、发送记录和浏览器资料不会受影响。</p><div><button class="secondary" id="remove-cancel">取消</button><button class="danger-button" id="remove-confirm">卸载</button></div></div></div>
  </div>`;
  const target = document.getElementById('target');
  target.onchange = () => { if (target.value) void selectTarget(target.value); };
  const move = delta => { const index = state.targets.findIndex(item => item.target_id === target.value); const next = state.targets[index + delta]; if (next) void selectTarget(next.target_id); };
  const startRun = (navigationOnly, automatic = false) => {
    runMode = automatic ? 'batch' : 'single';
    if (automatic) messageMode = 'today';
    const runId = globalThis.crypto?.randomUUID?.() || `run-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    activeRunId = runId;
    state = {...clearRunState(target.value, requestRevision), running:true, run_id:runId};
    render();
    void request('/dry-run/start', {target_id:target.value, run_id:runId, request_revision:requestRevision, test_text:testText, use_today_message:automatic || messageMode === 'today', automatic, navigation_only:navigationOnly})
      .then(response => { if (activeRunId === runId) applyStatus(response, {items:state.history || []}, ++loadSequence); })
      .then(load)
      .catch(error => {
        if (activeRunId !== runId) return;
        activeRunId = null;
        state = {...state, running:false};
        showError(error);
        void load();
      });
  };
  document.getElementById('previous').onclick = () => move(-1); document.getElementById('next').onclick = () => move(1);
  document.getElementById('focus-browser').onclick = () => running
    ? void request('/dry-run/focus-browser', {}).catch(showError)
    : startRun(true, false);
  document.getElementById('mode-single').onchange = () => { runMode = 'single'; render(); };
  document.getElementById('mode-batch').onchange = () => { runMode = 'batch'; messageMode = 'today'; if (target.value) void loadTodayMessage(target.value); else render(); };
  document.getElementById('use-today-message').onchange = () => void loadTodayMessage(target.value);
  document.getElementById('use-custom-message').onchange = () => { previewSequence += 1; messageMode = 'custom'; render(); };
  document.getElementById('reload-today-message').onclick = () => void loadTodayMessage(target.value);
  document.getElementById('test-text').oninput = event => {
    previewSequence += 1;
    messageMode = 'custom';
    testText = event.currentTarget.value;
    document.getElementById('use-custom-message').checked = true;
  };
  document.getElementById('start-single').onclick = () => startRun(false, false);
  document.getElementById('start-batch').onclick = () => startRun(false, true);
  document.getElementById('pause').onclick = () => void request('/dry-run/pause', {}).then(load).catch(showError);
  document.getElementById('resume').onclick = () => void request('/dry-run/resume', {}).then(load).catch(showError);
  document.getElementById('stop').onclick = () => void request('/dry-run/stop', {}).then(load).catch(showError);
  document.getElementById('save-settings').onclick = () => { const settings = {}; Object.keys(defaults).forEach(key => { const input = document.getElementById(`setting-${key}`); if (input) settings[key] = Number(input.value); }); void request('/dry-run/settings', settings, 'PUT').then(load).catch(showError); };
  document.getElementById('restore-defaults').onclick = () => void request('/dry-run/settings', defaults, 'PUT').then(load).catch(showError);
  document.getElementById('remove').onclick = () => { document.getElementById('remove-dialog').hidden = false; resizeHost(); };
  document.getElementById('remove-cancel').onclick = () => { document.getElementById('remove-dialog').hidden = true; resizeHost(); };
  document.getElementById('remove-confirm').onclick = () => void request('/uninstall', {confirmed:true}).then(() => window.parent.postMessage({type:'autody-test-center:removed', moduleId}, window.location.origin)).catch(showError);
  resizeHost();
}
function showError(error) { state.message = '请求未完成，请检查当前状态后重试。'; render(); }
void load();
setInterval(() => { if (state.running) void load(); }, 800);
"""

TEST_CENTER_CSS = r"""
*{box-sizing:border-box}html,body{margin:0;overflow-y:hidden;background:transparent;color:#263750;font:13px/1.5 "Segoe UI","Microsoft YaHei",sans-serif}#root{width:100%;padding:0}.module-shell{width:100%;margin:0}.module-bar{min-height:55px;display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:22px}.module-bar p{margin:0;color:#0b1830;font-size:27px;font-weight:700;letter-spacing:-.04em}.module-bar small,.empty{color:#738097}.dry-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.control-column,.status-column{padding:18px 22px;border:1px solid #e1e7ef;border-radius:10px;background:#fff}.control-column h2,.status-column h2,.control-column h3,.status-column h3{margin:0 0 10px;color:#344761;font-size:14px}.control-column h3,.status-column h3{margin-top:16px;font-size:13px}label{display:grid;gap:4px;margin-top:9px;color:#65748a;font-size:12px}.button-row,.message-mode,.mode-selector{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}.message-mode,.mode-selector{padding:8px 9px;border-radius:7px;background:#f7f9fc}.mode-selector span{margin-left:auto;color:#65748a;font-size:12px}.check{display:flex;align-items:center;gap:5px;margin:0}.check input{width:auto}.timing-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.timing-grid label{margin:0}.timing-grid span{display:flex;align-items:center;gap:4px}select,input,textarea,button{font:inherit}select,input,textarea{width:100%;border:1px solid #ced8e5;border-radius:7px;padding:7px;color:#263750;background:#fff}textarea{display:block;resize:vertical}button{border:1px solid #1769e8;border-radius:8px;padding:7px 10px;color:#fff;background:#1769e8;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}.secondary,.link{border-color:#d2dce8;color:#3f526d;background:#fff}.link{border:0;padding:0;text-decoration:underline;text-underline-offset:3px}.danger{color:#a53845}.danger-button{border-color:#b83e4d;background:#b83e4d}.status-column dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:0}.status-column dl div{padding:7px 9px;border-radius:7px;background:#f7f9fc}.status-column dt{color:#7a8799;font-size:11px}.status-column dd{margin:2px 0 0;font-weight:650;word-break:break-word}.diagnostics{margin-top:10px;border-top:1px solid #eef2f6;padding-top:8px}.diagnostics summary{cursor:pointer;color:#65748a}.diagnostics dl{margin-top:8px}.counter-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.counter-grid span{padding:6px 8px;border-radius:6px;background:#f7f9fc;color:#55657c;font-size:12px}.rows article{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 10px;padding:7px 0;border-top:1px solid #eef2f6}.rows article:first-child{border-top:0}.rows small{grid-column:1/-1;color:#8490a0}.warning{margin:8px 0 0;padding:8px 10px;border-radius:7px;background:#fff4dc;color:#875b0d}.modal[hidden]{display:none}.modal{position:fixed;inset:0;display:grid;place-items:center;padding:18px;background:rgba(20,36,60,.42)}.dialog{width:min(430px,100%);padding:18px;border-radius:10px;background:#fff;box-shadow:0 12px 34px rgba(20,36,60,.22)}.dialog h2{margin:0;font-size:16px}.dialog p{color:#65748a}.dialog div{display:flex;justify-content:flex-end;gap:8px}@media(max-width:760px){.module-bar{margin-bottom:16px}.module-bar p{font-size:23px}.dry-grid{grid-template-columns:1fr}.timing-grid,.status-column dl,.counter-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""

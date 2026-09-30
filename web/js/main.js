/* ============================================================
   main.js —— 应用逻辑（状态、事件、流式对话）
   ============================================================ */
import { $, $$, ICONS, fmtTime, debounce } from './util.js';
import * as api from './api.js';
import * as ui from './ui.js';

const refs = {
  sessions: $('#sessions'),
  radar: $('#radar'),
  badges: $('#badges'),
  bars: $('#bars'),
  trend: $('#trend'),
  legend: $('#legend'),
  roadmap: $('#roadmap'),
  timeline: $('#timeline'),
  messages: $('#messages'),
  input: $('#input'),
  send: $('#send'),
  dot: $('#statusDot'),
  statusText: $('#statusText'),
  analysisDot: $('#analysisDot'),
  composerHint: $('#composerHint'),
  title: $('#sessionTitle')
};

const state = {
  config: null,
  sessions: [],
  session: null,
  streaming: false,
  controller: null,
  lastUserText: ''
};

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function boot() {
  wireStatic();
  $('#send').innerHTML = ICONS.send;
  $('#btnGear').innerHTML = ICONS.gear;
  $('#btnNew').innerHTML = ICONS.plus + '<span>新建对话</span>';
  $('#secRadarIcon').innerHTML = ICONS.radar;
  $('#secTrendIcon').innerHTML = ICONS.trend;
  $('#secRouteIcon').innerHTML = ICONS.route;
  $('#secHistIcon').innerHTML = ICONS.history;
  $('#secSessionsIcon').innerHTML = ICONS.chat;

  if (location.protocol === 'file:') {
    refs.composerHint.innerHTML = '<span class="warn">请通过 python server.py 启动后访问 http://127.0.0.1:8787（直接双击打开 HTML 无法连接服务）</span>';
  }

  try {
    state.config = await api.getConfig();
    await refreshSessions();
    if (state.sessions.length) {
      await openSession(state.sessions[0].id);
    } else {
      await createSession();
    }
    paintStatus();
  } catch (err) {
    setStatus('off', '服务未连接');
    refs.composerHint.innerHTML = `<span class="warn">${err.message}</span>`;
  }
}

function paintStatus() {
  const cfg = state.config;
  if (!cfg) return setStatus('off', '未连接');
  if (!cfg.api_key_set) return setStatus('off', '未配置 Key');
  setStatus('ok', cfg.model || '已就绪');
}

function setStatus(kind, text) {
  refs.dot.className = 'dot ' + kind;
  refs.statusText.textContent = text;
}

/* ------------------------------------------------------------------ */
/* 会话                                                                */
/* ------------------------------------------------------------------ */
async function refreshSessions() {
  const data = await api.listSessions();
  state.sessions = data.sessions || [];
  ui.renderSessionList(refs.sessions, state.sessions, state.session?.id);
}

async function openSession(id) {
  state.session = await api.getSession(id);
  refs.title.textContent = state.session.title || '未命名对话';
  ui.renderMessages(refs.messages, state.session);
  ui.renderPanel(refs, state.session);
  await refreshSessions();
}

async function createSession() {
  state.session = await api.createSession();
  refs.title.textContent = state.session.title || '新的对话';
  ui.renderMessages(refs.messages, state.session);
  ui.renderPanel(refs, state.session);
  await refreshSessions();
  refs.input.focus();
}

/* ------------------------------------------------------------------ */
/* 发送                                                                */
/* ------------------------------------------------------------------ */
function renderAssistantFallback() { /* 占位：无分析结果时保持原面板 */ }

async function send(textOverride) {
  const text = (textOverride ?? refs.input.value).trim();
  if (!text || state.streaming) return;

  if (!state.config?.api_key_set) {
    ui.toast('请先在「设置」中配置 API Key');
    openSettings();
    return;
  }
  if (!state.session) await createSession();

  state.lastUserText = text;
  ui.clearNotices(refs.messages);
  refs.input.value = '';
  autoGrow();
  setSendEnabled(false);

  ui.appendUserMessage(refs.messages, text);
  state.streaming = true;
  refs.analysisDot.className = 'dot idle';
  refs.analysisDot.title = '正在分析…';

  const botEl = ui.beginBotMessage(refs.messages);
  let accumulated = '';
  let gotError = false;

  state.controller = api.streamChat(
    { session_id: state.session.id, message: text, analyze: true },
    {
      meta(data) {
        if (data.session_id) state.session.id = data.session_id;
        if (data.title) { state.session.title = data.title; refs.title.textContent = data.title; }
      },
      delta(data) {
        accumulated += data.text || '';
        ui.updateBotMessage(botEl, accumulated, true);
        ui.scrollToBottom(refs.messages);
      },
      state(data) {
        if (!data.ok) return;
        if (data.analyzed) {
          refs.analysisDot.className = 'dot ok';
          refs.analysisDot.title = '评估已更新';
          // 合并进当前会话对象并重绘面板
          if (data.scores) state.session.scores = data.scores;
          if (data.roadmap) state.session.roadmap = data.roadmap;
          if (data.timeline) state.session.timeline = data.timeline;
          if (data.history) state.session.history = data.history;
          ui.renderPanel(refs, state.session);
        } else {
          // 静默降级：不打扰用户，只留一个不显眼的状态点
          refs.analysisDot.className = 'dot idle';
          refs.analysisDot.title = '本轮未产生新评估（面板沿用上一次结果）';
        }
      },
      error(data) {
        gotError = true;
        ui.appendNotice(refs.messages, {
          message: data.message || '请求失败',
          detail: data.detail || '',
          onRetry: () => send(state.lastUserText)
        });
      },
      done(data) {
        state.streaming = false;
        setSendEnabled(true);
        const pending = $('[data-role="pending"]', refs.messages);
        if (pending) pending.remove();
        if (accumulated) {
          ui.updateBotMessage(botEl, accumulated, false);
          state.session.messages = state.session.messages || [];
          state.session.messages.push({ role: 'assistant', content: accumulated });
        } else if (!gotError) {
          ui.appendNotice(refs.messages, {
            message: '模型没有返回任何内容，可以点「重新发送」再试一次。',
            onRetry: () => send(state.lastUserText)
          });
        }
        refreshSessions().catch(() => {});
        refs.input.focus();
        if (data?.ok === false) refs.analysisDot.className = 'dot idle';
      }
    }
  );
}

function setSendEnabled(enabled) {
  refs.send.disabled = !enabled;
  refs.input.disabled = false;
}

/* ------------------------------------------------------------------ */
/* 输入框                                                             */
/* ------------------------------------------------------------------ */
function autoGrow() {
  const el = refs.input;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

/* ------------------------------------------------------------------ */
/* 设置弹窗                                                            */
/* ------------------------------------------------------------------ */
function openSettings() {
  const cfg = state.config || {};
  $('#cfgBase').value = cfg.base_url || '';
  $('#cfgKey').value = '';
  $('#cfgKey').placeholder = cfg.api_key_set ? `已保存（${cfg.api_key_hint}），留空则不修改` : 'sk-...';
  $('#cfgModel').value = cfg.model || '';
  $('#cfgAnalyst').value = cfg.analyst_model || '';
  $('#cfgTemp').value = cfg.temperature ?? 0.7;
  $('#cfgJson').checked = cfg.json_mode !== false;
  $('#cfgAnalyze').checked = cfg.auto_analyze !== false;
  $('#cfgResult').className = 'result';
  $('#cfgResult').style.display = 'none';
  $('#overlay').style.display = 'grid';
}

function closeSettings() { $('#overlay').style.display = 'none'; }

async function saveSettings() {
  const patch = {
    base_url: $('#cfgBase').value.trim(),
    model: $('#cfgModel').value.trim(),
    analyst_model: $('#cfgAnalyst').value.trim(),
    temperature: Number($('#cfgTemp').value) || 0.7,
    json_mode: $('#cfgJson').checked,
    auto_analyze: $('#cfgAnalyze').checked
  };
  const key = $('#cfgKey').value.trim();
  if (key) patch.api_key = key;
  try {
    state.config = await api.saveConfig(patch);
    paintStatus();
    closeSettings();
    ui.toast('设置已保存');
  } catch (err) {
    showResult('error', err.message);
  }
}

function showResult(kind, message) {
  const el = $('#cfgResult');
  el.className = 'result ' + kind;
  el.style.display = 'block';
  el.textContent = message;
}

async function runTest() {
  const btn = $('#btnTest');
  btn.disabled = true;
  btn.textContent = '测试中…';
  try {
    const r = await api.testConnection();
    showResult(r.level === 'ok' ? 'ok' : r.level === 'warn' ? 'warn' : 'error',
      r.message + (r.detail ? `\n\n${String(r.detail).slice(0, 300)}` : ''));
  } catch (err) {
    showResult('error', err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '测试连接';
  }
}

async function removeCurrentSession() {
  if (!state.session) return;
  if (!confirm(`确定删除对话「${state.session.title || '未命名'}」及其全部评估数据？此操作不可撤销。`)) return;
  await api.deleteSession(state.session.id);
  closeSettings();
  state.session = null;
  await refreshSessions();
  if (state.sessions.length) await openSession(state.sessions[0].id);
  else await createSession();
  ui.toast('已删除');
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
function wireStatic() {
  refs.send.addEventListener('click', () => send());

  refs.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });
  refs.input.addEventListener('input', autoGrow);

  refs.sessions.addEventListener('click', (e) => {
    const item = e.target.closest('.session-item');
    if (!item) return;
    if (state.streaming) return ui.toast('顾问正在回复，请稍候…');
    openSession(item.dataset.id).catch((err) => ui.toast(err.message));
  });

  refs.messages.addEventListener('click', (e) => {
    const q = e.target.closest('[data-q]');
    if (q) send(q.dataset.q);
  });

  $('#btnNew').addEventListener('click', () => {
    if (state.streaming) return ui.toast('顾问正在回复，请稍候…');
    createSession().catch((err) => ui.toast(err.message));
  });

  $('#btnGear').addEventListener('click', openSettings);
  $('#btnClose').addEventListener('click', closeSettings);
  $('#btnSave').addEventListener('click', saveSettings);
  $('#btnTest').addEventListener('click', runTest);
  $('#btnDelete').addEventListener('click', removeCurrentSession);
  $('#overlay').addEventListener('click', (e) => { if (e.target.id === 'overlay') closeSettings(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSettings();
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); createSession(); }
  });

  // 标题双击重命名
  refs.title.addEventListener('dblclick', async () => {
    if (!state.session) return;
    const next = prompt('对话名称', state.session.title || '');
    if (!next || !next.trim()) return;
    await api.renameSession(state.session.id, next.trim());
    state.session.title = next.trim();
    refs.title.textContent = next.trim();
    refreshSessions().catch(() => {});
  });
}

window.addEventListener('DOMContentLoaded', boot);

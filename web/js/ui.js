/* ============================================================
   ui.js —— 视图渲染
   ============================================================ */
import { $, escapeHtml, renderMarkdown, fmtTime, phaseOf, ICONS } from './util.js';
import { radarSvg, trendSvg, legendHtml, QUADRANTS } from './charts.js';

/* ------------------------------------------------------------------ */
/* 会话列表                                                            */
/* ------------------------------------------------------------------ */
export function renderSessionList(container, sessions, activeId) {
  if (!sessions.length) {
    container.innerHTML = `<div class="session-empty">还没有对话。点「新建对话」开始。</div>`;
    return;
  }
  container.innerHTML = sessions.map((s) => {
    const active = s.id === activeId ? ' active' : '';
    const title = escapeHtml(s.title || '未命名');
    const preview = escapeHtml(s.preview || '（暂无内容）');
    return `<div class="session-item${active}" data-id="${s.id}" title="${title}">
      <div class="st">${title}</div>
      <div class="sp">${preview}</div>
      <div class="sd">${fmtTime(s.updated)}</div>
    </div>`;
  }).join('');
}

/* ------------------------------------------------------------------ */
/* 左侧评估面板                                                        */
/* ------------------------------------------------------------------ */
export function renderPanel(refs, session) {
  const scores = session?.scores || {};
  const timeline = session?.timeline || [];
  const roadmap = session?.roadmap || [];
  const history = session?.history || [];

  refs.radar.innerHTML = radarSvg(scores);

  refs.badges.innerHTML = QUADRANTS.map((q) => {
    const v = Number(scores[q.key] ?? 1);
    return `<div class="badge">
      <div class="bk">${q.short}</div>
      <div class="bv" style="color:${q.color}">L${v.toFixed(1)}</div>
    </div>`;
  }).join('');

  refs.bars.innerHTML = QUADRANTS.map((q) => {
    const v = Number(scores[q.key] ?? 1);
    const ph = phaseOf(v);
    return `<div class="bar-row">
      <div class="bl">
        <span class="bn">${q.name}</span>
        <span class="bval">L${v.toFixed(1)} · ${ph.name}</span>
      </div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.min((v / 3) * 100, 100)}%;background:${q.color}"></div></div>
    </div>`;
  }).join('');

  refs.trend.innerHTML = trendSvg(history);
  refs.legend.innerHTML = legendHtml();

  refs.roadmap.innerHTML = roadmap.length
    ? roadmap.map((item) => `<div class="roadmap-item"><span class="ri">${ICONS.target}</span><span class="md">${renderMarkdown(item)}</span></div>`).join('')
    : `<div class="empty-note">通过与顾问对话，你的专属破局路线将在此生成。</div>`;

  refs.timeline.innerHTML = timeline.length
    ? timeline.map((t) => `<div class="tl-item">
        <div class="tl-date">${escapeHtml(t.date || '')}</div>
        <div class="tl-text">${escapeHtml(t.content || '')}</div>
      </div>`).join('')
    : `<div class="empty-note">你的认知突破将在此自动归档。</div>`;
}

/* ------------------------------------------------------------------ */
/* 消息区                                                              */
/* ------------------------------------------------------------------ */
const avatarBot = '顾';
const avatarMe = '我';

export function welcomeHtml() {
  return `<div class="welcome">
    <div class="wt">开始建设你的 Human 3.0 人生体系</div>
    <div class="ws">顾问每次只问你一个最重要的问题，并根据你的回答继续追问。左侧的评估、路线与成长档案会随着对话自动更新。</div>
    <div class="wq">
      <button data-q="我想先聊聊我理想中的人生状态，和现在的生活之间的差距。">我想先聊聊理想人生和现实的差距</button>
      <button data-q="我总在某些事情上反复拖延，想找出背后的原因。">我总在某些事情上反复拖延</button>
      <button data-q="帮我梳理一下：我目前的收入结构存在什么问题？">帮我梳理我的收入结构问题</button>
    </div>
  </div>`;
}

export function messageHtml(role, content) {
  const isBot = role === 'assistant';
  return `<div class="msg ${isBot ? 'bot' : 'me'}">
    <div class="av">${isBot ? avatarBot : avatarMe}</div>
    <div class="bubble md">${renderMarkdown(content)}</div>
  </div>`;
}

export function renderMessages(container, session, { thinking = false } = {}) {
  const messages = (session?.messages || []).filter((m) => m.role === 'user' || m.role === 'assistant');
  if (!messages.length && !thinking) {
    container.innerHTML = welcomeHtml();
    return;
  }
  let html = messages.map((m) => messageHtml(m.role, m.content)).join('');
  if (thinking) {
    html += `<div class="msg bot" data-role="pending">
      <div class="av">${avatarBot}</div>
      <div class="bubble"><span class="thinking"><i></i><i></i><i></i></span></div>
    </div>`;
  }
  container.innerHTML = html;
  scrollToBottom(container);
}

export function beginBotMessage(container) {
  const pending = $('[data-role="pending"]', container);
  const el = pending || document.createElement('div');
  if (!pending) {
    el.className = 'msg bot';
    el.innerHTML = `<div class="av">${avatarBot}</div><div class="bubble md"></div>`;
    container.appendChild(el);
  } else {
    el.removeAttribute('data-role');
  }
  return el;
}

export function updateBotMessage(el, text, streaming = true) {
  const bubble = $('.bubble', el);
  if (!bubble) return;
  bubble.innerHTML = renderMarkdown(text) + (streaming ? '<span class="cursor-blink"></span>' : '');
}

export function appendUserMessage(container, text) {
  const welcome = $('.welcome', container);
  if (welcome) container.innerHTML = '';
  container.insertAdjacentHTML('beforeend', messageHtml('user', text));
  scrollToBottom(container);
}

export function scrollToBottom(container) {
  requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}

/* ------------------------------------------------------------------ */
/* 提示条（仅用于真正的错误，永不用于“评估失败”）                        */
/* ------------------------------------------------------------------ */
export function appendNotice(container, { message, detail = '', onRetry = null }) {
  const wrap = document.createElement('div');
  wrap.className = 'notice';
  wrap.innerHTML = `<span class="nx">!</span><div>
      <div>${escapeHtml(message)}${onRetry ? '<button class="retry">重新发送</button>' : ''}</div>
      ${detail ? `<pre>${escapeHtml(detail)}</pre>` : ''}
    </div>`;
  if (onRetry) $('.retry', wrap).addEventListener('click', () => { wrap.remove(); onRetry(); });
  container.appendChild(wrap);
  scrollToBottom(container);
  return wrap;
}

export function clearNotices(container) {
  container.querySelectorAll('.notice').forEach((n) => n.remove());
}

/* ------------------------------------------------------------------ */
/* 轻提示                                                              */
/* ------------------------------------------------------------------ */
let toastTimer = null;
export function toast(message) {
  document.querySelectorAll('.toast').forEach((t) => t.remove());
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = message;
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 2200);
}

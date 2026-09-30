/* ============================================================
   api.js —— 与本地服务通信（含 SSE 流式解析）
   ============================================================ */

async function jsonFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* 非 JSON */ }
  if (!res.ok) {
    const message = (data && (data.error || data.message)) || text.slice(0, 300) || `HTTP ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    err.detail = data && data.detail;
    throw err;
  }
  return data;
}

export const getConfig = () => jsonFetch('/api/config');

export const saveConfig = (patch) =>
  jsonFetch('/api/config', { method: 'POST', body: JSON.stringify(patch) });

export const testConnection = () => jsonFetch('/api/test');

export const listSessions = () => jsonFetch('/api/sessions');

export const createSession = () =>
  jsonFetch('/api/sessions', { method: 'POST', body: '{}' });

export const getSession = (id) => jsonFetch(`/api/sessions/${encodeURIComponent(id)}`);

export const renameSession = (id, title) =>
  jsonFetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title })
  });

export const deleteSession = (id) =>
  jsonFetch(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });

/**
 * 发送消息并消费 SSE 流。
 * handlers: { meta, delta, state, error, done }
 * 返回一个可 abort 的控制器。
 */
export function streamChat(payload, handlers = {}) {
  const controller = new AbortController();

  (async () => {
    let res;
    try {
      res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
    } catch (err) {
      if (err.name !== 'AbortError') {
        handlers.error?.({ message: `无法连接本地服务：${err.message}。请确认 server.py 正在运行。` });
        handlers.done?.({ ok: false });
      }
      return;
    }

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      let msg = text.slice(0, 300);
      try { msg = JSON.parse(text).error || msg; } catch { /* ignore */ }
      handlers.error?.({ message: msg || `HTTP ${res.status}` });
      handlers.done?.({ ok: false });
      return;
    }
    if (!res.body) {
      handlers.error?.({ message: '当前浏览器不支持流式读取响应。' });
      handlers.done?.({ ok: false });
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    const dispatch = (raw) => {
      let event = 'message';
      let dataStr = '';
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
      }
      if (!dataStr) return;
      let data = null;
      try { data = JSON.parse(dataStr); } catch { return; }
      handlers[event]?.(data);
    };

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          dispatch(chunk);
        }
      }
      if (buffer.trim()) dispatch(buffer);
    } catch (err) {
      if (err.name !== 'AbortError') {
        handlers.error?.({ message: `连接中断：${err.message}` });
        handlers.done?.({ ok: false });
      }
    }
  })();

  return controller;
}

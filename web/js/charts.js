/* ============================================================
   charts.js —— 纯 SVG 图表：四象限雷达图 + 等级成长曲线
   ============================================================ */
import { clamp } from './util.js';

export const QUADRANTS = [
  { key: 'mind',     name: '心智 Mind',     short: '心智', color: '#3b82f6' },
  { key: 'body',     name: '身体 Body',     short: '身体', color: '#10b981' },
  { key: 'spirit',   name: '精神 Spirit',   short: '精神', color: '#8b5cf6' },
  { key: 'vocation', name: '事业 Vocation', short: '事业', color: '#f59e0b' }
];

/* ------------------------------------------------------------------ */
/* 雷达图                                                              */
/* ------------------------------------------------------------------ */
const RC = 120, RR = 76;
const polar = (ratio, i) => {
  const ang = (-90 + i * 90) * Math.PI / 180;
  return { x: RC + RR * ratio * Math.cos(ang), y: RC + RR * ratio * Math.sin(ang) };
};
const ring = (ratio) => [0, 1, 2, 3]
  .map((i) => { const p = polar(ratio, i); return `${p.x.toFixed(1)},${p.y.toFixed(1)}`; })
  .join(' ');

export function radarSvg(scores) {
  const values = QUADRANTS.map((q) => clamp(scores?.[q.key] ?? 1));

  const rings = [1 / 3, 2 / 3, 1].map((r, idx) =>
    `<polygon points="${ring(r)}" fill="none" stroke="#2f3542" stroke-width="1"${idx < 2 ? ' stroke-dasharray="3 3"' : ''}/>`
  ).join('');

  const axes = QUADRANTS.map((_, i) => {
    const p = polar(1, i);
    return `<line x1="${RC}" y1="${RC}" x2="${p.x.toFixed(1)}" y2="${p.y.toFixed(1)}" stroke="#2f3542" stroke-width="1"/>`;
  }).join('');

  const marks = [1, 2, 3].map((lv) => {
    const p = polar(lv / 3, 0);
    return `<text x="${(p.x + 5).toFixed(1)}" y="${(p.y + 3).toFixed(1)}" fill="#5b6678" font-size="9" font-family="ui-monospace, monospace">L${lv}</text>`;
  }).join('');

  const points = values.map((v, i) => {
    const p = polar(v / 3, i);
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(' ');

  const dots = values.map((v, i) => {
    const p = polar(v / 3, i);
    return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4" fill="${QUADRANTS[i].color}" stroke="#0e1015" stroke-width="2"/>`;
  }).join('');

  const labels = QUADRANTS.map((q, i) => {
    const p = polar(1.28, i);
    return `<text x="${p.x.toFixed(1)}" y="${(p.y + 4).toFixed(1)}" fill="${q.color}" font-size="11" font-weight="600" text-anchor="middle">${q.short}</text>`;
  }).join('');

  return `<svg viewBox="0 0 240 240" role="img" aria-label="四象限雷达图">
    ${rings}${axes}${marks}
    <polygon points="${points}" fill="rgba(59,130,246,.18)" stroke="#60a5fa" stroke-width="2" stroke-linejoin="round"
             style="transition:all .7s cubic-bezier(.4,0,.2,1)"/>
    ${dots}${labels}
  </svg>`;
}

/* ------------------------------------------------------------------ */
/* 成长曲线：四条折线，横轴为对话轮次                                    */
/* ------------------------------------------------------------------ */
export function trendSvg(history) {
  const pts = (history || []).filter((h) => h && h.scores);
  const W = 320, H = 96, PL = 4, PR = 4, PT = 8, PB = 16;
  const innerW = W - PL - PR, innerH = H - PT - PB;

  if (pts.length === 0) {
    return `<svg viewBox="0 0 ${W} ${H}">
      <text x="${W / 2}" y="${H / 2}" fill="#4d5666" font-size="11" text-anchor="middle">开始对话后，这里会记录你的成长曲线</text>
    </svg>`;
  }

  const n = pts.length;
  const x = (i) => (n === 1 ? PL + innerW / 2 : PL + (innerW * i) / (n - 1));
  const y = (v) => PT + innerH * (1 - (clamp(v) - 1) / 2);

  // L1 / L2 / L3 参考线
  const guides = [1, 2, 3].map((lv) => {
    const yy = y(lv).toFixed(1);
    return `<line x1="${PL}" y1="${yy}" x2="${W - PR}" y2="${yy}" stroke="#242933" stroke-width="1" stroke-dasharray="3 4"/>
            <text x="${W - PR}" y="${(Number(yy) - 2).toFixed(1)}" fill="#454e5d" font-size="8" text-anchor="end" font-family="ui-monospace, monospace">L${lv}</text>`;
  }).join('');

  const lines = QUADRANTS.map((q) => {
    const d = pts.map((h, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(h.scores[q.key]).toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${q.color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" opacity=".9"/>`;
  }).join('');

  const last = pts[n - 1];
  const dots = QUADRANTS.map((q) => {
    const cx = x(n - 1).toFixed(1), cy = y(last.scores[q.key]).toFixed(1);
    return `<circle cx="${cx}" cy="${cy}" r="2.6" fill="${q.color}"/>`;
  }).join('');

  const axisLabel = `<text x="${PL}" y="${H - 3}" fill="#454e5d" font-size="8.5">第 1 轮</text>
    <text x="${W - PR}" y="${H - 3}" fill="#454e5d" font-size="8.5" text-anchor="end">第 ${n} 轮</text>`;

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="等级成长曲线">
    ${guides}${lines}${dots}${axisLabel}
  </svg>`;
}

export function legendHtml() {
  return QUADRANTS.map((q) =>
    `<span><i style="background:${q.color}"></i>${q.short}</span>`
  ).join('');
}

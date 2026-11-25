// graph.js (화면, v7.6) — 조회 결과를 그래프로 한눈에
//   1) 기준 비교: 지표마다 본년과 고른 비교 기준(전년·최근 3년·최근 5년·평년)을 막대(기온은 점)로 비교
//   2) 기간 흐름: 고른 지표 하나를 날짜별 선으로 — 기온은 일별(7일 평균 선택), 강수·일조·일수는 누적
// 모두 '선택 지점 평균' 값이며, 서버가 계산해 보낸 값을 그리기만 합니다(계산 없음).
import { isOn } from "./bases.js";

const NS = "http://www.w3.org/2000/svg";
// 색은 항목(본년·전년…)을 따라가며, 고른 기준이 바뀌어도 바뀌지 않음
const SERIES = [
  { key: "cur", cls: "g-cur" },
  { key: "prev", cls: "g-prev", base: "prev" },
  { key: "r3", cls: "g-r3", base: "r3" },
  { key: "r5", cls: "g-r5", base: "r5" },
  { key: "normal", cls: "g-normal", base: "normal", dash: true },
];
const TEMP_KIND = new Set(["평균 기온", "최고 기온", "최저 기온"]);

let state = { res: null, metric: null, smooth: true };
let resizeTimer = null;

// ---------------------------------------------------------------- 공통
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  kids.flat().forEach((c) => c && e.append(c));
  return e;
}
function s(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) e.setAttribute(k, v);
  return e;
}
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "-" : Number(v).toFixed(d));
const md = (iso) => { const [, m, d] = iso.split("-"); return `${+m}/${+d}`; };

function labels(res) {
  const s0 = res.summary || {};
  const ch = res.chart || {};
  return {
    cur: `본년(${s0.currentYear || ""})`,
    prev: `전년(${s0.prevYear || ch.prevYear || ""})`,
    r3: `최근 3년(${s0.recent3Range || ch.recent3Range || ""})`,
    r5: `최근 5년(${s0.recentRange || ch.recentRange || ""})`,
    normal: "평년(1991~2020)",
  };
}
const shortLabel = { cur: "본년", prev: "전년", r3: "최근 3년", r5: "최근 5년", normal: "평년" };
const visible = () => SERIES.filter((x) => !x.base || isOn(x.base));

function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) { hi = lo + 1; }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) || raw;
  const a = Math.floor(lo / step) * step;
  const b = Math.ceil(hi / step) * step;
  const out = [];
  for (let v = a; v <= b + step / 2; v += step) out.push(+v.toFixed(6));
  return out;
}

// ---------------------------------------------------------------- 1) 기준 비교
function compareCard(name, unit, vals, lab) {
  const rows = visible().map((x) => ({ ...x, v: vals[x.key] })).filter((r) => r.v !== null && r.v !== undefined);
  const card = h("div", { class: "g-card" }, h("h4", { text: `${name} (${unit})` }));
  if (rows.length < 2) { card.append(h("p", { class: "muted small", text: "비교할 값이 없습니다." })); return card; }
  const W = 320, rowH = 30, labW = 66, valW = 92, top = 4;
  const H = top + rows.length * rowH + 18;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "g-svg", role: "img",
    "aria-label": `${name} 기준 비교: ` + rows.map((r) => `${shortLabel[r.key]} ${fmt(r.v)}${unit}`).join(", ") });
  const plotL = labW, plotR = W - valW;
  const vs = rows.map((r) => r.v);
  const isTemp = TEMP_KIND.has(name);
  let lo, hi;
  if (isTemp) { const pad = Math.max(0.5, (Math.max(...vs) - Math.min(...vs)) * 0.25); lo = Math.min(...vs) - pad; hi = Math.max(...vs) + pad; }
  else { lo = 0; hi = Math.max(...vs) * 1.08 || 1; }
  const ticks = niceTicks(lo, hi, 4).filter((t) => t >= lo - 1e-9 && t <= hi + 1e-9);
  const x = (v) => plotL + ((v - lo) / (hi - lo)) * (plotR - plotL);
  ticks.forEach((t) => {
    svg.append(s("line", { x1: x(t), x2: x(t), y1: top, y2: H - 16, class: "g-grid" }));
    const tx = s("text", { x: x(t), y: H - 4, class: "g-tick", "text-anchor": "middle" }); tx.textContent = fmt(t, Number.isInteger(t) ? 0 : 1); svg.append(tx);
  });
  const cur = vals.cur;
  rows.forEach((r, i) => {
    const cy = top + i * rowH + rowH / 2;
    const lt = s("text", { x: 0, y: cy + 4, class: "g-label" + (r.key === "cur" ? " strong" : "") }); lt.textContent = shortLabel[r.key]; svg.append(lt);
    const g = s("g", { class: `g-mark ${r.cls}` });
    if (isTemp) {
      g.append(s("line", { x1: plotL, x2: plotR, y1: cy, y2: cy, class: "g-track" }));
      g.append(s("circle", { cx: x(r.v), cy, r: 6, class: "g-dot" }));
    } else {
      const x0 = x(0), x1 = Math.max(x(r.v), x0 + 1), bh = 14, rr = Math.min(4, (x1 - x0) / 2);
      g.append(s("path", { class: "g-bar", d: `M${x0},${cy - bh / 2} H${x1 - rr} Q${x1},${cy - bh / 2} ${x1},${cy - bh / 2 + rr} V${cy + bh / 2 - rr} Q${x1},${cy + bh / 2} ${x1 - rr},${cy + bh / 2} H${x0} Z` }));
    }
    const tip = s("title"); tip.textContent = `${lab[r.key]}: ${fmt(r.v)}${unit}`; g.append(tip);
    svg.append(g);
    const vt = s("text", { x: W, y: cy + 4, class: "g-value", "text-anchor": "end" });
    const diff = r.key !== "cur" && cur !== null && cur !== undefined ? cur - r.v : null;
    vt.textContent = `${fmt(r.v)}` + (diff === null ? "" : ` (${diff >= 0 ? "+" : "−"}${fmt(Math.abs(diff))})`);
    svg.append(vt);
  });
  card.append(svg);
  card.append(h("p", { class: "g-note", text: isTemp ? "괄호: 본년과의 차이(본년 − 기준)" : "괄호: 본년과의 차이(본년 − 기준), 막대는 0부터" }));
  return card;
}

function renderCompare(res, host) {
  const avg = res.stationReport && res.stationReport.average;
  const units = (res.stationReport && res.stationReport.units) || {};
  if (!avg) return;
  const lab = labels(res);
  const grid = h("div", { class: "g-grid-cards" });
  res.params.metrics.filter((m) => avg[m]).forEach((m) => grid.append(compareCard(m, units[m] || "", avg[m], lab)));
  host.append(h("section", { class: "g-section" },
    h("h3", { class: "g-title", text: "기준 비교 — 기간 평균·합계" }), grid));
}

// ---------------------------------------------------------------- 2) 기간 흐름
function movingAvg(arr, win = 7) {
  const half = Math.floor(win / 2);
  return arr.map((_, i) => {
    const part = arr.slice(Math.max(0, i - half), i + half + 1).filter((v) => v !== null && v !== undefined);
    return part.length >= Math.ceil(win / 2) && arr[i] !== null ? part.reduce((a, b) => a + b, 0) / part.length : null;
  });
}

function lineChart(res, name, wrap) {
  const ch = res.chart;
  const m = ch.metrics[name];
  const lab = labels(res);
  const daily = m.mode === "daily";
  const series = visible().map((x) => {
    let vals = m[x.key] || [];
    if (daily && state.smooth) vals = movingAvg(vals);
    return { ...x, vals };
  }).filter((x) => x.vals.some((v) => v !== null && v !== undefined));
  const n = ch.dates.length;
  const W = Math.max(300, Math.round(wrap.clientWidth || 600));
  const H = W < 520 ? 240 : 300;
  const pad = { l: 44, r: 12, t: 10, b: 26 };
  const all = series.flatMap((x) => x.vals).filter((v) => v !== null && v !== undefined);
  if (!all.length) { wrap.replaceChildren(h("p", { class: "muted small", text: "그릴 자료가 없습니다." })); return; }
  let lo = Math.min(...all), hi = Math.max(...all);
  if (!daily) lo = 0;
  else { const p = Math.max(0.5, (hi - lo) * 0.08); lo -= p; hi += p; }
  const ticks = niceTicks(lo, hi, 5);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
  const x = (i) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "g-svg g-line", role: "img",
    "aria-label": `${name} 기간 흐름 그래프` });
  ticks.forEach((t) => {
    svg.append(s("line", { x1: pad.l, x2: W - pad.r, y1: y(t), y2: y(t), class: "g-grid" }));
    const tx = s("text", { x: pad.l - 6, y: y(t) + 4, class: "g-tick", "text-anchor": "end" }); tx.textContent = fmt(t, Number.isInteger(t) ? 0 : 1); svg.append(tx);
  });
  const nx = Math.min(n, W < 520 ? 4 : 7);
  for (let k = 0; k < nx; k++) {
    const i = nx <= 1 ? 0 : Math.round((k / (nx - 1)) * (n - 1));
    const tx = s("text", { x: x(i), y: H - 6, class: "g-tick", "text-anchor": k === 0 ? "start" : k === nx - 1 ? "end" : "middle" });
    tx.textContent = md(ch.dates[i]); svg.append(tx);
  }
  // 평년 → 최근 5년 → 최근 3년 → 전년 → 본년 순으로 그려 본년이 맨 위
  [...series].reverse().forEach((sr) => {
    let d = "", pen = false;
    sr.vals.forEach((v, i) => {
      if (v === null || v === undefined) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
    });
    svg.append(s("path", { d, class: `g-path ${sr.cls}${sr.dash ? " dash" : ""}${sr.key === "cur" ? " main" : ""}` }));
  });
  // 마우스·터치 안내선과 값 상자
  const cross = s("line", { y1: pad.t, y2: H - pad.b, class: "g-cross", visibility: "hidden" });
  svg.append(cross);
  const dots = series.map((sr) => { const c = s("circle", { r: 4, class: `g-hdot ${sr.cls}`, visibility: "hidden" }); svg.append(c); return c; });
  const hit = s("rect", { x: pad.l, y: 0, width: W - pad.l - pad.r, height: H, fill: "transparent" });
  svg.append(hit);
  const tip = h("div", { class: "g-tip", hidden: true });
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    const lines = [];
    series.forEach((sr, k) => {
      const v = sr.vals[i];
      if (v === null || v === undefined) { dots[k].setAttribute("visibility", "hidden"); return; }
      dots[k].setAttribute("cx", x(i)); dots[k].setAttribute("cy", y(v)); dots[k].setAttribute("visibility", "visible");
      lines.push(h("div", { class: "g-tip-row" }, h("span", { class: `g-sw ${sr.cls}${sr.dash ? " dash" : ""}` }),
        h("span", { text: shortLabel[sr.key] }), h("b", { text: `${fmt(v)}${m.unit}` })));
    });
    const curV = (series.find((x0) => x0.key === "cur") || {}).vals?.[i];
    const nrmV = (series.find((x0) => x0.key === "normal") || series.find((x0) => x0.key === "r5") || {});
    const refV = nrmV.vals?.[i];
    const extra = curV != null && refV != null
      ? h("div", { class: "g-tip-foot", text: `본년 − ${shortLabel[nrmV.key]}: ${curV - refV >= 0 ? "+" : "−"}${fmt(Math.abs(curV - refV))}${m.unit}` }) : null;
    tip.replaceChildren(h("div", { class: "g-tip-date", text: `${md(ch.dates[i])}${daily ? (state.smooth ? " (7일 평균)" : "") : "까지 누적"}` }), ...lines, extra);
    tip.hidden = false;
    const left = (x(i) / W) * r.width;
    tip.style.left = `${Math.min(Math.max(left + 12, 0), r.width - 190)}px`;
    if (left + 12 + 190 > r.width) tip.style.left = `${Math.max(left - 202, 0)}px`;
  };
  const leave = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", leave);

  const legend = h("div", { class: "g-legend" }, ...series.map((sr) =>
    h("span", { class: "g-leg" }, h("span", { class: `g-sw ${sr.cls}${sr.dash ? " dash" : ""}` }), h("span", { text: lab[sr.key] }))));
  wrap.replaceChildren(legend, h("div", { class: "g-plot" }, svg, tip));
}

function flowSummary(res, name) {
  const avg = res.stationReport && res.stationReport.average && res.stationReport.average[name];
  const unit = (res.chart.metrics[name] || {}).unit || "";
  if (!avg || avg.cur == null) return null;
  const ref = isOn("normal") && avg.normal != null ? ["평년", avg.normal] : isOn("r5") && avg.r5 != null ? ["최근 5년", avg.r5]
    : isOn("prev") && avg.prev != null ? ["전년", avg.prev] : null;
  const kind = res.chart.metrics[name].mode === "daily" ? "기간 평균" : "기간 합계";
  let txt = `${kind}: 본년 ${fmt(avg.cur)}${unit}`;
  if (ref) {
    const d = avg.cur - ref[1];
    txt += ` · ${ref[0]} ${fmt(ref[1])}${unit} (${d >= 0 ? "+" : "−"}${fmt(Math.abs(d))}${unit}`
      + (res.chart.metrics[name].mode !== "daily" && ref[1] ? `, ${Math.round((avg.cur / ref[1]) * 100)}%` : "") + ")";
  }
  return h("p", { class: "g-summary", text: txt });
}

function renderFlow(res, host) {
  const ch = res.chart;
  if (!ch || !ch.metrics) return;
  const names = res.params.metrics.filter((m) => ch.metrics[m]);
  if (!names.length) return;
  if (!names.includes(state.metric)) state.metric = names[0];
  const wrap = h("div", { class: "g-wrap" });
  const chips = h("div", { class: "chips g-chips", role: "tablist" }, ...names.map((m) => h("button", {
    type: "button", class: "chip" + (m === state.metric ? " active" : ""), role: "tab", text: m,
    onclick: () => { state.metric = m; renderGraph(res); },
  })));
  const daily = ch.metrics[state.metric].mode === "daily";
  const opts = daily ? h("label", { class: "switch g-smooth" },
    h("input", { type: "checkbox", ...(state.smooth ? { checked: true } : {}), onchange: (e) => { state.smooth = e.target.checked; renderGraph(res); } }),
    h("span", { class: "switch-ui" }), h("span", { text: "7일 평균으로 부드럽게" })) : null;
  const note = daily
    ? "날짜별 선택 지점 평균값입니다. 마우스를 올리거나 눌러 날짜별 값을 확인하세요."
    : "기간 첫날부터 날짜별로 더해 간 누적값입니다. 본년 선이 평년 선보다 아래로 벌어지면 부족, 위로 벌어지면 많음을 뜻합니다.";
  host.append(h("section", { class: "g-section" },
    h("h3", { class: "g-title", text: "기간 흐름 — 날짜별" + (daily ? "" : " 누적") }),
    chips, h("div", { class: "g-row" }, flowSummary(res, state.metric), opts), wrap,
    h("p", { class: "g-note", text: note })));
  requestAnimationFrame(() => lineChart(res, state.metric, wrap));
}

// ---------------------------------------------------------------- 진입점
export function renderGraph(res) {
  const host = document.getElementById("graph-panel");
  if (!host) return;
  state.res = res;
  host.replaceChildren();
  if (!res || !res.ok) return;
  if (!res.chart) {
    host.append(h("p", { class: "muted small", text: "이 결과에는 그래프 자료가 없습니다. 다시 조회하면 표시됩니다." }));
    return;
  }
  const names = (res.params.stations || []).join("·");
  host.append(h("p", { class: "g-head muted small", text: `${names} 선택 지점 평균 · 비교 기준은 위 '비교 기준' 버튼에서 고를 수 있습니다.` }));
  renderCompare(res, host);
  renderFlow(res, host);
}

window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.res) renderGraph(state.res); }, 200);
});

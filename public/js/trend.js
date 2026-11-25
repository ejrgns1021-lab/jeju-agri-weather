// trend.js (화면, v7.6) — 대시보드 아래 [그래프로 보기] 접는 칸: 조회 기간의 일별 추이를 금년·전년·평년으로 비교
//   탭 4개
//    · 평균기온 / 일조시간 : 꺾은선 3개 — 금년(굵은 실선+점), 전년(하늘색 얇은 실선), 평년(회색 점선)
//    · 최고·최저기온      : 금년·전년·평년 각각 최고·최저 두 선 (금년은 두 선 사이를 옅게 채움)
//    · 누적강수량        : 기간 첫날부터 더해 간 누적 곡선(금년은 영역 채우기), 아래 칸에 금년 일 강수 막대
//   확대·축소: Ctrl(⌘)+마우스 휠·[+][−]·전체 기간 / 이동: 그래프 끌기·Shift+휠·아래 기간 이동 막대
//   값은 모두 선택 지점 평균이며 서버가 보낸 일별 값을 그리기만 함(누적만 화면에서 더함). 순수 SVG, 라이브러리 없음.

const NS = "http://www.w3.org/2000/svg";
const TABS = [
  { key: "avg", label: "평균기온", metrics: ["평균 기온"], unit: "℃" },
  { key: "maxmin", label: "최고·최저기온", metrics: ["최고 기온", "최저 기온"], unit: "℃" },
  { key: "rain", label: "누적강수량", metrics: ["누적 강수량"], unit: "mm" },
  { key: "sun", label: "일조시간", metrics: ["일조 시간"], unit: "hr" },
];
const MIN_DAYS = 7;
const st = { res: null, resKey: "", tab: "avg", i0: 0, i1: 0 };
let ui = null;
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
const ok = (v) => v !== null && v !== undefined && !Number.isNaN(v);
const fmt = (v, d = 1) => (ok(v) ? Number(v).toFixed(d) : "-");
const sgn = (v, d = 1) => (ok(v) ? `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}` : "-");
const mmdd = (iso) => { const [, m, d] = iso.split("-"); return `${m}.${d}`; };

function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) hi = lo + 1;
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) || raw;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(+v.toFixed(6));
  return out;
}

/** 누적합 (빈 날은 0으로 보고 이어 가되, 마지막 자료 이후는 비움) */
function cumulate(arr) {
  let last = -1;
  arr.forEach((v, i) => { if (ok(v)) last = i; });
  let sum = 0;
  return arr.map((v, i) => { if (i > last) return null; sum += ok(v) ? v : 0; return +sum.toFixed(2); });
}

function years() {
  const sm = st.res.summary || {};
  return { cur: `금년(${sm.currentYear || ""})`, prev: `전년(${sm.prevYear || ""})`, normal: "평년(1991~2020)" };
}

// 탭별로 그릴 선·막대
function prepare() {
  const ch = st.res.chart;
  const tab = TABS.find((t) => t.key === st.tab);
  const get = (m, k) => ((ch.metrics[m] || {})[k] || []);
  const Y = years();
  const lines = [];
  let bars = null;
  if (tab.key === "maxmin") {
    [["normal", "s-normal dash"], ["prev", "s-prev"], ["cur", "s-cur main"]].forEach(([k, cls]) => {
      lines.push({ key: k, sub: "max", cls, vals: get("최고 기온", k), label: Y[k] });
      lines.push({ key: k, sub: "min", cls, vals: get("최저 기온", k), label: Y[k] });
    });
  } else if (tab.key === "rain") {
    const daily = get("누적 강수량", "cur");
    lines.push({ key: "normal", cls: "s-normal dash", vals: cumulate(get("누적 강수량", "normal")), label: Y.normal });
    lines.push({ key: "prev", cls: "s-prev dash", vals: cumulate(get("누적 강수량", "prev")), label: Y.prev });
    lines.push({ key: "cur", cls: "s-rain main", vals: cumulate(daily), label: Y.cur, area: true });
    bars = daily;
  } else {
    const m = tab.metrics[0];
    lines.push({ key: "normal", cls: "s-normal dash", vals: get(m, "normal"), label: Y.normal });
    lines.push({ key: "prev", cls: "s-prev", vals: get(m, "prev"), label: Y.prev });
    lines.push({ key: "cur", cls: "s-cur main", vals: get(m, "cur"), label: Y.cur, points: true });
  }
  return { tab, lines: lines.filter((l) => l.vals.some(ok)), bars };
}

// ---------------------------------------------------------------- 그리기
const PAD = { l: 44, r: 10, t: 10, b: 22 };

function drawMain() {
  const { gridG, dataG, W, H, P } = ui;
  const dates = st.res.chart.dates;
  const n = dates.length;
  const i0 = st.i0, i1 = st.i1, span = Math.max(1, i1 - i0);
  const plotW = W - PAD.l - PAD.r;
  const barH = P.bars ? Math.round(H * 0.2) : 0;              // 누적강수량 탭: 아래 일 강수 막대 칸
  const mainB = H - PAD.b - (P.bars ? barH + 10 : 0);
  const x = (i) => PAD.l + ((i - i0) / span) * plotW;
  const inView = (i) => i >= i0 - 1 && i <= i1 + 1;
  let vals = P.lines.flatMap((l) => l.vals.filter((v, i) => i >= i0 && i <= i1 && ok(v)));
  if (!vals.length) vals = [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (P.tab.key === "rain" || P.tab.key === "sun") { lo = 0; hi = Math.max(hi * 1.05, 1); }
  else { const p = Math.max(0.5, (hi - lo) * 0.08); lo -= p; hi += p; }
  const ticks = niceTicks(lo, hi, H < 270 ? 4 : 5);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
  const y = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * (mainB - PAD.t);
  ui.x = x; ui.y = y;

  gridG.replaceChildren();
  dataG.replaceChildren();
  ticks.forEach((t) => {
    gridG.append(s("line", { x1: PAD.l, x2: W - PAD.r, y1: y(t), y2: y(t), class: "t-grid" }));
    const tx = s("text", { x: PAD.l - 6, y: y(t) + 4, class: "t-tick", "text-anchor": "end" });
    tx.textContent = fmt(t, Number.isInteger(t) ? 0 : 1); gridG.append(tx);
  });
  // X축 날짜(MM.DD): 글자 폭 기준으로 겹치지 않게 간격 자동
  const maxLabels = Math.max(2, Math.floor(plotW / 54));
  const step = Math.max(1, Math.ceil((span + 1) / maxLabels));
  for (let i = i0; i <= i1; i += step) {
    const tx = s("text", { x: x(i), y: H - 6, class: "t-tick", "text-anchor": i === i0 ? "start" : "middle" });
    tx.textContent = mmdd(dates[i]); gridG.append(tx);
  }

  // 누적강수량 탭: 금년 일 강수 막대(아래 칸, 자체 눈금)
  if (P.bars) {
    const top = mainB + 10, bot = H - PAD.b;
    const bv = P.bars.filter((v, i) => i >= i0 && i <= i1 && ok(v));
    const bmax = niceTicks(0, Math.max(1, ...bv), 2).slice(-1)[0];
    const by = (v) => bot - (v / bmax) * (bot - top);
    gridG.append(s("line", { x1: PAD.l, x2: W - PAD.r, y1: bot, y2: bot, class: "t-grid" }));
    const lt = s("text", { x: PAD.l - 6, y: top + 8, class: "t-tick", "text-anchor": "end" }); lt.textContent = fmt(bmax, 0); gridG.append(lt);
    const cap = s("text", { x: PAD.l + 4, y: top + 9, class: "t-cap" }); cap.textContent = "금년 일 강수량(mm)"; gridG.append(cap);
    const bw = Math.max(1, Math.min(12, (plotW / (span + 1)) * 0.7));
    P.bars.forEach((v, i) => {
      if (!ok(v) || v <= 0 || !inView(i)) return;
      dataG.append(s("rect", { x: x(i) - bw / 2, y: by(v), width: bw, height: Math.max(0.5, bot - by(v)), class: "t-bar", rx: bw >= 6 ? 2 : 0 }));
    });
  }
  // 최고·최저: 금년 두 선 사이 옅게 채움
  if (P.tab.key === "maxmin") {
    const mx = P.lines.find((l) => l.key === "cur" && l.sub === "max"), mn = P.lines.find((l) => l.key === "cur" && l.sub === "min");
    if (mx && mn) {
      const pts = [];
      for (let i = i0 - 1; i <= i1 + 1; i++) if (ok(mx.vals[i]) && ok(mn.vals[i])) pts.push(i);
      if (pts.length > 1) {
        const d = pts.map((i, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(mx.vals[i]).toFixed(1)}`).join("")
          + [...pts].reverse().map((i) => `L${x(i).toFixed(1)},${y(mn.vals[i]).toFixed(1)}`).join("") + "Z";
        dataG.append(s("path", { d, class: "t-band" }));
      }
    }
  }
  // 선 (평년 → 전년 → 금년 순서로, 금년이 맨 위)
  P.lines.forEach((l) => {
    let d = "", pen = false, first = null, lastI = null;
    l.vals.forEach((v, i) => {
      if (!ok(v) || !inView(i)) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
      if (first === null) first = i; lastI = i;
    });
    if (l.area && first !== null) {
      dataG.append(s("path", { d: `${d}L${x(lastI).toFixed(1)},${y(lo).toFixed(1)}L${x(first).toFixed(1)},${y(lo).toFixed(1)}Z`, class: "t-area" }));
    }
    dataG.append(s("path", { d, class: `t-line ${l.cls}` }));
    if (l.points && span <= 62) {
      l.vals.forEach((v, i) => { if (ok(v) && i >= i0 && i <= i1) dataG.append(s("circle", { cx: x(i), cy: y(v), r: span <= 31 ? 3 : 2.2, class: `t-pt ${l.cls}` })); });
    }
  });

  // 기간 표시·이동 막대
  ui.range.textContent = `${mmdd(dates[i0])} ~ ${mmdd(dates[i1])} (${i1 - i0 + 1}일${i1 - i0 + 1 < n ? ` / 전체 ${n}일` : ""})`;
  const nW = ui.navW, nx0 = (i0 / Math.max(1, n - 1)) * nW, nx1 = (i1 / Math.max(1, n - 1)) * nW;
  ui.navWin.setAttribute("x", nx0); ui.navWin.setAttribute("width", Math.max(6, nx1 - nx0));
  ui.navShadeL.setAttribute("width", Math.max(0, nx0));
  ui.navShadeR.setAttribute("x", nx1); ui.navShadeR.setAttribute("width", Math.max(0, nW - nx1));
  ui.btnIn.disabled = i1 - i0 + 1 <= MIN_DAYS;
  ui.btnOut.disabled = ui.btnAll.disabled = i0 === 0 && i1 === n - 1;
  ui.zoomRow.hidden = n <= MIN_DAYS;
  hideTip();
}

function setView(i0, i1) {
  const n = st.res.chart.dates.length;
  const w = Math.max(Math.min(MIN_DAYS, n) - 1, Math.min(n - 1, Math.round(i1 - i0)));
  i0 = Math.round(i0);
  if (i0 + w > n - 1) i0 = n - 1 - w;
  st.i0 = Math.max(0, i0); st.i1 = Math.min(n - 1, st.i0 + w);
  drawMain();
}
function zoom(factor, c) {
  c = c ?? (st.i0 + st.i1) / 2;
  const w = (st.i1 - st.i0) * factor, ratio = (c - st.i0) / Math.max(1, st.i1 - st.i0);
  setView(c - w * ratio, c - w * ratio + w);
}

// ---------------------------------------------------------------- 값 상자(툴팁)
function hideTip() {
  if (!ui) return;
  ui.tip.hidden = true; ui.cross.setAttribute("visibility", "hidden");
  ui.hdots.forEach((d) => d.remove()); ui.hdots = [];
}
function showTip(i) {
  const { P } = ui;
  const unit = P.tab.unit;
  ui.cross.setAttribute("x1", ui.x(i)); ui.cross.setAttribute("x2", ui.x(i)); ui.cross.setAttribute("visibility", "visible");
  ui.hdots.forEach((d) => d.remove()); ui.hdots = [];
  P.lines.forEach((l) => {
    if (!ok(l.vals[i])) return;
    const c = s("circle", { r: 4, cx: ui.x(i), cy: ui.y(l.vals[i]), class: `t-hdot ${l.cls}` }); ui.overG.append(c); ui.hdots.push(c);
  });
  const Y = years();
  const sw = (cls) => h("span", { class: `t-sw ${cls}` });
  const rows = [];
  const byKey = (k, sub) => P.lines.find((l) => l.key === k && (!sub || l.sub === sub));
  ["cur", "prev", "normal"].forEach((k) => {
    if (P.tab.key === "maxmin") {
      const mx = byKey(k, "max"), mn = byKey(k, "min");
      if (!mx && !mn) return;
      rows.push(h("div", { class: "t-tip-row" }, sw((mx || mn).cls), h("span", { text: Y[k].replace(/\(.*\)/, "") }),
        h("b", { text: `${fmt(mx && mx.vals[i])} / ${fmt(mn && mn.vals[i])}${unit}` })));
    } else {
      const l = byKey(k);
      if (!l) return;
      rows.push(h("div", { class: "t-tip-row" }, sw(l.cls), h("span", { text: Y[k].replace(/\(.*\)/, "") }), h("b", { text: `${fmt(l.vals[i])}${unit}` })));
    }
  });
  let foot = null;
  if (P.tab.key === "maxmin") {
    const c = byKey("cur", "max"), nm = byKey("normal", "max"), c2 = byKey("cur", "min"), nm2 = byKey("normal", "min");
    if (c && nm && ok(c.vals[i]) && ok(nm.vals[i])) foot = `평년 대비 최고 ${sgn(c.vals[i] - nm.vals[i])} · 최저 ${sgn(c2 && nm2 ? c2.vals[i] - nm2.vals[i] : null)}${unit}`;
  } else {
    const c = byKey("cur"), nm = byKey("normal");
    if (c && nm && ok(c.vals[i]) && ok(nm.vals[i])) foot = `금년 − 평년: ${sgn(c.vals[i] - nm.vals[i])}${unit}`;
  }
  const head = P.tab.key === "rain" ? `${mmdd(st.res.chart.dates[i])}까지 누적` + (ok(P.bars[i]) ? ` · 이날 ${fmt(P.bars[i])}mm` : "")
    : P.tab.key === "maxmin" ? `${mmdd(st.res.chart.dates[i])} (최고 / 최저)` : mmdd(st.res.chart.dates[i]);
  ui.tip.replaceChildren(h("div", { class: "t-tip-date", text: head }), ...rows, foot ? h("div", { class: "t-tip-foot", text: foot }) : null);
  ui.tip.hidden = false;
  const r = ui.svg.getBoundingClientRect();
  const left = (ui.x(i) / ui.W) * r.width, tw = ui.tip.offsetWidth || 190;
  ui.tip.style.left = `${left + 12 + tw > r.width ? Math.max(left - tw - 12, 0) : left + 12}px`;
}

// ---------------------------------------------------------------- 조작
function bindMain() {
  const { svg } = ui;
  const idxAt = (clientX) => {
    const r = svg.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * ui.W;
    return Math.max(st.i0, Math.min(st.i1, Math.round(st.i0 + ((px - PAD.l) / (ui.W - PAD.l - PAD.r)) * (st.i1 - st.i0))));
  };
  svg.addEventListener("wheel", (ev) => {
    if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); zoom(ev.deltaY < 0 ? 0.8 : 1.25, idxAt(ev.clientX)); }
    else if (ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {
      ev.preventDefault();
      const d = (ev.shiftKey ? ev.deltaY : ev.deltaX) > 0 ? 1 : -1, stp = Math.max(1, Math.round((st.i1 - st.i0) * 0.1));
      setView(st.i0 + d * stp, st.i1 + d * stp);
    }
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (ev) => {
    drag = { x: ev.clientX, i0: st.i0, i1: st.i1, moved: false };
    if (ev.pointerType === "mouse") svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener("pointermove", (ev) => {
    if (drag && (ev.pointerType !== "mouse" || (ev.buttons & 1))) {
      const r = svg.getBoundingClientRect();
      const di = (((ev.clientX - drag.x) / r.width) * ui.W / (ui.W - PAD.l - PAD.r)) * (drag.i1 - drag.i0);
      if (Math.abs(ev.clientX - drag.x) > 6) drag.moved = true;
      if (drag.moved && drag.i1 - drag.i0 < st.res.chart.dates.length - 1) { svg.classList.add("dragging"); setView(drag.i0 - di, drag.i1 - di); return; }
    }
    showTip(idxAt(ev.clientX));
  });
  const end = () => { drag = null; svg.classList.remove("dragging"); };
  svg.addEventListener("pointerup", (ev) => { if (drag && !drag.moved) showTip(idxAt(ev.clientX)); end(); });
  svg.addEventListener("pointercancel", end);
  svg.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse" && !drag) hideTip(); });
}

function bindNav() {
  const { nav } = ui;
  const n = st.res.chart.dates.length;
  const geo = (clientX) => {
    const r = nav.getBoundingClientRect();
    return { px: clientX - r.left, w: r.width, idx: ((clientX - r.left) / r.width) * (n - 1),
      a: (st.i0 / Math.max(1, n - 1)) * r.width, b: (st.i1 / Math.max(1, n - 1)) * r.width };
  };
  let drag = null;
  nav.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    nav.setPointerCapture(ev.pointerId);
    const g = geo(ev.clientX);
    if (Math.abs(g.px - g.a) <= 10 && g.b - g.a > 20) drag = { type: "left" };
    else if (Math.abs(g.px - g.b) <= 10 && g.b - g.a > 20) drag = { type: "right" };
    else {
      if (!(g.px > g.a && g.px < g.b)) { const w = st.i1 - st.i0; setView(g.idx - w / 2, g.idx + w / 2); }
      drag = { type: "move", start: g.idx, i0: st.i0, i1: st.i1 };
    }
  });
  nav.addEventListener("pointermove", (ev) => {
    const g = geo(ev.clientX);
    if (!drag) { nav.style.cursor = Math.abs(g.px - g.a) <= 10 || Math.abs(g.px - g.b) <= 10 ? "ew-resize" : g.px > g.a && g.px < g.b ? "grab" : "pointer"; return; }
    if (drag.type === "left") setView(Math.min(g.idx, st.i1 - (MIN_DAYS - 1)), st.i1);
    else if (drag.type === "right") setView(st.i0, Math.max(g.idx, st.i0 + (MIN_DAYS - 1)));
    else { const d = g.idx - drag.start; setView(drag.i0 + d, drag.i1 + d); }
  });
  nav.addEventListener("pointerup", () => { drag = null; });
  nav.addEventListener("pointercancel", () => { drag = null; });
}

// ---------------------------------------------------------------- 화면 구성
function buildChart(host) {
  const P = prepare();
  const n = st.res.chart.dates.length;
  const W = Math.max(280, Math.round(host.clientWidth || 600));
  const H = W < 520 ? 250 : 280;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "t-svg t-main", role: "img", "aria-label": `${P.tab.label} 날짜별 그래프` });
  const defs = s("defs"), clip = s("clipPath", { id: "t-clip" });
  clip.append(s("rect", { x: PAD.l, y: 0, width: W - PAD.l - PAD.r, height: H })); defs.append(clip);
  const gridG = s("g"), dataG = s("g", { "clip-path": "url(#t-clip)" }), overG = s("g");
  const cross = s("line", { y1: PAD.t, y2: H - PAD.b, class: "t-cross", visibility: "hidden" });
  overG.append(cross);
  svg.append(defs, gridG, dataG, overG);
  const tip = h("div", { class: "t-tip", hidden: true });

  const navW = W, navH = 34;
  const nav = s("svg", { viewBox: `0 0 ${navW} ${navH}`, width: navW, height: navH, class: "t-svg t-nav", "aria-label": "기간 이동 막대" });
  const cur = (P.lines.find((l) => l.key === "cur") || { vals: [] }).vals;
  const cv = cur.filter(ok);
  if (cv.length) {
    let lo = Math.min(...cv), hi = Math.max(...cv); if (!(hi > lo)) hi = lo + 1;
    const nxp = (i) => (i / Math.max(1, n - 1)) * navW, nyp = (v) => 4 + (1 - (v - lo) / (hi - lo)) * (navH - 8);
    let d = "", pen = false;
    cur.forEach((v, i) => { if (!ok(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${nxp(i).toFixed(1)},${nyp(v).toFixed(1)}`; pen = true; });
    nav.append(s("path", { d, class: "t-nav-line" }));
  }
  const navShadeL = s("rect", { x: 0, y: 0, height: navH, class: "t-nav-shade" });
  const navShadeR = s("rect", { y: 0, height: navH, class: "t-nav-shade" });
  const navWin = s("rect", { y: 1, height: navH - 2, rx: 4, class: "t-nav-win" });
  nav.append(navShadeL, navShadeR, navWin);

  const range = h("span", { class: "t-range" });
  const btnOut = h("button", { type: "button", class: "btn ghost small", title: "축소(기간 넓게)", "aria-label": "축소", text: "−", onclick: () => zoom(1.5) });
  const btnIn = h("button", { type: "button", class: "btn ghost small", title: "확대(기간 좁게)", "aria-label": "확대", text: "+", onclick: () => zoom(0.66) });
  const btnAll = h("button", { type: "button", class: "btn ghost small", text: "전체 기간", onclick: () => setView(0, n - 1) });
  const zoomRow = h("div", { class: "t-zoom" },
    h("div", { class: "t-navrow" }, nav),
    h("div", { class: "t-zoomrow" }, h("div", { class: "t-zoombtns" }, btnOut, btnIn, btnAll), range),
    h("p", { class: "t-note", text: "확대·축소: Ctrl(맥은 ⌘)+마우스 휠 또는 [+][−] · 이동: 그래프를 좌우로 끌기, 아래 막대의 창 끌기(창 끝을 끌면 기간 길이 조절)" }));

  const Y = years();
  const seen = new Set();
  const legendItems = [];
  [...P.lines].reverse().forEach((l) => {
    if (seen.has(l.key)) return; seen.add(l.key);
    legendItems.push(h("span", { class: "t-leg" }, h("span", { class: `t-sw ${l.cls}${l.area ? " area" : ""}` }), h("span", { text: Y[l.key] })));
  });
  if (P.bars) legendItems.push(h("span", { class: "t-leg" }, h("span", { class: "t-box" }), h("span", { text: "금년 일 강수량" })));
  if (P.tab.key === "maxmin") legendItems.push(h("span", { class: "t-leg muted" }, h("span", { text: "위 선 최고 · 아래 선 최저" })));

  host.replaceChildren(h("div", { class: "t-legend" }, ...legendItems), h("div", { class: "t-plot" }, svg, tip), zoomRow);
  ui = { svg, gridG, dataG, overG, cross, hdots: [], tip, W, H, nav, navW, navWin, navShadeL, navShadeR, range, btnIn, btnOut, btnAll, zoomRow, P };
  bindMain();
  bindNav();
  drawMain();
}

function summaryText() {
  const avg = (st.res.stationReport || {}).average || {};
  const ch = st.res.chart.metrics;
  const sumOf = (a) => a.filter(ok).reduce((x, y) => x + y, 0);
  const meanOf = (a) => { const v = a.filter(ok); return v.length ? sumOf(v) / v.length : null; };
  const line = (label, m, agg, unit) => {
    const a = avg[m] || {}, c = ch[m] || {};
    const cur = ok(a.cur) ? a.cur : agg(c.cur || []), prev = ok(a.prev) ? a.prev : agg(c.prev || []), nrm = ok(a.normal) ? a.normal : agg(c.normal || []);
    if (!ok(cur)) return "";
    return `${label} 금년 ${fmt(cur)}${unit}` + (ok(prev) ? ` · 전년 ${fmt(prev)} (${sgn(cur - prev)})` : "") + (ok(nrm) ? ` · 평년 ${fmt(nrm)} (${sgn(cur - nrm)})` : "");
  };
  switch (st.tab) {
    case "avg": return line("기간 평균:", "평균 기온", meanOf, "℃");
    case "maxmin": return [line("최고 평균:", "최고 기온", meanOf, "℃"), line("최저 평균:", "최저 기온", meanOf, "℃")].filter(Boolean).join("\n");
    case "rain": return line("기간 누적:", "누적 강수량", sumOf, "mm");
    default: return line("기간 합계:", "일조 시간", sumOf, "hr");
  }
}

function render() {
  const host = document.getElementById("graph-panel");
  if (!host || !st.res) return;
  const ch = st.res.chart;
  if (!ch || !ch.metrics || !Object.keys(ch.metrics).length) {
    host.replaceChildren(h("p", { class: "muted small", text: "이 결과에는 그래프 자료가 없습니다. 다시 조회하면 표시됩니다." }));
    return;
  }
  const tabs = TABS.filter((t) => t.metrics.some((m) => ch.metrics[m]));
  if (!tabs.some((t) => t.key === st.tab)) st.tab = tabs[0].key;
  const chartHost = h("div", { class: "t-wrap" });
  const names = (st.res.params.stations || []).join("·");
  host.replaceChildren(
    h("div", { class: "segmented t-tabs", role: "tablist", "aria-label": "지표" }, ...tabs.map((t) => h("button", {
      type: "button", role: "tab", class: t.key === st.tab ? "active" : "", "aria-selected": t.key === st.tab ? "true" : "false",
      text: t.label, onclick: () => { st.tab = t.key; render(); },
    }))),
    h("p", { class: "t-summary", text: summaryText() }),
    chartHost,
    h("p", { class: "t-note", text: `${names} 선택 지점의 날짜별 평균값 · 전년·평년은 같은 날짜의 값(평년은 1991~2020 평균)` +
      (st.tab === "rain" ? " · 누적은 조회 첫날부터 더한 값" : "") }),
  );
  requestAnimationFrame(() => buildChart(chartHost));
}

// ---------------------------------------------------------------- 진입점
export function setResult(res) {
  st.res = res && res.ok ? res : null;
  const key = st.res ? `${res.params.start}_${res.params.end}_${(res.params.stations || []).join()}` : "";
  if (key !== st.resKey) {
    st.resKey = key;
    const n = st.res && st.res.chart ? st.res.chart.dates.length : 0;
    st.i0 = 0; st.i1 = Math.max(0, n - 1);
  }
  const fold = document.getElementById("graph-fold");
  if (fold) fold.hidden = !st.res;
  if (fold && fold.open) render();
}
export function refresh() { /* 비교 기준 버튼과 무관 (금년·전년·평년 고정) */ }
export function setup() {
  const fold = document.getElementById("graph-fold");
  if (!fold) return;
  fold.addEventListener("toggle", () => { if (fold.open && st.res) render(); });
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (st.res && fold.open) render(); }, 200);
  });
}

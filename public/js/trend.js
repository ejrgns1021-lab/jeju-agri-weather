// trend.js (화면, v7.6.1) — 대시보드 아래 [그래프로 보기] 접는 칸
//   · 지표(평균·최고·최저 기온, 강수량, 일조 시간)를 하나씩 골라 날짜별(일별) 그래프로 봄 — 모두 선택 지점 평균
//   · 본년과 고른 비교 기준(전년·최근 3년·최근 5년·평년)을 겹쳐 보거나, '편차'로 본년 − 기준을 막대로 봄
//   · 기간 확대·축소: Ctrl(맥은 ⌘) + 마우스 휠, 또는 [+][−] 버튼 · 이동: 그래프를 끌거나 아래 막대(기간 이동 막대)를 끎
// 서버가 계산해 보낸 일별 값을 그리기만 합니다(계산 없음).
import { isOn } from "./bases.js";

const NS = "http://www.w3.org/2000/svg";
const SERIES = [                       // 색은 항목을 따라감 (고른 기준이 바뀌어도 그대로)
  { key: "cur", cls: "g-cur" },
  { key: "prev", cls: "g-prev", base: "prev" },
  { key: "r3", cls: "g-r3", base: "r3" },
  { key: "r5", cls: "g-r5", base: "r5" },
  { key: "normal", cls: "g-normal", base: "normal", dash: true },
];
const ORDER = ["평균 기온", "최고 기온", "최저 기온", "누적 강수량", "일조 시간"];
const SHOW_NAME = { "누적 강수량": "강수량" };          // 일별 값이므로 '누적' 빼고 표시
const SHORT = { cur: "본년", prev: "전년", r3: "최근 3년", r5: "최근 5년", normal: "평년" };
const MIN_DAYS = 7;

const st = { res: null, resKey: "", metric: null, mode: "value", diffBase: "normal", smooth: false, i0: 0, i1: 0 };
let ui = null;            // 현재 그려진 화면 요소
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
const md = (iso) => { const [, m, d] = iso.split("-"); return `${+m}/${+d}`; };
const showName = (m) => SHOW_NAME[m] || m;

function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) hi = lo + 1;
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) || raw;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(+v.toFixed(6));
  return out;
}

function movingAvg(arr, win = 7) {
  const half = Math.floor(win / 2);
  return arr.map((v, i) => {
    if (!ok(v)) return null;
    const part = arr.slice(Math.max(0, i - half), i + half + 1).filter(ok);
    return part.length >= Math.ceil(win / 2) ? part.reduce((a, b) => a + b, 0) / part.length : null;
  });
}

function labels(res) {
  const sm = res.summary || {};
  return {
    cur: `본년(${sm.currentYear || ""})`, prev: `전년(${sm.prevYear || ""})`,
    r3: `최근 3년(${sm.recent3Range || ""})`, r5: `최근 5년(${sm.recentRange || ""})`, normal: "평년(1991~2020)",
  };
}

// 지금 고른 지표·방식으로 그릴 자료
function prepare() {
  const ch = st.res.chart;
  const m = ch.metrics[st.metric];
  const isBarKind = m.kind === "rain" || m.kind === "sun";
  const raw = (k) => (st.smooth ? movingAvg(m[k] || []) : (m[k] || []));
  const bases = SERIES.filter((x) => x.base && isOn(x.base) && (m[x.key] || []).some(ok));
  if (st.mode === "diff") {
    if (!bases.some((b) => b.key === st.diffBase)) st.diffBase = bases.length ? bases[bases.length - 1].key : "normal";
    const cur = raw("cur"), ref = raw(st.diffBase);
    const d = cur.map((v, i) => (ok(v) && ok(ref[i]) ? v - ref[i] : null));
    return { m, isBarKind, bases, diff: d, series: [] };
  }
  const series = [{ ...SERIES[0], vals: raw("cur"), bar: isBarKind && !st.smooth }]
    .concat(bases.map((b) => ({ ...b, vals: raw(b.key) })));
  return { m, isBarKind, bases, series };
}

// ---------------------------------------------------------------- 그리기
const PAD = { l: 46, r: 12, t: 10, b: 24 };

function drawMain() {
  const { svg, dataG, gridG, W, H } = ui;
  const P = ui.prep;
  const n = st.res.chart.dates.length;
  const i0 = st.i0, i1 = st.i1;
  const span = Math.max(1, i1 - i0);
  const plotW = W - PAD.l - PAD.r;
  const x = (i) => PAD.l + ((i - i0) / span) * plotW;
  const inView = (i) => i >= i0 - 1 && i <= i1 + 1;
  // 세로축: 보이는 구간 값으로 자동 맞춤
  let vals;
  if (st.mode === "diff") vals = P.diff.filter((v, i) => i >= i0 && i <= i1 && ok(v));
  else vals = P.series.flatMap((sr) => sr.vals.filter((v, i) => i >= i0 && i <= i1 && ok(v)));
  if (!vals.length) vals = [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (st.mode === "diff") { const a = Math.max(Math.abs(lo), Math.abs(hi), 0.5); lo = -a; hi = a; }
  else if (P.isBarKind) { lo = 0; hi = Math.max(hi, 1); }
  else { const p = Math.max(0.5, (hi - lo) * 0.08); lo -= p; hi += p; }
  const ticks = niceTicks(lo, hi, 5);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
  const y = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  ui.x = x; ui.y = y;

  gridG.replaceChildren();
  dataG.replaceChildren();
  ticks.forEach((t) => {
    gridG.append(s("line", { x1: PAD.l, x2: W - PAD.r, y1: y(t), y2: y(t), class: t === 0 && st.mode === "diff" ? "g-zero" : "g-grid" }));
    const tx = s("text", { x: PAD.l - 6, y: y(t) + 4, class: "g-tick", "text-anchor": "end" });
    tx.textContent = st.mode === "diff" && t > 0 ? `+${fmt(t, Number.isInteger(t) ? 0 : 1)}` : fmt(t, Number.isInteger(t) ? 0 : 1);
    gridG.append(tx);
  });
  const nx = Math.min(span + 1, W < 520 ? 4 : 7);
  for (let k = 0; k < nx; k++) {
    const i = nx <= 1 ? i0 : Math.round(i0 + (k / (nx - 1)) * span);
    const tx = s("text", { x: x(i), y: H - 6, class: "g-tick", "text-anchor": k === 0 ? "start" : k === nx - 1 ? "end" : "middle" });
    tx.textContent = md(st.res.chart.dates[i]); gridG.append(tx);
  }
  const bw = Math.max(1, Math.min(14, (plotW / (span + 1)) * 0.7));
  const bar = (i, v, cls) => {
    const y0 = y(0), y1 = y(v);
    dataG.append(s("rect", { x: x(i) - bw / 2, y: Math.min(y0, y1), width: bw, height: Math.max(0.5, Math.abs(y1 - y0)), class: cls, rx: bw >= 6 ? 2 : 0 }));
  };
  if (st.mode === "diff") {
    P.diff.forEach((v, i) => { if (ok(v) && inView(i)) bar(i, v, `${v >= 0 ? "g-pos" : "g-neg"} k-${P.m.kind}`); });
  } else {
    [...P.series].reverse().forEach((sr) => {     // 평년 → … → 본년 순으로 그려 본년이 맨 위
      if (sr.bar) { sr.vals.forEach((v, i) => { if (ok(v) && inView(i)) bar(i, v, `g-barfill ${sr.cls}`); }); return; }
      let d = "", pen = false;
      sr.vals.forEach((v, i) => {
        if (!ok(v) || !inView(i)) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
      });
      dataG.append(s("path", { d, class: `g-path ${sr.cls}${sr.dash ? " dash" : ""}${sr.key === "cur" ? " main" : ""}` }));
    });
  }
  // 기간 표시·이동 막대
  ui.range.textContent = `${md(st.res.chart.dates[i0])} ~ ${md(st.res.chart.dates[i1])} (${i1 - i0 + 1}일${i1 - i0 + 1 < n ? ` / 전체 ${n}일` : ""})`;
  const nW = ui.navW, nx0 = (i0 / Math.max(1, n - 1)) * nW, nx1 = (i1 / Math.max(1, n - 1)) * nW;
  ui.navWin.setAttribute("x", nx0); ui.navWin.setAttribute("width", Math.max(6, nx1 - nx0));
  ui.navShadeL.setAttribute("width", Math.max(0, nx0));
  ui.navShadeR.setAttribute("x", nx1); ui.navShadeR.setAttribute("width", Math.max(0, nW - nx1));
  ui.btnIn.disabled = i1 - i0 + 1 <= MIN_DAYS;
  ui.btnOut.disabled = ui.btnAll.disabled = i0 === 0 && i1 === n - 1;
  hideTip();
}

function setView(i0, i1) {
  const n = st.res.chart.dates.length;
  let w = Math.max(Math.min(MIN_DAYS, n) - 1, Math.min(n - 1, Math.round(i1 - i0)));
  i0 = Math.round(i0);
  if (i0 < 0) i0 = 0;
  if (i0 + w > n - 1) i0 = n - 1 - w;
  st.i0 = Math.max(0, i0); st.i1 = Math.min(n - 1, st.i0 + w);
  drawMain();
}
function zoom(factor, centerIdx) {
  const c = centerIdx ?? (st.i0 + st.i1) / 2;
  const w = (st.i1 - st.i0) * factor;
  const ratio = (c - st.i0) / Math.max(1, st.i1 - st.i0);
  setView(c - w * ratio, c - w * ratio + w);
}

// ---------------------------------------------------------------- 값 상자
function hideTip() {
  if (!ui) return;
  ui.tip.hidden = true; ui.cross.setAttribute("visibility", "hidden");
  ui.hdots.forEach((d) => d.remove()); ui.hdots = [];
}
function showTip(i) {
  const P = ui.prep, m = P.m, lab = SHORT;
  const unit = m.unit;
  ui.cross.setAttribute("x1", ui.x(i)); ui.cross.setAttribute("x2", ui.x(i)); ui.cross.setAttribute("visibility", "visible");
  ui.hdots.forEach((d) => d.remove()); ui.hdots = [];
  const rows = [];
  if (st.mode === "diff") {
    const v = P.diff[i];
    const cur = (st.smooth ? movingAvg(m.cur) : m.cur)[i], ref = (st.smooth ? movingAvg(m[st.diffBase]) : m[st.diffBase])[i];
    rows.push(h("div", { class: "g-tip-row" }, h("span", { class: "g-sw g-cur" }), h("span", { text: "본년" }), h("b", { text: `${fmt(cur)}${unit}` })));
    rows.push(h("div", { class: "g-tip-row" }, h("span", { class: `g-sw ${SERIES.find((x) => x.key === st.diffBase).cls}` }), h("span", { text: lab[st.diffBase] }), h("b", { text: `${fmt(ref)}${unit}` })));
    rows.push(h("div", { class: "g-tip-foot", text: `편차(본년 − ${lab[st.diffBase]}): ${sgn(v)}${unit}` }));
  } else {
    P.series.forEach((sr) => {
      const v = sr.vals[i];
      if (!ok(v)) return;
      if (!sr.bar) { const c = s("circle", { r: 4, cx: ui.x(i), cy: ui.y(v), class: `g-hdot ${sr.cls}` }); ui.overG.append(c); ui.hdots.push(c); }
      rows.push(h("div", { class: "g-tip-row" }, h("span", { class: `g-sw ${sr.cls}${sr.dash ? " dash" : ""}` }), h("span", { text: lab[sr.key] }), h("b", { text: `${fmt(v)}${unit}` })));
    });
    const cur = P.series[0].vals[i];
    const ref = P.series.find((x) => x.key === "normal") || P.series.find((x) => x.key === "r5") || P.series[1];
    if (ref && ok(cur) && ok(ref.vals[i])) rows.push(h("div", { class: "g-tip-foot", text: `본년 − ${lab[ref.key]}: ${sgn(cur - ref.vals[i])}${unit}` }));
  }
  ui.tip.replaceChildren(h("div", { class: "g-tip-date", text: md(st.res.chart.dates[i]) + (st.smooth ? " (7일 평균)" : "") }), ...rows);
  ui.tip.hidden = false;
  const r = ui.svg.getBoundingClientRect();
  const left = (ui.x(i) / ui.W) * r.width;
  ui.tip.style.left = `${left + 12 + 190 > r.width ? Math.max(left - 202, 0) : left + 12}px`;
}

// ---------------------------------------------------------------- 조작 (휠·끌기·이동 막대)
function bindMain() {
  const { svg } = ui;
  const idxAt = (clientX) => {
    const r = svg.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * ui.W;
    return Math.max(st.i0, Math.min(st.i1, Math.round(st.i0 + ((px - PAD.l) / (ui.W - PAD.l - PAD.r)) * (st.i1 - st.i0))));
  };
  svg.addEventListener("wheel", (ev) => {
    if (ev.ctrlKey || ev.metaKey) {                 // Ctrl + 휠: 확대(위로)·축소(아래로)
      ev.preventDefault();
      zoom(ev.deltaY < 0 ? 0.8 : 1.25, idxAt(ev.clientX));
    } else if (ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {   // Shift + 휠·가로 휠: 이동
      ev.preventDefault();
      const d = (ev.shiftKey ? ev.deltaY : ev.deltaX) > 0 ? 1 : -1;
      const step = Math.max(1, Math.round((st.i1 - st.i0) * 0.1));
      setView(st.i0 + d * step, st.i1 + d * step);
    }                                               // 그냥 휠은 페이지 스크롤
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (ev) => {
    drag = { x: ev.clientX, i0: st.i0, i1: st.i1, moved: false, id: ev.pointerId };
    if (ev.pointerType === "mouse") svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener("pointermove", (ev) => {
    if (drag && (ev.pointerType === "mouse" ? ev.buttons & 1 : true)) {
      const r = svg.getBoundingClientRect();
      const di = ((ev.clientX - drag.x) / r.width) * ui.W / (ui.W - PAD.l - PAD.r) * (drag.i1 - drag.i0);
      if (Math.abs(ev.clientX - drag.x) > 4) drag.moved = true;
      if (drag.moved && (drag.i1 - drag.i0) < st.res.chart.dates.length - 1) {
        svg.classList.add("dragging");
        setView(drag.i0 - di, drag.i1 - di);
        return;
      }
    }
    showTip(idxAt(ev.clientX));
  });
  const end = () => { if (drag && !drag.moved) { /* 그냥 누름: 값 상자 유지 */ } drag = null; svg.classList.remove("dragging"); };
  svg.addEventListener("pointerup", (ev) => { if (drag && !drag.moved) showTip(idxAt(ev.clientX)); end(); });
  svg.addEventListener("pointercancel", end);
  svg.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse" && !drag) hideTip(); });
}

function bindNav() {
  const { nav } = ui;
  const n = st.res.chart.dates.length;
  const idxAt = (clientX) => {
    const r = nav.getBoundingClientRect();
    return ((clientX - r.left) / r.width) * (n - 1);
  };
  let drag = null;
  nav.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    nav.setPointerCapture(ev.pointerId);
    const r = nav.getBoundingClientRect();
    const px = ev.clientX - r.left;
    const a = (st.i0 / Math.max(1, n - 1)) * r.width, b = (st.i1 / Math.max(1, n - 1)) * r.width;
    const edge = 10;
    if (Math.abs(px - a) <= edge && b - a > 2 * edge) drag = { type: "left" };
    else if (Math.abs(px - b) <= edge && b - a > 2 * edge) drag = { type: "right" };
    else if (px > a && px < b) drag = { type: "move", start: idxAt(ev.clientX), i0: st.i0, i1: st.i1 };
    else {                                    // 창 밖을 누르면 그 위치로 창을 옮김
      const w = st.i1 - st.i0, c = idxAt(ev.clientX);
      setView(c - w / 2, c + w / 2);
      drag = { type: "move", start: idxAt(ev.clientX), i0: st.i0, i1: st.i1 };
    }
  });
  nav.addEventListener("pointermove", (ev) => {
    if (!drag) {
      const r = nav.getBoundingClientRect(), px = ev.clientX - r.left;
      const a = (st.i0 / Math.max(1, n - 1)) * r.width, b = (st.i1 / Math.max(1, n - 1)) * r.width;
      nav.style.cursor = (Math.abs(px - a) <= 10 || Math.abs(px - b) <= 10) ? "ew-resize" : (px > a && px < b ? "grab" : "pointer");
      return;
    }
    const c = idxAt(ev.clientX);
    if (drag.type === "left") setView(Math.min(c, st.i1 - (MIN_DAYS - 1)), st.i1);
    else if (drag.type === "right") setView(st.i0, Math.max(c, st.i0 + (MIN_DAYS - 1)));
    else { const d = c - drag.start; setView(drag.i0 + d, drag.i1 + d); }
  });
  const end = () => { drag = null; };
  nav.addEventListener("pointerup", end);
  nav.addEventListener("pointercancel", end);
}

// ---------------------------------------------------------------- 화면 구성
function buildChart(host) {
  const P = prepare();
  const n = st.res.chart.dates.length;
  const W = Math.max(300, Math.round(host.clientWidth || 600));
  const H = W < 520 ? 250 : 320;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "g-svg g-main", role: "img",
    "aria-label": `${showName(st.metric)} 날짜별 그래프` });
  const clip = s("clipPath", { id: "g-clip" }); clip.append(s("rect", { x: PAD.l, y: 0, width: W - PAD.l - PAD.r, height: H }));
  const defs = s("defs"); defs.append(clip); svg.append(defs);
  const gridG = s("g"), dataG = s("g", { "clip-path": "url(#g-clip)" }), overG = s("g");
  const cross = s("line", { y1: PAD.t, y2: H - PAD.b, class: "g-cross", visibility: "hidden" });
  overG.append(cross);
  svg.append(gridG, dataG, overG);
  const tip = h("div", { class: "g-tip", hidden: true });

  // 기간 이동 막대: 전체 기간의 본년 값 모양 + 지금 보는 구간(창)
  const navW = W, navH = 40;
  const nav = s("svg", { viewBox: `0 0 ${navW} ${navH}`, width: navW, height: navH, class: "g-svg g-nav", "aria-label": "기간 이동 막대" });
  const curAll = (P.series[0] || {}).vals || P.diff || [];
  const valsAll = curAll.filter(ok);
  if (valsAll.length) {
    let lo = Math.min(...valsAll, 0), hi = Math.max(...valsAll);
    if (!P.isBarKind && st.mode !== "diff") lo = Math.min(...valsAll);
    if (!(hi > lo)) hi = lo + 1;
    const nxp = (i) => (i / Math.max(1, n - 1)) * navW, nyp = (v) => 4 + (1 - (v - lo) / (hi - lo)) * (navH - 8);
    let d = "", pen = false;
    curAll.forEach((v, i) => { if (!ok(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${nxp(i).toFixed(1)},${nyp(v).toFixed(1)}`; pen = true; });
    nav.append(s("path", { d, class: "g-nav-line" }));
  }
  const navShadeL = s("rect", { x: 0, y: 0, height: navH, class: "g-nav-shade" });
  const navShadeR = s("rect", { y: 0, height: navH, class: "g-nav-shade" });
  const navWin = s("rect", { y: 1, height: navH - 2, rx: 4, class: "g-nav-win" });
  nav.append(navShadeL, navShadeR, navWin);

  const range = h("span", { class: "g-range" });
  const btnOut = h("button", { type: "button", class: "btn ghost small", title: "축소(기간 넓게)", "aria-label": "축소", text: "−", onclick: () => zoom(1.5) });
  const btnIn = h("button", { type: "button", class: "btn ghost small", title: "확대(기간 좁게)", "aria-label": "확대", text: "+", onclick: () => zoom(0.66) });
  const btnAll = h("button", { type: "button", class: "btn ghost small", text: "전체 기간", onclick: () => setView(0, n - 1) });

  const lab = labels(st.res);
  const legend = st.mode === "diff"
    ? h("div", { class: "g-legend" }, h("span", { class: "g-leg" }, h("span", { class: `g-box g-pos k-${P.m.kind}` }), h("span", { text: `${SHORT[st.diffBase]}보다 ${P.m.kind === "temp" ? "높음" : "많음"}` })),
      h("span", { class: "g-leg" }, h("span", { class: `g-box g-neg k-${P.m.kind}` }), h("span", { text: `${SHORT[st.diffBase]}보다 ${P.m.kind === "temp" ? "낮음" : "적음"}` })))
    : h("div", { class: "g-legend" }, ...P.series.map((sr) => h("span", { class: "g-leg" },
      h("span", { class: sr.bar ? `g-box g-barfill ${sr.cls}` : `g-sw ${sr.cls}${sr.dash ? " dash" : ""}` }), h("span", { text: lab[sr.key] }))));

  host.replaceChildren(
    legend,
    h("div", { class: "g-plot" }, svg, tip),
    h("div", { class: "g-navrow" }, nav),
    h("div", { class: "g-zoomrow" }, h("div", { class: "g-zoombtns" }, btnOut, btnIn, btnAll), range),
    h("p", { class: "g-note", text: "확대·축소: Ctrl(맥은 ⌘) + 마우스 휠 또는 [+][−] · 기간 이동: 그래프를 좌우로 끌기, Shift + 휠, 아래 막대의 창을 끌기(창 끝을 끌면 기간 길이 조절)" }),
  );
  ui = { svg, gridG, dataG, overG, cross, hdots: [], tip, W, H, nav, navW, navWin, navShadeL, navShadeR, range, btnIn, btnOut, btnAll, prep: P };
  bindMain();
  bindNav();
  drawMain();
}

function summaryText() {
  const avg = st.res.stationReport && st.res.stationReport.average && st.res.stationReport.average[st.metric];
  const m = st.res.chart.metrics[st.metric];
  if (!avg || !ok(avg.cur)) {     // 조회에서 고르지 않은 지표: 일별 값으로 간단히
    const v = (m.cur || []).filter(ok);
    if (!v.length) return "";
    const tot = v.reduce((a, b) => a + b, 0);
    return m.kind === "rain" || m.kind === "sun" ? `기간 합계: 본년 ${fmt(tot)}${m.unit}` : `기간 평균: 본년 ${fmt(tot / v.length)}${m.unit}`;
  }
  const kind = m.kind === "rain" || m.kind === "sun" ? "기간 합계" : "기간 평균";
  const parts = [`${kind}: 본년 ${fmt(avg.cur)}${m.unit}`];
  ["prev", "r5", "normal"].filter((k) => isOn(k) && ok(avg[k])).forEach((k) => {
    parts.push(`${SHORT[k]} ${fmt(avg[k])} (${sgn(avg.cur - avg[k])})`);
  });
  return parts.join(" · ");
}

function render() {
  const host = document.getElementById("graph-panel");
  if (!host || !st.res) return;
  const ch = st.res.chart;
  if (!ch || !ch.metrics || !Object.keys(ch.metrics).length) {
    host.replaceChildren(h("p", { class: "muted small", text: "이 결과에는 그래프 자료가 없습니다. 다시 조회하면 표시됩니다." }));
    return;
  }
  const names = ORDER.filter((m) => ch.metrics[m]);
  if (!names.includes(st.metric)) {
    st.metric = names.find((m) => (st.res.params.metrics || []).includes(m)) || names[0];
  }
  const m = ch.metrics[st.metric];
  const bases = SERIES.filter((x) => x.base && isOn(x.base) && (m[x.key] || []).some(ok));
  const chartHost = h("div", { class: "g-wrap" });
  const metricChips = h("div", { class: "chips g-chips", role: "tablist", "aria-label": "지표" }, ...names.map((nm) => h("button", {
    type: "button", class: "chip" + (nm === st.metric ? " active" : ""), role: "tab", text: showName(nm),
    onclick: () => { st.metric = nm; render(); },
  })));
  const modeSeg = h("div", { class: "segmented g-seg", role: "tablist", "aria-label": "보기 방식" },
    h("button", { type: "button", class: st.mode === "value" ? "active" : "", text: "값", onclick: () => { st.mode = "value"; render(); } }),
    h("button", { type: "button", class: st.mode === "diff" ? "active" : "", text: "편차(본년 − 기준)", onclick: () => { st.mode = "diff"; render(); } }));
  const baseSel = st.mode === "diff" && bases.length ? h("label", { class: "field inline g-base" }, h("span", { text: "기준" }),
    h("select", { onchange: (e) => { st.diffBase = e.target.value; render(); } },
      ...bases.map((b) => h("option", { value: b.key, ...(b.key === st.diffBase ? { selected: true } : {}), text: SHORT[b.key] })))) : null;
  const smooth = h("label", { class: "switch g-smooth" },
    h("input", { type: "checkbox", ...(st.smooth ? { checked: true } : {}), onchange: (e) => { st.smooth = e.target.checked; render(); } }),
    h("span", { class: "switch-ui" }), h("span", { text: "7일 평균" }));
  const names2 = (st.res.params.stations || []).join("·");
  host.replaceChildren(
    metricChips,
    h("div", { class: "g-row" }, h("p", { class: "g-summary", text: summaryText() }), h("div", { class: "g-opts" }, modeSeg, baseSel, smooth)),
    chartHost,
    h("p", { class: "g-note", text: `${names2} 선택 지점의 날짜별 평균값입니다. 강수량·일조 시간은 그날 하루의 값이고, 비교 기준은 같은 날짜의 여러 해 평균입니다. 비교 기준은 위 '비교 기준' 버튼에서 고를 수 있습니다.` }),
  );
  if (bases.length && st.mode === "diff" && !bases.some((b) => b.key === st.diffBase)) st.diffBase = bases[bases.length - 1].key;
  requestAnimationFrame(() => buildChart(chartHost));
}

// ---------------------------------------------------------------- 진입점
/** 새 조회 결과 (접는 칸이 열려 있으면 바로 그림) */
export function setResult(res) {
  st.res = res && res.ok ? res : null;
  const key = st.res ? `${res.params.start}_${res.params.end}_${(res.params.stations || []).join()}` : "";
  if (key !== st.resKey) {                      // 다른 기간·지점이면 전체 기간 보기로
    st.resKey = key;
    const n = st.res && st.res.chart ? st.res.chart.dates.length : 0;
    st.i0 = 0; st.i1 = Math.max(0, n - 1);
  }
  const fold = document.getElementById("graph-fold");
  if (fold) fold.hidden = !st.res;
  if (fold && fold.open) render();
}

/** 비교 기준 칩이 바뀌었을 때 */
export function refresh() {
  const fold = document.getElementById("graph-fold");
  if (st.res && fold && fold.open) render();
}

export function setup() {
  const fold = document.getElementById("graph-fold");
  if (!fold) return;
  fold.addEventListener("toggle", () => { if (fold.open && st.res) render(); });
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (st.res && fold.open) render(); }, 200);
  });
}

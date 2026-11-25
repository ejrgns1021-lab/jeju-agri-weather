// trend.js (화면, v7.8) — 대시보드 아래 [그래프로 보기] 접는 칸
//   탭 4개 × 보기 2가지
//    [추이 보기]  평균기온: 꺾은선(금년 굵은 선+점) / 최고·최저기온: 연도별 두 선+금년 범위 채움
//                누적강수량·일조시간: 첫날부터 누적 곡선(금년 영역+실선, 기준 점선) + 아래 칸 금년 일별 막대
//    [편차 보기(±)] 기준을 0선으로 — 기온·일조: 날짜별 (금년 − 기준) 막대(높음 주황·낮음 파랑)
//                  누적강수량: (금년 누적 − 기준 누적) 영역(위 파랑=많음, 아래 갈색=적음)
//   비교 기준: 위 '비교 기준' 버튼(bases.js)에서 켠 기준만 쓸 수 있고, 그래프 범례 칩으로 선을 하나씩 켜고 끔
//             (처음엔 평년·전년만 켜서 깔끔하게) / 편차 보기에서는 칩이 '○○ 대비' 하나 고르기로 동작
//   요약 배지·이미지 복사: trend_export.js / 확대·축소·이동: Ctrl(⌘)+휠, 끌기, Shift+휠, 기간 이동 막대, [+][−][전체]
//   서버가 보낸 일별 값을 그리기만 함(누적·편차만 화면에서 계산). 순수 SVG.
import { isOn } from "./bases.js";
import { summaryBadges, copyChartImage, LIGHT, mmdd } from "./trend_export.js";

const NS = "http://www.w3.org/2000/svg";
const TABS = [
  { key: "avg", label: "평균기온", metrics: ["평균 기온"], unit: "℃" },
  { key: "maxmin", label: "최고·최저기온", metrics: ["최고 기온", "최저 기온"], unit: "℃" },
  { key: "rain", label: "누적강수량", metrics: ["누적 강수량"], unit: "mm" },
  { key: "sun", label: "일조시간", metrics: ["일조 시간"], unit: "hr" },
];
const BASES = [   // 선 모양까지 정해 두어 색만으로 구분하지 않게
  { key: "prev", label: "전년", cls: "s-prev", style: "" },
  { key: "r3", label: "최근 3년", cls: "s-r3", style: "dot" },
  { key: "r5", label: "최근 5년", cls: "s-r5", style: "" },
  { key: "normal", label: "평년", cls: "s-normal", style: "dash" },
];
const DEFAULT_SHOWN = ["normal", "prev"];
const MIN_DAYS = 7;
const st = { res: null, resKey: "", tab: "avg", mode: "trend", shown: null, diffBase: null, diffUser: false, sub: "max", i0: 0, i1: 0 };
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
const tabDef = () => TABS.find((t) => t.key === st.tab);
const baseDef = (k) => BASES.find((b) => b.key === k);

function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) hi = lo + 1;
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) || raw;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(+v.toFixed(6));
  return out;
}
/** 누적합 (빈 날은 0으로 이어 가되, 마지막 자료 이후는 비움) */
function cumulate(arr) {
  let last = -1;
  (arr || []).forEach((v, i) => { if (ok(v)) last = i; });
  let sum = 0;
  return (arr || []).map((v, i) => { if (i > last) return null; sum += ok(v) ? v : 0; return +sum.toFixed(2); });
}
function yearLabels() {
  const sm = st.res.summary || {}, ch = st.res.chart || {};
  return {
    cur: `금년(${sm.currentYear || ""})`, prev: `전년(${sm.prevYear || ch.prevYear || ""})`,
    r3: `최근 3년(${sm.recent3Range || ch.recent3Range || ""})`, r5: `최근 5년(${sm.recentRange || ch.recentRange || ""})`,
    normal: "평년(1991~2020)",
  };
}

// ---------------------------------------------------------------- 비교 기준 (위 버튼과 연동)
/** 이 탭에서 쓸 수 있는 기준 = 위 '비교 기준'에서 켠 것 중 자료가 있는 것 */
function availBases() {
  const M = st.res.chart.metrics;
  return BASES.filter((b) => isOn(b.key) && tabDef().metrics.some((m) => (M[m] && M[m][b.key] || []).some(ok))).map((b) => b.key);
}
function syncBases() {
  const avail = availBases();
  if (st.shown === null) st.shown = DEFAULT_SHOWN.filter((k) => avail.includes(k));
  st.shown = st.shown.filter((k) => avail.includes(k));
  if (!st.shown.length && avail.length) st.shown = [avail.includes("normal") ? "normal" : avail[0]];
  // 편차 기준: 사용자가 직접 고르지 않았으면 평년 우선(평년이 꺼졌다 다시 켜지면 평년으로 돌아옴)
  if (!avail.includes(st.diffBase) || (!st.diffUser && avail.includes("normal"))) {
    if (!avail.includes(st.diffBase)) st.diffUser = false;
    st.diffBase = avail.includes("normal") ? "normal" : (avail[0] || null);
  }
  return avail;
}

// ---------------------------------------------------------------- 탭·보기별 그릴 자료
function prepare() {
  const M = st.res.chart.metrics;
  const tab = tabDef();
  const get = (m, k) => ((M[m] || {})[k] || []);
  const Y = yearLabels();
  const P = { tab, lines: [], bars: null, barCls: "", diffBars: null, diffArea: null, unit: tab.unit };
  const cumTab = tab.key === "rain" || tab.key === "sun";
  const metric = tab.key === "maxmin" ? (st.mode === "diff" ? (st.sub === "min" ? "최저 기온" : "최고 기온") : null) : tab.metrics[0];

  if (st.mode === "diff") {
    const b = st.diffBase;
    P.baseKey = b; P.baseLabel = b ? baseDef(b).label : "";
    if (!b) return P;
    if (tab.key === "rain") {
      const cc = cumulate(get(metric, "cur")), cb = cumulate(get(metric, b));
      P.cur = cc; P.ref = cb;
      P.diffArea = cc.map((v, i) => (ok(v) && ok(cb[i]) ? +(v - cb[i]).toFixed(2) : null));
    } else {
      const c = get(metric, "cur"), r = get(metric, b);
      P.cur = c; P.ref = r;
      P.diffBars = c.map((v, i) => (ok(v) && ok(r[i]) ? +(v - r[i]).toFixed(2) : null));
    }
    P.metric = metric;
    return P;
  }
  // 추이 보기: 기준선(뒤) → 금년(앞)
  const shown = BASES.filter((b) => st.shown.includes(b.key));
  if (tab.key === "maxmin") {
    shown.forEach((b) => ["max", "min"].forEach((sub) => P.lines.push({
      key: b.key, sub, cls: `${b.cls} ${b.style}`, vals: get(sub === "max" ? "최고 기온" : "최저 기온", b.key), label: Y[b.key] })));
    ["max", "min"].forEach((sub) => P.lines.push({ key: "cur", sub, cls: "s-cur main", vals: get(sub === "max" ? "최고 기온" : "최저 기온", "cur"), label: Y.cur }));
    P.band = true;
  } else if (cumTab) {
    const daily = get(metric, "cur");
    shown.forEach((b) => P.lines.push({ key: b.key, cls: `${b.cls} ${b.style || "dash"}`, vals: cumulate(get(metric, b.key)), label: Y[b.key] }));
    P.lines.push({ key: "cur", cls: `${tab.key === "rain" ? "s-rain" : "s-sun"} main`, vals: cumulate(daily), label: Y.cur,
      area: tab.key === "rain" ? "a-rain" : "a-sun" });
    P.bars = daily; P.barCls = tab.key === "rain" ? "b-rain" : "b-sun";
  } else {
    shown.forEach((b) => P.lines.push({ key: b.key, cls: `${b.cls} ${b.style}`, vals: get(metric, b.key), label: Y[b.key] }));
    P.lines.push({ key: "cur", cls: "s-cur main", vals: get(metric, "cur"), label: Y.cur, points: true });
  }
  P.lines = P.lines.filter((l) => l.vals.some(ok));
  return P;
}

// ---------------------------------------------------------------- 그리기
const PAD = { l: 46, r: 10, t: 10, b: 22 };

function drawMain() {
  const { gridG, dataG, W, H, P } = ui;
  const dates = st.res.chart.dates;
  const n = dates.length;
  const i0 = st.i0, i1 = st.i1, span = Math.max(1, i1 - i0);
  const plotW = W - PAD.l - PAD.r;
  const barH = P.bars ? Math.round(H * 0.2) : 0;
  const mainB = H - PAD.b - (P.bars ? barH + 10 : 0);
  const x = (i) => PAD.l + ((i - i0) / span) * plotW;
  const inView = (i) => i >= i0 - 1 && i <= i1 + 1;
  const inWin = (v, i) => i >= i0 && i <= i1 && ok(v);

  // 세로축 범위(보이는 구간 기준)
  let vals, lo, hi;
  const diffMode = st.mode === "diff";
  if (diffMode) {
    vals = (P.diffBars || P.diffArea || []).filter(inWin);
    const a = Math.max(0.5, ...vals.map(Math.abs));
    lo = -a; hi = a;
  } else {
    vals = P.lines.flatMap((l) => l.vals.filter(inWin));
    if (!vals.length) vals = [0, 1];
    lo = Math.min(...vals); hi = Math.max(...vals);
    if (P.tab.key === "rain" || P.tab.key === "sun") { lo = 0; hi = Math.max(hi * 1.05, 1); }
    else { const p = Math.max(0.5, (hi - lo) * 0.08); lo -= p; hi += p; }
  }
  const ticks = niceTicks(lo, hi, H < 270 ? 4 : 5);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
  const y = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * (mainB - PAD.t);
  ui.x = x; ui.y = y;

  gridG.replaceChildren();
  dataG.replaceChildren();
  ticks.forEach((t) => {
    gridG.append(s("line", { x1: PAD.l, x2: W - PAD.r, y1: y(t), y2: y(t), class: diffMode && t === 0 ? "t-zero" : "t-grid" }));
    const tx = s("text", { x: PAD.l - 6, y: y(t) + 4, class: "t-tick", "text-anchor": "end" });
    tx.textContent = (diffMode && t > 0 ? "+" : "") + fmt(t, Number.isInteger(t) ? 0 : 1); gridG.append(tx);
  });
  const maxLabels = Math.max(2, Math.floor(plotW / 54));
  const step = Math.max(1, Math.ceil((span + 1) / maxLabels));
  for (let i = i0; i <= i1; i += step) {
    const tx = s("text", { x: x(i), y: H - 6, class: "t-tick", "text-anchor": i === i0 ? "start" : "middle" });
    tx.textContent = mmdd(dates[i]); gridG.append(tx);
  }
  const bw = Math.max(1, Math.min(12, (plotW / (span + 1)) * 0.7));
  const rect = (i, v0, v1, cls, yy) => {
    const a = yy(v0), b = yy(v1);
    dataG.append(s("rect", { x: x(i) - bw / 2, y: Math.min(a, b), width: bw, height: Math.max(0.5, Math.abs(b - a)), class: cls, rx: bw >= 6 ? 2 : 0 }));
  };

  if (diffMode) {
    if (P.diffBars) P.diffBars.forEach((v, i) => { if (ok(v) && inView(i)) rect(i, 0, v, v >= 0 ? "t-pos" : "t-neg", y); });
    if (P.diffArea) {     // 누적 편차: 0선 위(많음)·아래(적음)를 서로 다른 색으로
      const y0 = y(0);
      ui.clipUp.setAttribute("y", 0); ui.clipUp.setAttribute("height", Math.max(0, y0));
      ui.clipDn.setAttribute("y", y0); ui.clipDn.setAttribute("height", Math.max(0, H - y0));
      let d = "", first = null, last = null;
      P.diffArea.forEach((v, i) => { if (!ok(v) || !inView(i)) return; d += `${first === null ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; if (first === null) first = i; last = i; });
      if (first !== null) {
        const area = `${d}L${x(last).toFixed(1)},${y0.toFixed(1)}L${x(first).toFixed(1)},${y0.toFixed(1)}Z`;
        dataG.append(s("path", { d: area, class: "t-wet", "clip-path": "url(#t-clip-up)" }));
        dataG.append(s("path", { d: area, class: "t-dry", "clip-path": "url(#t-clip-dn)" }));
        dataG.append(s("path", { d, class: "t-line s-ink" }));
      }
    }
  } else {
    if (P.bars) {   // 아래 칸: 금년 일별 막대(자체 눈금)
      const top = mainB + 10, bot = H - PAD.b;
      const bv = P.bars.filter(inWin);
      const bmax = niceTicks(0, Math.max(1, ...bv), 2).slice(-1)[0];
      const by = (v) => bot - (v / bmax) * (bot - top);
      gridG.append(s("line", { x1: PAD.l, x2: W - PAD.r, y1: bot, y2: bot, class: "t-grid" }));
      const lt = s("text", { x: PAD.l - 6, y: top + 8, class: "t-tick", "text-anchor": "end" }); lt.textContent = fmt(bmax, 0); gridG.append(lt);
      const cap = s("text", { x: PAD.l + 4, y: top + 9, class: "t-cap" });
      cap.textContent = P.tab.key === "rain" ? "금년 일 강수량(mm)" : "금년 일 일조시간(hr)"; gridG.append(cap);
      P.bars.forEach((v, i) => { if (ok(v) && v > 0 && inView(i)) rect(i, 0, v, `t-bar ${P.barCls}`, by); });
    }
    if (P.band) {
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
    P.lines.forEach((l) => {
      let d = "", pen = false, first = null, lastI = null;
      l.vals.forEach((v, i) => {
        if (!ok(v) || !inView(i)) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
        if (first === null) first = i; lastI = i;
      });
      if (l.area && first !== null) dataG.append(s("path", { d: `${d}L${x(lastI).toFixed(1)},${y(lo).toFixed(1)}L${x(first).toFixed(1)},${y(lo).toFixed(1)}Z`, class: `t-area ${l.area}` }));
      dataG.append(s("path", { d, class: `t-line ${l.cls}` }));
      if (l.points && span <= 62) l.vals.forEach((v, i) => { if (inWin(v, i)) dataG.append(s("circle", { cx: x(i), cy: y(v), r: span <= 31 ? 3 : 2.2, class: "t-pt" })); });
    });
  }

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

// ---------------------------------------------------------------- 툴팁
function hideTip() {
  if (!ui) return;
  ui.tip.hidden = true; ui.cross.setAttribute("visibility", "hidden");
  ui.hdots.forEach((d) => d.remove()); ui.hdots = [];
}
function showTip(i) {
  const { P } = ui;
  const unit = P.unit, Y = yearLabels();
  const date = mmdd(st.res.chart.dates[i]);
  ui.cross.setAttribute("x1", ui.x(i)); ui.cross.setAttribute("x2", ui.x(i)); ui.cross.setAttribute("visibility", "visible");
  ui.hdots.forEach((d) => d.remove()); ui.hdots = [];
  const sw = (cls) => h("span", { class: `t-sw ${cls}` });
  const row = (cls, label, val) => h("div", { class: "t-tip-row" }, sw(cls), h("span", { text: label }), h("b", { text: val }));
  const strip = (l) => l.replace(/\(.*\)/, "");
  const rows = [];
  let head = date, foot = null;
  if (st.mode === "diff") {
    if (!P.baseKey) return;
    const cur = P.cur[i], ref = P.ref[i], d = (P.diffBars || P.diffArea)[i];
    const cum = P.tab.key === "rain";
    head = cum ? `${date}까지 누적` : P.tab.key === "maxmin" ? `${date} ${st.sub === "min" ? "최저기온" : "최고기온"}` : date;
    rows.push(row(P.tab.key === "rain" ? "s-rain main" : "s-cur main", "금년", `${fmt(cur)}${unit}`));
    rows.push(row(`${baseDef(P.baseKey).cls} ${baseDef(P.baseKey).style}`, P.baseLabel, `${fmt(ref)}${unit}`));
    const pct = unit !== "℃" && ok(cur) && ok(ref) && ref > 0 ? ` (${Math.round((cur / ref) * 100)}%)` : "";
    foot = `편차(금년 − ${P.baseLabel}): ${sgn(d)}${unit}${pct}`;
  } else {
    P.lines.forEach((l) => {
      if (!ok(l.vals[i])) return;
      const c = s("circle", { r: 4, cx: ui.x(i), cy: ui.y(l.vals[i]), class: `t-hdot ${l.cls.split(" ")[0]}` }); ui.overG.append(c); ui.hdots.push(c);
    });
    const seen = new Set();
    [...P.lines].reverse().forEach((l) => {
      if (seen.has(l.key)) return; seen.add(l.key);
      if (P.tab.key === "maxmin") {
        const mx = P.lines.find((q) => q.key === l.key && q.sub === "max"), mn = P.lines.find((q) => q.key === l.key && q.sub === "min");
        rows.push(row(l.cls, strip(Y[l.key]), `${fmt(mx && mx.vals[i])} / ${fmt(mn && mn.vals[i])}${unit}`));
      } else rows.push(row(l.cls, strip(Y[l.key]), `${fmt(l.vals[i])}${unit}`));
    });
    if (P.tab.key === "rain" || P.tab.key === "sun") head = `${date}까지 누적` + (ok(P.bars[i]) ? ` · 이날 ${fmt(P.bars[i])}${unit}` : "");
    if (P.tab.key === "maxmin") head = `${date} (최고 / 최저)`;
    const ref = st.diffBase && st.shown.includes(st.diffBase) ? st.diffBase : st.shown[0];
    const c = P.lines.find((l) => l.key === "cur" && (!l.sub || l.sub === "max")), r = P.lines.find((l) => l.key === ref && (!l.sub || l.sub === "max"));
    if (c && r && ok(c.vals[i]) && ok(r.vals[i])) foot = `금년 − ${baseDef(ref).label}${P.tab.key === "maxmin" ? "(최고)" : ""}: ${sgn(c.vals[i] - r.vals[i])}${unit}`;
  }
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
    return { px: clientX - r.left, idx: ((clientX - r.left) / r.width) * (n - 1),
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

// ---------------------------------------------------------------- 범례(이미지용)
function legendItems(P) {
  const Y = yearLabels();
  if (st.mode === "diff") {
    if (!P.baseKey) return [];
    const up = P.tab.key === "rain" ? "많음" : "높음", dn = P.tab.key === "rain" ? "적음" : "낮음";
    return P.tab.key === "rain"
      ? [{ label: `${P.baseLabel}보다 ${up}(누적)`, color: LIGHT.wet, type: "area" }, { label: `${P.baseLabel}보다 ${dn}(누적)`, color: LIGHT.dry, type: "area" }]
      : [{ label: `${P.baseLabel}보다 ${up}`, color: LIGHT.pos, type: "pos" }, { label: `${P.baseLabel}보다 ${dn}`, color: LIGHT.neg, type: "neg" }];
  }
  const curColor = P.tab.key === "rain" ? LIGHT.rain : P.tab.key === "sun" ? LIGHT.sun : LIGHT.cur;
  const items = [{ label: Y.cur, color: curColor, type: "line", main: true }];
  BASES.filter((b) => st.shown.includes(b.key)).forEach((b) => items.push({ label: Y[b.key], color: LIGHT[b.key],
    type: (P.tab.key === "rain" || P.tab.key === "sun") && !b.style ? "dash" : (b.style || "line") }));
  if (P.bars) items.push({ label: P.tab.key === "rain" ? "금년 일 강수량" : "금년 일 일조시간", color: P.tab.key === "rain" ? LIGHT.rainBar : LIGHT.sunBar, type: "bar" });
  return items;
}

// ---------------------------------------------------------------- 화면 구성
function buildChart(host) {
  const P = prepare();
  const n = st.res.chart.dates.length;
  const W = Math.max(280, Math.round(host.clientWidth || 600));
  const H = W < 520 ? 250 : 280;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "t-svg t-main", role: "img",
    "aria-label": `${P.tab.label} ${st.mode === "diff" ? "편차" : "추이"} 그래프` });
  const defs = s("defs");
  const clip = s("clipPath", { id: "t-clip" }); clip.append(s("rect", { x: PAD.l, y: 0, width: W - PAD.l - PAD.r, height: H }));
  const cu = s("clipPath", { id: "t-clip-up" }), clipUp = s("rect", { x: PAD.l, y: 0, width: W - PAD.l - PAD.r, height: 0 }); cu.append(clipUp);
  const cd = s("clipPath", { id: "t-clip-dn" }), clipDn = s("rect", { x: PAD.l, y: 0, width: W - PAD.l - PAD.r, height: 0 }); cd.append(clipDn);
  defs.append(clip, cu, cd);
  const gridG = s("g"), dataG = s("g", { "clip-path": "url(#t-clip)" }), overG = s("g");
  const cross = s("line", { y1: PAD.t, y2: H - PAD.b, class: "t-cross", visibility: "hidden" });
  overG.append(cross);
  svg.append(defs, gridG, dataG, overG);
  const tip = h("div", { class: "t-tip", hidden: true });

  // 기간 이동 막대
  const navW = W, navH = 34;
  const nav = s("svg", { viewBox: `0 0 ${navW} ${navH}`, width: navW, height: navH, class: "t-svg t-nav", "aria-label": "기간 이동 막대" });
  const ov = st.mode === "diff" ? (P.diffBars || P.diffArea || []) : ((P.lines.find((l) => l.key === "cur") || { vals: [] }).vals);
  const cv = ov.filter(ok);
  if (cv.length) {
    let lo = Math.min(...cv), hi = Math.max(...cv); if (!(hi > lo)) hi = lo + 1;
    const nxp = (i) => (i / Math.max(1, n - 1)) * navW, nyp = (v) => 4 + (1 - (v - lo) / (hi - lo)) * (navH - 8);
    let d = "", pen = false;
    ov.forEach((v, i) => { if (!ok(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${nxp(i).toFixed(1)},${nyp(v).toFixed(1)}`; pen = true; });
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

  const empty = st.mode === "diff" && !P.baseKey
    ? h("p", { class: "muted small", text: "편차를 계산할 비교 기준이 없습니다. 위 '비교 기준'에서 하나 이상 켜 주세요." }) : null;
  host.replaceChildren(...(empty ? [empty] : [h("div", { class: "t-plot" }, svg, tip), zoomRow]));
  ui = { svg, gridG, dataG, overG, cross, hdots: [], tip, W, H, nav, navW, navWin, navShadeL, navShadeR, clipUp, clipDn,
    range, btnIn, btnOut, btnAll, zoomRow, P };
  if (empty) return;
  bindMain();
  bindNav();
  drawMain();
}

/** 범례 겸 기준 선택 칩 */
function legendChips(avail) {
  const Y = yearLabels();
  const tab = tabDef();
  const curCls = tab.key === "rain" ? "s-rain main" : tab.key === "sun" ? "s-sun main" : "s-cur main";
  if (st.mode === "diff") {
    if (!avail.length) return null;
    return h("div", { class: "t-chips", role: "radiogroup", "aria-label": "편차 기준" },
      h("span", { class: "t-chips-k", text: "기준" }),
      ...avail.map((k) => h("button", { type: "button", role: "radio", class: `t-chip${k === st.diffBase ? " on" : ""}`,
        "aria-checked": k === st.diffBase ? "true" : "false", onclick: () => { st.diffBase = k; st.diffUser = true; render(); } },
        h("span", { text: `${baseDef(k).label} 대비` }))),
      h("span", { class: "t-leg-sep" }),
      h("span", { class: "t-leg" }, h("span", { class: `t-box ${tab.key === "rain" ? "t-wet" : "t-pos"}` }), h("span", { text: tab.key === "rain" ? "많음" : "높음" })),
      h("span", { class: "t-leg" }, h("span", { class: `t-box ${tab.key === "rain" ? "t-dry" : "t-neg"}` }), h("span", { text: tab.key === "rain" ? "적음" : "낮음" })));
  }
  return h("div", { class: "t-chips", "aria-label": "범례 · 누르면 기준선 켜기/끄기" },
    h("span", { class: "t-leg static" }, h("span", { class: `t-sw ${curCls}${tab.key === "rain" || tab.key === "sun" ? " area" : ""}` }), h("span", { text: Y.cur })),
    ...avail.map((k) => {
      const b = baseDef(k), on = st.shown.includes(k);
      const style = (tab.key === "rain" || tab.key === "sun") && !b.style ? "dash" : b.style;
      return h("button", { type: "button", class: `t-chip${on ? " on" : ""}`, "aria-pressed": on ? "true" : "false",
        title: on ? "누르면 이 선을 숨김" : "누르면 이 선을 표시",
        onclick: () => { st.shown = on ? st.shown.filter((x) => x !== k) : [...st.shown, k]; render(); } },
      h("span", { class: `t-sw ${b.cls} ${style}` }), h("span", { text: Y[k] }));
    }),
    tab.key === "rain" || tab.key === "sun" ? h("span", { class: "t-leg static" }, h("span", { class: `t-box ${tab.key === "rain" ? "b-rain" : "b-sun"}` }),
      h("span", { text: tab.key === "rain" ? "금년 일 강수량" : "금년 일 일조" })) : null,
    tab.key === "maxmin" ? h("span", { class: "t-leg static muted", text: "위 선 최고 · 아래 선 최저" }) : null);
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
  const avail = syncBases();
  const badgeBase = st.diffBase;
  const badges = summaryBadges(st.tab, ch, badgeBase, badgeBase ? baseDef(badgeBase).label : "");
  const chartHost = h("div", { class: "t-wrap" });
  const copyBtn = h("button", { type: "button", class: "btn ghost small t-copy", title: "보고서용 그림(PNG)으로 복사" },
    h("span", { text: "이미지 복사" }));
  copyBtn.addEventListener("click", () => doCopy(copyBtn, badges));
  const names = (st.res.params.stations || []).join("·");
  host.replaceChildren(
    h("div", { class: "segmented t-tabs", role: "tablist", "aria-label": "지표" }, ...tabs.map((t) => h("button", {
      type: "button", role: "tab", class: t.key === st.tab ? "active" : "", "aria-selected": t.key === st.tab ? "true" : "false",
      text: t.label, onclick: () => { st.tab = t.key; render(); },
    }))),
    h("div", { class: "t-badges" }, ...badges.map((b) => h("span", { class: "t-badge", text: b }))),
    h("div", { class: "t-toolbar" },
      h("div", { class: "segmented t-mode", role: "tablist", "aria-label": "보기 방식" },
        h("button", { type: "button", class: st.mode === "trend" ? "active" : "", text: "추이 보기", onclick: () => { st.mode = "trend"; render(); } }),
        h("button", { type: "button", class: st.mode === "diff" ? "active" : "", text: "편차 보기(±)", onclick: () => { st.mode = "diff"; render(); } })),
      st.mode === "diff" && st.tab === "maxmin" ? h("div", { class: "segmented t-sub", role: "tablist", "aria-label": "최고·최저" },
        h("button", { type: "button", class: st.sub === "max" ? "active" : "", text: "최고기온 편차", onclick: () => { st.sub = "max"; render(); } }),
        h("button", { type: "button", class: st.sub === "min" ? "active" : "", text: "최저기온 편차", onclick: () => { st.sub = "min"; render(); } })) : null,
      copyBtn),
    legendChips(avail),
    chartHost,
    h("p", { class: "t-note", text: `${names} 선택 지점의 날짜별 평균값 · 비교 기준은 같은 날짜의 값(평년은 1991~2020 평균) · 위 '비교 기준'에서 켠 기준만 고를 수 있음`
      + (st.tab === "rain" || st.tab === "sun" ? " · 누적은 조회 첫날부터 더한 값" : "")
      + (st.mode === "diff" && st.tab === "rain" ? " · 편차는 (금년 누적 − 기준 누적)" : "") }),
  );
  requestAnimationFrame(() => buildChart(chartHost));
}

async function doCopy(btn, badges) {
  if (!ui || !ui.svg || !ui.svg.isConnected) return;
  const p = st.res.params, tab = tabDef();
  const label = btn.querySelector("span");
  btn.disabled = true; label.textContent = "만드는 중…";
  try {
    const modeTxt = st.mode === "diff" ? ` · 편차(금년 − ${ui.P.baseLabel})${tab.key === "maxmin" ? (st.sub === "min" ? " 최저" : " 최고") : ""}` : "";
    const dates = st.res.chart.dates;
    const res = await copyChartImage({
      title: `${tab.label}${st.tab === "rain" || st.tab === "sun" ? "(누적)" : ""} — ${(p.stations || []).join("·")}`,
      sub: `조회 기간 ${p.start.replaceAll("-", ".")} ~ ${p.end.replaceAll("-", ".")}` + (st.i1 - st.i0 + 1 < dates.length ? ` (그래프 구간 ${mmdd(dates[st.i0])}~${mmdd(dates[st.i1])})` : "") + modeTxt,
      badges, legend: legendItems(ui.P), svg: ui.svg,
      source: "자료: 기상청 관측(선택 지점 평균) · 제주 농업 기상계산기",
      fileName: `기상그래프_${tab.label}_${p.start}_${p.end}.png`.replace(/[·\s]/g, ""),
    });
    label.textContent = res === "copied" ? "복사됨 ✓" : "파일로 저장됨 ✓";
  } catch (e) {
    label.textContent = "복사 실패";
  }
  setTimeout(() => { btn.disabled = false; label.textContent = "이미지 복사"; }, 2000);
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
/** 위 '비교 기준' 버튼이 바뀌었을 때 */
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

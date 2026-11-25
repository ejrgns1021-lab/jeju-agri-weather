// glance.js (화면, v7.7) — '한눈에 보는 기상 비교' (가로 불릿 차트)
//   지표마다 한 줄: 금년 값은 굵은 막대, 비교 기준은 트랙 위 표시(전년 ┃ 세로선, 평년 ◆, 최근 5년 ●, 최근 3년 ○)
//   · 지표마다 눈금이 따로라 기온(℃)·강수(mm)·일조(hr)·일수(일)가 섞여도 서로 눌리지 않음
//   · 눈금 범위 = 그 지표의 금년·기준값 중 최솟값~최댓값 ± 15% (차이가 잘 보이도록)
//   · 위 '비교 기준' 버튼(전년·최근 3년·최근 5년·평년)에서 고른 기준만 표시 · 오른쪽 배지도 고른 기준 대비
// 서버가 계산해 보낸 선택 지점 평균값을 그리기만 합니다 (순수 HTML·CSS, 차트 라이브러리 없음).
import { isOn } from "./bases.js";

const BASES = [   // 표시 순서·모양
  { key: "prev", label: "전년", mark: "line" },
  { key: "r3", label: "최근 3년", mark: "ring" },
  { key: "r5", label: "최근 5년", mark: "dot" },
  { key: "normal", label: "평년", mark: "diamond" },
];
// 막대 색의 뜻: 지표 종류별로 '높음·많음'과 '낮음·적음'
const TONE = {
  temp: { up: "warm", down: "cool", upTxt: "높음", downTxt: "낮음" },
  rain: { up: "wet", down: "dry", upTxt: "많음", downTxt: "적음" },
  sun: { up: "sunny", down: "dim", upTxt: "많음", downTxt: "적음" },
  rdays: { up: "wet", down: "dry", upTxt: "많음", downTxt: "적음" },
  night: { up: "warm", down: "cool", upTxt: "많음", downTxt: "적음" },
};
const KIND = { "평균 기온": "temp", "최고 기온": "temp", "최저 기온": "temp", "누적 강수량": "rain", "일조 시간": "sun", "강우일수": "rdays", "열대야 일수": "night" };

const ok = (v) => v !== null && v !== undefined && !Number.isNaN(Number(v));
const fmt = (v, d = 1) => (ok(v) ? Number(v).toFixed(d) : "-");

function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k === "style") e.style.cssText = v;
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  kids.flat().forEach((c) => c && e.append(c));
  return e;
}

let last = null;

/** 색을 정하는 기준: 평년 → 전년 → 최근 5년 → 최근 3년 중 고른 것 */
function primaryBase(vals) {
  return ["normal", "prev", "r5", "r3"].find((k) => isOn(k) && ok(vals[k])) || null;
}

function badge(name, vals, base, unit) {
  const cur = Number(vals.cur), ref = Number(vals[base.key]);
  const d = cur - ref;
  const kind = KIND[name] || "temp";
  const showRatio = kind !== "temp" && ref > 0;
  const tone = Math.abs(d) < 0.05 ? "same" : (d > 0 ? TONE[kind].up : TONE[kind].down);
  return h("span", { class: `gl-badge t-${tone}`, title: `${base.label} ${fmt(ref)}${unit}` },
    h("span", { class: "gl-badge-k", text: base.label }),
    h("b", { text: `${d >= 0 ? "+" : "−"}${fmt(Math.abs(d))}${unit}` + (showRatio ? ` / ${Math.round((cur / ref) * 100)}%` : "") }));
}

function row(name, vals, unit, years) {
  const kind = KIND[name] || "temp";
  const on = BASES.filter((b) => isOn(b.key) && ok(vals[b.key]));
  const nums = [vals.cur, ...on.map((b) => vals[b.key])].filter(ok).map(Number);
  let lo = Math.min(...nums), hi = Math.max(...nums);
  const span = hi - lo || Math.max(Math.abs(hi) * 0.1, 1);
  lo -= span * 0.15; hi += span * 0.15;
  if (kind !== "temp" && lo < 0) lo = 0;                 // 강수·일조·일수는 0보다 작아질 수 없음
  const pos = (v) => `${(((Number(v) - lo) / (hi - lo)) * 100).toFixed(2)}%`;
  const pb = primaryBase(vals);
  const d = pb ? Number(vals.cur) - Number(vals[pb]) : 0;
  const tone = !pb || Math.abs(d) < 0.05 ? "same" : (d > 0 ? TONE[kind].up : TONE[kind].down);

  const track = h("div", { class: "gl-track", role: "img",
    "aria-label": `${name}: 금년 ${fmt(vals.cur)}${unit}` + on.map((b) => `, ${b.label} ${fmt(vals[b.key])}${unit}`).join("") });
  if (ok(vals.cur)) track.append(h("div", { class: `gl-bar t-${tone}`, style: `width:${pos(vals.cur)}` }));
  on.forEach((b) => track.append(h("span", { class: `gl-mk m-${b.mark}`, style: `left:${pos(vals[b.key])}`,
    title: `${b.label === "전년" ? `전년(${years.prev})` : b.label} ${fmt(vals[b.key])}${unit}` })));
  const scale = h("div", { class: "gl-scale" }, h("span", { text: fmt(lo) }), h("span", { text: fmt(hi) }));

  return h("div", { class: "gl-row" },
    h("div", { class: "gl-name" }, h("span", { text: name }), h("small", { text: unit })),
    h("div", { class: "gl-gauge" }, track, scale),
    h("div", { class: "gl-val" }, h("strong", { text: fmt(vals.cur) }), h("small", { text: unit })),
    h("div", { class: "gl-badges" }, ...on.slice(0, 4).map((b) => badge(name, vals, b, unit))));
}

export function renderGlance(res) {
  last = res;
  const card = document.getElementById("glance-card");
  const host = document.getElementById("glance-body");
  if (!card || !host) return;
  const avg = res && res.ok && res.stationReport && res.stationReport.average;
  card.hidden = !avg;
  if (!avg) return;
  const units = res.stationReport.units || {};
  const sm = res.summary || {};
  const years = { cur: sm.currentYear || "", prev: sm.prevYear || "" };
  const on = BASES.filter((b) => isOn(b.key));
  const legend = h("div", { class: "gl-legend" },
    h("span", { class: "gl-leg" }, h("span", { class: "gl-sw-bar" }), h("span", { text: `금년(${years.cur})` })),
    ...on.map((b) => h("span", { class: "gl-leg" }, h("span", { class: `gl-mk m-${b.mark} gl-mk-leg` }),
      h("span", { text: b.key === "prev" ? `전년(${years.prev})` : b.key === "normal" ? "평년(1991~2020)" : b.label }))));
  const rows = res.params.metrics.filter((m) => avg[m] && ok(avg[m].cur)).map((m) => row(m, avg[m], units[m] || "", years));
  const pb = ["normal", "prev", "r5", "r3"].find((k) => isOn(k));
  const pbLabel = (BASES.find((b) => b.key === pb) || {}).label || "기준";
  host.replaceChildren(legend, h("div", { class: "gl-list" }, ...rows),
    h("p", { class: "gl-note", text: `막대 색: ${pbLabel}보다 높음·많음은 기온 주황·강수 파랑·일조 노랑, 낮음·적음은 기온 파랑·강수 갈색·일조 회색. `
      + "줄마다 눈금이 따로이며(왼쪽·오른쪽 끝 숫자), 금년과 기준값의 차이가 잘 보이도록 범위를 좁혀 그렸습니다. 배지는 금년 − 기준(강수·일조·일수는 금년÷기준 %)." }));
}

export function refreshGlance() { if (last) renderGlance(last); }

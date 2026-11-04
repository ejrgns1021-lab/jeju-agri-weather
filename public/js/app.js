// app.js (화면) — 로그인, 조회 설정, 결과 표시
// 계산은 모두 백엔드(functions/)에서 하고, 여기서는 받은 표를 보여주기만 합니다.
import { analyze, login, ApiError } from "./api.js";
import { buildTable, copyForHwp, copyText, downloadCsv, toTsv } from "./tables.js";
import { icon, hydrateIcons } from "./icons.js";
import { renderDekad, refreshDekadCompare } from "./dekad.js";
import { saveWorkbook } from "./excel.js";

const METRICS = ["평균 기온", "최고 기온", "최저 기온", "누적 강수량", "일조 시간", "강우일수", "열대야 일수"];
const STATIONS = { 제주: 184, 서귀포: 189, 성산: 188, 고산: 185 }; // 표시 순서: 제주 → 서귀포 → 성산 → 고산
const NORMAL_MODES = [
  ["api", "평년값 API", "권장 · 빠름"],
  ["compute", "과거 30년 실측 계산", "느림 · 첫 조회 1~3분"],
  ["skip", "평년 비교 생략", ""],
];
const SOURCES = [["apihub", "기상청 API허브"], ["datagokr", "공공데이터포털"]];
// 화면 표시용: 지표별 아이콘·색상 계열 (계산과 무관)
const METRIC_VIEW = {
  "평균 기온": { icon: "thermometer", kind: "temp", tone: "avg", primary: "diff" },
  "최고 기온": { icon: "thermometer-sun", kind: "temp", tone: "max", primary: "diff" },
  "최저 기온": { icon: "thermometer-snowflake", kind: "temp", tone: "min", primary: "diff" },
  "누적 강수량": { icon: "cloud-rain", kind: "rain", tone: "rain", primary: "diff" },
  "일조 시간": { icon: "sun", kind: "sun", tone: "sun", primary: "diff" },
  "강우일수": { icon: "umbrella", kind: "rain", tone: "rdays", primary: "diff" },
  "열대야 일수": { icon: "moon", kind: "temp", tone: "night", primary: "diff" },
};
const MOBILE = "(max-width: 900px)";
const TOKEN_KEY = "jeju-weather-token";
const PREFS_KEY = "jeju-weather-prefs";
const PREFS_VERSION = 3; // v6.8: 열대야 일수 추가 — 이전에 저장된 지표 선택은 새 지표가 빠져 있으므로 초기화
const MAX_DAYS = 366;

const $ = (id) => document.getElementById(id);
const DEFAULT_LAYOUT = "combined"; // 기본 표 구성: 한 칸에 편차 (비율%)
const state = { result: null, layout: DEFAULT_LAYOUT };

// ---------------------------------------------------------------- 공통
const storage = {
  get(store, key) { try { return store.getItem(key); } catch (e) { return null; } },
  set(store, key, v) { try { store.setItem(key, v); } catch (e) { /* 저장 불가 환경 무시 */ } },
  del(store, key) { try { store.removeItem(key); } catch (e) { /* 무시 */ } },
};

function todayKst() {
  const now = new Date(Date.now() + 9 * 3600 * 1000);
  return now.toISOString().slice(0, 10);
}
function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function daysBetween(a, b) {
  return Math.round((new Date(`${b}T00:00:00Z`) - new Date(`${a}T00:00:00Z`)) / 86400000) + 1;
}
function fmtDate(iso, sep = ".") { return iso.replaceAll("-", sep); }
function compact(iso) { return iso.replaceAll("-", ""); }
// 화면 표시용 짧은 기간 표기 (같은 해면 연도 생략: 2026.10.27~11.02)
function shortRange(a, b) {
  return a.slice(0, 4) === b.slice(0, 4) ? `${fmtDate(a)}~${fmtDate(b).slice(5)}` : `${fmtDate(a)}~${fmtDate(b)}`;
}

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  });
  children.flat().forEach((c) => c != null && node.append(c));
  return node;
}

// ---------------------------------------------------------------- 로그인
function token() {
  const t = storage.get(sessionStorage, TOKEN_KEY);
  const exp = Number((t || "").split(".")[0]);
  if (!t || !exp || exp * 1000 < Date.now()) return null; // 만료된 토큰은 버림
  return t;
}

function showLogin(message = "") {
  $("app-view").hidden = true;
  $("login-view").hidden = false;
  $("login-error").textContent = message;
  $("login-password").value = "";
  $("login-password").focus();
}

function showApp() {
  $("login-view").hidden = true;
  $("app-view").hidden = false;
}

async function onLogin(ev) {
  ev.preventDefault();
  const btn = $("login-btn");
  btn.disabled = true;
  btn.textContent = "확인 중…";
  $("login-error").textContent = "";
  try {
    const res = await login($("login-password").value);
    storage.set(sessionStorage, TOKEN_KEY, res.token);
    showApp();
  } catch (e) {
    $("login-error").textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "로그인";
  }
}

function logout() {
  storage.del(sessionStorage, TOKEN_KEY);
  state.result = null;
  $("results").hidden = true;
  $("welcome").hidden = false;
  $("notices").replaceChildren();
  showLogin();
}

// ---------------------------------------------------------------- 조회 설정
function buildSettings() {
  const prefs = JSON.parse(storage.get(localStorage, PREFS_KEY) || "{}");
  const today = todayKst();
  const yesterday = addDays(today, -1);
  $("start").max = today;
  $("end").max = today;
  $("start").value = addDays(yesterday, -6);
  $("end").value = yesterday;

  const quick = [["최근 7일", 7], ["최근 30일", 30], ["이번 달", "month"], ["올해", "year"]];
  $("quick-ranges").replaceChildren(...quick.map(([label, v]) => el("button", {
    type: "button", class: "chip", text: label,
    onclick: () => {
      const end = yesterday;
      let start;
      if (v === "month") start = `${end.slice(0, 7)}-01`;
      else if (v === "year") start = `${end.slice(0, 4)}-01-01`;
      else start = addDays(end, -(v - 1));
      $("start").value = start;
      $("end").value = end;
      validateForm();
    },
  })));

  const selMetrics = prefs.v === PREFS_VERSION && prefs.metrics ? prefs.metrics : METRICS;
  $("metric-list").replaceChildren(...METRICS.map((m) => el("label", { class: `toggle tone-${METRIC_VIEW[m].tone}` },
    el("input", { type: "checkbox", name: "metric", value: m, ...(selMetrics.includes(m) ? { checked: "" } : {}) }),
    el("span", { class: "toggle-ui" }, icon(METRIC_VIEW[m].icon), el("span", { text: m })))));

  const selStations = prefs.stations || Object.keys(STATIONS);
  $("station-list").replaceChildren(...Object.entries(STATIONS).map(([n, id]) => el("label", { class: "toggle" },
    el("input", { type: "checkbox", name: "station", value: n, ...(selStations.includes(n) ? { checked: "" } : {}) }),
    el("span", { class: "toggle-ui" }, icon("map-pin"), el("span", { text: n }), el("small", { text: String(id) })))));

  const normal = prefs.normalMode || "api";
  $("normal-list").replaceChildren(...NORMAL_MODES.map(([v, label, hint]) => el("label", { class: "option" },
    el("input", { type: "radio", name: "normal", value: v, ...(v === normal ? { checked: "" } : {}) }),
    el("span", { class: "option-ui" }, el("span", { class: "option-dot" }),
      el("span", { class: "option-text" }, el("strong", { text: label }), hint ? el("small", { text: hint }) : null)))));

  const source = prefs.source || "apihub";
  $("source-list").replaceChildren(...SOURCES.map(([v, label]) => el("label", { class: "option" },
    el("input", { type: "radio", name: "source", value: v, ...(v === source ? { checked: "" } : {}) }),
    el("span", { class: "option-ui" }, el("span", { class: "option-dot" }),
      el("span", { class: "option-text" }, el("strong", { text: label }))))));

  if (prefs.extreme != null) $("extreme").checked = prefs.extreme;
  if (prefs.rainZero != null) $("rain-zero").checked = prefs.rainZero;

  $("query-form").addEventListener("input", validateForm);
  $("query-form").addEventListener("change", validateForm);
  validateForm();
}

function readQuery() {
  const checked = (name) => [...document.querySelectorAll(`input[name=${name}]:checked`)].map((i) => i.value);
  return {
    start: $("start").value,
    end: $("end").value,
    metrics: checked("metric"),
    stations: checked("station"),
    normalMode: checked("normal")[0] || "api",
    source: checked("source")[0] || "apihub",
    extreme: $("extreme").checked,
    rainZero: $("rain-zero").checked,
  };
}

function validateForm() {
  const q = readQuery();
  const today = todayKst();
  const w = [];
  if (!q.start || !q.end) w.push("시작일과 종료일을 입력해 주세요.");
  else {
    if (q.start > q.end) w.push("시작일이 종료일보다 늦습니다.");
    if (q.start > today || q.end > today) w.push(`미래 날짜는 조회할 수 없습니다. (${today} 이전)`);
    if (q.start <= q.end && daysBetween(q.start, q.end) > MAX_DAYS) w.push("조회 기간이 366일을 초과합니다. 1년 이내로 설정해 주세요.");
  }
  if (!q.metrics.length) w.push("최소 1개 이상의 지표를 선택해 주세요.");
  if (!q.stations.length) w.push("최소 1개 지점을 선택해 주세요.");
  if (q.normalMode === "api" && q.source !== "apihub") w.push("평년값 API는 기상청 API허브 전용입니다. 다른 평년 산출 방식을 선택하세요.");
  $("form-warnings").replaceChildren(...w.map((t) => el("li", {}, icon("alert-triangle", "ic-sm"), el("span", { text: t }))));
  $("query-btn").disabled = w.length > 0;
  $("quick-query").disabled = w.length > 0;
  $("cond-summary-text").textContent = q.start && q.end && q.start <= q.end
    ? `${shortRange(q.start, q.end)} · ${daysBetween(q.start, q.end)}일`
    : "조회 조건을 확인해 주세요";

  $("period-title").textContent = q.start && q.end && q.start <= q.end
    ? `${fmtDate(q.start)} ~ ${fmtDate(q.end)}`
    : "조회 기간 오류";
  if (state.result) markStale(q);
  return w.length === 0;
}

function markStale(q) {
  const p = state.result.params;
  const same = JSON.stringify([p.start, p.end, p.metrics, p.stations, p.normalMode, p.source, p.extreme, p.rainZero]) ===
    JSON.stringify([q.start, q.end, q.metrics, q.stations, q.normalMode, q.source, q.extreme, q.rainZero]);
  $("stale-notice")?.remove();
  if (!same) {
    $("notices").prepend(el("div", { id: "stale-notice", class: "notice warning" }, icon("alert-triangle"),
      el("span", { text: "조회 설정이 바뀌었습니다. 아래 결과는 이전 조건 기준입니다. 데이터 조회를 다시 눌러 주세요." })));
  }
}

// ---------------------------------------------------------------- 조회
let loadingTimer;
function setLoading(on, normalMode) {
  $("loading").hidden = !on;
  clearInterval(loadingTimer);
  if (!on) return;
  const t0 = Date.now();
  $("loading-bar").style.width = "2%";
  $("loading-pct").textContent = "0%";
  $("loading-stage").textContent = "서버에 요청하는 중";
  $("loading-detail").textContent = "";
  $("loading-text").textContent = normalMode === "compute"
    ? "과거 30년 실측 계산은 처음 1~3분 걸릴 수 있습니다. (한 번 받은 자료는 저장되어 다음부터 빨라집니다)"
    : "보통 10~40초 걸립니다. (한 번 받은 자료는 저장되어 다음부터 빨라집니다)";
  const tick = () => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    $("loading-time").textContent = sec >= 60 ? `${Math.floor(sec / 60)}분 ${sec % 60}초 경과` : `${sec}초 경과`;
  };
  tick();
  loadingTimer = setInterval(tick, 1000);
}

// 서버가 알려 준 진행 상황 표시 (어느 자료를 기상청 API 에서 받는 중인지)
function showProgress(p) {
  const pct = Math.max(2, Math.min(100, Number(p.pct) || 0));
  $("loading-bar").style.width = `${pct}%`;
  $("loading-pct").textContent = `${Math.round(pct)}%`;
  if (p.stage) $("loading-stage").textContent = p.stage;
  $("loading-detail").textContent = p.detail || "";
}

async function onQuery(ev) {
  ev.preventDefault();
  if (!validateForm()) return;
  const q = readQuery();
  storage.set(localStorage, PREFS_KEY, JSON.stringify({ ...q, v: PREFS_VERSION }));
  closeSettings();
  setLoading(true, q.normalMode);
  try {
    const res = await analyze(token(), q, showProgress);
    state.result = res;
    renderResult(res);
  } catch (e) {
    if (e instanceof ApiError && e.status === "UNAUTHENTICATED") {
      storage.del(sessionStorage, TOKEN_KEY);
      showLogin(e.message);
    } else {
      renderNotices([{ level: "error", text: e.message }]);
    }
  } finally {
    setLoading(false);
  }
}

// ---------------------------------------------------------------- 결과 표시
const NOTICE_ICON = { info: "info", warning: "alert-triangle", error: "alert-circle" };
function renderNotices(list) {
  $("notices").replaceChildren(...list.map((n) => el("div", { class: `notice ${n.level}` },
    icon(NOTICE_ICON[n.level] || "info"), el("span", { text: n.text }))));
}

function tableBlock(tbl, title, caption) {
  if (!tbl) return el("p", { class: "muted", text: "표시할 자료가 없습니다." });
  return el("div", { class: "table-block" },
    el("div", { class: "table-tools" },
      el("button", { type: "button", class: "btn small", title: "복사 후 한글에서 Ctrl+V 하면 표 모양 그대로 붙습니다",
        onclick: async () => toast((await copyForHwp(tbl, caption)) ? "표를 복사했습니다. 한글에서 Ctrl+V 하세요." : "복사하지 못했습니다.") },
        icon("copy"), el("span", { text: "한글용 표 복사" })),
      el("button", { type: "button", class: "btn small ghost", title: "한글: 붙여넣기 → 블록 지정 → 표 → 문자열을 표로 (구분: 탭)",
        onclick: async () => toast((await copyText(toTsv(tbl))) ? "탭 구분 텍스트를 복사했습니다." : "복사하지 못했습니다.") },
        icon("file-text"), el("span", { text: "탭 텍스트" })),
      el("button", { type: "button", class: "btn small ghost", onclick: () => downloadCsv(tbl, title) },
        icon("download"), el("span", { text: "CSV" }))),
    el("div", { class: "table-scroll" }, buildTable(tbl, caption)));
}

function renderComparison() {
  const res = state.result;
  const p = res.params;
  if (!res.comparison[state.layout]) state.layout = DEFAULT_LAYOUT;
  const tbl = res.comparison[state.layout];
  const caption = `제주 지역 기상 현황 (${fmtDate(p.start)}~${fmtDate(p.end)})`;
  $("cmp-table").replaceChildren(tableBlock(tbl, `제주기상비교표_${compact(p.start)}_${compact(p.end)}`, caption));
  document.querySelectorAll("#layout-switch button").forEach((b) => b.classList.toggle("active", b.dataset.layout === state.layout));
}

function renderResult(res) {
  renderNotices(res.notices || []);
  if (!res.ok) {
    $("results").hidden = true;
    $("welcome").hidden = false;
    return;
  }
  $("welcome").hidden = true;
  $("results").hidden = false;
  const p = res.params;
  const s = res.summary;
  const tag = `${compact(p.start)}_${compact(p.end)}`;

  $("summary").replaceChildren(
    ...[
      ["calendar", "조회 기간", `${s.periodDays}일`, shortRange(p.start, p.end)],
      ["check-circle", "자료 확보", `${s.gotDays}일`, `${Math.round((s.gotDays / s.periodDays) * 100)}%`],
      ["history", "비교 연도", `${s.prevYear}년`, `최근 5년 ${s.recentRange}`],
      ["layers", "평년 기준", s.normalNote.split(" (")[0], s.normalIncluded ? "1991~2020" : "미포함"],
    ].map(([ic, k, v, sub]) => el("div", { class: "stat" },
      el("span", { class: "stat-icon" }, icon(ic)),
      el("span", { class: "stat-body" },
        el("span", { class: "stat-label", text: k }), el("strong", { text: v }), el("span", { class: "stat-sub", text: sub })))),
  );
  renderMetricCards(res);

  $("cmp-caption").textContent = `${s.aggDesc}. 결측 지점은 제외하고 나머지 지점 평균으로 산출. ` +
    "대비(편차)=해당 연도−기준값, 대비(비율)=해당 연도÷기준값×100(%). '-' 는 자료 없음.";
  $("layout-switch").replaceChildren(...res.layouts.map(([key, label]) => el("button", {
    type: "button", "data-layout": key, text: label, role: "tab",
    onclick: () => { state.layout = key; renderComparison(); refreshDekadCompare(); },
  })));
  renderComparison();

  $("basis-table").replaceChildren(el("div", { class: "table-scroll" }, buildTable(res.basis)));
  $("basis-note").textContent = `평년: ${s.normalNote} · 조회 시각 ${res.fetchedAt}`;

  $("station-table").replaceChildren(
    el("p", { class: "muted small", text: "지점별 기간 집계와 4개 지점 평균(결측 제외)" }),
    tableBlock(res.station, `지점별요약_${tag}`));

  const sel = $("daily-metric");
  const prev = sel.value;
  sel.replaceChildren(...p.metrics.map((m) => el("option", { value: m, text: m })));
  if (p.metrics.includes(prev)) sel.value = prev;
  const drawDaily = () => $("daily-table").replaceChildren(tableBlock(res.daily[sel.value], `일별_${sel.value}_${tag}`));
  sel.onchange = drawDaily;
  drawDaily();

  const yearly = [el("p", { class: "muted small", text: "전년 및 최근 5년 각 연도의 동일 기간 제주 전체 집계값" }),
    res.recent ? tableBlock(res.recent, `최근5년_${tag}`) : el("p", { class: "muted", text: "최근 5년 자료가 없습니다." })];
  if (res.normal30) {
    yearly.push(el("p", { class: "muted small gap", text: "평년(1991~2020) 각 연도의 동일 기간 집계값" }),
      tableBlock(res.normal30, `평년30년_${tag}`));
  }
  $("yearly-tables").replaceChildren(...yearly);

  renderDekad(res, { $, el, icon, tableBlock, toast, fmtDate, compact, layout: () => state.layout });

  const d = res.diagnostics || {};
  const diag = [el("p", { class: "muted small", text: "해당 연도 표준화 자료 (결측은 '-')" }), tableBlock(res.raw, `원자료_${tag}`)];
  if (d.dailyRawText) {
    diag.push(el("details", {}, el("summary", { text: "일자료 API 원문 (첫 지점·시작일 1일치)" }),
      el("pre", { class: "raw", text: d.dailyRawText }),
      el("p", { class: "muted small", text: `인식된 컬럼: ${d.dailyColumns.join(", ")}` })));
  }
  if (d.normalRawText) {
    diag.push(el("details", {}, el("summary", { text: "평년값 API 원문" }),
      el("pre", { class: "raw", text: d.normalRawText }),
      el("p", { class: "muted small", text: `인식된 컬럼: ${d.normalColumns.join(", ")}` })));
  }
  $("diag").replaceChildren(...diag);
}

// ---------------------------------------------------------------- 지표 카드 (서버가 보낸 비교표 값을 그대로 카드로 배치)
function readComparisonRows(res) {
  const t = res.comparison && res.comparison.all;
  if (!t) return [];
  const idx = (pred) => t.columns.findIndex(pred);
  const at = (row, i) => (i >= 0 ? row[i] : "-");
  const iCur = idx((c) => c.startsWith("해당 연도"));
  const iPrev = idx((c) => c.startsWith("전년("));
  const cols = {
    prev: [iPrev, idx((c) => c === "전년 대비"), idx((c) => c === "전년도 대비(비율)")],
    recent: [idx((c) => c === "최근 5년 평균"), idx((c) => c === "최근 5년 대비"), idx((c) => c === "최근5년평균대비(비율)")],
    normal: [idx((c) => c === "평년 평균"), idx((c) => c === "평년 대비"), idx((c) => c === "평년대비(비율)")],
  };
  return t.rows.map((row) => {
    const m = String(row[0]).match(/^(.*?)\(([^)]*)\)$/);
    const label = m ? m[1].trim() : String(row[0]);
    const base = METRICS.find((k) => label.startsWith(k)) || label;
    const refs = Object.fromEntries(Object.entries(cols).map(([k, [v, d, r]]) => [k, { value: at(row, v), diff: at(row, d), ratio: at(row, r) }]));
    return { label, base, unit: m ? m[2] : "", current: at(row, iCur), refs };
  });
}

function trendOf(diffText) {
  if (!diffText || diffText === "-") return "none";
  if (diffText.startsWith("+")) return "up";
  if (/^-\d/.test(diffText) && !/^-0(\.0+)?$/.test(diffText)) return "down";
  return "flat";
}

function gauge(ratioText) {
  // 비율(%)을 100% 기준 막대로만 표현 (표시 전용: 50%~150% 범위를 막대 폭에 대응)
  const r = parseFloat(ratioText);
  const wrap = el("span", { class: "gauge", "aria-hidden": "true" }, el("span", { class: "gauge-mid" }));
  if (Number.isFinite(r)) {
    const pos = Math.max(-1, Math.min(1, (r - 100) / 50));
    const fill = el("span", { class: `gauge-fill ${pos >= 0 ? "up" : "down"}` });
    fill.style.width = `${Math.abs(pos) * 50}%`;
    fill.style[pos >= 0 ? "left" : "right"] = "50%";
    wrap.append(fill);
  }
  return wrap;
}

function renderMetricCards(res) {
  const s = res.summary;
  // 비교 기준 이름과 기준값 설명 (예: "전년 대비" / "2025년 19.6℃")
  const names = { prev: "전년 대비", recent: "최근 5년 대비", normal: "평년 대비" };
  const refDesc = { prev: `${s.prevYear}년`, recent: `${s.recentRange} 평균`, normal: "1991~2020 평균" };
  const cards = readComparisonRows(res).map((row) => {
    const view = METRIC_VIEW[row.base] || { icon: "bar-chart", kind: "etc", tone: "avg", primary: "diff" };
    const compareRows = Object.entries(row.refs).map(([key, ref]) => {
      const t = trendOf(ref.diff);
      const arrow = t === "up" ? icon("trending-up", "ic-sm") : t === "down" ? icon("trending-down", "ic-sm") : icon("minus", "ic-sm");
      const diffEl = el("span", { class: `delta trend-${t}` }, arrow,
        el("span", { text: ref.diff === "-" ? "-" : `${ref.diff}${row.unit}` }));
      const ratioEl = el("span", { class: "ratio", text: ref.ratio === "-" ? "-" : `${ref.ratio}%` });
      return el("div", { class: `cmp-row ${ref.value === "-" ? "is-empty" : ""}` },
        el("span", { class: "cmp-name" }, el("span", { text: names[key] }),
          el("small", { text: ref.value === "-" ? `${refDesc[key]} · 자료 없음` : `${refDesc[key]} ${ref.value}${row.unit}` })),
        el("span", { class: "cmp-values" }, view.primary === "ratio" ? [ratioEl, diffEl] : [diffEl, ratioEl]),
        gauge(ref.ratio));
    });
    return el("article", { class: `metric-card tone-${view.tone} kind-${view.kind}` },
      el("header", { class: "metric-head" },
        el("span", { class: "metric-icon" }, icon(view.icon)),
        el("span", { class: "metric-name", text: row.label })),
      el("div", { class: "metric-value" },
        el("strong", { text: row.current }), el("span", { class: "unit", text: row.unit }),
        el("span", { class: "metric-year", text: `${s.currentYear}년` })),
      el("div", { class: "cmp-list" }, compareRows));
  });
  $("metric-cards").replaceChildren(...cards);
}

// ---------------------------------------------------------------- 모바일 조회 조건 시트
function openSettings() {
  if (!matchMedia(MOBILE).matches) { $("start").focus(); return; }
  $("settings").classList.add("open");
  $("sheet-backdrop").hidden = false;
  document.body.classList.add("sheet-open");
}
function closeSettings() {
  $("settings").classList.remove("open");
  $("sheet-backdrop").hidden = true;
  document.body.classList.remove("sheet-open");
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".tab-panel").forEach((pnl) => { pnl.hidden = pnl.dataset.panel !== tab.dataset.tab; });
  }));
}

// ---------------------------------------------------------------- 시작
// ---------------------------------------------------------------- 전체 결과 엑셀 저장
async function exportAll() {
  const res = state.result;
  if (!res || !res.ok) return;
  const p = res.params;
  const tag = `${compact(p.start)}_${compact(p.end)}`;
  const info = [`조회 기간: ${fmtDate(p.start)} ~ ${fmtDate(p.end)}`, `관측 지점: ${(p.stations || []).join(", ")}`,
    `평년 기준: ${res.summary.normalNote}`, `조회 시각: ${res.fetchedAt}`];
  const sheets = [
    { name: "비교표", title: "제주 전체(4개 지점 평균) 기상 비교표", notes: info, table: res.comparison.all },
    { name: "산출 근거", table: res.basis },
    { name: "지점별 요약", table: res.station },
    ...p.metrics.filter((m) => res.daily && res.daily[m]).map((m) => ({ name: `일별_${m}`, table: res.daily[m] })),
    { name: "연도별 동기간", table: res.recent },
    { name: "평년 30년", table: res.normal30 },
  ];
  if (res.dekad) {
    sheets.push({ name: "순별 요약", table: res.dekad.overview });
    p.metrics.filter((m) => res.dekad.byMetric[m]).forEach((m) => sheets.push({ name: `순별_${m}`, table: res.dekad.byMetric[m].all }));
  }
  const btn = $("excel-all");
  btn.disabled = true;
  try {
    await saveWorkbook(sheets, `제주_기상분석_${tag}.xlsx`);
    toast("전체 결과를 엑셀 파일로 저장했습니다.");
  } catch (e) {
    toast(`엑셀 저장 실패: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

function init() {
  $("login-form").addEventListener("submit", onLogin);
  $("logout-btn").addEventListener("click", logout);
  $("query-form").addEventListener("submit", onQuery);
  hydrateIcons();
  // 테스트(프리뷰) 주소에서 열었을 때 표시 (예: jeju-agri-weather--preview-test-xxxx.web.app)
  if (/--/.test(location.hostname)) $("preview-banner").hidden = false;
  $("excel-all").addEventListener("click", exportAll);
  $("toggle-settings").addEventListener("click", openSettings);
  $("welcome-start").addEventListener("click", openSettings);
  $("close-settings").addEventListener("click", closeSettings);
  $("sheet-backdrop").addEventListener("click", closeSettings);
  $("quick-query").addEventListener("click", () => $("query-form").requestSubmit());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSettings(); });
  matchMedia(MOBILE).addEventListener("change", (e) => { if (!e.matches) closeSettings(); });
  setupTabs();
  buildSettings();
  if (token()) showApp(); else showLogin();
}

init();

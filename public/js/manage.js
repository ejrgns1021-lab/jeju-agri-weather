// manage.js (관리자 화면, v7.2) — 이용 현황 · 연결 점검 · 저장분 비우기
// 관리자 비밀번호(ADMIN_PASSWORD)로 받은 토큰은 이 탭에서만 보관 (sessionStorage)
import { callFunction, ApiError } from "./api.js";
import { hydrateIcons } from "./icons.js";

const $ = (id) => document.getElementById(id);
const KEY = "jeju-manage-token";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ss = {
  get() { try { const t = sessionStorage.getItem(KEY); return t && Number(t.split(".")[0]) * 1000 > Date.now() ? t : null; } catch (e) { return null; } },
  set(v) { try { sessionStorage.setItem(KEY, v); } catch (e) { /* 무시 */ } },
  del() { try { sessionStorage.removeItem(KEY); } catch (e) { /* 무시 */ } },
};
let days = 30;

function show(app) {
  $("m-login").hidden = app;
  $("m-app").hidden = !app;
  if (app) loadStats();
}

async function call(action, extra = {}) {
  try {
    return await callFunction("jejumanage", { token: ss.get(), action, ...extra });
  } catch (e) {
    if (e instanceof ApiError && e.status === "UNAUTHENTICATED") { ss.del(); show(false); $("m-login-error").textContent = e.message; }
    throw e;
  }
}

function renderStats(r) {
  const t = r.total || {};
  const rows = r.days || [];
  const today = rows.length ? rows[rows.length - 1] : null;
  const todayStr = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
  const kpi = (label, val, sub = "") => `<div class="m-kpi"><span>${label}</span><strong>${val}</strong><small>${sub}</small></div>`;
  $("m-kpis").innerHTML = [
    kpi(`최근 ${days}일 접속 브라우저`, r.uniqueBrowsers ?? 0, "대"),
    kpi("오늘 접속 브라우저", today && today.date === todayStr ? today.browsers : 0, "대"),
    kpi("로그인", t.login || 0, "회"),
    kpi("조회", t.query || 0, "회"),
    kpi("AI 기상분석", t.brief || 0, "회"),
  ].join("");

  // 날짜별 막대 (빈 날짜도 0으로 채움)
  const byDate = Object.fromEntries(rows.map((x) => [x.date, x]));
  const dates = [];
  const start = new Date((r.since || todayStr) + "T00:00:00+09:00");
  for (let i = 0; i < days; i += 1) dates.push(new Date(start.getTime() + i * 864e5 + 9 * 3600e3).toISOString().slice(0, 10));
  const max = Math.max(1, ...rows.map((x) => x.browsers));
  $("m-chart").innerHTML = dates.map((d) => {
    const v = byDate[d] ? byDate[d].browsers : 0;
    return `<div class="m-bar${v ? "" : " zero"}" style="height:${v ? Math.max(4, (v / max) * 100) : 2}%" title="${d} · 브라우저 ${v}대"></div>`;
  }).join("");
  $("m-chart").insertAdjacentHTML("afterend", "");
  const x = $("m-chart").nextElementSibling;
  if (!x || !x.classList.contains("m-chart-x")) $("m-chart").insertAdjacentHTML("afterend", `<div class="m-chart-x"></div>`);
  $("m-chart").nextElementSibling.innerHTML = `<span>${dates[0].slice(5)}</span><span>날짜별 접속 브라우저 수</span><span>${dates[dates.length - 1].slice(5)}</span>`;

  const body = rows.slice().reverse().map((x) => `<tr><td>${x.date}</td><td>${x.browsers}</td><td>${x.queryBrowsers}</td>`
    + `<td>${x.login}</td><td>${x.query}</td><td>${x.brief}</td><td>${x.forecast}</td></tr>`).join("")
    || `<tr><td colspan="7" style="text-align:center">아직 기록이 없습니다</td></tr>`;
  $("m-table").innerHTML = `<div class="m-scroll"><table class="m-tbl"><thead><tr><th>날짜</th><th>접속 브라우저</th><th>조회한 브라우저</th>`
    + `<th>로그인</th><th>조회</th><th>AI 분석</th><th>예보 표시</th></tr></thead><tbody>${body}</tbody>`
    + `<tfoot><tr><td>합계</td><td>${r.uniqueBrowsers ?? 0}</td><td>-</td><td>${t.login || 0}</td><td>${t.query || 0}</td><td>${t.brief || 0}</td><td>${t.forecast || 0}</td></tr></tfoot></table></div>`;

  // 저장분
  const names = r.cacheNames || {};
  $("m-cache").innerHTML = Object.entries(names).map(([k, label]) => {
    const n = (r.cache || {})[k];
    return `<div class="m-cache-item"><strong>${esc(label)}</strong><span class="n">${n == null || n < 0 ? "-" : n.toLocaleString()}건</span>`
      + `<button type="button" class="btn ghost small danger" data-kind="${k}">비우기</button></div>`;
  }).join("");
}

async function loadStats() {
  $("m-kpis").innerHTML = '<p class="muted small">불러오는 중…</p>';
  try {
    renderStats(await call("stats", { days }));
  } catch (e) {
    $("m-kpis").innerHTML = `<p class="form-error">${esc(e.message)}</p>`;
  }
}

async function runHealth() {
  const btn = $("m-health");
  btn.disabled = true;
  $("m-health-out").innerHTML = '<p class="muted small">점검 중… (최대 30초)</p>';
  try {
    const r = await call("health");
    $("m-health-out").innerHTML = r.checks.map((c) => `<div class="m-check ${c.ok ? "ok" : "bad"}"><b>${c.ok ? "✔" : "✖"} ${esc(c.name)}</b>`
      + `<span>${esc(c.detail)}</span><span class="sec">${c.sec}초</span></div>`).join("");
  } catch (e) {
    $("m-health-out").innerHTML = `<p class="form-error">${esc(e.message)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

// 비우기: 첫 클릭은 확인 상태로 바뀌고, 3초 안에 한 번 더 눌러야 실행 (브라우저 확인창 대신)
async function onCacheClick(ev) {
  const btn = ev.target.closest("button[data-kind]");
  if (!btn) return;
  if (!btn.classList.contains("armed")) {
    btn.classList.add("armed");
    btn.textContent = "한 번 더 누르면 비움";
    setTimeout(() => { if (btn.isConnected) { btn.classList.remove("armed"); btn.textContent = "비우기"; } }, 3000);
    return;
  }
  btn.disabled = true;
  btn.textContent = "비우는 중…";
  try {
    const r = await call("clear", { kind: btn.dataset.kind });
    btn.textContent = `${r.deleted}건 비움`;
    setTimeout(loadStats, 800);
  } catch (e) {
    btn.textContent = "실패";
  }
}

function init() {
  hydrateIcons();
  if (/--/.test(location.hostname)) $("preview-banner").hidden = false;
  $("m-login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = $("m-login-btn");
    btn.disabled = true;
    $("m-login-error").textContent = "";
    try {
      const r = await callFunction("managelogin", { password: $("m-password").value });
      ss.set(r.token);
      $("m-password").value = "";
      show(true);
    } catch (e) {
      $("m-login-error").textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  });
  $("m-logout").addEventListener("click", () => { ss.del(); show(false); });
  $("m-health").addEventListener("click", runHealth);
  $("m-cache").addEventListener("click", onCacheClick);
  $("m-range").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-days]");
    if (!b) return;
    days = Number(b.dataset.days);
    $("m-range").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    loadStats();
  });
  show(!!ss.get());
}

init();

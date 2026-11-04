// forecast.js (화면) — 대시보드 맨 위 기상 예보 (v6.9)
// 한 줄 요약(오늘·내일) + 접는 칸(지역별 단기예보, 주간 중기예보)
// 예보는 서버(jejuforecast)가 기상청에서 받아 20분간 저장해 두고 나눠 줍니다.
import { callFunction } from "./api.js";
import { iconHtml } from "./icons.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const t = (v) => (v === null || v === undefined ? "-" : `${Math.round(v)}℃`);
const pct = (v) => (v === null || v === undefined ? "-" : `${Math.round(v)}%`);
let loading = null;

// 날씨별 아이콘·색 (서버가 준 이름 → 화면용): 맑음=빨간 해, 비·소나기=파란 우산, 눈=하늘색 눈송이
const WX_ICON = {
  sun: ["sun", "wx-sun"],
  "cloud-sun": ["cloud-sun", "wx-partly"],
  cloud: ["cloud", "wx-cloud"],
  "cloud-rain": ["umbrella", "wx-rain"],
  snowflake: ["snowflake", "wx-snow"],
};
function wxIcon(name, extra = "") {
  const [ic, cls] = WX_ICON[name] || WX_ICON.cloud;
  return iconHtml(ic, `fc-ic ${cls} ${extra}`.trim());
}

function wxText(d) {
  if (d.am && d.pm && d.am !== d.pm) return `${d.am} → ${d.pm}`;
  return d.pm || d.am || d.wx || "-";
}

function summaryLine(days) {
  const parts = days.slice(0, 2).map((d, i) => {
    const name = d.label.split(" ")[0];
    const temp = `${t(d.tmin)} / ${t(d.tmax)}`;
    const pop = d.pop !== null && d.pop !== undefined ? ` · 강수 ${pct(d.pop)}` : "";
    return `<span class="fc-day${i ? " fc-next" : ""}">${wxIcon(d.icon, "fc-ic-lg")}<b>${esc(name)}</b> ${esc(wxText(d))} <span class="fc-temp">${temp}</span>${pop}</span>`;
  });
  return parts.join('<span class="fc-sep" aria-hidden="true">|</span>');
}

function shortTable(short) {
  const days = short.stations[0]?.days || [];
  const head = days.map((d) => `<th>${esc(d.label)}</th>`).join("");
  const rows = short.stations.map((s) => {
    const byDate = Object.fromEntries(s.days.map((d) => [d.date, d]));
    const cells = days.map((dd) => {
      const d = byDate[dd.date];
      if (!d) return "<td>-</td>";
      const pops = [d.popAm, d.popPm].filter((x) => x !== null && x !== undefined);
      const rain = d.rainMm > 0 ? ` · ${d.rainMm >= 1 ? `${d.rainMm}mm` : "1mm 미만"}` : "";
      return `<td>${wxIcon(d.icon)}<div class="fc-wx">${esc(wxText(d))}</div>`
        + `<div class="fc-tt"><span class="lo">${t(d.tmin)}</span> / <span class="hi">${t(d.tmax)}</span></div>`
        + `<div class="fc-pop">강수 ${pops.length ? pops.map(pct).join("·") : pct(d.pop)}${rain}</div></td>`;
    }).join("");
    return `<tr><th scope="row">${esc(s.name)}</th>${cells}</tr>`;
  }).join("");
  return `<div class="fc-scroll"><table class="fc-table"><thead><tr><th>지점</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function midTable(mid) {
  const days = mid.days || [];
  if (!days.length) return '<p class="muted small">중기예보 자료가 아직 없습니다.</p>';
  const head = days.map((d) => `<th>${esc(d.label)}</th>`).join("");
  const wx = days.map((d) => `<td>${wxIcon(d.icon)}<div class="fc-wx">${esc(wxText(d))}</div>`
    + `<div class="fc-pop">강수 ${[d.popAm, d.popPm].filter((x) => x !== null && x !== undefined).map(pct).join("·") || "-"}</div></td>`).join("");
  const names = (days[0].stations || []).map((s) => s.name);
  const temps = names.map((n) => `<tr><th scope="row">${esc(n)}</th>${days.map((d) => {
    const s = (d.stations || []).find((x) => x.name === n) || {};
    return `<td class="fc-tt"><span class="lo">${t(s.tmin)}</span> / <span class="hi">${t(s.tmax)}</span></td>`;
  }).join("")}</tr>`).join("");
  return `<div class="fc-scroll"><table class="fc-table"><thead><tr><th>구분</th>${head}</tr></thead>`
    + `<tbody><tr class="fc-wxrow"><th scope="row">제주도 날씨</th>${wx}</tr>${temps}</tbody></table></div>`;
}

function render(res) {
  const card = $("forecast");
  const short = res.short;
  const days = short && short.jeju ? short.jeju : [];
  $("fc-line").innerHTML = days.length ? summaryLine(days)
    : `<span class="muted">${esc((res.errors && res.errors.short) || "단기예보를 불러오지 못했습니다.")}</span>`;
  $("fc-short").innerHTML = short ? shortTable(short) : `<p class="muted small">${esc(res.errors?.short || "단기예보 없음")}</p>`;
  $("fc-mid").innerHTML = res.mid ? midTable(res.mid) : `<p class="muted small">${esc(res.errors?.mid || "중기예보 없음")}</p>`;
  const bases = [short && `단기 ${short.base} 발표`, res.mid && `중기 ${res.mid.base} 발표`].filter(Boolean).join(" · ");
  $("fc-base").textContent = bases ? `기상청 ${bases}` : "";
  card.hidden = false;
}

/** 로그인 후 한 번 불러옴 (실패해도 다른 기능에는 영향 없음) */
export function loadForecast(token) {
  if (!token || loading) return loading;
  const card = $("forecast");
  card.hidden = false;
  $("fc-line").innerHTML = '<span class="muted">예보를 불러오는 중…</span>';
  loading = callFunction("jejuforecast", { token })
    .then(render)
    .catch((e) => {
      if (e && e.status === "UNAUTHENTICATED") { card.hidden = true; return; }
      $("fc-line").innerHTML = `<span class="muted">예보를 불러오지 못했습니다. (${esc(e.message || e)})</span>`;
    })
    .finally(() => { loading = null; });
  return loading;
}

export function setupForecast() {
  const toggle = $("fc-toggle");
  toggle.addEventListener("click", () => {
    const open = $("fc-body").hidden;
    $("fc-body").hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.classList.toggle("open", open);
  });
}

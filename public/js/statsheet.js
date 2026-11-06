// statsheet.js (화면, v7.3) — 파일 이름에 report 등을 쓰면 일부 차단 기능이 막을 수 있어 중립적인 이름 사용
// — [보고서 표] 탭: 지표 × 지점 × (본년 + 고른 비교 기준) 한 장짜리 표
// 한글 보고서에 그대로 붙여넣는 용도 (제목·출처 자동, 지점별 본년/전년/평년 등)
import { selectedBases } from "./bases.js";
import { copyText } from "./tables.js";
import { saveWorkbook } from "./excel.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SHORT = { "평균 기온": "평균기온", "최고 기온": "최고기온", "최저 기온": "최저기온", "누적 강수량": "강수량",
  "일조 시간": "일조시간", "강우일수": "강우일수", "열대야 일수": "열대야일수" };
const opts = { avg: false, diff: false };
const HWP = {
  table: "border-collapse:collapse;font-family:'맑은 고딕','Malgun Gothic',sans-serif;font-size:10pt;",
  th: "border:1px solid #000;background:#e9eef5;padding:3px 8px;text-align:center;font-weight:bold;white-space:nowrap;",
  td: "border:1px solid #000;padding:3px 8px;text-align:center;white-space:nowrap;",
  title: "font-family:'맑은 고딕','Malgun Gothic',sans-serif;font-size:13pt;font-weight:bold;margin:0 0 2px;",
  src: "font-family:'맑은 고딕','Malgun Gothic',sans-serif;font-size:9pt;text-align:right;margin:0 0 2px;",
};

function fmtNum(v) { return v === null || v === undefined || Number.isNaN(v) ? "-" : Number(v).toFixed(1); }

/** '26. 10. 1. ~ 10. 21. */
function periodText(s, e) {
  const [ys, ms, ds] = s.split("-").map(Number);
  const [ye, me, de] = e.split("-").map(Number);
  const yy = (y) => `'${String(y).slice(2)}`;
  const left = `${yy(ys)}. ${ms}. ${ds}.`;
  return ys === ye ? `${left} ~ ${me}. ${de}.` : `${left} ~ ${yy(ye)}. ${me}. ${de}.`;
}

/** 표 자료: groups=[{name, cells:[...]}], rows=[{label, values:[...]}] */
function buildModel(res) {
  const sr = res.stationReport;
  const bases = selectedBases();
  const cols = [{ key: "cur", label: "본년" }, ...bases.map((b) => ({ key: b.key === "r3" ? "r3" : b.key === "r5" ? "r5" : b.key, label: b.short }))];
  const groups = sr.stations.map((s) => ({ name: s.name, values: s.values }));
  if (opts.avg && sr.average) groups.push({ name: res.params.stations.length >= 4 ? "제주 평균" : "선택 지점 평균", values: sr.average });
  const metrics = res.params.metrics.filter((m) => sr.stations[0] && sr.stations[0].values[m]);
  const rows = metrics.map((m) => ({
    label: `${SHORT[m] || m}(${sr.units[m] || ""})`,
    cells: groups.flatMap((g) => cols.map((c) => {
      const v = (g.values[m] || {})[c.key];
      const cur = (g.values[m] || {}).cur;
      let text = fmtNum(v);
      if (opts.diff && c.key !== "cur" && v != null && cur != null) {
        const d = Math.round((cur - v) * 10) / 10;
        text += ` (${d > 0 ? "+" : ""}${d.toFixed(1)})`;
      }
      return text;
    })),
  }));
  return { cols, groups, rows };
}

function tableHtml(model, styled) {
  const st = (k) => (styled ? ` style="${HWP[k]}"` : "");
  const h1 = [`<th rowspan="2"${st("th")}>구분</th>`, ...model.groups.map((g) => `<th colspan="${model.cols.length}"${st("th")}>${esc(g.name)}</th>`)];
  const h2 = model.groups.flatMap(() => model.cols.map((c) => `<th${st("th")}>${esc(c.label)}</th>`));
  const body = model.rows.map((r) => `<tr><th${st("th")}>${esc(r.label)}</th>${r.cells.map((v, i) =>
    `<td${st("td")}${!styled && i % model.cols.length === 0 ? ' class="rep-cur"' : ""}>${esc(v)}</td>`).join("")}</tr>`).join("");
  return `<table${styled ? ` style="${HWP.table}"` : ' class="data-table rep-table"'}><thead><tr>${h1.join("")}</tr><tr>${h2.join("")}</tr></thead><tbody>${body}</tbody></table>`;
}

function flat(model) {
  const columns = ["구분", ...model.groups.flatMap((g) => model.cols.map((c) => `${g.name} ${c.label}`))];
  return { columns, rows: model.rows.map((r) => [r.label, ...r.cells]) };
}

async function copyHwp(res, toast) {
  const model = buildModel(res);
  const title = $("rep-title").value;
  const src = $("rep-src").value;
  const html = `<p style="${HWP.title}">${esc(title)}</p>${src ? `<p style="${HWP.src}">${esc(src)}</p>` : ""}${tableHtml(model, true)}`;
  const t = flat(model);
  const text = [title, src, ...[t.columns, ...t.rows].map((r) => r.join("\t"))].filter(Boolean).join("\n");
  try {
    await navigator.clipboard.write([new ClipboardItem({
      "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([text], { type: "text/plain" }) })]);
    toast("보고서 표를 복사했습니다. 한글에서 Ctrl+V 하세요.");
  } catch (e) {
    toast((await copyText(text)) ? "표를 텍스트로 복사했습니다." : "복사하지 못했습니다.");
  }
}

export function renderReport(res, toast) {
  const box = $("report-panel");
  if (!box) return;
  if (!res || !res.ok || !res.stationReport || !res.stationReport.stations.length) {
    box.innerHTML = '<p class="muted small">이 조회 결과에는 보고서 표 자료가 없습니다. 다시 조회해 주세요.</p>';
    return;
  }
  const p = res.params;
  const keepTitle = $("rep-title") && $("rep-title").dataset.period === `${p.start}_${p.end}` ? $("rep-title").value : null;
  const keepSrc = $("rep-src") ? $("rep-src").value : "(자료출처: 제주지방기상청)";
  const model = buildModel(res);
  box.innerHTML = `
    <div class="rep-opts">
      <label class="field inline rep-title-f"><span>제목</span><input id="rep-title" type="text"></label>
      <label class="field inline"><span>출처</span><input id="rep-src" type="text"></label>
      <label class="switch"><input id="rep-avg" type="checkbox"${opts.avg ? " checked" : ""}><span class="switch-ui"></span><span>지점 평균 열 추가</span></label>
      <label class="switch"><input id="rep-diff" type="checkbox"${opts.diff ? " checked" : ""}><span class="switch-ui"></span><span>기준값 옆에 차이(본년−기준) 표시</span></label>
    </div>
    <div class="rep-head"><strong id="rep-title-view"></strong><span id="rep-src-view" class="muted small"></span></div>
    <div class="table-scroll">${tableHtml(model, false)}</div>
    <div class="table-tools rep-tools">
      <button type="button" class="btn small" id="rep-copy">한글용 표 복사</button>
      <button type="button" class="btn small ghost" id="rep-excel">엑셀 저장</button>
      <span class="muted small">비교 기준은 결과 맨 위 '비교 기준'에서 고르면 바로 바뀝니다.</span>
    </div>`;
  const title = $("rep-title");
  title.value = keepTitle || `□ 기상현황 (${periodText(p.start, p.end)})`;
  title.dataset.period = `${p.start}_${p.end}`;
  $("rep-src").value = keepSrc;
  const sync = () => { $("rep-title-view").textContent = title.value; $("rep-src-view").textContent = $("rep-src").value; };
  sync();
  title.addEventListener("input", sync);
  $("rep-src").addEventListener("input", sync);
  $("rep-avg").addEventListener("change", (e) => { opts.avg = e.target.checked; renderReport(res, toast); });
  $("rep-diff").addEventListener("change", (e) => { opts.diff = e.target.checked; renderReport(res, toast); });
  $("rep-copy").addEventListener("click", () => copyHwp(res, toast));
  $("rep-excel").addEventListener("click", async () => {
    const t = flat(buildModel(res));
    try {
      await saveWorkbook([{ name: "보고서 표", title: title.value, notes: [$("rep-src").value], table: t }],
        `기상현황_${p.start.replaceAll("-", "")}_${p.end.replaceAll("-", "")}.xlsx`);
      toast("보고서 표를 엑셀로 저장했습니다.");
    } catch (e) { toast(`엑셀 저장 실패: ${e.message}`); }
  });
}

export function reportSheet(res) {
  if (!res || !res.stationReport || !res.stationReport.stations.length) return null;
  return { name: "보고서 표", title: `□ 기상현황 (${periodText(res.params.start, res.params.end)})`, notes: ["(자료출처: 제주지방기상청)"], table: flat(buildModel(res)) };
}

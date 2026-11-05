// dekad.js (화면) — 순별(상순·중순·하순) 집계 표시 · 엑셀 저장
// 서버가 계산해 보낸 순별 표(res.dekad)를 보여주기만 합니다. 토글은 다시 조회하지 않고 즉시 보이기/숨기기만 합니다.
import { saveWorkbook } from "./excel.js";

const TOGGLE_KEY = "jeju-weather-dekad-on";

function readToggle() {
  try { return localStorage.getItem(TOGGLE_KEY) === "1"; } catch (e) { return false; }
}
function writeToggle(on) {
  try { localStorage.setItem(TOGGLE_KEY, on ? "1" : "0"); } catch (e) { /* 저장 불가 환경 무시 */ }
}

/**
 * @param {object} res  서버 조회 결과
 * @param {object} ui   { $, el, icon, tableBlock, toast, layout(): "combined"|"all"|..., fmtDate, compact }
 */
export function renderDekad(res, ui) {
  const { $, el, icon, tableBlock, toast } = ui;
  const card = $("dekad-card");
  const dk = res && res.dekad;
  if (!dk || !dk.overview || !dk.overview.rows.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const p = res.params;
  const tag = `${ui.compact(p.start)}_${ui.compact(p.end)}`;
  const metrics = (p.metrics || []).filter((m) => dk.byMetric && dk.byMetric[m]);

  const toggle = $("dekad-toggle");
  const body = $("dekad-body");
  toggle.checked = readToggle();
  body.hidden = !toggle.checked;
  $("dekad-count").textContent = `${dk.count}개 순`;
  toggle.onchange = () => {
    body.hidden = !toggle.checked;
    writeToggle(toggle.checked);
    if (toggle.checked) body.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  $("dekad-note").textContent = dk.note || "";

  // 1) 순별 요약 (해당 연도 값)
  $("dekad-overview").replaceChildren(tableBlock(dk.overview, `순별요약_${tag}`, `순별 기상 요약 (${ui.fmtDate(p.start)}~${ui.fmtDate(p.end)})`));

  // 2) 지표별 순별 비교 (전년·최근 5년·평년)
  const sel = $("dekad-metric");
  const prev = sel.value;
  sel.replaceChildren(...metrics.map((m) => el("option", { value: m, text: m })));
  if (metrics.includes(prev)) sel.value = prev;
  const draw = () => {
    const m = sel.value;
    const lay = ui.layout() === "combined" ? "combined" : "all";
    const tbl = dk.byMetric[m] && dk.byMetric[m][lay];
    $("dekad-compare").replaceChildren(tableBlock(tbl, `순별_${m}_${tag}`, `${m} 순별 비교`));
  };
  sel.onchange = draw;
  draw();

  // 3) 엑셀 저장: 요약 + 지표별 비교(편차·비율이 각각 숫자 열로 나뉜 표)
  const btn = $("dekad-excel");
  btn.onclick = async () => {
    btn.disabled = true;
    const label = btn.querySelector("span");
    const old = label.textContent;
    label.textContent = "엑셀 만드는 중…";
    try {
      const info = [
        `조회 기간: ${ui.fmtDate(p.start)} ~ ${ui.fmtDate(p.end)}`,
        `관측 지점: ${(p.stations || []).join(", ")} (값 = 선택 지점 평균)`,
        `평년 기준: ${res.summary && res.summary.normalNote ? res.summary.normalNote : "-"}`,
        dk.note || "",
      ];
      await saveWorkbook([
        { name: "순별 요약", title: "순별 기상 요약 (해당 연도)", notes: info, table: dk.overview },
        ...metrics.map((m) => ({ name: m, title: `${m} 순별 비교 (전년·최근 5년·평년)`, notes: info.slice(0, 3), table: dk.byMetric[m].all })),
      ], `제주_순별기상_${tag}.xlsx`);
      toast("순별 엑셀 파일을 저장했습니다.");
    } catch (e) {
      toast(`엑셀 저장 실패: ${e.message}`);
    } finally {
      btn.disabled = false;
      label.textContent = old;
    }
  };
}

/** 표 구성(편차·비율 보기 방식)을 바꾸면 순별 비교표도 같은 방식으로 다시 그림 */
export function refreshDekadCompare() {
  const sel = document.getElementById("dekad-metric");
  if (sel && sel.onchange) sel.onchange();
}

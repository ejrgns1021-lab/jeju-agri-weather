// brief.js (화면) — [AI 기상분석] 버튼 (v7.0)
// 파일·함수 이름에 ai/analyze 등을 쓰면 브라우저 광고·추적 차단 기능(특히 엣지)이 막으므로 중립적인 이름 사용
// 버튼을 누를 때만 서버(jejubrief)를 호출합니다. 서버는 방금 조회한 결과를 간추려 제미나이(Vertex AI)에게 해설을 받습니다.
// 같은 조회 조건으로 이미 분석한 내용은 저장돼 있어 바로 다시 보여 줍니다.
import { callFunction, ApiError } from "./api.js";
import { copyText } from "./tables.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let current = null;     // { params, text }
let busy = false;

/** 제미나이가 준 마크다운(제목·목록·굵게)만 간단히 표시 — 다른 HTML 은 모두 글자로 처리 */
function mdToHtml(md) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const out = [];
  let list = false;
  let buf = "";
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const li = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if ((h || !li) && list) { out.push(`<ul>${buf}</ul>`); buf = ""; list = false; }
    if (h) { out.push(h[1].length >= 3 ? `<h5>${inline(h[2])}</h5>` : `<h4>${inline(h[2])}</h4>`); continue; }
    if (li) { list = true; buf += `<li>${inline(li[1])}</li>`; continue; }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${buf}</ul>`);
  return out.join("");
}

function setButton(state) {
  const btn = $("ai-btn");
  $("ai-run").disabled = state === "busy";
  btn.disabled = state === "busy";
  btn.classList.toggle("busy", state === "busy");
  $("ai-btn-label").textContent = state === "busy" ? "AI가 분석 중…" : "AI 기상분석";
}

/** AI에 필요한 표만 골라 보냄 (서버가 기상청에 다시 묻지 않도록) */
function pickResult(res) {
  // 지점별 비교(stationBrief)와 일별 자료만 있으면 됨 — 제주 전체 평균표는 보내지 않음(평균의 착시 방지)
  return { params: res.params, summary: res.summary, stationBrief: res.stationBrief, daily: res.daily };
}

// v7.9: 요청 예시 — 누르면 입력칸에 채움(고쳐 쓸 수 있음)
const EXAMPLES = [
  ["마늘·양파 생육", "이번 기간 기상을 마늘·양파 생육 관점에서 분석해 줘"],
  ["감귤 착색·품질", "이번 기간 기상이 감귤 착색과 당도·품질에 미칠 영향을 중심으로 분석해 줘"],
  ["월동채소 관리", "월동채소(양배추·브로콜리·무·당근) 관리 관점에서 주의할 점을 정리해 줘"],
  ["보고서 5줄 요약", "보고서에 넣을 수 있게 핵심만 5줄 이내로 요약해 줘"],
];

async function run(getToken, getResult, refresh = false) {
  const res0 = getResult();
  const params = res0 && res0.params;
  if (!params || busy) return;
  const prompt = $("ai-prompt").value.trim();
  busy = true;
  setButton("busy");
  const card = $("ai-card");
  card.hidden = false;
  $("ai-body").innerHTML = '<div class="ai-wait"><span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span>'
    + `<p>${prompt ? "적어 주신 요청에 맞춰 조회 자료를 분석하는 중입니다." : "고른 지점별로 일별 자료에서 가뭄·과습·일조 부족·폭염 등이 며칠간 이어졌는지 찾고 최근 5년과 비교해 종합하는 중입니다."} 보통 20~60초 걸립니다.</p></div>`;
  $("ai-meta").textContent = "";
  try {
    const res = await callFunction("jejubrief", { token: getToken(), query: params, result: pickResult(res0), refresh, prompt });
    current = { params, text: res.text };
    $("ai-body").innerHTML = mdToHtml(res.text);
    $("ai-meta").textContent = (res.prompt ? `요청: "${res.prompt.length > 60 ? `${res.prompt.slice(0, 60)}…` : res.prompt}" · ` : "기본 분석 · ")
      + `${res.period} · ${res.cached ? "저장된 분석" : "새 분석"} ${res.createdAt} · ${res.modelLabel || `${res.model} (Vertex AI)`}`
      + " · AI가 작성한 참고 자료이므로 중요한 판단에는 표의 수치를 함께 확인하세요.";
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : String(e);
    $("ai-body").innerHTML = `<p class="ai-err">AI 분석을 하지 못했습니다. ${esc(msg)}</p>`;
  } finally {
    busy = false;
    setButton("idle");
  }
}

/** 새 조회 결과가 나오면 지난 분석을 숨기고 버튼을 켬 */
export function resetAi(hasResult) {
  $("ai-btn").hidden = !hasResult;
  $("ai-card").hidden = true;
  $("ai-body").innerHTML = "";
  $("ai-meta").textContent = "";
  current = null;     // 적어 둔 요청 문구는 그대로 둠(다른 기간에도 같은 요청으로 분석하기 쉽게)
}

export function setupAi(getToken, getResult) {
  // [AI 기상분석] 버튼은 요청 입력칸을 열고, [분석하기]를 눌러야 분석 (비워 두면 기본 분석)
  $("ai-btn").addEventListener("click", () => {
    $("ai-card").hidden = false;
    $("ai-prompt").focus({ preventScroll: true });
  });
  $("ai-run").addEventListener("click", () => run(getToken, getResult, false));
  $("ai-prompt").addEventListener("keydown", (ev) => {   // Ctrl(⌘)+Enter 로도 분석
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); run(getToken, getResult, false); }
  });
  $("ai-examples").replaceChildren(...EXAMPLES.map(([label, text]) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip ai-ex"; b.textContent = label; b.title = text;
    b.addEventListener("click", () => { $("ai-prompt").value = text; $("ai-prompt").focus({ preventScroll: true }); });
    return b;
  }));
  $("ai-refresh").addEventListener("click", () => run(getToken, getResult, true));
  $("ai-copy").addEventListener("click", async () => {
    if (!current) return;
    const plain = current.text.replace(/\*\*(.+?)\*\*/g, "$1").replace(/^#{1,4}\s+/gm, "■ ");
    const ok = await copyText(plain);
    const b = $("ai-copy");
    const old = b.lastChild.textContent;
    b.lastChild.textContent = ok ? "복사됨" : "복사 실패";
    setTimeout(() => { b.lastChild.textContent = old; }, 1500);
  });
}

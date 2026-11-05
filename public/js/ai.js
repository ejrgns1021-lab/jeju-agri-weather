// ai.js (화면) — [AI 기상분석] 버튼 (v7.0)
// 버튼을 누를 때만 서버(jejuai)를 호출합니다. 서버는 방금 조회한 결과를 간추려 제미나이(Vertex AI)에게 해설을 받습니다.
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
    if (h) { out.push(`<h4>${inline(h[2])}</h4>`); continue; }
    if (li) { list = true; buf += `<li>${inline(li[1])}</li>`; continue; }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${buf}</ul>`);
  return out.join("");
}

function setButton(state) {
  const btn = $("ai-btn");
  btn.disabled = state === "busy";
  btn.classList.toggle("busy", state === "busy");
  $("ai-btn-label").textContent = state === "busy" ? "AI가 분석 중…" : "AI 기상분석";
}

async function run(getToken, getParams, refresh = false) {
  const params = getParams();
  if (!params || busy) return;
  busy = true;
  setButton("busy");
  const card = $("ai-card");
  card.hidden = false;
  $("ai-body").innerHTML = '<div class="ai-wait"><span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span>'
    + "<p>조회한 기간의 기온·강수·일조·열대야를 전년, 최근 5년과 비교해 분석하고 있습니다. 보통 20~40초 걸립니다.</p></div>";
  $("ai-meta").textContent = "";
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const res = await callFunction("jejuai", { token: getToken(), query: params, refresh });
    current = { params, text: res.text };
    $("ai-body").innerHTML = mdToHtml(res.text);
    $("ai-meta").textContent = `${res.period} · ${res.cached ? "저장된 분석" : "새 분석"} ${res.createdAt} · ${res.model} (Vertex AI)`
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
  current = null;
}

export function setupAi(getToken, getParams) {
  $("ai-btn").addEventListener("click", () => run(getToken, getParams, false));
  $("ai-refresh").addEventListener("click", () => run(getToken, getParams, true));
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

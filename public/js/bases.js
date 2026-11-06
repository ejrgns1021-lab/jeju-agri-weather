// bases.js (화면, v7.3) — 비교 기준(전년·최근 3년·최근 5년·평년) 선택
// 서버는 네 가지를 모두 계산해 보내고, 화면은 고른 기준의 열만 보여 줍니다 (다시 조회할 필요 없음).
export const BASES = [
  { key: "prev", label: "전년", short: "전년", re: /^전년/ },
  { key: "r3", label: "최근 3년", short: "최근3년", re: /최근 ?3년/ },
  { key: "r5", label: "최근 5년", short: "최근5년", re: /최근 ?5년/ },
  { key: "normal", label: "평년", short: "평년", re: /^평년/ },
];
const KEY = "jeju-weather-bases";
const DEFAULT = ["prev", "r5", "normal"];
let selected = load();
const listeners = [];

function load() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "null");
    if (Array.isArray(v) && v.length && v.every((k) => BASES.some((b) => b.key === k))) return v;
  } catch (e) { /* 저장 불가 환경 */ }
  return [...DEFAULT];
}

export const isOn = (key) => selected.includes(key);
export const selectedBases = () => BASES.filter((b) => isOn(b.key));
export function onBasesChange(fn) { listeners.push(fn); }

export function toggleBase(key) {
  const next = isOn(key) ? selected.filter((k) => k !== key) : [...selected, key];
  if (!next.length) return false;            // 최소 1개는 남김
  selected = BASES.map((b) => b.key).filter((k) => next.includes(k));
  try { localStorage.setItem(KEY, JSON.stringify(selected)); } catch (e) { /* 무시 */ }
  listeners.forEach((fn) => { try { fn(); } catch (e) { /* 개별 화면 오류 무시 */ } });
  return true;
}

/** 서버 표에서 고르지 않은 기준의 열을 뺀 표 (해당 연도·구분 등 다른 열은 그대로) */
export function filterBases(tbl) {
  if (!tbl || !tbl.columns) return tbl;
  const off = BASES.filter((b) => !isOn(b.key));
  const keep = tbl.columns.map((c) => !off.some((b) => b.re.test(String(c))));
  if (keep.every(Boolean)) return tbl;
  return { columns: tbl.columns.filter((_, i) => keep[i]), rows: (tbl.rows || []).map((r) => r.filter((_, i) => keep[i])) };
}

/** 칩 버튼 그리기 */
export function renderBaseChips(container) {
  container.replaceChildren();
  const label = document.createElement("span");
  label.className = "base-label";
  label.textContent = "비교 기준";
  container.append(label);
  BASES.forEach((b) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `base-chip${isOn(b.key) ? " on" : ""}`;
    btn.setAttribute("aria-pressed", String(isOn(b.key)));
    btn.textContent = b.label;
    btn.addEventListener("click", () => {
      if (toggleBase(b.key)) renderBaseChips(container);
    });
    container.append(btn);
  });
}

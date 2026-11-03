// excel.js (화면) — 표(JSON)를 엑셀(.xlsx) 파일로 저장 (SheetJS, 필요할 때만 불러옴)
// 숫자처럼 보이는 칸("19.3", "-0.4")은 엑셀에서 계산할 수 있도록 숫자로, "-"(자료 없음)는 빈 칸으로 저장합니다.

const SHEETJS_URLS = [
  "https://cdn.sheetjs.com/xlsx-0.20.3/package/xlsx.mjs",
  "https://cdn.jsdelivr.net/npm/xlsx@0.18.5/xlsx.mjs",
];
let libPromise = null;

async function loadXlsx() {
  if (!libPromise) {
    libPromise = (async () => {
      let lastErr;
      for (const url of SHEETJS_URLS) {
        try { return await import(/* @vite-ignore */ url); } catch (e) { lastErr = e; }
      }
      libPromise = null;
      throw lastErr || new Error("엑셀 라이브러리를 불러오지 못했습니다.");
    })();
  }
  return libPromise;
}

const NUM_RE = /^[+-]?\d+(\.\d+)?$/;

function cell(v) {
  const s = v == null ? "" : String(v).trim();
  if (s === "-" || s === "" || s.toLowerCase() === "nan") return null;
  return NUM_RE.test(s) ? Number(s) : s;
}

function safeSheetName(name, used) {
  let base = String(name).replace(/[\\/?*[\]:]/g, " ").slice(0, 28).trim() || "Sheet";
  let n = base, i = 2;
  while (used.has(n)) n = `${base.slice(0, 25)} ${i++}`;
  used.add(n);
  return n;
}

/**
 * sheets: [{ name, table: {columns, rows}, title?, notes?: string[] }]
 */
export async function saveWorkbook(sheets, filename) {
  const XLSX = await loadXlsx();
  const wb = XLSX.utils.book_new();
  const used = new Set();
  for (const sh of sheets) {
    if (!sh || !sh.table || !sh.table.columns) continue;
    const aoa = [];
    if (sh.title) aoa.push([sh.title]);
    (sh.notes || []).forEach((n) => aoa.push([n]));
    if (aoa.length) aoa.push([]);
    const headerRow = aoa.length;
    aoa.push(sh.table.columns);
    sh.table.rows.forEach((r) => aoa.push(r.map(cell)));
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    // 열 너비: 머리글·내용 길이에 맞춤 (한글은 2칸으로 계산)
    const width = (v) => [...String(v ?? "")].reduce((w, ch) => w + (/[ㄱ-힣]/.test(ch) ? 2 : 1), 0);
    ws["!cols"] = sh.table.columns.map((c, i) => ({
      wch: Math.min(40, Math.max(6, width(c), ...sh.table.rows.map((r) => width(r[i]))) + 2),
    }));
    if (sh.table.rows.length) ws["!autofilter"] = { ref: XLSX.utils.encode_range({ s: { r: headerRow, c: 0 }, e: { r: headerRow + sh.table.rows.length, c: sh.table.columns.length - 1 } }) };
    XLSX.utils.book_append_sheet(wb, ws, safeSheetName(sh.name, used));
  }
  if (!wb.SheetNames.length) throw new Error("저장할 표가 없습니다.");
  XLSX.writeFile(wb, filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`, { compression: true });
}

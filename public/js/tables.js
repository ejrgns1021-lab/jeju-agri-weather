// tables.js (화면) — 서버가 보낸 표(JSON)를 그리기 · 한글(HWP) 복사 · CSV 저장

const HWP = {
  table: "border-collapse:collapse;border:1px solid #000;font-family:'맑은 고딕','Malgun Gothic',sans-serif;font-size:10pt;margin:4px 0;",
  th: "border:1px solid #000;background:#e9eef5;padding:4px 10px;text-align:center;font-weight:bold;white-space:nowrap;",
  tdC: "border:1px solid #000;padding:4px 10px;text-align:center;white-space:nowrap;",
  tdL: "border:1px solid #000;padding:4px 10px;text-align:left;white-space:nowrap;",
  cap: "caption-side:top;text-align:left;font-weight:bold;padding:2px 0;",
};

/** 화면 표시용 표 */
export function buildTable(tbl, caption) {
  const table = document.createElement("table");
  table.className = "data-table";
  if (caption) {
    const c = table.createCaption();
    c.textContent = caption;
  }
  const thead = table.createTHead().insertRow();
  tbl.columns.forEach((col) => {
    const th = document.createElement("th");
    th.textContent = col;
    thead.appendChild(th);
  });
  const tbody = table.createTBody();
  tbl.rows.forEach((row) => {
    const tr = tbody.insertRow();
    row.forEach((v, i) => {
      const td = tr.insertCell();
      td.textContent = v;
      if (i === 0) td.className = "first";
      else if (/^[+]/.test(v)) td.classList.add("pos");
      else if (/^-\d/.test(v)) td.classList.add("neg");
    });
  });
  return table;
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** 한글에 붙여넣어도 표 모양이 유지되도록 모든 칸에 스타일을 넣은 HTML */
export function toHwpHtml(tbl, caption) {
  const parts = [`<table style="${HWP.table}">`];
  if (caption) parts.push(`<caption style="${HWP.cap}">${esc(caption)}</caption>`);
  parts.push("<thead><tr>", ...tbl.columns.map((c) => `<th style="${HWP.th}">${esc(c)}</th>`), "</tr></thead><tbody>");
  tbl.rows.forEach((row) => {
    parts.push("<tr>", ...row.map((v, i) => `<td style="${i === 0 ? HWP.tdL : HWP.tdC}">${esc(v)}</td>`), "</tr>");
  });
  parts.push("</tbody></table>");
  return parts.join("");
}

export function toTsv(tbl) {
  return [tbl.columns, ...tbl.rows].map((r) => r.join("\t")).join("\n");
}

function toCsv(tbl) {
  const q = (v) => (/[",\n]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : v);
  return [tbl.columns, ...tbl.rows].map((r) => r.map(q).join(",")).join("\r\n");
}

/** 한글용 복사: HTML 표 + 탭 텍스트를 함께 클립보드에 */
export async function copyForHwp(tbl, caption) {
  const html = toHwpHtml(tbl, caption);
  const text = toTsv(tbl);
  if (navigator.clipboard && window.ClipboardItem) {
    try {
      await navigator.clipboard.write([new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([text], { type: "text/plain" }),
      })]);
      return true;
    } catch (e) { /* 아래 방식으로 재시도 */ }
  }
  // 예전 브라우저: 화면 밖에 표를 만들어 선택 후 복사
  const holder = document.createElement("div");
  holder.style.cssText = "position:fixed;left:-9999px;top:0;";
  holder.innerHTML = html;
  document.body.appendChild(holder);
  const range = document.createRange();
  range.selectNodeContents(holder);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  const ok = document.execCommand("copy");
  sel.removeAllRanges();
  holder.remove();
  return ok;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px;";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

export function downloadCsv(tbl, filename) {
  const blob = new Blob(["﻿" + toCsv(tbl)], { type: "text/csv;charset=utf-8" }); // 엑셀 한글 깨짐 방지(BOM)
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

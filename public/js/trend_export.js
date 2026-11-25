// trend_export.js (화면, v7.8) — [그래프로 보기] 보조 기능
//   1) 핵심 요약 배지 계산 (탭별 2~3개, 결측 null 제외)
//   2) 보고서용 이미지 만들기: 제목·요약 배지·범례·그래프를 흰 배경 2배 해상도 PNG로 → 클립보드 복사(안 되면 파일 저장)
// 화면 색(다크 모드 포함)과 관계없이 이미지는 항상 밝은 색으로 만듦(인쇄·한글 붙여넣기용).

const ok = (v) => v !== null && v !== undefined && !Number.isNaN(v);
const fmt = (v, d = 1) => (ok(v) ? Number(v).toFixed(d) : "-");
const sgn = (v, d = 1) => (ok(v) ? `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}` : "-");
export const mmdd = (iso) => { const [, m, d] = iso.split("-"); return `${m}.${d}`; };

const valid = (a) => (a || []).filter(ok);
const mean = (a) => { const v = valid(a); return v.length ? v.reduce((x, y) => x + y, 0) / v.length : null; };
const sum = (a) => { const v = valid(a); return v.length ? v.reduce((x, y) => x + y, 0) : null; };
function argBest(a, better) {
  let bi = -1;
  (a || []).forEach((v, i) => { if (ok(v) && (bi < 0 || better(v, a[bi]))) bi = i; });
  return bi;
}
/** 기준값 합계는 금년 자료가 있는 날만 더함 (기간 끝 미관측일이 기준에만 더해지지 않도록) */
const sumWhere = (base, cur) => { let s = 0, n = 0; (base || []).forEach((v, i) => { if (ok(v) && ok(cur[i])) { s += v; n++; } }); return n ? s : null; };

/**
 * 탭별 핵심 요약 배지 (문자열 배열)
 * @param {string} tab avg|maxmin|rain|sun
 * @param {object} chart result.chart
 * @param {string|null} base 비교 기준 키(prev|r3|r5|normal)
 * @param {string} baseLabel 예: "평년"
 */
export function summaryBadges(tab, chart, base, baseLabel) {
  const M = chart.metrics, D = chart.dates;
  const vs = (d, unit) => (base && ok(d) ? ` (${baseLabel} 대비 ${sgn(d)}${unit})` : "");
  if (tab === "avg") {
    const m = M["평균 기온"] || {};
    const c = mean(m.cur), b = base ? mean(m[base]) : null;
    const hi = argBest(m.cur, (x, y) => x > y), lo = argBest(m.cur, (x, y) => x < y);
    return [
      `기간 평균 ${fmt(c)}℃${vs(ok(b) ? c - b : null, "℃")}`,
      hi >= 0 ? `최고 ${fmt(m.cur[hi])}℃ (${mmdd(D[hi])})` : null,
      lo >= 0 ? `최저 ${fmt(m.cur[lo])}℃ (${mmdd(D[lo])})` : null,
    ].filter(Boolean);
  }
  if (tab === "maxmin") {
    const mx = M["최고 기온"] || {}, mn = M["최저 기온"] || {};
    const cx = mean(mx.cur), cn = mean(mn.cur);
    const bx = base ? mean(mx[base]) : null, bn = base ? mean(mn[base]) : null;
    const range = mean((mx.cur || []).map((v, i) => (ok(v) && ok((mn.cur || [])[i]) ? v - mn.cur[i] : null)));
    return [
      `평균 최고 ${fmt(cx)}℃${vs(ok(bx) ? cx - bx : null, "℃")}`,
      `평균 최저 ${fmt(cn)}℃${vs(ok(bn) ? cn - bn : null, "℃")}`,
      ok(range) ? `평균 일교차 ${fmt(range)}℃` : null,
    ].filter(Boolean);
  }
  const isRain = tab === "rain";
  const m = M[isRain ? "누적 강수량" : "일조 시간"] || {};
  const unit = isRain ? "mm" : "hr";
  const c = sum(m.cur), b = base ? sumWhere(m[base], m.cur || []) : null;
  const cmp = base && ok(b) ? ` (${baseLabel} 대비 ${b > 0 ? `${Math.round((c / b) * 100)}%, ` : ""}${sgn(c - b)}${unit})` : "";
  if (isRain) {
    const hi = argBest(m.cur, (x, y) => x > y);
    let run = 0, best = 0;
    (m.cur || []).forEach((v) => { if (ok(v) && v < 0.1) { run++; best = Math.max(best, run); } else run = 0; });
    return [
      `누적 ${fmt(c)}mm${cmp}`,
      hi >= 0 && m.cur[hi] > 0 ? `최다 일강수 ${fmt(m.cur[hi])}mm (${mmdd(D[hi])})` : "강수 없음",
      `무강수 최장 ${best}일`,
    ];
  }
  const short = valid(m.cur).filter((v) => v < 3).length;
  return [`누적 ${fmt(c)}hr${cmp}`, `일평균 ${fmt(mean(m.cur))}hr`, `일조부족일(3hr 미만) ${short}일`];
}

// ---------------------------------------------------------------- 이미지 내보내기
/** 이미지에 쓰는 밝은 색 (화면 CSS 의 라이트 모드 값과 같게 유지) */
export const LIGHT = {
  cur: "#2e7d32", prev: "#0096d6", r3: "#b88400", r5: "#e0457f", normal: "#4a4f4b",
  rain: "#1f4e9c", rainArea: "rgba(42,120,214,0.16)", rainBar: "#a9cbe8",
  sun: "#d06a00", sunArea: "rgba(230,140,20,0.16)", sunBar: "#f2cf8c",
  pos: "#e0603a", neg: "#2a78d6", wet: "#1f8fb8", dry: "#c0782a",
  band: "rgba(46,125,50,0.10)", grid: "#dfe7e1", zero: "#9aa79f", tick: "#64746c", ink: "#17261f", sub: "#52625a",
};
const L = LIGHT;
const EXPORT_CSS = `
text { font-family: 'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif; }
.t-grid { stroke: ${L.grid}; stroke-width: 1; } .t-zero { stroke: ${L.zero}; stroke-width: 1.5; }
.t-tick, .t-cap { fill: ${L.tick}; font-size: 11px; } .t-cap { font-size: 10.5px; }
.t-line { fill: none; stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; }
.t-line.main { stroke-width: 2.5; } .t-line.dash { stroke-dasharray: 5 4; stroke-width: 1.75; } .t-line.dot { stroke-dasharray: 1.5 3.5; stroke-width: 2; }
.s-cur { stroke: ${L.cur}; } .s-prev { stroke: ${L.prev}; } .s-r3 { stroke: ${L.r3}; } .s-r5 { stroke: ${L.r5}; } .s-normal { stroke: ${L.normal}; }
.s-rain { stroke: ${L.rain}; } .s-sun { stroke: ${L.sun}; } .s-ink { stroke: ${L.ink}; }
.t-pt { fill: ${L.cur}; stroke: #fff; stroke-width: 1; }
.t-area.a-rain { fill: ${L.rainArea}; } .t-area.a-sun { fill: ${L.sunArea}; }
.t-band { fill: ${L.band}; } .t-bar.b-rain { fill: ${L.rainBar}; } .t-bar.b-sun { fill: ${L.sunBar}; }
.t-pos { fill: ${L.pos}; } .t-neg { fill: ${L.neg}; } .t-wet { fill: ${L.wet}; opacity: .55; } .t-dry { fill: ${L.dry}; opacity: .55; }
.t-cross, .t-hdot { display: none; }
`;

const FONT = "'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif";
let measureCtx = null;
function textW(text, size, weight = 400) {
  measureCtx = measureCtx || document.createElement("canvas").getContext("2d");
  measureCtx.font = `${weight} ${size}px ${FONT}`;
  return measureCtx.measureText(text).width;
}
const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/**
 * 그래프 SVG + 제목·배지·범례로 보고서용 SVG 문자열 만들기
 * legend: [{label, color, type: line|dash|dot|area|bar|pos|neg}]
 */
function composeSvg({ title, sub, badges, legend, svg, source }) {
  const vb = svg.viewBox.baseVal;
  const cw = vb.width, ch = vb.height, pad = 20, W = cw + pad * 2;
  const parts = [];
  let y = pad + 18;
  parts.push(`<text x="${pad}" y="${y}" font-size="17" font-weight="700" fill="${L.ink}">${esc(title)}</text>`);
  y += 20;
  if (sub) { parts.push(`<text x="${pad}" y="${y}" font-size="12.5" fill="${L.sub}">${esc(sub)}</text>`); y += 14; }
  // 배지 (줄바꿈)
  let x = pad; y += 8;
  const bh = 26;
  badges.forEach((b) => {
    const w = textW(b, 13, 600) + 22;
    if (x + w > W - pad && x > pad) { x = pad; y += bh + 6; }
    parts.push(`<rect x="${x}" y="${y}" width="${w}" height="${bh}" rx="13" fill="#eef4ef" stroke="${L.grid}"/>`
      + `<text x="${x + 11}" y="${y + 17.5}" font-size="13" font-weight="600" fill="${L.ink}">${esc(b)}</text>`);
    x += w + 6;
  });
  y += bh + 12;
  // 범례
  x = pad;
  legend.forEach((g) => {
    const w = 26 + textW(g.label, 12.5) + 14;
    if (x + w > W - pad && x > pad) { x = pad; y += 20; }
    const cy = y + 8;
    if (g.type === "bar" || g.type === "pos" || g.type === "neg" || g.type === "area") {
      parts.push(`<rect x="${x}" y="${cy - 6}" width="18" height="12" rx="2" fill="${g.color}"/>`);
    } else {
      const da = g.type === "dash" ? ' stroke-dasharray="5 4"' : g.type === "dot" ? ' stroke-dasharray="1.5 3.5"' : "";
      parts.push(`<line x1="${x}" x2="${x + 20}" y1="${cy}" y2="${cy}" stroke="${g.color}" stroke-width="${g.main ? 3 : 2}"${da}/>`);
    }
    parts.push(`<text x="${x + 26}" y="${cy + 4.5}" font-size="12.5" fill="${L.sub}">${esc(g.label)}</text>`);
    x += w;
  });
  y += 24;
  // 그래프 본체 (마우스 표시선·점은 뺌)
  const clone = svg.cloneNode(true);
  clone.querySelectorAll(".t-cross, .t-hdot").forEach((e) => e.remove());
  const inner = Array.from(clone.childNodes).map((n) => new XMLSerializer().serializeToString(n)).join("");
  parts.push(`<g transform="translate(${pad},${y})">${inner}</g>`);
  y += ch + 8;
  if (source) { parts.push(`<text x="${W - pad}" y="${y + 6}" font-size="11" fill="${L.tick}" text-anchor="end">${esc(source)}</text>`); y += 12; }
  const H = Math.ceil(y + pad - 6);
  return {
    W, H,
    text: `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`
      + `<style>${EXPORT_CSS}</style><rect width="100%" height="100%" fill="#ffffff"/>${parts.join("")}</svg>`,
  };
}

function svgToPng({ text, W, H }, scale = 2) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml;charset=utf-8" }));
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = Math.round(W * scale); c.height = Math.round(H * scale);
      const ctx = c.getContext("2d");
      ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      c.toBlob((b) => (b ? resolve(b) : reject(new Error("이미지를 만들지 못했습니다."))), "image/png");
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("이미지를 만들지 못했습니다.")); };
    img.src = url;
  });
}

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

/**
 * 보고서용 그래프 이미지를 클립보드에 복사 (안 되면 PNG 파일로 저장)
 * @returns {Promise<"copied"|"downloaded">}
 */
export async function copyChartImage(opts) {
  const composed = composeSvg(opts);
  const blobPromise = svgToPng(composed, 2);
  const name = opts.fileName || "기상_그래프.png";
  // 클립보드 쓰기는 버튼을 누른 직후에 요청해야 함 → 이미지(약속)를 그대로 넘김
  if (window.isSecureContext && navigator.clipboard && window.ClipboardItem) {
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blobPromise })]);
      return "copied";
    } catch (e) { /* 사내망·브라우저 설정으로 막힘 → 파일 저장 */ }
  }
  download(await blobPromise, name);
  return "downloaded";
}

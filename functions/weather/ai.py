# -*- coding: utf-8 -*-
"""
ai.py - AI 기상분석 (v7.0)

조회 결과(비교표·순별·일별·지점별)를 그대로 넘기지 않고, 농업적으로 의미 있는 내용만 간추린
'분석 자료'를 만들어 제미나이(Vertex AI)에게 해설을 요청합니다.

  - 비교표: 해당 연도 / 전년 / 최근 5년 / 평년
  - 순별 비교: 순마다 해당 연도와 전년·최근 5년 차이
  - 일별 사건(자동 탐지): 연속 무강수(건조), 폭염일(최고 33℃ 이상), 열대야, 호우, 영하·서리 위험, 일조 부족 연속
  - 지점별 값과 극값(가장 더웠던 날 등)

수치는 모두 기존 계산 결과에서 가져오며, 여기서 새로 계산하는 것은 '사건 구간 찾기'뿐입니다.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

MODEL = "gemini-3.8-flash"          # Vertex AI 안정 버전 (2026-09 출시)
LOCATION = "global"
PROMPT_VERSION = 2                  # v2: 최근 5년 대비만, 특이 기상 요인만 기간 중심으로 짧게

DRY_SPELL_DAYS = 7                  # 연속 무강수 기준(일)
HEAT_DAY_C = 33.0                   # 폭염일: 일 최고기온
TROPICAL_C = 25.0                   # 열대야: 일 최저기온
HEAVY_RAIN_MM = 80.0                # 호우: 일강수량(지점)
FROST_C = 2.0                       # 서리 위험: 일 최저기온
LOW_SUN_HR = 2.0                    # 일조 부족: 일 일조시간
LOW_SUN_DAYS = 5


class AiError(Exception):
    """화면에 보여줄 짧은 문구"""


# ------------------------------------------------------------------ 표 읽기 도우미
def _f(v: Any) -> Optional[float]:
    try:
        s = str(v).replace(",", "").replace("+", "").strip()
        return float(s) if s not in ("", "-", "nan", "None") else None
    except ValueError:
        return None


def _table_text(tbl: Dict[str, Any], max_rows: int = 60) -> str:
    if not tbl or not tbl.get("columns"):
        return "(자료 없음)"
    lines = [" | ".join(map(str, tbl["columns"]))]
    lines += [" | ".join(map(str, r)) for r in tbl.get("rows", [])[:max_rows]]
    return "\n".join(lines)


def _keep_cols(tbl: Dict[str, Any], head: Tuple[str, ...]) -> Dict[str, Any]:
    """표에서 앞부분(구분·순·기간)과 '해당 연도 / 최근 5년' 관련 열만 남김 (전년·평년 열 제외)"""
    cols = tbl.get("columns") or []
    keep = [i for i, c in enumerate(cols) if c in head or c.startswith("해당 연도") or "최근 5년" in c or "최근5년" in c]
    return {"columns": [cols[i] for i in keep], "rows": [[r[i] for i in keep] for r in tbl.get("rows") or []]}


def _md(d: str) -> str:
    """2025-07-03 → 7/3"""
    try:
        return f"{int(d[5:7])}/{int(d[8:10])}"
    except (ValueError, IndexError):
        return d


def _spells(dates: List[str], flags: List[bool], min_len: int) -> List[Tuple[str, str, int]]:
    """연속 구간 (시작일, 종료일, 일수) — min_len일 이상만"""
    out, start, n = [], None, 0
    for d, f in zip(dates + [None], flags + [False]):
        if f:
            start = start or d
            n += 1
        else:
            if start and n >= min_len:
                out.append((start, prev, n))
            start, n = None, 0
        prev = d
    return out


def _daily_series(daily: Dict[str, Any], metric: str) -> Tuple[List[str], List[Optional[float]], Dict[str, List[Optional[float]]]]:
    tbl = (daily or {}).get(metric) or {}
    cols = tbl.get("columns") or []
    rows = tbl.get("rows") or []
    if not cols:
        return [], [], {}
    i_avg = cols.index("제주 전체 평균") if "제주 전체 평균" in cols else None
    st_cols = [c for c in cols[1:] if c not in ("제주 전체 평균", "유효 지점 수")]
    dates = [r[0] for r in rows]
    avg = [_f(r[i_avg]) if i_avg is not None else None for r in rows]
    per = {c: [_f(r[cols.index(c)]) for r in rows] for c in st_cols}
    return dates, avg, per


def _fmt_spells(spells: List[Tuple[str, str, int]], unit: str = "일") -> str:
    return ", ".join(f"{_md(a)}~{_md(b)}({n}{unit})" for a, b, n in spells) or "없음"


def detect_events(daily: Dict[str, Any]) -> List[str]:
    """일별 자료에서 농업적으로 의미 있는 사건 구간을 찾음 (제주 전체 평균 기준, 호우·극값은 지점 기준)"""
    ev: List[str] = []
    # 강수: 연속 무강수(제주 전체 평균 0.1mm 미만)
    d, rain, rain_st = _daily_series(daily, "누적 강수량")
    if d:
        dry = _spells(d, [r is not None and r < 0.1 for r in rain], DRY_SPELL_DAYS)
        ev.append(f"- 연속 무강수({DRY_SPELL_DAYS}일 이상, 4개 지점 평균 일강수량 0.1mm 미만): {_fmt_spells(dry)}")
        longest = max(dry, key=lambda x: x[2]) if dry else None
        if longest:
            ev.append(f"  · 가장 긴 무강수 기간: {_md(longest[0])}~{_md(longest[1])} {longest[2]}일")
        heavy = []
        for i, day in enumerate(d):
            hits = [(st, v[i]) for st, v in rain_st.items() if v[i] is not None and v[i] >= HEAVY_RAIN_MM]
            if hits:
                heavy.append(f"{_md(day)}(" + ", ".join(f"{st} {x:.0f}mm" for st, x in hits) + ")")
        ev.append(f"- 호우(지점 일강수량 {HEAVY_RAIN_MM:.0f}mm 이상): {', '.join(heavy) if heavy else '없음'}")
        wet = [(day, r) for day, r in zip(d, rain) if r is not None]
        if wet:
            top = sorted(wet, key=lambda x: -x[1])[:3]
            ev.append("- 강수가 가장 많았던 날(4개 지점 평균): " + ", ".join(f"{_md(a)} {b:.1f}mm" for a, b in top if b > 0))
    # 고온: 폭염일·열대야
    d, tmax, tmax_st = _daily_series(daily, "최고 기온")
    if d:
        hot = [x is not None and x >= HEAT_DAY_C for x in tmax]
        ev.append(f"- 폭염일(4개 지점 평균 최고기온 {HEAT_DAY_C:.0f}℃ 이상): 총 {sum(hot)}일, 연속 구간 {_fmt_spells(_spells(d, hot, 2))}")
        best = max(((v[i], st, d[i]) for st, v in tmax_st.items() for i in range(len(d)) if v[i] is not None), default=None)
        if best:
            ev.append(f"- 최고기온 극값: {best[1]} {_md(best[2])} {best[0]:.1f}℃")
    d, tmin, tmin_st = _daily_series(daily, "최저 기온")
    if d:
        trop = [x is not None and x >= TROPICAL_C for x in tmin]
        ev.append(f"- 열대야(4개 지점 평균 최저기온 {TROPICAL_C:.0f}℃ 이상): 총 {sum(trop)}일, 연속 구간 {_fmt_spells(_spells(d, trop, 3))}")
        frost = [(st, d[i], v[i]) for st, v in tmin_st.items() for i in range(len(d)) if v[i] is not None and v[i] <= FROST_C]
        if frost:
            days = sorted({x[1] for x in frost})
            low = min(frost, key=lambda x: x[2])
            ev.append(f"- 서리·저온 위험(지점 최저기온 {FROST_C:.0f}℃ 이하): {len(days)}일 ({', '.join(_md(x) for x in days[:12])}{' 등' if len(days) > 12 else ''}), 최저 {low[0]} {_md(low[1])} {low[2]:.1f}℃")
        else:
            ev.append(f"- 서리·저온 위험(지점 최저기온 {FROST_C:.0f}℃ 이하): 없음")
    d, sun, _ = _daily_series(daily, "일조 시간")
    if d:
        dim = _spells(d, [x is not None and x < LOW_SUN_HR for x in sun], LOW_SUN_DAYS)
        ev.append(f"- 일조 부족 연속({LOW_SUN_DAYS}일 이상, 일조 {LOW_SUN_HR:.0f}시간 미만): {_fmt_spells(dim)}")
    return ev


def build_digest(result: Dict[str, Any]) -> str:
    """제미나이에게 줄 분석 자료(텍스트). 수치는 조회 결과 그대로"""
    s = result.get("summary") or {}
    p = result.get("params") or {}
    parts = [
        f"[조회 조건] 기간 {p.get('start')} ~ {p.get('end')} ({s.get('periodDays')}일), 지점 {', '.join(p.get('stations') or [])}",
        f"[비교 기준] 해당 연도 {s.get('currentYear')} vs 최근 5년 평균({s.get('recentRange')})",
        f"[집계 방법] {s.get('aggDesc', '')}",
        "",
        "## 1. 기간 전체 비교표 (제주 전체 = 4개 지점 평균, 해당 연도 vs 최근 5년 평균)",
        _table_text(_keep_cols((result.get("comparison") or {}).get("all") or {}, ("구분",))),
        "",
        "## 2. 지점별 값 (해당 연도)",
        _table_text(result.get("station") or {}),
        "",
        "## 3. 순별(상·중·하순) 비교 (해당 연도 vs 최근 5년 평균)",
    ]
    dek = result.get("dekad") or {}
    for metric, tbls in (dek.get("byMetric") or {}).items():
        t = (tbls or {}).get("all") or {}
        if not t.get("rows"):
            continue
        parts.append(f"### {metric}")
        parts.append(_table_text(_keep_cols(t, ("순", "기간"))))
    if dek.get("partial"):
        parts.append(f"(일부만 포함된 순: {', '.join(map(str, dek['partial']))})")
    parts += ["", "## 4. 일별 자료에서 찾은 주요 기상 사건 (해당 연도)"]
    parts += detect_events(result.get("daily") or {})
    return "\n".join(parts)


SYSTEM = """당신은 제주특별자치도 농업기술원의 농업기상 전문 지도사입니다.
주어진 '분석 자료'의 수치만 근거로, 제주 농작물에 영향을 줄 만한 '특별한 기상 요인'만 골라 짧게 알려 줍니다.

가장 중요한 원칙
1. 모든 항목은 그 기상 요인이 나타난 '기간'(예: 7/21~7/31, 11일간 / 8월 중순)으로 시작합니다. 기간이 없는 항목은 쓰지 않습니다.
2. 비교는 '최근 5년 평균'과만 합니다. 전년·평년은 언급하지 않습니다.
3. 평범한 수준(최근 5년과 비슷함)은 쓰지 않습니다. 아래처럼 뚜렷한 것만 고릅니다.
   - 순별 강수량이 최근 5년의 50% 이하(가뭄·건조) 또는 200% 이상(과습)
   - 연속 무강수 7일 이상, 지점 일강수량 80mm 이상 호우
   - 순별 기온이 최근 5년보다 1.5℃ 이상 높거나 낮음, 폭염일·열대야가 여러 날 이어짐
   - 최저기온 2℃ 이하(서리·저온), 일조 부족이 5일 이상 이어지거나 순별 일조가 최근 5년의 70% 이하
4. 자료에 없는 수치·날짜를 만들지 않습니다. 수치는 자료 값을 그대로 씁니다.
5. 농작물 영향은 그 시기에 제주에서 실제 재배·관리하는 작목(감귤·만감류, 월동채소: 양배추·브로콜리·무·당근·양파·마늘, 감자, 콩·메밀 등 밭작물, 시설작물)만 한 줄로 짚습니다. 불확실하면 "~우려"로 씁니다.
6. 보고서체(~임, ~함), 전체 500자 안팎. 군더더기 설명·서론·맺음말 없이.

출력 형식(마크다운, 이 형식 그대로)
## 한 줄 요약
(가장 두드러진 기상 요인 1~2개를 기간과 함께 한 문장)
## 주의할 기상 요인
- **기간(일수)** 요인 — 최근 5년 대비 수치 → 작목 영향
(시간 순서, 최대 5개. 특별한 요인이 없으면 "- 최근 5년과 비교해 두드러진 기상 요인 없음" 한 줄)
## 관리 포인트
- 지금 또는 다음에 할 일 1~3개 (짧게)
"""


def _project_id() -> str:
    for k in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        if os.environ.get(k):
            return os.environ[k]
    return "jeju-agri-weather"


def generate(digest: str) -> str:
    """Vertex AI 제미나이 호출 (서버의 서비스 계정으로 인증 — API 키 없음)"""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:      # pragma: no cover
        raise AiError("AI 분석 모듈이 설치되지 않았습니다.") from exc
    try:
        client = genai.Client(enterprise=True, project=_project_id(), location=LOCATION)
        resp = client.models.generate_content(
            model=MODEL,
            contents=f"다음 분석 자료로 기상 분석 보고를 작성해 주세요.\n\n{digest}",
            config=types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0.2, max_output_tokens=2048),
        )
    except Exception as exc:
        msg = str(exc)
        if "PERMISSION_DENIED" in msg or "403" in msg:
            raise AiError("Vertex AI 사용 권한이 없습니다. (프로젝트에서 Vertex AI API 사용 설정 필요)") from exc
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            raise AiError("AI 사용량이 많아 잠시 후 다시 시도해 주세요.") from exc
        raise AiError(f"AI 분석 요청 실패 ({exc.__class__.__name__})") from exc
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise AiError("AI가 분석 내용을 만들지 못했습니다. 다시 시도해 주세요.")
    return text

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

v7.4: 모델 2종 — 관리자 화면에서 선택 (분석 자료·지시문은 모델과 무관하게 같음, 호출부만 다름)
  - gemini-3.8-flash : Vertex AI (서버 서비스 계정 인증, API 키 없음)
  - gemma-4-26b-a4b-it / gemma-4-31b-it : Google AI Studio 무료 API 키 (젬마, 행정안전부 AI 공통기반 지원 모델 계열)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# 모델 목록 — 새 모델은 여기에 한 줄 추가 (provider: vertex | aistudio)
MODELS: Dict[str, Dict[str, str]] = {
    "gemini-3.8-flash": {"label": "Gemini 3.8 Flash", "provider": "vertex", "note": "Vertex AI · 서버 계정 인증"},
    "gemma-4-26b-a4b-it": {"label": "Gemma 4 26B (빠름)", "provider": "aistudio", "note": "Google AI Studio 무료 키 · 공개 모델 · 응답 빠름"},
    "gemma-4-31b-it": {"label": "Gemma 4 31B", "provider": "aistudio", "note": "Google AI Studio 무료 키 · 공개 모델 · 더 크고 느림"},
}
DEFAULT_MODEL = "gemini-3.8-flash"
MODEL = DEFAULT_MODEL               # (호환용) 기본 모델
LOCATION = "global"
PROMPT_VERSION = 6                  # v6: 순별 대신 일별 연속 구간(정확한 날짜·일수), 선택한 지점만 출력

DRY_SPELL_DAYS = 7                  # 연속 무강수(일강수량 0.1mm 미만) 기준(일)
EFFECTIVE_RAIN_MM = 5.0             # 유효 강우: 일강수량 5mm 이상
NO_EFFECTIVE_RAIN_DAYS = 10         # 유효 강우 없는 연속 기간 → 가뭄 우려 기준(일)
WET_DAY_MM = 1.0                    # 강우일(연속 강우 판단): 일강수량 1mm 이상
WET_SPELL_DAYS = 3                  # 연속 강우 → 과습 우려 기준(일)
MUCH_RAIN_MM = 30.0                 # 많은 비: 일강수량
HEAVY_RAIN_MM = 80.0                # 호우: 일강수량(지점)
HEAT_DAY_C = 33.0                   # 폭염일: 일 최고기온
TROPICAL_C = 25.0                   # 열대야: 일 최저기온
FROST_C = 2.0                       # 서리 위험: 일 최저기온
LOW_SUN_HR = 2.0                    # 일조 부족: 일 일조시간
LOW_SUN_DAYS = 3                    # 일조 부족 연속 기준(일)
SUNNY_HR = 8.0                      # 강한 일사(맑음): 일 일조시간
SUNNY_DAYS = 5                      # 강한 일사 연속 기준(일)


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


UNITS = {"평균 기온": "℃", "최고 기온": "℃", "최저 기온": "℃", "누적 강수량": "mm", "일조 시간": "hr", "강우일수": "일", "열대야 일수": "일"}


def valid_result(res: Any, query: Dict[str, Any]) -> bool:
    """화면이 보낸 결과가 이 조회 조건의 것이고 지점별 비교 자료가 들어 있는지 간단히 확인"""
    try:
        p = res["params"]
        return (isinstance(res.get("daily"), dict) and isinstance(res.get("stationBrief"), dict)
                and bool(res["stationBrief"].get("stations"))
                and p["start"] == query["start"].isoformat() and p["end"] == query["end"].isoformat())
    except (KeyError, TypeError, AttributeError):
        return False


RAIN_LOW, RAIN_HIGH, RAIN_MIN_MM = 0.5, 2.0, 5.0


def rain_verdict(cur: Optional[float], five: Optional[float], normal: Optional[float]) -> str:
    """강수량 판정: 최근 5년·평년 두 기준 모두의 50% 미만이면 '부족', 모두의 200% 초과면 '과다', 그 밖은 '적정'
    (기준값이 둘 다 5mm 미만인 건조한 시기는 '적정')"""
    refs = [x for x in (five, normal) if x is not None]
    if cur is None or not refs:
        return "판정불가"
    if max(refs) < RAIN_MIN_MM:
        return "적정"
    if all(cur < RAIN_LOW * r for r in refs):
        return "부족"
    if all(r > 0 and cur > RAIN_HIGH * r for r in refs):
        return "과다"
    return "적정"


def _rain(v: Any) -> str:
    """[해당, 5년, 평년] → '12.3mm(5년 20.1mm 61%, 평년 30.0mm 41%) 판정:부족'"""
    vals = list(v or []) + [None, None, None]
    cur, five, normal = vals[:3]
    if cur is None:
        return "-"
    pct = lambda r: f" {cur / r * 100:.0f}%" if r else ""
    parts = []
    if five is not None:
        parts.append(f"5년 {five}mm{pct(five)}")
    if normal is not None:
        parts.append(f"평년 {normal}mm{pct(normal)}")
    return f"{cur}mm({', '.join(parts)}) 판정:{rain_verdict(cur, five, normal)}"


def _pair(v: Any, unit: str) -> str:
    """[해당, 5년] → '12.3mm(5년 20.1mm, 61%)'"""
    cur, avg = (v or [None, None])[:2]
    if cur is None:
        return "-"
    if avg is None:
        return f"{cur}{unit}"
    if unit in ("mm", "hr", "일") and avg:
        return f"{cur}{unit}(5년 {avg}{unit}, {cur / avg * 100:.0f}%)"
    d = cur - avg
    return f"{cur}{unit}(5년 {avg}{unit}, {d:+.1f})"


def _sum(vals: List[Optional[float]], i0: int, i1: int) -> Optional[float]:
    xs = [v for v in vals[i0:i1 + 1] if v is not None]
    return sum(xs) if xs else None


def _mean(vals: List[Optional[float]], i0: int, i1: int) -> Optional[float]:
    xs = [v for v in vals[i0:i1 + 1] if v is not None]
    return sum(xs) / len(xs) if xs else None


def _spans(dates: List[str], flags: List[bool], min_len: int) -> List[Tuple[int, int]]:
    """연속 구간의 (시작 인덱스, 끝 인덱스) — min_len일 이상만"""
    out, start = [], None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    return out


def _span_txt(d: List[str], i0: int, i1: int) -> str:
    return f"{_md(d[i0])}~{_md(d[i1])}({i1 - i0 + 1}일간)" if i1 > i0 else f"{_md(d[i0])}(1일)"


def _fmt_num(v: Optional[float], unit: str, nd: int = 1) -> str:
    return "-" if v is None else f"{v:.{nd}f}{unit}"


def station_events(daily: Dict[str, Any], station: str) -> List[str]:
    """한 지점의 일별 자료에서 사건 구간을 찾음 — 모든 항목에 정확한 날짜·일수와 그 구간의 합계·평균"""
    ev: List[str] = []
    d, _, per = _daily_series(daily, "누적 강수량")
    rain = per.get(station) or []
    _, _, sper = _daily_series(daily, "일조 시간")
    sun = sper.get(station) or []
    _, _, xper = _daily_series(daily, "최고 기온")
    tmax = xper.get(station) or []

    def extra(i0: int, i1: int) -> str:
        bits = []
        if rain:
            bits.append(f"강수 합계 {_fmt_num(_sum(rain, i0, i1), 'mm')}")
        if sun:
            bits.append(f"일조 합계 {_fmt_num(_sum(sun, i0, i1), '시간')}")
        if tmax:
            bits.append(f"평균 최고기온 {_fmt_num(_mean(tmax, i0, i1), '℃')}")
        return f" [{', '.join(bits)}]" if bits else ""

    if d and rain:
        dry = _spans(d, [r is not None and r < 0.1 for r in rain], DRY_SPELL_DAYS)
        for i0, i1 in dry:
            ev.append(f"[건조] 연속 무강수(일강수량 0.1mm 미만) {_span_txt(d, i0, i1)}{extra(i0, i1)}")
        noeff = _spans(d, [r is not None and r < EFFECTIVE_RAIN_MM for r in rain], NO_EFFECTIVE_RAIN_DAYS)
        for i0, i1 in noeff:
            ev.append(f"[가뭄 우려] 유효 강우(일 {EFFECTIVE_RAIN_MM:.0f}mm 이상) 없음 {_span_txt(d, i0, i1)}{extra(i0, i1)}")
        wet = _spans(d, [r is not None and r >= WET_DAY_MM for r in rain], WET_SPELL_DAYS)
        for i0, i1 in wet:
            ev.append(f"[과습 우려] 연속 강우(일강수량 {WET_DAY_MM:.0f}mm 이상) {_span_txt(d, i0, i1)}{extra(i0, i1)}")
        much = [f"{_md(x)} {r:.1f}mm" for x, r in zip(d, rain) if r is not None and MUCH_RAIN_MM <= r < HEAVY_RAIN_MM]
        if much:
            ev.append(f"[많은 비] 일강수량 {MUCH_RAIN_MM:.0f}mm 이상: {', '.join(much)}")
        heavy = [f"{_md(x)} {r:.1f}mm" for x, r in zip(d, rain) if r is not None and r >= HEAVY_RAIN_MM]
        if heavy:
            ev.append(f"[호우] 일강수량 {HEAVY_RAIN_MM:.0f}mm 이상: {', '.join(heavy)}")
    if d and sun:
        for i0, i1 in _spans(d, [x is not None and x < LOW_SUN_HR for x in sun], LOW_SUN_DAYS):
            ev.append(f"[일조 부족] 일조 {LOW_SUN_HR:.0f}시간 미만 연속 {_span_txt(d, i0, i1)}{extra(i0, i1)}")
        for i0, i1 in _spans(d, [x is not None and x >= SUNNY_HR for x in sun], SUNNY_DAYS):
            ev.append(f"[강한 일사] 일조 {SUNNY_HR:.0f}시간 이상 연속 {_span_txt(d, i0, i1)}{extra(i0, i1)}")
    if d and tmax:
        hot = [x is not None and x >= HEAT_DAY_C for x in tmax]
        if sum(hot):
            spans = _spans(d, hot, 1)
            ev.append(f"[폭염] 최고기온 {HEAT_DAY_C:.0f}℃ 이상 총 {sum(hot)}일: " + ", ".join(_span_txt(d, a, b) for a, b in spans))
        vals = [(x, i) for i, x in enumerate(tmax) if x is not None]
        if vals:
            mx = max(vals)
            ev.append(f"[극값] 최고기온 최고 {_md(d[mx[1]])} {mx[0]:.1f}℃")
    _, _, nper = _daily_series(daily, "최저 기온")
    tmin = nper.get(station) or []
    if d and tmin:
        trop = [x is not None and x >= TROPICAL_C for x in tmin]
        if sum(trop):
            spans = _spans(d, trop, 1)
            ev.append(f"[열대야] 최저기온 {TROPICAL_C:.0f}℃ 이상 총 {sum(trop)}일: " + ", ".join(_span_txt(d, a, b) for a, b in spans))
        cold = [x is not None and x <= FROST_C for x in tmin]
        if sum(cold):
            low = min((x, i) for i, x in enumerate(tmin) if x is not None)
            spans = _spans(d, cold, 1)
            ev.append(f"[저온·서리] 최저기온 {FROST_C:.0f}℃ 이하 총 {sum(cold)}일: " + ", ".join(_span_txt(d, a, b) for a, b in spans)
                      + f" (최저 {_md(d[low[1]])} {low[0]:.1f}℃)")
    return ev


def _fmt_metric(m: str, v: Any) -> str:
    return _rain(v) if m == "누적 강수량" else _pair(v, UNITS.get(m, ""))


def build_digest(result: Dict[str, Any]) -> str:
    """AI에게 줄 분석 자료(텍스트): 고른 지점마다 기간 전체 vs 그 지점의 최근 5년 평균 + 일별 연속 구간"""
    s = result.get("summary") or {}
    p = result.get("params") or {}
    sb = result.get("stationBrief") or {}
    metrics = sb.get("metrics") or []
    parts = [
        f"[조회 조건] 기간 {p.get('start')} ~ {p.get('end')} ({s.get('periodDays')}일)",
        f"[비교 기준] 해당 연도 {s.get('currentYear')} vs 같은 지점의 최근 5년 평균({sb.get('years') or s.get('recentRange')})",
        f"[집계 방법] {s.get('aggDesc', '')}",
        "[표기] 값(5년 평균, 차이 또는 비율%) — 기온은 차이(℃), 일조·일수는 최근 5년 대비 비율(%)",
        "[강수량] 값(최근 5년·평년 대비 비율) 판정:적정/부족/과다 — 두 기준 모두의 50% 미만=부족, 모두의 200% 초과=과다, 그 밖=적정",
    ]
    parts.append(f"[분석 지점] {', '.join(digest_stations(result))} (이 지점들만 분석)")
    parts.append("[일별 구간] 각 지점의 일별 관측값으로 찾은 연속 구간 — 날짜·일수·구간 합계가 정확한 값")
    for st in sb.get("stations") or []:
        name = st.get("name")
        parts += ["", f"## 지점: {name}",
                  "기간 전체(최근 5년 대비): " + " / ".join(f"{m} {_fmt_metric(m, st['period'].get(m))}" for m in metrics)]
        ev = station_events(result.get("daily") or {}, name)
        parts += ["일별 구간:"] + ([f"- {e}" for e in ev] if ev else ["- 기준에 해당하는 구간 없음"])
    return "\n".join(parts)


def digest_stations(result: Dict[str, Any]) -> List[str]:
    """조회에서 고른 지점(분석 자료에 들어간 지점)만"""
    return [st.get("name") for st in ((result or {}).get("stationBrief") or {}).get("stations") or [] if st.get("name")]


SYSTEM_BASE = """당신은 제주특별자치도 농업기술원의 농업기상 전문 지도사입니다.
제주는 지점(제주·서귀포·성산·고산)마다 기상이 크게 다르므로, 지점 평균으로 판단하지 않습니다.
주어진 '분석 자료'의 수치만 근거로, 지점별로 농작물에 영향을 줄 만한 '특별한 기상 요인'을 찾고 종합합니다.

가장 중요한 원칙
1. 상순·중순·하순(순별)이나 월 단위로 나누어 쓰지 않습니다. 모든 항목은 '일별 구간'에 나온 정확한 날짜와 일수로 시작합니다
   (예: **7/21~7/31(11일간)** 연속 무강수 / **8/3~8/6(4일간)** 연속 강우). 날짜·일수가 없는 항목은 쓰지 않습니다.
2. 강수·일조는 며칠간 과다·부족했는지가 핵심입니다. 일별 구간의 [건조]·[가뭄 우려]·[과습 우려]·[많은 비]·[호우]·[일조 부족]·[강한 일사]를
   그 구간의 강수 합계·일조 합계와 함께 씁니다. 같은 기간에 무강수와 강한 일사가 겹치면 '고온 건조(가뭄 스트레스)'로, 연속 강우와 일조 부족이
   겹치면 '과습·일조 부족'으로 묶어서 씁니다.
3. [건조](무강수 7일 이상)는 그 지점의 기간 전체 강수 판정이 '적정'이고 [가뭄 우려]가 없으면 짧게만 언급하거나 생략합니다.
   [가뭄 우려](유효 강우 없는 10일 이상)와 [호우]는 반드시 씁니다.
4. 기간 전체 수치는 같은 지점의 최근 5년 평균과만 비교합니다(평년·전년 비교는 쓰지 않음, 강수량만 자료의 판정 문구를 그대로 따름).
   기온은 [폭염]·[열대야]·[저온·서리] 구간과 극값을 날짜로 씁니다.
5. 자료에 없는 수치·날짜를 만들지 않습니다. 수치는 자료 값을 그대로 씁니다.
6. 농작물 영향은 그 시기 제주에서 실제 재배·관리하는 작목(감귤·만감류, 월동채소: 양배추·브로콜리·무·당근·양파·마늘, 감자, 콩·메밀 등 밭작물, 시설작물)만 짧게 짚고, 해당 지역 주산지를 고려합니다
   (예: 성산·구좌 동부 당근·무, 고산·한경·대정 서부 양배추·브로콜리·마늘, 서귀포 남부 감귤·만감류). 불확실하면 "~우려"로 씁니다.
7. 분석 자료에 없는 지점은 제목도 쓰지 않고 언급하지 않습니다.
8. 보고서체(~임, ~함). 군더더기 설명·서론·맺음말 없이. 전체 {length}자.
"""


def build_system(stations: Optional[List[str]] = None) -> str:
    """고른 지점에 맞춘 지시문 — 고르지 않은 지점은 출력 형식에서 아예 빠짐"""
    names = [x for x in (stations or []) if x] or ["제주", "서귀포", "성산", "고산"]
    many = len(names) > 1
    length = "700~1000" if len(names) >= 3 else ("500~800" if many else "400~600")
    out = [SYSTEM_BASE.format(length=length), "출력 형식(마크다운, 이 형식 그대로)", "## 종합"]
    if many:
        out += ["- 공통: (여러 지점에서 나타난 요인 — 정확한 날짜·일수와 해당 지점)",
                "- 지점 차이: (특정 지점에만 두드러진 요인 — 정확한 날짜·일수와 지점)"]
    else:
        out += ["- (가장 중요한 요인 1~2개 — 정확한 날짜·일수)"]
    out.append("## 지점별 주의 기상 요인")
    for i, n in enumerate(names):
        out.append(f"### {n}")
        if i == 0:
            out.append("- **월/일~월/일(N일간)** 요인 — 구간 수치(강수·일조 합계 등) → 작목 영향")
    out += [f"(위 {len(names)}개 지점만, 지점마다 날짜 순서로 최대 4개. 해당 구간이 없으면 \"- 두드러진 요인 없음\")",
            "## 관리 포인트", "- 지역·작목별로 할 일 1~3개 (짧게)"]
    return "\n".join(out)


SYSTEM = build_system()             # (호환용) 4개 지점 기본 지시문


def _project_id() -> str:
    for k in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        if os.environ.get(k):
            return os.environ[k]
    return "jeju-agri-weather"


def model_label(model: str) -> str:
    m = MODELS.get(model) or {}
    where = "Vertex AI" if m.get("provider") == "vertex" else "AI Studio"
    return f"{m.get('label', model)} ({where})"


def _client(model: str, api_key: Optional[str], timeout_ms: int) -> Any:
    """모델 제공 방식에 맞는 연결 — 모델을 바꿔도 이 부분만 다름"""
    from google import genai
    from google.genai import types
    provider = (MODELS.get(model) or {}).get("provider")
    if provider == "vertex":
        return genai.Client(enterprise=True, project=_project_id(), location=LOCATION,
                            http_options=types.HttpOptions(timeout=timeout_ms))
    if provider == "aistudio":
        if not api_key:
            raise AiError("젬마용 AI Studio API 키가 등록되지 않았습니다. 관리자 화면의 'AI 모델'에서 키를 저장해 주세요.")
        return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
    raise AiError(f"알 수 없는 AI 모델입니다: {model}")


def _thinking_level(model: str) -> str:
    # 제미나이: 추론을 짧게(LOW) · 젬마 4: 추론 끄기(minimal) → 답변에 쓸 분량 확보
    return "LOW" if (MODELS.get(model) or {}).get("provider") == "vertex" else "minimal"


def _friendly_error(model: str, exc: Exception) -> AiError:
    msg = str(exc)
    vertex = (MODELS.get(model) or {}).get("provider") == "vertex"
    if "API_KEY_INVALID" in msg or "API key not valid" in msg:
        return AiError("AI Studio API 키가 올바르지 않습니다. 관리자 화면에서 키를 다시 저장해 주세요.")
    if "PERMISSION_DENIED" in msg or "403" in msg:
        return AiError("Vertex AI 사용 권한이 없습니다. (프로젝트에서 Vertex AI API 사용 설정 필요)" if vertex
                       else "AI Studio 키로 이 모델을 쓸 권한이 없습니다. 키를 확인해 주세요.")
    if "timed out" in msg.lower() or "timeout" in msg.lower() or "DEADLINE" in msg:
        return AiError("AI 응답이 늦어지고 있습니다. 잠시 후 다시 시도해 주세요.")
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        return AiError("AI 사용량이 많아 잠시 후 다시 시도해 주세요." if vertex
                       else "젬마 무료 사용 한도에 걸렸습니다. 잠시 후 다시 시도하거나 관리자 화면에서 모델을 바꿔 주세요.")
    if "NOT_FOUND" in msg or "404" in msg:
        return AiError(f"AI 모델({model})을 찾을 수 없습니다. 모델 이름을 확인해 주세요.")
    return AiError(f"AI 분석 요청 실패 ({exc.__class__.__name__})")


def generate(digest: str, model: str = DEFAULT_MODEL, api_key: Optional[str] = None,
             stations: Optional[List[str]] = None) -> str:
    """선택된 모델로 해설 작성 — 분석 자료(digest)와 지시문(SYSTEM)은 모델과 무관하게 동일"""
    try:
        from google.genai import types
    except ImportError as exc:      # pragma: no cover
        raise AiError("AI 분석 모듈이 설치되지 않았습니다.") from exc
    vertex = (MODELS.get(model) or {}).get("provider") == "vertex"
    try:
        # 제미나이는 90초, 젬마(공개 모델·무료 키라 대기가 길 수 있음)는 120초 안에 답이 없으면 실패 처리
        client = _client(model, api_key, 90_000 if vertex else 120_000)
        contents = f"다음 분석 자료로 기상 분석 보고를 작성해 주세요.\n\n{digest}"
        system = build_system(stations)

        def ask(max_tokens: int, thinking: bool = True) -> Any:
            cfg: Dict[str, Any] = {"system_instruction": system, "temperature": 0.2, "max_output_tokens": max_tokens}
            if thinking:   # 지원하지 않는 모델이면 빼고 다시 요청
                cfg["thinking_config"] = types.ThinkingConfig(thinking_level=_thinking_level(model))
            try:
                return client.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(**cfg))
            except Exception as e:
                if thinking and "thinking" in str(e).lower():
                    return ask(max_tokens, thinking=False)
                raise

        resp = ask(8192)
        if _cut_off(resp):              # 최대 길이에 걸려 잘렸으면 한도를 더 늘려 한 번 더
            resp = ask(16384)
    except AiError:
        raise
    except Exception as exc:
        raise _friendly_error(model, exc) from exc
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise AiError("AI가 분석 내용을 만들지 못했습니다. 다시 시도해 주세요.")
    if _cut_off(resp):
        raise AiError("AI 분석이 끝까지 작성되지 않았습니다. '다시 분석'을 눌러 주세요.")
    return text


def _cut_off(resp: Any) -> bool:
    """답변이 최대 길이에 걸려 중간에 끊겼는지"""
    try:
        reason = resp.candidates[0].finish_reason
    except (AttributeError, IndexError, TypeError):
        return False
    return "MAX_TOKENS" in str(reason)


def ping(model: str = DEFAULT_MODEL, api_key: Optional[str] = None) -> str:
    """관리자 연결 점검용: 아주 짧은 요청으로 모델 응답 확인"""
    from google.genai import types
    vertex = (MODELS.get(model) or {}).get("provider") == "vertex"
    client = _client(model, api_key, 30_000 if vertex else 90_000)
    cfg: Dict[str, Any] = {"max_output_tokens": 256, "temperature": 0}
    if not vertex:   # 젬마 4: 추론 끄기 → 짧은 답을 바로
        cfg["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
    resp = client.models.generate_content(model=model, contents="'정상'이라는 한 단어로만 답하세요.",
                                          config=types.GenerateContentConfig(**cfg))
    return (getattr(resp, "text", None) or "").strip()[:40]

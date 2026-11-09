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
  - gemma-4-31b-it   : Google AI Studio 무료 API 키 (젬마, 행정안전부 AI 공통기반 지원 모델 계열)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# 모델 목록 — 새 모델은 여기에 한 줄 추가 (provider: vertex | aistudio)
MODELS: Dict[str, Dict[str, str]] = {
    "gemini-3.8-flash": {"label": "Gemini 3.8 Flash", "provider": "vertex", "note": "Vertex AI · 서버 계정 인증"},
    "gemma-4-31b-it": {"label": "Gemma 4 31B", "provider": "aistudio", "note": "Google AI Studio 무료 키 · 공개 모델"},
}
DEFAULT_MODEL = "gemini-3.8-flash"
MODEL = DEFAULT_MODEL               # (호환용) 기본 모델
LOCATION = "global"
PROMPT_VERSION = 5                  # v5: 기온 등은 최근 5년, 강수량은 최근 5년+평년 적정 범위 판정

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


def station_events(daily: Dict[str, Any], station: str) -> List[str]:
    """한 지점의 일별 자료에서 사건 구간 찾기 (해당 연도)"""
    ev: List[str] = []
    d, _, per = _daily_series(daily, "누적 강수량")
    rain = per.get(station) or []
    if d and rain:
        dry = _spells(d, [r is not None and r < 0.1 for r in rain], DRY_SPELL_DAYS)
        ev.append(f"연속 무강수({DRY_SPELL_DAYS}일 이상): {_fmt_spells(dry)}")
        heavy = [f"{_md(x)} {r:.0f}mm" for x, r in zip(d, rain) if r is not None and r >= HEAVY_RAIN_MM]
        if heavy:
            ev.append(f"호우({HEAVY_RAIN_MM:.0f}mm 이상): {', '.join(heavy)}")
    d, _, per = _daily_series(daily, "최고 기온")
    tmax = per.get(station) or []
    if d and tmax:
        hot = [x is not None and x >= HEAT_DAY_C for x in tmax]
        if sum(hot):
            ev.append(f"폭염일(최고 {HEAT_DAY_C:.0f}℃ 이상) {sum(hot)}일, 연속 {_fmt_spells(_spells(d, hot, 3))}")
        vals = [(x, i) for i, x in enumerate(tmax) if x is not None]
        if vals:
            mx = max(vals)
            ev.append(f"최고기온 극값 {_md(d[mx[1]])} {mx[0]:.1f}℃")
    d, _, per = _daily_series(daily, "최저 기온")
    tmin = per.get(station) or []
    if d and tmin:
        trop = [x is not None and x >= TROPICAL_C for x in tmin]
        if sum(trop):
            ev.append(f"열대야(최저 {TROPICAL_C:.0f}℃ 이상) {sum(trop)}일, 연속 {_fmt_spells(_spells(d, trop, 3))}")
        frost = [(x, i) for i, x in enumerate(tmin) if x is not None and x <= FROST_C]
        if frost:
            low = min(frost)
            ev.append(f"저온·서리 위험(최저 {FROST_C:.0f}℃ 이하) {len(frost)}일, 최저 {_md(d[low[1]])} {low[0]:.1f}℃")
    d, _, per = _daily_series(daily, "일조 시간")
    sun = per.get(station) or []
    if d and sun:
        dim = _spells(d, [x is not None and x < LOW_SUN_HR for x in sun], LOW_SUN_DAYS)
        if dim:
            ev.append(f"일조 부족 연속({LOW_SUN_DAYS}일 이상, {LOW_SUN_HR:.0f}시간 미만): {_fmt_spells(dim)}")
    return ev


def _fmt_metric(m: str, v: Any) -> str:
    return _rain(v) if m == "누적 강수량" else _pair(v, UNITS.get(m, ""))


def build_digest(result: Dict[str, Any]) -> str:
    """제미나이에게 줄 분석 자료(텍스트): 지점마다 해당 연도 vs 그 지점의 최근 5년 평균 + 사건 구간"""
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
    for st in sb.get("stations") or []:
        name = st.get("name")
        parts += ["", f"## 지점: {name}",
                  "기간 전체: " + " / ".join(f"{m} {_fmt_metric(m, st['period'].get(m))}" for m in metrics),
                  "순별:"]
        for dk in st.get("dekads") or []:
            tag = " (일부)" if dk.get("partial") else ""
            parts.append(f"- {dk['label']}({dk['range']}){tag}: " +
                         " / ".join(f"{m} {_fmt_metric(m, dk['values'].get(m))}" for m in metrics))
        ev = station_events(result.get("daily") or {}, name)
        parts.append("일별 사건: " + ("; ".join(ev) if ev else "없음"))
    return "\n".join(parts)


SYSTEM = """당신은 제주특별자치도 농업기술원의 농업기상 전문 지도사입니다.
제주는 지점(제주·서귀포·성산·고산)마다 기상이 크게 다르므로, 4개 지점 평균으로 판단하지 않습니다.
주어진 '분석 자료'의 수치만 근거로, 먼저 지점별로 농작물에 영향을 줄 만한 '특별한 기상 요인'을 찾고, 그 결과를 종합합니다.

가장 중요한 원칙
1. 모든 항목은 그 기상 요인이 나타난 '기간'(예: 7/21~7/31, 11일간 / 8월 중순)으로 시작합니다. 기간이 없는 항목은 쓰지 않습니다.
2. 기온·일조·열대야·폭염 등은 '같은 지점의 최근 5년 평균'과만 비교합니다(온난화로 평년 기준은 맞지 않음). 전년은 언급하지 않습니다.
3. 강수량은 자료의 '판정'을 그대로 따릅니다. '적정'이면 최근 5년이나 평년보다 적거나 많아도 문제로 쓰지 않습니다.
   '부족'(가뭄·건조) 또는 '과다'(과습)일 때만 최근 5년·평년 대비 비율을 함께 적어 짚습니다.
   연속 무강수는 그 기간이 들어 있는 순의 강수량 판정이 '부족'일 때만 가뭄으로 씁니다. 일강수량 80mm 이상 호우는 판정과 관계없이 씁니다.
   그 밖에 뚜렷한 요인만 고릅니다.
   - 순별 기온이 최근 5년보다 1.5℃ 이상 높거나 낮음, 폭염일·열대야가 여러 날 이어짐(최근 5년보다 뚜렷이 많음)
   - 최저기온 2℃ 이하(서리·저온), 일조 부족 5일 이상 연속 또는 순별 일조가 최근 5년의 70% 이하
4. 종합은 '여러 지점에서 공통으로 나타난 요인'과 '특정 지점에만 나타난 요인'을 구분해 씁니다. 평균값으로 뭉뚱그리지 않습니다.
5. 자료에 없는 수치·날짜를 만들지 않습니다. 수치는 자료 값을 그대로 씁니다.
6. 농작물 영향은 그 시기 제주에서 실제 재배·관리하는 작목(감귤·만감류, 월동채소: 양배추·브로콜리·무·당근·양파·마늘, 감자, 콩·메밀 등 밭작물, 시설작물)만 짧게 짚고, 해당 지역 주산지를 고려합니다
   (예: 성산·구좌 동부 당근·무, 고산·한경·대정 서부 양배추·브로콜리·마늘, 서귀포 남부 감귤·만감류). 불확실하면 "~우려"로 씁니다.
7. 보고서체(~임, ~함). 군더더기 설명·서론·맺음말 없이. 전체 700~1000자.

출력 형식(마크다운, 이 형식 그대로)
## 종합
- 공통: (여러 지점에서 나타난 요인, 기간과 해당 지점)
- 지점 차이: (특정 지점에만 두드러진 요인, 기간과 지점)
## 지점별 주의 기상 요인
### 제주
- **기간(일수)** 요인 — 수치(최근 5년 대비) → 작목 영향
### 서귀포
### 성산
### 고산
(지점마다 시간 순서로 최대 3개. 해당 없으면 "- 두드러진 요인 없음")
## 관리 포인트
- 지역·작목별로 할 일 1~3개 (짧게)
"""


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


def generate(digest: str, model: str = DEFAULT_MODEL, api_key: Optional[str] = None) -> str:
    """선택된 모델로 해설 작성 — 분석 자료(digest)와 지시문(SYSTEM)은 모델과 무관하게 동일"""
    try:
        from google.genai import types
    except ImportError as exc:      # pragma: no cover
        raise AiError("AI 분석 모듈이 설치되지 않았습니다.") from exc
    vertex = (MODELS.get(model) or {}).get("provider") == "vertex"
    try:
        # 제미나이는 90초, 젬마(큰 공개 모델)는 150초 안에 답이 없으면 실패 처리
        client = _client(model, api_key, 90_000 if vertex else 150_000)
        contents = f"다음 분석 자료로 기상 분석 보고를 작성해 주세요.\n\n{digest}"

        def ask(max_tokens: int, thinking: bool = True) -> Any:
            cfg: Dict[str, Any] = {"system_instruction": SYSTEM, "temperature": 0.2, "max_output_tokens": max_tokens}
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
    client = _client(model, api_key, 45_000)
    resp = client.models.generate_content(model=model, contents="'정상'이라는 한 단어로만 답하세요.",
                                          config=types.GenerateContentConfig(max_output_tokens=256, temperature=0))
    return (getattr(resp, "text", None) or "").strip()[:40]

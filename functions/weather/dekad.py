# -*- coding: utf-8 -*-
"""
dekad.py  (백엔드)
순별(상순·중순·하순) 집계

  상순: 매월 1~10일 / 중순: 11~20일 / 하순: 21일~말일(28·29·30·31일)

- 조회 기간이 순 중간에서 시작·끝나면 그 순은 '부분 순'으로 표시하고, 비교 연도도 같은 날짜 범위로만 집계합니다.
- 집계 방식은 기존 기간 집계(processing.aggregate_period)와 동일합니다.
  (기온 = 평균, 강수량·일조·강우일수·열대야 일수 = 합계, 제주 전체 = 4개 지점 평균)
- 편차·비율은 기존 비교표 함수(build_comparison_table)를 순마다 그대로 사용합니다.
- 추가 기상청 호출 없음: 이미 받아 둔(캐시된) 연도별 기간 자료를 날짜로 잘라 씁니다.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .kma_api import METRICS
from .processing import (
    aggregate_period,
    build_comparison_table,
    format_comparison_for_display,
    format_report,
    jeju_daily_mean,
    shift_period,
)

DEKAD_NAMES = {1: "상순", 2: "중순", 3: "하순"}


def _dekad_bounds(year: int, month: int, idx: int) -> tuple[dt.date, dt.date]:
    last = calendar.monthrange(year, month)[1]
    first_day, last_day = {1: (1, 10), 2: (11, 20), 3: (21, last)}[idx]
    return dt.date(year, month, first_day), dt.date(year, month, last_day)


def dekad_periods(start: dt.date, end: dt.date) -> List[Dict[str, Any]]:
    """조회 기간과 겹치는 순 목록 (기간 밖 날짜는 잘라냄)"""
    out: List[Dict[str, Any]] = []
    if start is None or end is None or start > end:
        return out
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        for idx in (1, 2, 3):
            full_s, full_e = _dekad_bounds(y, m, idx)
            s, e = max(full_s, start), min(full_e, end)
            if s > e:
                continue
            partial = (s, e) != (full_s, full_e)
            label = f"{y}년 {m}월 {DEKAD_NAMES[idx]}"
            out.append({
                "key": f"{y}-{m:02d}-{idx}", "label": label, "start": s, "end": e,
                "days": (e - s).days + 1, "fullDays": (full_e - full_s).days + 1, "partial": partial,
                "range": f"{m}/{s.day}~{m}/{e.day}",
            })
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def month_periods(start: dt.date, end: dt.date) -> List[Dict[str, Any]]:
    """조회 기간과 겹치는 월 목록 (v7.6, 기간 밖 날짜는 잘라내고 '일부'로 표시)"""
    out: List[Dict[str, Any]] = []
    if start is None or end is None or start > end:
        return out
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        full_s, full_e = dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
        s, e = max(full_s, start), min(full_e, end)
        out.append({
            "key": f"{y}-{m:02d}", "label": f"{y}년 {m}월", "start": s, "end": e,
            "days": (e - s).days + 1, "fullDays": (full_e - full_s).days + 1, "partial": (s, e) != (full_s, full_e),
            "range": f"{m}/{s.day}~{m}/{e.day}",
        })
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def build_station_month_tables(station_df: pd.DataFrame, metric_names: List[str], extreme: bool,
                               periods: List[Dict[str, Any]], station_names: Dict[int, str]) -> Dict[str, Dict[str, Any]]:
    """지표별 '월 × 지점' 값 표 (+ 선택 지점 평균) — 일별 탭의 월 단위판"""
    out: Dict[str, Dict[str, Any]] = {}
    if station_df is None or station_df.empty:
        return out
    stations = [sid for sid in station_names if sid in set(station_df["stn_id"])]
    daily_mean = jeju_daily_mean(station_df)
    for n in metric_names:
        rows = []
        for p in periods:
            row: Dict[str, Any] = {"월": p["label"], "기간": p["range"], "일수": f"{p['days']}일" + (" (일부)" if p["partial"] else "")}
            for sid in stations:
                sub = _slice(station_df[station_df["stn_id"] == sid], p["start"], p["end"])
                row[station_names[sid]] = aggregate_period(sub, [n], extreme).get(n, float("nan"))
            if len(stations) > 1:
                row["선택 지점 평균"] = aggregate_period(_slice(daily_mean, p["start"], p["end"]), [n], extreme).get(n, float("nan"))
            rows.append(row)
        df = format_report(pd.DataFrame(rows), decimals=1)
        out[n] = {"columns": [str(c) for c in df.columns], "rows": [[str(v) for v in r] for r in df.itertuples(index=False, name=None)]}
    return out


def _slice(daily: pd.DataFrame, s: dt.date, e: dt.date) -> pd.DataFrame:
    if daily is None or daily.empty or "date" not in daily.columns:
        return pd.DataFrame(columns=daily.columns if daily is not None else [])
    return daily[(daily["date"] >= s) & (daily["date"] <= e)]


def _year_offset_dates(s: dt.date, e: dt.date, base_year: int, target_year: int) -> tuple[dt.date, dt.date]:
    """조회 시작 연도를 기준으로 같은 연수만큼 옮긴 날짜 (연도를 넘는 기간·2월 29일 처리 포함)"""
    return shift_period(s, e, s.year + (target_year - base_year))


def build_dekad_tables(
    start: dt.date, end: dt.date, metric_names: List[str], extreme: bool,
    current_daily: pd.DataFrame,
    fetch_period: Callable[[dt.date, dt.date], pd.DataFrame],
    recent_years: List[int],
    normal_mode: str,
    normal_daily: Optional[pd.DataFrame],
    normal_years: List[int],
    normal_from_years: List[str],
    periods: Optional[List[Dict[str, Any]]] = None,
    col: str = "순",
    note: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    순별 비교표 생성. periods·col·note 를 주면 같은 방식으로 월별 등 다른 구간에도 사용(v7.6 월별).
      normal_daily      : 평년값 API 일별 평년(제주 전체) — 해당 날짜로 바로 잘라 씀
      normal_from_years : 평년을 30년 실측에서 계산해야 하는 지표 (compute 모드면 전체 지표)
    """
    periods = periods if periods is not None else dekad_periods(start, end)
    if not periods or current_daily is None or current_daily.empty:
        return None

    # 연도별 제주 전체 일평균 (이미 받아 둔 기간 자료를 재사용 → 추가 기상청 호출 없음)
    def daily_for_year(y: int) -> pd.DataFrame:
        s_y, e_y = shift_period(start, end, y)
        try:
            return jeju_daily_mean(fetch_period(s_y, e_y))
        except Exception:
            return pd.DataFrame()

    recent_daily = {y: daily_for_year(y) for y in recent_years}
    nyears_daily: Dict[int, pd.DataFrame] = {}
    if normal_from_years and normal_mode != "skip":
        nyears_daily = {y: daily_for_year(y) for y in normal_years}

    def mean_over(years_daily: Dict[int, pd.DataFrame], s: dt.date, e: dt.date, names: List[str]) -> Dict[str, float]:
        vals: Dict[str, List[float]] = {n: [] for n in names}
        for y, d in years_daily.items():
            s_y, e_y = _year_offset_dates(s, e, start.year, y)
            agg = aggregate_period(_slice(d, s_y, e_y), names, extreme)
            for n in names:
                v = agg.get(n, float("nan"))
                if not pd.isna(v):
                    vals[n].append(float(v))
        return {n: (sum(v) / len(v) if v else float("nan")) for n, v in vals.items()}

    prev_year = start.year - 1
    by_metric_rows: Dict[str, List[pd.DataFrame]] = {n: [] for n in metric_names}
    layout_cols: Dict[str, List[str]] = {}   # 표 구성별 열 순서 (기존 비교표와 같은 순서 유지)
    overview_rows: List[Dict[str, Any]] = []

    for p in periods:
        s, e = p["start"], p["end"]
        current = aggregate_period(_slice(current_daily, s, e), metric_names, extreme)
        prev = {}
        if prev_year in recent_daily:
            ps, pe = _year_offset_dates(s, e, start.year, prev_year)
            prev = aggregate_period(_slice(recent_daily[prev_year], ps, pe), metric_names, extreme)
        recent = mean_over(recent_daily, s, e, metric_names)
        recent3 = mean_over({y: recent_daily[y] for y in recent_years[:3] if y in recent_daily}, s, e, metric_names)

        normal: Dict[str, float] = {n: float("nan") for n in metric_names}
        if normal_mode != "skip":
            api_names = [n for n in metric_names if n not in normal_from_years]
            if api_names and normal_daily is not None and not normal_daily.empty:
                normal.update(aggregate_period(_slice(normal_daily, s, e), api_names, extreme=False))
            if normal_from_years:
                normal.update(mean_over(nyears_daily, s, e, [n for n in metric_names if n in normal_from_years]))

        cmp = build_comparison_table(current, recent, normal, metric_names, start.year,
                                     extreme=extreme, decimals=1, prev=prev, recent3=recent3)
        days_txt = f"{p['days']}일" + (" (일부)" if p["partial"] else "")
        for layout in ("combined", "all"):
            f = format_comparison_for_display(cmp, start.year, layout_mode=layout, decimals=1)
            layout_cols.setdefault(layout, [col, "기간", "일수"] + [c for c in f.columns if c != "구분"])
            for _, row in f.iterrows():
                name = next((n for n in metric_names if str(row["구분"]).startswith(n)), None)
                if name is None:
                    continue
                r = row.to_dict()
                r.pop("구분", None)
                by_metric_rows[name].append(pd.DataFrame([{
                    "_layout": layout, col: p["label"], "기간": p["range"], "일수": days_txt, **r}]))

        ov = {col: p["label"], "기간": p["range"], "일수": days_txt}
        for n in metric_names:
            ov[f"{n}({METRICS[n]['unit']})"] = current.get(n, float("nan"))
        overview_rows.append(ov)

    def to_json(df: pd.DataFrame) -> Dict[str, Any]:
        return {"columns": [str(c) for c in df.columns],
                "rows": [[str(v) for v in r] for r in df.itertuples(index=False, name=None)]}

    overview = format_report(pd.DataFrame(overview_rows), decimals=1)
    by_metric: Dict[str, Dict[str, Any]] = {}
    for n, parts in by_metric_rows.items():
        if not parts:
            continue
        allp = pd.concat(parts, ignore_index=True)
        by_metric[n] = {lay: to_json(allp[allp["_layout"] == lay][layout_cols[lay]].reset_index(drop=True))
                        for lay in ("combined", "all")}

    return {
        "overview": to_json(overview),
        "byMetric": by_metric,
        "count": len(periods),
        "partial": [p["label"] for p in periods if p["partial"]],
        "note": note or ("상순 1~10일 · 중순 11~20일 · 하순 21일~말일. 조회 기간에 일부만 포함된 순은 '(일부)'로 표시하며, "
                         "비교 연도도 같은 날짜 범위로만 집계합니다."),
    }

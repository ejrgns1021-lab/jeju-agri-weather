# -*- coding: utf-8 -*-
"""
station_dekad.py - 지점별 '해당 연도 vs 최근 5년 평균' 비교 (v7.1, AI 기상분석용)

제주 전체(4개 지점 평균)로 보면 지점 간 차이가 평균에 묻히므로, 지점마다 따로
  - 기간 전체 값
  - 순별(상·중·하순) 값
을 해당 연도와 '그 지점의' 최근 5년 평균으로 비교합니다.

기존 비교표·순별 표 계산은 건드리지 않으며, 이미 받아 둔 기간 자료(메모리·Firestore 캐시)를
다시 쓰므로 기상청 추가 호출이 없습니다.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .dekad import _slice, _year_offset_dates, dekad_periods
from .kma_api import STATION_NAMES
from .processing import aggregate_period, shift_period


def _r(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, 1)


def _station_daily(df: pd.DataFrame, stn_id: int) -> pd.DataFrame:
    if df is None or df.empty or "stn_id" not in df.columns:
        return pd.DataFrame()
    return df[df["stn_id"] == stn_id].sort_values("date").reset_index(drop=True)


def build_station_brief(start: dt.date, end: dt.date, metric_names: List[str], extreme: bool,
                        current_df: pd.DataFrame, fetch_period: Callable[[dt.date, dt.date], pd.DataFrame],
                        recent_years: List[int], station_ids: List[int]) -> Dict[str, Any]:
    """{"metrics": [...], "stations": [{"name", "period": {지표: [해당, 5년]}, "dekads": [{"label","range", "values": {지표: [해당, 5년]}}]}]}"""
    periods = dekad_periods(start, end)
    past = {}
    for y in recent_years:
        s_y, e_y = shift_period(start, end, y)
        try:
            past[y] = fetch_period(s_y, e_y)
        except Exception:
            past[y] = pd.DataFrame()

    out: List[Dict[str, Any]] = []
    for sid in station_ids:
        cur = _station_daily(current_df, sid)
        yrs = {y: _station_daily(df, sid) for y, df in past.items()}

        def five_year(s: dt.date, e: dt.date) -> Dict[str, Optional[float]]:
            vals: Dict[str, List[float]] = {n: [] for n in metric_names}
            for y, d in yrs.items():
                s_y, e_y = _year_offset_dates(s, e, start.year, y)
                agg = aggregate_period(_slice(d, s_y, e_y), metric_names, extreme)
                for n in metric_names:
                    v = agg.get(n)
                    if v is not None and not pd.isna(v):
                        vals[n].append(float(v))
            return {n: (_r(sum(v) / len(v)) if v else None) for n, v in vals.items()}

        whole_cur = aggregate_period(cur, metric_names, extreme)
        whole_5 = five_year(start, end)
        dekads = []
        for p in periods:
            c = aggregate_period(_slice(cur, p["start"], p["end"]), metric_names, extreme)
            f = five_year(p["start"], p["end"])
            dekads.append({"label": p["label"], "range": f"{p['start'].month}/{p['start'].day}~{p['end'].month}/{p['end'].day}",
                           "partial": bool(p.get("partial")),
                           "values": {n: [_r(c.get(n)), f.get(n)] for n in metric_names}})
        out.append({"name": STATION_NAMES.get(sid, str(sid)),
                    "period": {n: [_r(whole_cur.get(n)), whole_5.get(n)] for n in metric_names},
                    "dekads": dekads})
    return {"metrics": list(metric_names), "years": f"{min(recent_years)}~{max(recent_years)}", "stations": out}

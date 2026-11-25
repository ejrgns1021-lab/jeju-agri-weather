# -*- coding: utf-8 -*-
"""
chart.py - 그래프 탭용 일별 흐름 자료 (v7.6)

조회 기간의 날짜마다 '선택 지점 평균' 값을 본년·전년·최근 3년·최근 5년·평년으로 정리합니다.
  · 모두 일별 값 (강수량은 그날 내린 비, 일조는 그날 일조시간) — v7.6.1, 누적 없음
  · 조회에서 고른 지표와 관계없이 평균·최고·최저 기온, 강수량, 일조 시간 5가지를 모두 보냄
    (그래프에서 지표를 따로따로 눌러 볼 수 있도록. 강우일수·열대야 일수는 하루 단위로는 0/1이라 제외)
이미 받아 둔(메모리·저장소) 자료를 다시 쓰므로 기상청 추가 호출이 없습니다.
"""
from __future__ import annotations

import datetime as dt
import math
import warnings
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .kma_api import METRICS
from .processing import jeju_daily_mean, shift_period


def _r(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, 2)


def _by_offset(daily: pd.DataFrame, s: dt.date, n: int, col: str) -> np.ndarray:
    """기간 시작일(s)부터 n일 동안의 값 배열 (없는 날은 NaN)"""
    out = np.full(n, np.nan)
    if daily is None or daily.empty or col not in daily.columns:
        return out
    for d, v in zip(daily["date"], pd.to_numeric(daily[col], errors="coerce")):
        i = (d - s).days
        if 0 <= i < n and not pd.isna(v):
            out[i] = float(v)
    return out


def _mean_years(arrs: List[np.ndarray], n: int, min_years: int) -> np.ndarray:
    if not arrs:
        return np.full(n, np.nan)
    stack = np.vstack(arrs)
    cnt = np.sum(~np.isnan(stack), axis=0)
    with warnings.catch_warnings():          # 모든 연도가 비어 있는 날의 경고 무시
        warnings.simplefilter("ignore", category=RuntimeWarning)
        mean = np.nanmean(stack, axis=0)
    mean[cnt < min_years] = np.nan
    return mean


def _cumulate(a: np.ndarray) -> np.ndarray:
    """누적합. 마지막 자료 이후(아직 관측 전인 날)는 비워 둠"""
    valid = np.where(~np.isnan(a))[0]
    out = np.full(len(a), np.nan)
    if len(valid) == 0:
        return out
    last = valid[-1]
    out[: last + 1] = np.cumsum(np.nan_to_num(a[: last + 1]))
    return out


def build_chart(start: dt.date, end: dt.date, metric_names: List[str],
                current_daily: pd.DataFrame, fetch_period: Callable[[dt.date, dt.date], pd.DataFrame],
                recent_years: List[int], normal_mode: str, normal_daily_api: Optional[pd.DataFrame],
                normal_years: List[int]) -> Dict[str, Any]:
    n = (end - start).days + 1
    dates = [(start + dt.timedelta(days=i)) for i in range(n)]

    daily_cache: Dict[int, Optional[pd.DataFrame]] = {}

    def year_daily(y: int):
        if y not in daily_cache:
            s_y, e_y = shift_period(start, end, y)
            try:
                daily_cache[y] = (jeju_daily_mean(fetch_period(s_y, e_y)), s_y)
            except Exception:
                daily_cache[y] = None
        return daily_cache[y]

    def years_series(years: List[int], col: str, min_years: int) -> np.ndarray:
        arrs = []
        for y in years:
            got = year_daily(y)
            if got is not None:
                arrs.append(_by_offset(got[0], got[1], n, col))
        return _mean_years(arrs, n, min_years)

    prev_year = start.year - 1
    metrics: Dict[str, Any] = {}
    names = [n for n, m in METRICS.items() if not m.get("derived_from")]
    for name in names:
        meta = METRICS[name]
        col = meta["col"]
        cur = _by_offset(current_daily, start, n, col)
        prev = years_series([prev_year], col, 1)
        r3 = years_series(recent_years[:3], col, 2)
        r5 = years_series(recent_years, col, 3)
        normal = np.full(n, np.nan)
        if normal_mode != "skip":
            api_ok = (normal_mode == "api" and normal_daily_api is not None and not normal_daily_api.empty
                      and col in normal_daily_api.columns and normal_daily_api[col].notna().any())
            if api_ok:
                normal = _by_offset(normal_daily_api, start, n, col)
            else:   # 평년값 API 에 없는 지표·실측 계산 모드: 1991~2020 실측 평균 (20개 연도 이상)
                normal = years_series(normal_years, col, 20)
        series = {"cur": cur, "prev": prev, "r3": r3, "r5": r5, "normal": normal}
        if np.isnan(cur).all():
            continue
        metrics[name] = {"unit": meta["unit"], "mode": "daily", "kind": meta["kind"],
                         **{k: [_r(x) for x in v] for k, v in series.items()}}
    return {"dates": [d.isoformat() for d in dates], "prevYear": prev_year,
            "recent3Range": f"{min(recent_years[:3])}~{max(recent_years[:3])}",
            "recentRange": f"{min(recent_years)}~{max(recent_years)}", "metrics": metrics}

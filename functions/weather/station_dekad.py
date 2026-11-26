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
from .kma_api import METRICS, STATION_NAMES
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


RAIN = "누적 강수량"     # 강수량만 평년과도 비교 (기온은 온난화 추세로 최근 5년과만 비교)


def build_station_brief(start: dt.date, end: dt.date, metric_names: List[str], extreme: bool,
                        current_df: pd.DataFrame, fetch_period: Callable[[dt.date, dt.date], pd.DataFrame],
                        recent_years: List[int], station_ids: List[int],
                        normal_df: Optional[pd.DataFrame] = None,
                        normal_years: Optional[List[int]] = None) -> Dict[str, Any]:
    """{"metrics": [...], "stations": [{"name", "period": {지표: [해당, 5년(, 평년)]}, "dekads": [{"label","range", "values": {...}}]}]}
    누적 강수량은 [해당, 최근 5년, 평년] 세 값 — 평년은 평년값 API(지점별 일평년)가 있으면 그것, 없으면 1991~2020 실측 평균"""
    periods = dekad_periods(start, end)
    past = {}
    for y in recent_years:
        s_y, e_y = shift_period(start, end, y)
        try:
            past[y] = fetch_period(s_y, e_y)
        except Exception:
            past[y] = pd.DataFrame()

    # 강수량 평년: 평년값 API 지점별 일평년이 있으면 사용, 없으면 30년 실측(이미 받아 둔 경우만 — 캐시에서 즉시)
    use_api_normal = (RAIN in metric_names and normal_df is not None and not normal_df.empty
                      and "sumRn" in normal_df.columns and normal_df["sumRn"].notna().any())
    normal_past: Dict[int, pd.DataFrame] = {}
    if RAIN in metric_names and not use_api_normal and normal_years:
        for y in normal_years:
            s_y, e_y = shift_period(start, end, y)
            try:
                normal_past[y] = fetch_period(s_y, e_y)
            except Exception:
                pass

    out: List[Dict[str, Any]] = []
    for sid in station_ids:
        cur = _station_daily(current_df, sid)
        yrs = {y: _station_daily(df, sid) for y, df in past.items()}
        nrm_api = _station_daily(normal_df, sid) if use_api_normal else None
        nrm_yrs = {y: _station_daily(df, sid) for y, df in normal_past.items()}

        def normal_rain(s: dt.date, e: dt.date) -> Optional[float]:
            if RAIN not in metric_names:
                return None
            if nrm_api is not None and not nrm_api.empty:
                v = aggregate_period(_slice(nrm_api, s, e), [RAIN]).get(RAIN)
                return _r(v)
            vals = []
            for y, d in nrm_yrs.items():
                s_y, e_y = _year_offset_dates(s, e, start.year, y)
                v = aggregate_period(_slice(d, s_y, e_y), [RAIN]).get(RAIN)
                if v is not None and not pd.isna(v):
                    vals.append(float(v))
            return _r(sum(vals) / len(vals)) if len(vals) >= 20 else None   # 30년 중 20년 이상 있을 때만

        def with_normal(vals: Dict[str, List[Optional[float]]], s: dt.date, e: dt.date) -> Dict[str, List[Optional[float]]]:
            if RAIN in vals:
                vals[RAIN] = vals[RAIN] + [normal_rain(s, e)]
            return vals

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
                           "values": with_normal({n: [_r(c.get(n)), f.get(n)] for n in metric_names}, p["start"], p["end"])})
        out.append({"name": STATION_NAMES.get(sid, str(sid)),
                    "period": with_normal({n: [_r(whole_cur.get(n)), whole_5.get(n)] for n in metric_names}, start, end),
                    "dekads": dekads})
    return {"metrics": list(metric_names), "years": f"{min(recent_years)}~{max(recent_years)}", "stations": out}


# ---------------------------------------------------------------------
# 평년값 API 지점 빈칸 보완 (v7.6): 일부 지점만 평년이 없는 지표(성산·고산 일조 등)
# ---------------------------------------------------------------------
def fill_station_normals(normal_df: pd.DataFrame, start: dt.date, end: dt.date, metric_names: List[str],
                         station_ids: List[int], fetch_period: Callable[[dt.date, dt.date], pd.DataFrame],
                         normal_years: List[int], prefetch_years: Optional[Callable[[List[int]], None]] = None,
                         min_years: int = 20) -> tuple:
    """평년값 API 에 어떤 지점만 빠진 지표가 있으면, 그 지점의 날짜별 평년을 1991~2020 같은 날짜 실측 평균으로 채움.
    → 지점 평균을 낼 때 모든 선택 지점이 같은 기준으로 들어가도록(빠진 지점 때문에 평균 지점 구성이 달라지는 오류 방지).
    지표 전체가 빠진 경우(강우일수 등)는 기존처럼 30년 실측 계산으로 따로 처리하므로 여기서는 건드리지 않음.
    반환: (채운 normal_df, ["성산 일조 시간", ...])"""
    if normal_df is None or normal_df.empty:
        return normal_df, []
    cand = [n for n in metric_names if not METRICS[n].get("derived_from") and METRICS[n]["col"] in normal_df.columns]
    missing: Dict[int, List[str]] = {}
    for n in cand:
        col = METRICS[n]["col"]
        has = {sid: bool(normal_df.loc[normal_df["stn_id"] == sid, col].notna().any()) for sid in station_ids}
        if any(has.values()):                        # 일부 지점만 있는 지표
            for sid, ok in has.items():
                if not ok:
                    missing.setdefault(sid, []).append(n)
    if not missing:
        return normal_df, []
    if prefetch_years:
        try:
            prefetch_years(normal_years)
        except Exception:
            pass
    n_days = (end - start).days + 1
    stacks: Dict[tuple, List[List[float]]] = {}
    for y in normal_years:
        s_y, e_y = shift_period(start, end, y)
        try:
            df = fetch_period(s_y, e_y)
        except Exception:
            continue
        for sid, names in missing.items():
            sub = _station_daily(df, sid)
            for n in names:
                arr = [float("nan")] * n_days
                for d, v in zip(sub.get("date", []), pd.to_numeric(sub.get(METRICS[n]["col"], pd.Series(dtype=float)), errors="coerce")):
                    i = (d - s_y).days
                    if 0 <= i < n_days and not pd.isna(v):
                        arr[i] = float(v)
                stacks.setdefault((sid, n), []).append(arr)
    out = normal_df.copy()
    filled: List[str] = []
    for (sid, n), arrs in stacks.items():
        col = METRICS[n]["col"]
        means = []
        for i in range(n_days):
            vals = [a[i] for a in arrs if not math.isnan(a[i])]
            means.append(sum(vals) / len(vals) if len(vals) >= min_years else float("nan"))
        if all(math.isnan(v) for v in means):
            continue
        by_date = {start + dt.timedelta(days=i): v for i, v in enumerate(means)}
        mask = out["stn_id"] == sid
        out.loc[mask, col] = out.loc[mask, "date"].map(by_date)
        filled.append(f"{STATION_NAMES.get(sid, sid)} {n}")
    return out, filled


# ---------------------------------------------------------------------
# 보고서 표 (v7.3): 지점별 본년 · 전년 · 최근 3년 · 최근 5년 · 평년
# ---------------------------------------------------------------------
def build_station_report(start: dt.date, end: dt.date, metric_names: List[str], extreme: bool,
                         current_df: pd.DataFrame, fetch_period: Callable[[dt.date, dt.date], pd.DataFrame],
                         recent_years: List[int], station_ids: List[int],
                         normal_mode: str, normal_df: Optional[pd.DataFrame],
                         normal_years: List[int], normal_from_years: List[str],
                         prefetch_years: Optional[Callable[[List[int]], None]] = None) -> Dict[str, Any]:
    """{"stations": [{"name", "values": {지표: {"cur","prev","r3","r5","normal"}}}]}
    - 평년: 평년값 API 지표는 지점별 일평년(API), API에 없는 지표·실측 계산 모드는 1991~2020 지점별 실측 평균
    - 이미 받아 둔 기간 자료를 재사용 (기상청 추가 호출 없음)"""
    prev_year = start.year - 1
    years3 = recent_years[:3]

    def fetch(y: int) -> pd.DataFrame:
        s_y, e_y = shift_period(start, end, y)
        try:
            return fetch_period(s_y, e_y)
        except Exception:
            return pd.DataFrame()

    past = {y: fetch(y) for y in recent_years}
    year_metrics = [n for n in metric_names if normal_mode == "compute" or n in (normal_from_years or [])]
    nyears = {y: fetch(y) for y in normal_years} if (normal_mode != "skip" and year_metrics) else {}
    api_metrics = [n for n in metric_names if n not in year_metrics] if normal_mode == "api" else []

    def mean_years(frames: Dict[int, pd.DataFrame], sid: int, names: List[str]) -> Dict[str, Optional[float]]:
        vals: Dict[str, List[float]] = {n: [] for n in names}
        for y, df in frames.items():
            agg = aggregate_period(_station_daily(df, sid), names, extreme)
            for n in names:
                v = agg.get(n)
                if v is not None and not pd.isna(v):
                    vals[n].append(float(v))
        # 30년 평균은 20개 연도 이상 있을 때만 (자료가 너무 적으면 평년으로 보기 어려움)
        need = 20 if len(frames) >= 20 else 1
        return {n: (_r(sum(v) / len(v)) if len(v) >= need else None) for n, v in vals.items()}

    # 평년값 API 에 특정 지점 값이 없으면(예: 성산·고산 일조시간) 그 칸만 1991~2020 지점 실측 평균으로 보완
    api_normal: Dict[int, Dict[str, Optional[float]]] = {}
    for sid in station_ids:
        if api_metrics and normal_df is not None and not normal_df.empty:
            agg = aggregate_period(_station_daily(normal_df, sid), api_metrics, extreme=False)
            api_normal[sid] = {n: _r(agg.get(n)) for n in api_metrics}
        else:
            api_normal[sid] = {n: None for n in api_metrics}
    missing = {sid: [n for n in api_metrics if api_normal[sid].get(n) is None] for sid in station_ids}
    filled_from_years: List[str] = []
    if normal_mode == "api" and any(missing.values()):
        if not nyears:
            if prefetch_years:
                try:
                    prefetch_years(normal_years)       # 30개 연도 동시 수신 (한 번 받으면 저장분 재사용)
                except Exception:
                    pass
            nyears = {y: fetch(y) for y in normal_years}

    out = []
    for sid in station_ids:
        cur = aggregate_period(_station_daily(current_df, sid), metric_names, extreme)
        prev = aggregate_period(_station_daily(past.get(prev_year, pd.DataFrame()), sid), metric_names, extreme)
        r3 = mean_years({y: past[y] for y in years3 if y in past}, sid, metric_names)
        r5 = mean_years(past, sid, metric_names)
        normal: Dict[str, Optional[float]] = {n: None for n in metric_names}
        normal.update(api_normal.get(sid, {}))
        if nyears and year_metrics:
            normal.update(mean_years(nyears, sid, year_metrics))
        if nyears and missing.get(sid):
            filled = mean_years(nyears, sid, missing[sid])
            for n, v in filled.items():
                if v is not None:
                    normal[n] = v
                    filled_from_years.append(f"{STATION_NAMES.get(sid, sid)} {n}")
        out.append({"name": STATION_NAMES.get(sid, str(sid)),
                    "values": {n: {"cur": _r(cur.get(n)), "prev": _r(prev.get(n)), "r3": r3.get(n),
                                   "r5": r5.get(n), "normal": normal.get(n)} for n in metric_names}})
    return {"stations": out, "normalFilled": filled_from_years}

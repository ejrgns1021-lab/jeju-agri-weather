# -*- coding: utf-8 -*-
"""
processing.py  (백엔드)
Pandas 데이터 가공 모듈
  - 4개 지점 일별 자료 -> 결측 지점 제외 '제주 전체 일일 평균값'
  - 기간 집계 (기온 = 평균, 누적 강수량·일조 시간 = 합산)
  - 해당 연도 / 전년 / 최근 5년 평균 / 평년(1991~2020) 평균 비교표
  - 표시 자릿수(소수 첫째 자리) 기준 편차(대비) 및 대비 비율(%) 산출 (표시 숫자와 검산 결과 100% 일치)
  - 평균기온·최고기온·최저기온·누적강수량·일조시간 전년도 대비(비율), 최근5년평균대비(비율), 평년대비(비율) 산출
  - 한글(HWP) 보고서용 표 포맷
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .kma_api import METRICS, METRIC_COLS, STATIONS, STATION_NAMES

NORMAL_START_YEAR = 1991
NORMAL_END_YEAR = 2020


# ---------------------------------------------------------------------
# 1. 제주 전체 일일 평균
# ---------------------------------------------------------------------
def jeju_daily_mean(station_df: pd.DataFrame) -> pd.DataFrame:
    """
    날짜별로 4개 지점 값을 평균. 결측(NaN) 지점은 분자·분모에서 자동 제외(skipna).
    지표별 유효 지점 수를 n_<col> 컬럼으로 함께 반환. 전 지점 결측이면 NaN 유지.
    """
    cols = ["date"] + METRIC_COLS + [f"n_{c}" for c in METRIC_COLS]
    if station_df is None or station_df.empty:
        return pd.DataFrame(columns=cols)
    work = station_df.copy()
    for c in METRIC_COLS:
        work[c] = pd.to_numeric(work[c], errors="coerce") if c in work.columns else np.nan
    g = work.groupby("date")
    mean_df = g[METRIC_COLS].mean()
    cnt_df = g[METRIC_COLS].count().add_prefix("n_")
    out = pd.concat([mean_df, cnt_df], axis=1).reset_index()
    return out.sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------
# 2. 기간 집계
# ---------------------------------------------------------------------
def _agg(series: pd.Series, how: str) -> float:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if valid.empty:
        return float("nan")
    if how == "sum":
        return float(valid.sum())
    if how == "max":
        return float(valid.max())
    if how == "min":
        return float(valid.min())
    return float(valid.mean())


def aggregate_period(daily_df: pd.DataFrame, metric_names: List[str], extreme: bool = False) -> Dict[str, float]:
    """일별 DataFrame -> 지표별 기간 대표값. extreme=True 면 최고/최저 기온을 기간 극값으로."""
    result: Dict[str, float] = {}
    for name in metric_names:
        meta = METRICS[name]
        how = meta["agg"]
        if extreme and name == "최고 기온":
            how = "max"
        elif extreme and name == "최저 기온":
            how = "min"
        if daily_df is None or daily_df.empty or meta["col"] not in daily_df.columns:
            result[name] = float("nan")
        else:
            result[name] = _agg(daily_df[meta["col"]], how)
    return result


def valid_days(daily_df: pd.DataFrame, metric_names: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for name in metric_names:
        col = METRICS[name]["col"]
        out[name] = int(daily_df[col].notna().sum()) if (daily_df is not None and not daily_df.empty and col in daily_df.columns) else 0
    return out


# ---------------------------------------------------------------------
# 3. 기간 이동 (과거 동일 기간)
# ---------------------------------------------------------------------
def _shift_year(d: dt.date, years: int) -> dt.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 2월 29일 -> 2월 28일
        return d.replace(year=d.year + years, day=28)


def shift_period(start: dt.date, end: dt.date, target_year: int) -> Tuple[dt.date, dt.date]:
    """시작일의 연도를 target_year 로 옮기고 종료일도 같은 연수만큼 이동 (연도를 넘는 기간 유지)"""
    k = target_year - start.year
    return _shift_year(start, k), _shift_year(end, k)


# ---------------------------------------------------------------------
# 4. 연도별 집계 (전년 · 최근 5년 · 과거 30년 실측 계산용)
# ---------------------------------------------------------------------
def yearly_period_values(fetch_period: Callable[[dt.date, dt.date], pd.DataFrame],
                         start: dt.date, end: dt.date, years: List[int],
                         metric_names: List[str], extreme: bool = False,
                         progress: Optional[Callable[[int, int], None]] = None) -> pd.DataFrame:
    """
    years 각 연도의 동일 기간 자료를 fetch_period 로 가져와
    '제주 전체 일일 평균 -> 기간 집계' 값을 연도별 행으로 반환.
    """
    rows: List[Dict[str, Any]] = []
    for i, y in enumerate(years):
        s, e = shift_period(start, end, y)
        if progress:
            progress(i, len(years))
        station_df = fetch_period(s, e)
        daily = jeju_daily_mean(station_df)
        vals = aggregate_period(daily, metric_names, extreme)
        days = valid_days(daily, metric_names)
        row: Dict[str, Any] = {"연도": y, "기간": f"{s:%Y-%m-%d} ~ {e:%Y-%m-%d}"}
        for name in metric_names:
            row[name] = vals[name]
            row[f"{name}_유효일"] = days[name]
        rows.append(row)
    return pd.DataFrame(rows)


def extract_year_values(yearly_df: pd.DataFrame, target_year: int,
                        metric_names: List[str]) -> Tuple[Dict[str, float], Dict[str, int]]:
    """연도별 집계 DataFrame에서 특정 연도(예: 전년도)의 지표별 값과 유효일수를 추출."""
    vals: Dict[str, float] = {n: float("nan") for n in metric_names}
    days: Dict[str, int] = {n: 0 for n in metric_names}
    if yearly_df is None or yearly_df.empty or "연도" not in yearly_df.columns:
        return vals, days
    sub = yearly_df[yearly_df["연도"] == target_year]
    if sub.empty:
        return vals, days
    row = sub.iloc[0]
    for name in metric_names:
        if name in row and not pd.isna(row[name]):
            vals[name] = float(row[name])
        day_col = f"{name}_유효일"
        if day_col in row and not pd.isna(row[day_col]):
            days[name] = int(row[day_col])
    return vals, days


def mean_of_years(yearly_df: pd.DataFrame, metric_names: List[str]) -> Tuple[Dict[str, float], Dict[str, int]]:
    """연도별 집계값의 평균(결측 연도 제외)과 유효 연도 수"""
    means: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for name in metric_names:
        if yearly_df is None or yearly_df.empty or name not in yearly_df.columns:
            means[name], counts[name] = float("nan"), 0
            continue
        valid = pd.to_numeric(yearly_df[name], errors="coerce").dropna()
        means[name] = float(valid.mean()) if not valid.empty else float("nan")
        counts[name] = int(len(valid))
    return means, counts


# ---------------------------------------------------------------------
# 5. 비교표 작성
# ---------------------------------------------------------------------
def _label(name: str) -> str:
    return f"{name}({METRICS[name]['unit']})"


def _round_val(val: Any, decimals: int = 1) -> float:
    if val is None or pd.isna(val):
        return float("nan")
    try:
        f = float(val)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(f):
        return float("nan")
    return round(f, decimals)


def _calc_ratio(num: float, den: float, allow: bool, decimals: int = 1) -> float:
    if not allow or pd.isna(num) or pd.isna(den) or abs(den) < 1e-9:
        return float("nan")
    return round((num / den) * 100.0, decimals)


def build_comparison_table(current: Dict[str, float], recent: Dict[str, float], normal: Dict[str, float],
                           metric_names: List[str], current_year: int, extreme: bool = False,
                           decimals: int = 1,
                           prev: Optional[Dict[str, float]] = None,
                           recent3: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """
    보고서용 비교표 (수치형).
    열:
      구분 / 해당 연도 / 전년 / 최근 5년 평균 / 평년 평균 /
      전년 대비 / 최근 5년 대비 / 평년 대비 /
      전년도 대비(비율) / 최근5년평균대비(비율) / 평년대비(비율)
    - 표에 출력되는 숫자(소수 decimals 자리 반올림값)를 기준으로 편차(대비)와 비율(%)을 산출하여,
      보고서 표의 숫자를 그대로 검산했을 때 오차가 발생하지 않도록 한다.
    - 평균 기온, 최고 기온, 최저 기온, 누적 강수량, 일조 시간 모두 대비(비율)을 산출한다.
    - recent3 를 주면 '최근 3년 평균 / 최근 3년 대비 / 최근3년평균대비(비율)' 열을 추가한다 (v7.3, 계산 방식은 5년과 동일).
    """
    prev_dict = prev or {}
    rows: List[Dict[str, Any]] = []
    for name in metric_names:
        meta = METRICS[name]
        label = _label(name)
        if extreme and name in ("최고 기온", "최저 기온"):
            label = f"{name} 극값({meta['unit']})"
        cur = _round_val(current.get(name, float("nan")), decimals)
        prv = _round_val(prev_dict.get(name, float("nan")), decimals)
        rec = _round_val(recent.get(name, float("nan")), decimals)
        nor = _round_val(normal.get(name, float("nan")), decimals)

        d_prv = round(cur - prv, decimals) if not (pd.isna(cur) or pd.isna(prv)) else float("nan")
        d_rec = round(cur - rec, decimals) if not (pd.isna(cur) or pd.isna(rec)) else float("nan")
        d_nor = round(cur - nor, decimals) if not (pd.isna(cur) or pd.isna(nor)) else float("nan")

        allow_ratio = True
        r_prv = _calc_ratio(cur, prv, allow_ratio, decimals)
        r_rec = _calc_ratio(cur, rec, allow_ratio, decimals)
        r_nor = _calc_ratio(cur, nor, allow_ratio, decimals)

        row = {
            "구분": label,
            "해당 연도": cur,
            "전년": prv,
            "최근 5년 평균": rec,
            "평년 평균": nor,
            "전년 대비": d_prv,
            "최근 5년 대비": d_rec,
            "평년 대비": d_nor,
            "전년도 대비(비율)": r_prv,
            "최근5년평균대비(비율)": r_rec,
            "평년대비(비율)": r_nor,
        }
        if recent3 is not None:
            r3 = _round_val(recent3.get(name, float("nan")), decimals)
            row["최근 3년 평균"] = r3
            row["최근 3년 대비"] = round(cur - r3, decimals) if not (pd.isna(cur) or pd.isna(r3)) else float("nan")
            row["최근3년평균대비(비율)"] = _calc_ratio(cur, r3, allow_ratio, decimals)
        rows.append(row)
    df = pd.DataFrame(rows)
    df.attrs["current_year"] = current_year
    df.attrs["prev_year"] = current_year - 1
    return df


def format_comparison_for_display(comparison: pd.DataFrame, current_year: int,
                                  layout_mode: str = "all", decimals: int = 1) -> pd.DataFrame:
    """
    수치형 comparison DataFrame을 화면/HWP/CSV 출력용 문자열 DataFrame으로 변환.
    layout_mode:
      - "all": 편차(대비) 열 + 비율(%) 열 모두 표시 (기본)
      - "grouped": 기준별로 편차와 비율을 나란히 배치
      - "ratio_only": 비율(%) 열만 표시
      - "combined": 한 칸에 '편차 (비율%)' 형태로 병기
    """
    if comparison is None or comparison.empty:
        return pd.DataFrame()
    year_label = f"해당 연도({current_year})"
    prev_label = f"전년({current_year - 1})"
    work = comparison.copy()
    if "전년" not in work.columns:
        work["전년"] = float("nan")
    for col in ("전년 대비", "최근 5년 대비", "평년 대비", "전년도 대비(비율)", "최근5년평균대비(비율)", "평년대비(비율)"):
        if col not in work.columns:
            work[col] = float("nan")

    work = work.rename(columns={"해당 연도": year_label, "전년": prev_label})
    has3 = "최근 3년 평균" in work.columns
    # 기준별 (기준값 열, 편차 열, 비율 열) — 최근 3년은 값이 있을 때만 (v7.3)
    bases = [(prev_label, "전년 대비", "전년도 대비(비율)")]
    if has3:
        bases.append(("최근 3년 평균", "최근 3년 대비", "최근3년평균대비(비율)"))
    bases += [("최근 5년 평균", "최근 5년 대비", "최근5년평균대비(비율)"), ("평년 평균", "평년 대비", "평년대비(비율)")]
    formatted = format_report(work, decimals=decimals, signed_cols=[b[1] for b in bases])
    base_cols = ["구분", year_label] + [b[0] for b in bases]

    if layout_mode == "grouped":
        cols = list(base_cols)
        for _, d, r in bases:
            cols += [d, r]
        return formatted[cols]

    if layout_mode == "ratio_only":
        return formatted[base_cols + [b[2] for b in bases]]

    if layout_mode == "combined":
        out = formatted[base_cols].copy()
        for _, diff_col, ratio_col in bases:
            combined_vals: List[str] = []
            for d_str, r_str in zip(formatted[diff_col].tolist(), formatted[ratio_col].tolist()):
                if d_str == "-" and r_str == "-":
                    combined_vals.append("-")
                elif r_str == "-":
                    combined_vals.append(d_str)
                elif d_str == "-":
                    combined_vals.append(f"{r_str}%")
                else:
                    combined_vals.append(f"{d_str} ({r_str}%)")
            out[ratio_col] = combined_vals
        return out

    # 기본값 ("all")
    return formatted[base_cols + [b[1] for b in bases] + [b[2] for b in bases]]


def build_station_table(station_df: pd.DataFrame, metric_names: List[str], extreme: bool = False,
                        include_jeju_mean: bool = True) -> pd.DataFrame:
    """지점별 기간 집계표 (행: 지점, 열: 지표). 마지막 행에 4개 지점 평균(결측 제외) 추가."""
    rows: List[Dict[str, Any]] = []
    if station_df is None or station_df.empty:
        return pd.DataFrame(columns=["구분"] + [_label(n) for n in metric_names] + ["자료 일수"])
    for name_s, stn_id in STATIONS.items():
        sub = station_df[station_df["stn_id"] == stn_id]
        if sub.empty:
            continue
        vals = aggregate_period(sub, metric_names, extreme)
        row: Dict[str, Any] = {"구분": f"{name_s}({stn_id})"}
        for n in metric_names:
            row[_label(n)] = vals[n]
        row["자료 일수"] = int(sub["date"].nunique())
        rows.append(row)
    if include_jeju_mean:
        daily = jeju_daily_mean(station_df)
        vals = aggregate_period(daily, metric_names, extreme)
        row = {"구분": "제주 전체 평균"}
        for n in metric_names:
            row[_label(n)] = vals[n]
        row["자료 일수"] = int(daily["date"].nunique()) if not daily.empty else 0
        rows.append(row)
    return pd.DataFrame(rows)


def build_daily_table(station_df: pd.DataFrame, jeju_daily: pd.DataFrame, metric_name: str) -> pd.DataFrame:
    """특정 지표의 일자 × 지점 피벗표 + 제주 전체 평균 + 유효 지점 수"""
    col = METRICS[metric_name]["col"]
    if station_df is None or station_df.empty:
        return pd.DataFrame()
    piv = station_df.pivot_table(index="date", columns="stn_name", values=col, aggfunc="mean")
    order = [s for s in STATIONS if s in piv.columns]
    piv = piv.reindex(columns=order).reset_index().rename(columns={"date": "일자"})
    if jeju_daily is not None and not jeju_daily.empty:
        j = jeju_daily[["date", col, f"n_{col}"]].rename(
            columns={"date": "일자", col: "제주 전체 평균", f"n_{col}": "유효 지점 수"})
        piv = piv.merge(j, on="일자", how="left")
        piv["유효 지점 수"] = piv["유효 지점 수"].fillna(0).astype(int)
    piv["일자"] = piv["일자"].astype(str)
    return piv


# ---------------------------------------------------------------------
# 6. 보고서 포맷 (문자열 변환)
# ---------------------------------------------------------------------
def format_report(df: pd.DataFrame, decimals: int = 1, int_cols: Optional[List[str]] = None,
                  signed_cols: Optional[List[str]] = None) -> pd.DataFrame:
    """NaN -> '-', 실수 -> 소수 decimals 자리, 지정 열은 정수/부호 표기 (+0.0/-0.0 은 0.0 으로 정규화)"""
    if df is None or df.empty:
        return pd.DataFrame()
    int_cols = set(int_cols or [])
    signed_cols = set(signed_cols or [])
    out = df.copy()
    for c in out.columns:
        if c in int_cols:
            out[c] = out[c].apply(lambda v: "-" if pd.isna(v) else f"{int(v)}")
        elif pd.api.types.is_numeric_dtype(out[c]):
            fmt_signed = f"{{:+.{decimals}f}}"
            fmt_plain = f"{{:.{decimals}f}}"
            def _fmt_cell(v: Any, is_signed: bool = (c in signed_cols)) -> str:
                if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
                    return "-"
                fv = float(v)
                if abs(fv) < 0.5 * (10 ** (-decimals)):
                    fv = 0.0
                    return fmt_plain.format(0.0)
                return (fmt_signed if is_signed else fmt_plain).format(fv)
            out[c] = out[c].apply(_fmt_cell)
        else:
            out[c] = out[c].fillna("-").astype(str)
    return out


def to_tsv(df: pd.DataFrame) -> str:
    """한글 '문자열을 표로' 변환용 탭 구분 텍스트"""
    if df is None or df.empty:
        return ""
    return df.to_csv(sep="\t", index=False, lineterminator="\n")

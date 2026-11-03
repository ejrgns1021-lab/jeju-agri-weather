# -*- coding: utf-8 -*-
"""
pipeline.py  (백엔드)
조회 조건 → 기상청 자료 수집 → 비교표 가공 → 화면에 그대로 그릴 수 있는 JSON 표 묶음

화면(프런트엔드)은 계산을 하지 않고, 여기서 만든 문자열 표를 받아 보여주기만 합니다.
표 구성 전환(4가지)과 일별 지표 선택은 미리 모두 만들어 보내므로 재조회 없이 즉시 바뀝니다.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .kma_api import (
    DEFAULT_DAILY_COLS,
    METRICS,
    STATIONS,
    WeatherApiError,
    detected_columns,
    fetch_all_stations,
    fetch_apihub_daily_text,
    normals_for_period,
)
from .processing import (
    NORMAL_END_YEAR,
    NORMAL_START_YEAR,
    aggregate_period,
    build_comparison_table,
    build_daily_table,
    build_station_table,
    extract_year_values,
    format_comparison_for_display,
    format_report,
    jeju_daily_mean,
    mean_of_years,
    shift_period,
    valid_days,
    yearly_period_values,
)

KST = dt.timezone(dt.timedelta(hours=9))
RECENT_YEARS = 5
MAX_PERIOD_DAYS = 366
PREFETCH_WORKERS = 4          # 과거 연도 자료를 동시에 받아오는 개수 (기상청 서버 부담 고려)
NORMAL_MODES = ("api", "compute", "skip")
SOURCES = ("apihub", "datagokr")
LAYOUTS = {   # 첫 번째 항목이 화면의 기본 표 구성
    "combined": "한 칸에 편차 (비율%)",
    "all": "편차 + 비율 모두 표시",
    "grouped": "기준별 편차·비율 나란히",
    "ratio_only": "대비(비율%) 열만",
}


class InputError(ValueError):
    """사용자 입력 오류 (화면에 그대로 보여줄 메시지)"""


# ---------------------------------------------------------------------
# 입력 검증
# ---------------------------------------------------------------------
def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def _parse_date(value: Any, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise InputError(f"{label} 형식이 올바르지 않습니다. (YYYY-MM-DD)")


def validate_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """화면에서 받은 조회 조건을 검사하고 표준 형태로 정리"""
    start = _parse_date(data.get("start"), "시작일")
    end = _parse_date(data.get("end"), "종료일")
    today = today_kst()
    if start > end:
        raise InputError("시작일이 종료일보다 늦습니다. 시작일을 종료일 이전으로 설정해 주세요.")
    if end > today:
        raise InputError(f"미래 날짜는 조회할 수 없습니다. ({today:%Y-%m-%d} 이전 날짜만 선택)")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise InputError("조회 기간이 366일을 초과합니다. 연 단위 비교를 위해 1년 이내로 설정해 주세요.")

    metrics = [m for m in METRICS if m in (data.get("metrics") or [])]
    if not metrics:
        raise InputError("최소 1개 이상의 지표를 선택해 주세요.")
    stations = [sid for name, sid in STATIONS.items() if name in (data.get("stations") or [])]
    if not stations:
        raise InputError("최소 1개 지점을 선택해 주세요.")

    source = data.get("source") or "apihub"
    if source not in SOURCES:
        raise InputError("데이터 소스가 올바르지 않습니다.")
    normal_mode = data.get("normalMode") or "api"
    if normal_mode not in NORMAL_MODES:
        raise InputError("평년 산출 방식이 올바르지 않습니다.")
    if normal_mode == "api" and source != "apihub":
        raise InputError("평년값 API 는 기상청 API허브 전용입니다. '과거 30년 실측 계산' 또는 '평년 비교 생략'을 선택하세요.")

    return {
        "start": start, "end": end, "metrics": metrics, "stations": stations, "source": source,
        "normal_mode": normal_mode, "extreme": bool(data.get("extreme", False)),
        "rain_zero": bool(data.get("rainZero", True)),
    }


# ---------------------------------------------------------------------
# 표 → JSON
# ---------------------------------------------------------------------
def table_json(df: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
    if df is None or df.empty:
        return None
    return {"columns": [str(c) for c in df.columns],
            "rows": [[str(v) for v in row] for row in df.itertuples(index=False, name=None)]}


def _metric_label(name: str) -> str:
    return f"{name}({METRICS[name]['unit']})"


# ---------------------------------------------------------------------
# 메인 파이프라인
# ---------------------------------------------------------------------
ProgressFn = Callable[[int, str, str], None]   # (진행률 0~100, 단계, 세부 내용)


def run_analysis(req: Dict[str, Any], api_key: str, progress: Optional[ProgressFn] = None) -> Dict[str, Any]:
    start: dt.date = req["start"]
    end: dt.date = req["end"]
    metric_names: List[str] = req["metrics"]
    station_ids: List[int] = req["stations"]
    source: str = req["source"]
    normal_mode: str = req["normal_mode"]
    extreme: bool = req["extreme"]
    rain_zero: bool = req["rain_zero"]

    notices: List[Dict[str, str]] = []   # {"level": info|warning|error, "text": ...}

    def report(pct: int, stage: str, detail: str = "") -> None:
        """진행 상황 알림 (화면 로딩창 표시용, 계산에는 영향 없음)"""
        if progress:
            try:
                progress(pct, stage, detail)
            except Exception:  # 진행 표시 실패가 조회를 막지 않도록
                pass

    def fetch_period(s: dt.date, e: dt.date) -> pd.DataFrame:
        df, _ = fetch_all_stations(s, e, api_key, source, station_ids, rain_zero)
        return df

    def prefetch(years: List[int], pct_from: int = 0, pct_to: int = 0, stage: str = "") -> None:
        """과거 연도 자료를 동시에 받아 메모리 캐시에 채워 둔다 (이후 계산은 캐시에서 즉시 처리)."""
        periods = {y: shift_period(start, end, y) for y in years}
        with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
            futures = {pool.submit(fetch_period, *periods[y]): y for y in years}
            done_years: List[int] = []
            for fut in as_completed(futures):
                fut.result()
                done_years.append(futures[fut])
                n = len(done_years)
                report(pct_from + (pct_to - pct_from) * n // len(years), stage,
                       f"{futures[fut]}년 동기간 자료 수신 완료 ({n}/{len(years)})")

    # 1) 해당 연도
    src_name = "기상청 API허브" if source == "apihub" else "공공데이터포털"
    report(2, f"해당 연도({start.year}) 관측 자료", f"{src_name}에 접속하는 중")
    current_df, errors = fetch_all_stations(start, end, api_key, source, station_ids, rain_zero,
                                            progress=lambda m: report(5, f"해당 연도({start.year}) 관측 자료", m))
    current_daily = jeju_daily_mean(current_df)
    current_vals = aggregate_period(current_daily, metric_names, extreme)
    current_days = valid_days(current_daily, metric_names)
    for name, msg in errors.items():
        notices.append({"level": "warning", "text": f"{name} 지점: {msg}"})

    params = {"start": start.isoformat(), "end": end.isoformat(), "metrics": metric_names,
              "stations": [n for n, sid in STATIONS.items() if sid in station_ids], "source": source,
              "normalMode": normal_mode, "extreme": extreme, "rainZero": rain_zero}
    if current_df.empty:
        return {"ok": False, "params": params, "notices": notices + [{
            "level": "error", "text": "선택한 기간에 조회된 관측 자료가 없습니다. 기간과 API 키를 확인해 주세요."}]}

    # '진단' 탭용 원문(1일치)은 별도 스레드에서 받고, 결과 정리 시점까지 안 오면 생략 (조회를 지연시키지 않도록)
    diag_pool = ThreadPoolExecutor(max_workers=1)
    diag_future = None
    if source == "apihub":
        ymd = start.strftime("%Y%m%d")
        diag_future = diag_pool.submit(fetch_apihub_daily_text, station_ids[0], ymd, ymd, api_key)
    diag_pool.shutdown(wait=False)

    # 2) 최근 5년 (첫 해 = 전년도)
    recent_years = [start.year - k for k in range(1, RECENT_YEARS + 1)]
    recent_stage = f"전년·최근 5년({min(recent_years)}~{max(recent_years)}) 자료"
    report(15, recent_stage, f"{len(recent_years)}개 연도 × {len(station_ids)}개 지점 조회 시작")
    prefetch(recent_years, 15, 55, recent_stage)
    recent_df = yearly_period_values(fetch_period, start, end, recent_years, metric_names, extreme)
    prev_year = start.year - 1
    prev_vals, prev_days = extract_year_values(recent_df, prev_year, metric_names)
    recent_vals, recent_cnt = mean_of_years(recent_df, metric_names)

    # 3) 평년
    normal_years = list(range(NORMAL_START_YEAR, NORMAL_END_YEAR + 1))
    normal_vals: Dict[str, float] = {n: float("nan") for n in metric_names}
    normal_cnt: Dict[str, int] = {n: 0 for n in metric_names}
    normal_note = "평년 비교 생략"
    unmapped: List[str] = []
    normal_raw_text = ""
    normal_yearly_df = pd.DataFrame()
    if normal_mode == "api":
        report(58, "평년값(1991~2020)", "기상청 평년값 API 조회 중")
        normal_df, unmapped, normal_raw_text = normals_for_period(start, end, api_key, station_ids)
        normal_vals = aggregate_period(jeju_daily_mean(normal_df), metric_names, extreme=False)
        normal_cnt = {n: 30 if not pd.isna(normal_vals[n]) else 0 for n in metric_names}
        normal_note = f"기상청 평년값 API ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"
        # 평년값 API 에서 얻지 못한 지표는 과거 30년 실측으로 대체 계산
        fallback = [n for n in metric_names if n in unmapped or pd.isna(normal_vals.get(n, float("nan")))]
        if fallback:
            prefetch(normal_years, 62, 92, "평년 보완: 1991~2020 실측 자료")
            normal_yearly_df = yearly_period_values(fetch_period, start, end, normal_years, fallback, extreme)
            fb_vals, fb_cnt = mean_of_years(normal_yearly_df, fallback)
            for n in fallback:
                normal_vals[n] = fb_vals.get(n, float("nan"))
                normal_cnt[n] = fb_cnt.get(n, 0)
            if len(fallback) == len(metric_names):
                normal_note = f"과거 30년 실측 계산 ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"
            else:
                normal_note = f"평년값 API + 30년 실측 보완 ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"
            unmapped = [n for n in fallback if pd.isna(normal_vals.get(n, float("nan")))]
            if not unmapped:
                notices.append({"level": "info", "text": "평년값 API 응답에 없는 지표(" + ", ".join(fallback) +
                                ")는 1991~2020년 30년 실측 자료로 대체 계산했습니다."})
    elif normal_mode == "compute":
        prefetch(normal_years, 58, 92, "평년: 1991~2020 실측 30년 자료")
        normal_yearly_df = yearly_period_values(fetch_period, start, end, normal_years, metric_names, extreme)
        normal_vals, normal_cnt = mean_of_years(normal_yearly_df, metric_names)
        normal_note = f"과거 30년 실측 계산 ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"
    if unmapped:
        notices.append({"level": "warning", "text": "다음 지표는 평년값을 산출하지 못했습니다: " + ", ".join(unmapped) +
                        " — '진단' 탭의 인식 컬럼 목록을 확인해 주세요."})

    # 지표 값이 비어 있는지 점검
    empty_metrics = [n for n in metric_names
                     if METRICS[n]["col"] in current_df.columns and current_df[METRICS[n]["col"]].isna().all()]
    if empty_metrics and len(empty_metrics) == len(metric_names):
        notices.append({"level": "error", "text": "관측 자료(날짜·지점)는 받았지만 선택한 지표 값이 모두 비어 있습니다. "
                        "'진단' 탭의 일자료 원문과 인식된 컬럼을 확인해 주세요."})
    elif empty_metrics:
        notices.append({"level": "warning", "text": "다음 지표는 관측 자료에서 값을 하나도 읽지 못했습니다: " +
                        ", ".join(empty_metrics) + " — '진단' 탭의 인식된 컬럼을 확인해 주세요."})

    # 4) 표 가공
    report(95, "비교표 가공", "편차·비율 계산 및 표 작성 중")
    daily_raw_text = ""
    if diag_future is not None:
        try:
            daily_raw_text = diag_future.result(timeout=3)
        except Exception:   # 시간 초과·기상청 오류 시 진단 원문만 생략
            daily_raw_text = ""
    comparison = build_comparison_table(current_vals, recent_vals, normal_vals, metric_names, start.year,
                                        extreme=extreme, decimals=1, prev=prev_vals)
    comparison_layouts = {key: table_json(format_comparison_for_display(comparison, start.year, layout_mode=key, decimals=1))
                          for key in LAYOUTS}

    basis = pd.DataFrame([{
        "구분": _metric_label(n),
        f"해당 연도({start.year}) 유효일수": current_days.get(n, 0),
        f"전년({prev_year}) 유효일수": prev_days.get(n, 0),
        "최근 5년 유효연수": recent_cnt.get(n, 0),
        "평년 유효연수": normal_cnt.get(n, 0),
    } for n in metric_names])

    station_table = format_report(build_station_table(current_df, metric_names, extreme), decimals=1, int_cols=["자료 일수"])
    daily_tables = {n: table_json(format_report(build_daily_table(current_df, current_daily, n), decimals=1,
                                                int_cols=["유효 지점 수"]))
                    for n in metric_names}

    def yearly_table(df: pd.DataFrame, names: List[str]) -> Optional[Dict[str, Any]]:
        if df is None or df.empty:
            return None
        names = [n for n in names if n in df.columns]
        out = df[["연도", "기간"] + names].rename(columns={n: _metric_label(n) for n in names})
        return table_json(format_report(out, decimals=1, int_cols=["연도"]))

    raw_cols = ["date", "stn_id", "stn_name"] + [METRICS[n]["col"] for n in metric_names]
    raw_df = current_df[[c for c in raw_cols if c in current_df.columns]].rename(
        columns={"date": "일자", "stn_id": "지점번호", "stn_name": "지점",
                 **{METRICS[n]["col"]: _metric_label(n) for n in metric_names}})

    period_days = (end - start).days + 1
    got_days = int(current_daily["date"].nunique()) if not current_daily.empty else 0
    return {
        "ok": True,
        "params": params,
        "notices": notices,
        "summary": {
            "periodDays": period_days,
            "gotDays": got_days,
            "currentYear": start.year,
            "prevYear": prev_year,
            "recentRange": f"{min(recent_years)}~{max(recent_years)}",
            "normalNote": normal_note,
            "normalIncluded": bool(normal_cnt) and max(normal_cnt.values()) > 0,
            "aggDesc": "기온은 기간 평균, 누적 강수량·일조 시간은 기간 합산" + (" / 최고·최저 기온은 기간 극값" if extreme else ""),
        },
        "layouts": [[k, v] for k, v in LAYOUTS.items()],   # 순서 유지를 위해 목록으로 전달
        "comparison": comparison_layouts,
        "basis": table_json(basis.astype(str)),
        "station": table_json(station_table),
        "daily": daily_tables,
        "recent": yearly_table(recent_df, metric_names),
        "normal30": yearly_table(normal_yearly_df, metric_names),
        "raw": table_json(format_report(raw_df, decimals=1, int_cols=["지점번호"])),
        "diagnostics": {
            "dailyRawText": daily_raw_text[:6000],
            "dailyColumns": detected_columns(daily_raw_text, DEFAULT_DAILY_COLS) if daily_raw_text else [],
            "normalRawText": normal_raw_text[:4000],
            "normalColumns": detected_columns(normal_raw_text, None) if normal_raw_text else [],
        },
        "fetchedAt": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
    }

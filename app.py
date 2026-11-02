# -*- coding: utf-8 -*-
"""
app.py - 제주도 농업 기상 분석 대시보드 (Phase 1~4 통합 최종본)

  Phase 1  비밀번호 인증 (환경변수 APP_PASSWORD 또는 .streamlit/secrets.toml 의 password)
  Phase 2  사이드바(기간·지표) + 날짜 유효성 검증
  Phase 3  기상청 API 연동(api_handler) + Pandas 가공(data_processor)
  Phase 4  한글(HWP) 붙여넣기용 HTML 표 · 보고서용 열 이름 · 서버 지연 예외 처리

실행:  streamlit run app.py
배포:  Cloud Run(컨테이너) + Firebase Hosting + Firestore 캐시 — README.md 참고
"""
from __future__ import annotations

import datetime as dt
import hmac
import html
import os
import pathlib
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from api_handler import (
    METRICS,
    STATIONS,
    WeatherApiError,
    WeatherApiTimeoutError,
    DEFAULT_DAILY_COLS,
    detected_columns,
    fetch_all_stations,
    fetch_apihub_daily_text,
    normals_for_period,
)
import firestore_cache
from data_processor import (
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
    to_tsv,
    valid_days,
    yearly_period_values,
)

BASE_DIR = pathlib.Path(__file__).resolve().parent
SERVER_DELAY_MSG = "기상청 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."
RECENT_YEARS = 5


# =====================================================================
# 설정값(secrets) 읽기
# =====================================================================
# Cloud Run 에서는 Secret Manager 값을 아래 환경변수로 주입합니다.
ENV_NAMES: Dict[str, str] = {"password": "APP_PASSWORD", "api_key": "KMA_API_KEY", "api_source": "API_SOURCE"}


def get_secret(key: str, default: str = "") -> str:
    """환경변수(Cloud Run) → .streamlit/secrets.toml(로컬) 순서로 설정값을 읽는다."""
    env_value = os.environ.get(ENV_NAMES.get(key, key.upper()), "").strip()
    if env_value:
        return env_value
    try:
        value = st.secrets.get(key, default)
    except Exception:
        return default
    return str(value).strip() if value is not None else default


# =====================================================================
# Phase 1. 비밀번호 인증
# =====================================================================
def check_password() -> bool:
    if st.session_state.get("authenticated", False):
        return True

    expected = get_secret("password", "")
    st.markdown("## 🔒 제주도 농업 기상 분석 대시보드")
    if not expected:
        st.error("접속 비밀번호가 설정되지 않았습니다. Cloud Run 의 `APP_PASSWORD` 시크릿(또는 로컬 `.streamlit/secrets.toml` 의 `password`)을 확인해 주세요.")
        return False

    st.caption("대시보드에 접근하려면 비밀번호를 입력하세요.")
    with st.form("login_form"):
        pw = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("로그인")
    if ok:
        if hmac.compare_digest(pw.encode("utf-8"), expected.encode("utf-8")):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    return False


# =====================================================================
# Phase 2. 날짜 유효성 검증
# =====================================================================
def validate_period(start: dt.date, end: dt.date, today: dt.date) -> List[str]:
    warnings: List[str] = []
    if start > end:
        warnings.append("시작일이 종료일보다 늦습니다. 시작일을 종료일 이전으로 설정해 주세요.")
    if start > today or end > today:
        warnings.append(f"미래 날짜는 조회할 수 없습니다. ({today:%Y-%m-%d} 이전 날짜만 선택)")
    if start <= end and (end - start).days + 1 > 366:
        warnings.append("조회 기간이 366일을 초과합니다. 연 단위 비교를 위해 1년 이내로 설정해 주세요.")
    return warnings


# =====================================================================
# Phase 4. 한글(HWP) 붙여넣기용 HTML 표
# =====================================================================
def df_to_hwp_html(df: pd.DataFrame, caption: Optional[str] = None) -> str:
    """모든 셀에 인라인 스타일(테두리·정렬)을 넣어 브라우저 복사 -> 한글 붙여넣기 시 표 유지"""
    if df is None or df.empty:
        return "<p>표시할 자료가 없습니다.</p>"
    table_style = ("border-collapse:collapse;border:1px solid #000;font-family:'맑은 고딕','Malgun Gothic',sans-serif;"
                   "font-size:10pt;margin:4px 0;")
    th_style = "border:1px solid #000;background:#e9eef5;padding:4px 10px;text-align:center;font-weight:bold;white-space:nowrap;"
    td_c = "border:1px solid #000;padding:4px 10px;text-align:center;white-space:nowrap;"
    td_l = "border:1px solid #000;padding:4px 10px;text-align:left;white-space:nowrap;"
    parts: List[str] = [f'<table style="{table_style}">']
    if caption:
        parts.append(f'<caption style="caption-side:top;text-align:left;font-weight:bold;padding:2px 0;">{html.escape(caption)}</caption>')
    parts.append("<thead><tr>")
    for c in df.columns:
        parts.append(f'<th style="{th_style}">{html.escape(str(c))}</th>')
    parts.append("</tr></thead><tbody>")
    for _, row in df.iterrows():
        parts.append("<tr>")
        for i, v in enumerate(row.tolist()):
            style = td_l if i == 0 else td_c
            parts.append(f'<td style="{style}">{html.escape(str(v))}</td>')
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def render_table_block(df_text: pd.DataFrame, title: str, key: str, caption: Optional[str] = None) -> None:
    """HTML 표 + 복사용 탭 텍스트 + CSV 다운로드"""
    st.markdown(df_to_hwp_html(df_text, caption), unsafe_allow_html=True)
    with st.expander(f"'{title}' 복사·다운로드 도구"):
        st.markdown(
            "- **방법 1**: 위 표를 마우스로 드래그 → `Ctrl+C` → 한글에서 `Ctrl+V` (표 형태 유지)\n"
            "- **방법 2**: 아래 상자 우측 상단 복사 아이콘 → 한글에 붙여넣기 → 블록 지정 → **표 → 문자열을 표로** (구분: 탭)"
        )
        st.code(to_tsv(df_text), language="text")
        st.download_button(
            f"CSV 다운로드 ({title})",
            data=df_text.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{title}.csv",
            mime="text/csv",
            key=f"dl_{key}",
        )


# =====================================================================
# Phase 3. 데이터 수집 · 가공 파이프라인
# =====================================================================
def run_pipeline(start: dt.date, end: dt.date, metric_names: List[str], api_key: str, source: str,
                 station_ids: List[int], normal_mode: str, extreme: bool, rain_zero: bool,
                 ratio_all: bool = True) -> Dict[str, Any]:
    """해당 연도 · 전년 · 최근 5년 · 평년 자료를 조회하고 비교표를 생성. 예외는 호출측에서 처리."""
    bar = st.progress(0.0, text="조회 준비 중")
    normal_years = list(range(NORMAL_START_YEAR, NORMAL_END_YEAR + 1))
    total_steps = 1 + RECENT_YEARS + (len(normal_years) if normal_mode == "compute" else (1 if normal_mode == "api" else 0))
    done = {"n": 0}

    def tick(label: str) -> None:
        done["n"] += 1
        bar.progress(min(done["n"] / total_steps, 1.0), text=label)

    def fetch_period(s: dt.date, e: dt.date) -> pd.DataFrame:
        df, _ = fetch_all_stations(s, e, api_key, source, station_ids, rain_zero)
        return df

    # 1) 해당 연도
    current_df, errors = fetch_all_stations(start, end, api_key, source, station_ids, rain_zero,
                                            progress=lambda m: bar.progress(0.0, text=m))
    tick(f"해당 연도 {start.year} 조회 완료")
    current_daily = jeju_daily_mean(current_df)
    daily_raw_text = ""
    if source == "apihub" and station_ids:
        try:
            ymd = start.strftime("%Y%m%d")
            daily_raw_text = fetch_apihub_daily_text(station_ids[0], ymd, ymd, api_key)
        except WeatherApiError:
            daily_raw_text = ""
    current_vals = aggregate_period(current_daily, metric_names, extreme)
    current_days = valid_days(current_daily, metric_names)

    # 2) 최근 5년 (직전 5개년 동일 기간; 첫 해 start.year - 1 이 전년도 자료)
    recent_years = [start.year - k for k in range(1, RECENT_YEARS + 1)]
    recent_df = yearly_period_values(
        fetch_period, start, end, recent_years, metric_names, extreme,
        progress=lambda i, n: bar.progress(min((done["n"] + i) / total_steps, 1.0), text=f"최근 5년: {recent_years[i]}년 조회 중"),
    )
    done["n"] += RECENT_YEARS
    prev_year = start.year - 1
    prev_vals, prev_days = extract_year_values(recent_df, prev_year, metric_names)
    recent_vals, recent_cnt = mean_of_years(recent_df, metric_names)

    # 3) 평년
    normal_vals: Dict[str, float] = {n: float("nan") for n in metric_names}
    normal_cnt: Dict[str, int] = {n: 0 for n in metric_names}
    normal_note = "평년 비교 생략"
    unmapped: List[str] = []
    normal_raw_text = ""
    normal_yearly_df = pd.DataFrame()
    normal_fallback: List[str] = []
    if normal_mode == "api":
        bar.progress(min(done["n"] / total_steps, 1.0), text="평년값(1991~2020) API 조회 중")
        normal_df, unmapped, normal_raw_text = normals_for_period(start, end, api_key, station_ids)
        normal_daily = jeju_daily_mean(normal_df)
        normal_vals = aggregate_period(normal_daily, metric_names, extreme=False)
        normal_cnt = {n: 30 if not pd.isna(normal_vals[n]) else 0 for n in metric_names}
        normal_note = f"기상청 평년값 API ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"
        tick("평년값 조회 완료")
        # 평년값 API 에서 값을 얻지 못한 지표는 과거 30년 실측으로 자동 대체 계산
        fallback_names = [n for n in metric_names if n in unmapped or pd.isna(normal_vals.get(n, float("nan")))]
        if fallback_names:
            normal_yearly_df = yearly_period_values(
                fetch_period, start, end, normal_years, fallback_names, extreme,
                progress=lambda i, n: bar.progress(
                    min((i + 1) / max(n, 1), 1.0),
                    text=f"평년값 API 미제공 지표 대체 계산: {normal_years[i]}년 실측 조회 중 ({i + 1}/{n})",
                ),
            )
            fb_vals, fb_cnt = mean_of_years(normal_yearly_df, fallback_names)
            for n in fallback_names:
                normal_vals[n] = fb_vals.get(n, float("nan"))
                normal_cnt[n] = fb_cnt.get(n, 0)
            normal_fallback = fallback_names
            if len(fallback_names) == len(metric_names):
                normal_note = f"과거 30년 실측 계산 ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"
            else:
                normal_note = f"평년값 API + 30년 실측 보완 ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"
            unmapped = [n for n in fallback_names if pd.isna(normal_vals.get(n, float("nan")))]
    elif normal_mode == "compute":
        normal_yearly_df = yearly_period_values(
            fetch_period, start, end, normal_years, metric_names, extreme,
            progress=lambda i, n: bar.progress(min((done["n"] + i) / total_steps, 1.0), text=f"평년(실측 30년): {normal_years[i]}년 조회 중"),
        )
        done["n"] += len(normal_years)
        normal_vals, normal_cnt = mean_of_years(normal_yearly_df, metric_names)
        normal_note = f"과거 30년 실측 계산 ({NORMAL_START_YEAR}~{NORMAL_END_YEAR})"

    bar.progress(1.0, text="가공 완료")
    comparison = build_comparison_table(
        current_vals, recent_vals, normal_vals, metric_names, start.year,
        extreme=extreme, decimals=1, ratio_all=ratio_all, prev=prev_vals,
    )
    station_table = build_station_table(current_df, metric_names, extreme)
    return {
        "params": {"start": start, "end": end, "metrics": metric_names, "source": source, "extreme": extreme,
                   "normal_mode": normal_mode, "stations": station_ids, "ratio_all": ratio_all},
        "current_df": current_df, "current_daily": current_daily, "current_days": current_days, "errors": errors,
        "prev_year": prev_year, "prev_vals": prev_vals, "prev_days": prev_days,
        "recent_df": recent_df, "recent_cnt": recent_cnt, "recent_years": recent_years,
        "normal_cnt": normal_cnt, "normal_note": normal_note, "unmapped": unmapped,
        "normal_raw_text": normal_raw_text, "normal_yearly_df": normal_yearly_df,
        "normal_fallback": normal_fallback, "daily_raw_text": daily_raw_text,
        "comparison": comparison, "station_table": station_table,
        "fetched_at": dt.datetime.now(),
    }


# =====================================================================
# 결과 화면
# =====================================================================
def render_results(res: Dict[str, Any]) -> None:
    p = res["params"]
    start, end, metric_names = p["start"], p["end"], p["metrics"]
    comparison: pd.DataFrame = res["comparison"]

    if res["errors"]:
        for name, msg in res["errors"].items():
            st.warning(f"{name} 지점: {msg}")
    if res["current_df"].empty:
        st.error("선택한 기간에 조회된 관측 자료가 없습니다. 기간과 API 키를 확인해 주세요.")
        return
    if res.get("normal_fallback") and not res["unmapped"]:
        st.info("평년값 API 응답에 없는 지표(" + ", ".join(res["normal_fallback"]) +
                ")는 1991~2020년 30년 실측 자료로 대체 계산했습니다. (첫 조회만 느리고 이후는 캐시 사용)")
    if res["unmapped"]:
        st.warning("다음 지표는 평년값을 산출하지 못했습니다: " + ", ".join(res["unmapped"]) +
                   " — '5. 원문 응답' 탭의 인식 컬럼 목록을 확인해 주세요.")
    metric_cols_all = [METRICS[n]["col"] for n in metric_names if n in METRICS]
    cur = res["current_df"]
    empty_metrics = [n for n in metric_names
                     if n in METRICS and METRICS[n]["col"] in cur.columns and cur[METRICS[n]["col"]].isna().all()]
    if not cur.empty and metric_cols_all and len(empty_metrics) == len(metric_cols_all):
        st.error("관측 자료(날짜·지점)는 받았지만 선택한 지표 값이 모두 비어 있습니다. "
                 "'5. 원문 응답' 탭의 '일자료 원문' 위쪽 # 줄과 '인식된 컬럼'을 복사해 알려 주세요.")
    elif empty_metrics:
        st.warning("다음 지표는 관측 자료에서 값을 하나도 읽지 못했습니다: " + ", ".join(empty_metrics) +
                   " — '5. 원문 응답' 탭의 '인식된 컬럼'을 확인해 주세요.")

    # 요약 카드
    period_days = (end - start).days + 1
    got_days = int(res["current_daily"]["date"].nunique()) if not res["current_daily"].empty else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("조회 기간", f"{start:%Y.%m.%d} ~ {end:%Y.%m.%d}", f"{period_days}일")
    c2.metric("자료 확보 일수", f"{got_days}일", f"{got_days / period_days * 100:.0f}%")
    c3.metric("전년 / 최근 5년", f"{start.year - 1}년 / {min(res['recent_years'])}~{max(res['recent_years'])}년")
    c4.metric("평년 기준", res["normal_note"].split(" (")[0], f"{NORMAL_START_YEAR}~{NORMAL_END_YEAR}" if res["normal_cnt"] and max(res["normal_cnt"].values()) > 0 else "미포함")

    # ---------------- 핵심 비교표 ----------------
    st.subheader("1. 제주 전체(4개 지점 평균) 기상 비교표")
    agg_desc = "기온·풍속은 기간 평균, 누적 강수량·일조 시간은 기간 합산" + (" / 최고·최저 기온은 기간 극값" if p["extreme"] else "")
    st.caption(f"{agg_desc}. 결측 지점은 제외하고 나머지 지점 평균으로 산출. 대비(편차)=해당 연도−기준값, 대비(비율)=해당 연도÷기준값×100(%). '-' 는 자료 없음.")

    layout_options = {
        "편차 + 비율 모두 표시 (기본)": "all",
        "기준별(전년·최근5년·평년) 편차·비율 나란히": "grouped",
        "대비(비율%) 열만 표시": "ratio_only",
        "한 칸에 병기 — 편차 (비율%)": "combined",
    }
    layout_label = st.radio(
        "표 열 구성 방식 (재조회 없이 즉시 전환)",
        list(layout_options.keys()),
        index=0,
        horizontal=True,
        key="cmp_layout_mode",
    )
    layout_mode = layout_options[layout_label]
    show_text = format_comparison_for_display(comparison, start.year, layout_mode=layout_mode, decimals=1)
    render_table_block(show_text, f"제주기상비교표_{start:%Y%m%d}_{end:%Y%m%d}", "cmp",
                       caption=f"제주 지역 기상 현황 ({start:%Y.%m.%d}~{end:%Y.%m.%d})")

    # 산출 근거
    prev_days_dict = res.get("prev_days", {})
    basis_rows = []
    for n in metric_names:
        basis_rows.append({"구분": f"{n}({METRICS[n]['unit']})",
                           f"해당 연도({start.year}) 유효일수": res["current_days"].get(n, 0),
                           f"전년({start.year - 1}) 유효일수": prev_days_dict.get(n, 0),
                           "최근 5년 유효연수": res["recent_cnt"].get(n, 0),
                           "평년 유효연수": res["normal_cnt"].get(n, 0)})
    with st.expander("산출 근거 (유효 일수·연수)"):
        st.dataframe(pd.DataFrame(basis_rows), hide_index=True)
        st.caption(f"평년: {res['normal_note']} / 조회 시각 {res['fetched_at']:%Y-%m-%d %H:%M}")

    # ---------------- 탭 ----------------
    t1, t2, t3, t4 = st.tabs(["2. 지점별 요약", "3. 일별 자료", "4. 연도별 동기간 집계", "5. 원문 응답"])

    with t1:
        st.caption("지점별 기간 집계와 4개 지점 평균(결측 제외)")
        st_text = format_report(res["station_table"], decimals=1, int_cols=["자료 일수"])
        render_table_block(st_text, f"지점별요약_{start:%Y%m%d}_{end:%Y%m%d}", "stn")

    with t2:
        metric_pick = st.selectbox("지표 선택", metric_names, key="daily_metric")
        daily_tbl = build_daily_table(res["current_df"], res["current_daily"], metric_pick)
        daily_text = format_report(daily_tbl, decimals=1, int_cols=["유효 지점 수"])
        render_table_block(daily_text, f"일별_{metric_pick}_{start:%Y%m%d}_{end:%Y%m%d}", "daily")

    with t3:
        st.caption("전년 및 최근 5년 각 연도의 동일 기간 제주 전체 집계값")
        rec = res["recent_df"]
        if rec.empty:
            st.info("최근 5년 자료가 없습니다.")
        else:
            cols = ["연도", "기간"] + metric_names
            rec_text = format_report(rec[cols].rename(columns={n: f"{n}({METRICS[n]['unit']})" for n in metric_names}),
                                     decimals=1, int_cols=["연도"])
            render_table_block(rec_text, f"최근5년_{start:%Y%m%d}_{end:%Y%m%d}", "recent")
        if not res["normal_yearly_df"].empty:
            st.caption("평년(1991~2020) 각 연도의 동일 기간 집계값")
            nor = res["normal_yearly_df"]
            nor_metrics = [n for n in metric_names if n in nor.columns]
            cols = ["연도", "기간"] + nor_metrics
            nor_text = format_report(nor[cols].rename(columns={n: f"{n}({METRICS[n]['unit']})" for n in nor_metrics}),
                                     decimals=1, int_cols=["연도"])
            render_table_block(nor_text, f"평년30년_{start:%Y%m%d}_{end:%Y%m%d}", "normal30")

    with t4:
        st.caption("해당 연도 표준화 자료 (결측은 빈칸)")
        st.dataframe(res["current_df"], hide_index=True)
        if res.get("daily_raw_text"):
            with st.expander("일자료 API 원문 (첫 지점·시작일 1일치, 앞 6000자)"):
                st.code(res["daily_raw_text"][:6000], language="text")
                st.caption("인식된 컬럼: " + ", ".join(detected_columns(res["daily_raw_text"], DEFAULT_DAILY_COLS)))
        if res["normal_raw_text"]:
            with st.expander("평년값 API 원문 (앞 4000자)"):
                st.code(res["normal_raw_text"][:4000], language="text")
                st.caption("인식된 컬럼: " + ", ".join(detected_columns(res["normal_raw_text"], None)))


# =====================================================================
# 메인
# =====================================================================
def main() -> None:
    st.set_page_config(page_title="제주도 농업 기상 분석 대시보드", page_icon="🌾", layout="wide")

    if not check_password():          # Phase 1 게이트: 통과 전에는 아래 UI 미노출
        st.stop()

    today = dt.date.today()
    default_end = today - dt.timedelta(days=1)
    default_start = default_end - dt.timedelta(days=6)

    # ---------------- Phase 2. 사이드바 ----------------
    with st.sidebar:
        st.header("조회 설정")
        st.subheader("기간")
        start = st.date_input("시작일", value=default_start, max_value=today, key="start_date")
        end = st.date_input("종료일", value=default_end, max_value=today, key="end_date")
        warnings = validate_period(start, end, today)
        for w in warnings:
            st.warning(w)

        st.subheader("지표")
        metric_names: List[str] = [n for n in METRICS if st.checkbox(n, value=True, key=f"m_{n}")]
        if not metric_names:
            st.warning("최소 1개 이상의 지표를 선택해 주세요.")

        st.subheader("옵션")
        station_names = st.multiselect("관측 지점", list(STATIONS.keys()), default=list(STATIONS.keys()))
        station_ids = [STATIONS[n] for n in station_names]
        if not station_ids:
            st.warning("최소 1개 지점을 선택해 주세요.")
        normal_label = st.radio("평년(1991~2020) 산출 방식",
                                ["평년값 API (권장, 빠름)", "과거 30년 실측 계산 (느림, 약 120회 호출)", "평년 비교 생략"], index=0)
        normal_mode_map = {"평년값 API (권장, 빠름)": "api",
                           "과거 30년 실측 계산 (느림, 약 120회 호출)": "compute",
                           "평년 비교 생략": "skip"}
        normal_mode = normal_mode_map[normal_label]
        extreme = st.toggle("최고·최저 기온을 기간 극값으로 표시", value=False)
        rain_zero = st.toggle("기온 관측일의 강수 결측을 0mm로 간주", value=True,
                              help="ASOS 는 무강수일을 공란(-9)으로 제공하므로 기본값 사용을 권장합니다.")
        ratio_all = st.toggle("풍속까지 모든 지표에 대비(비율%) 표시", value=True,
                              help="평균기온·최고기온·최저기온·누적강수량·일조시간은 항상 대비(비율%)를 표시하며, 기본값(켜짐)에서는 풍속까지 모든 지표에 비율(%)을 표시합니다.")

        st.subheader("API")
        source_label = st.radio("데이터 소스", ["기상청 API허브", "공공데이터포털"], index=0 if get_secret("api_source", "apihub") == "apihub" else 1)
        source = "apihub" if source_label == "기상청 API허브" else "datagokr"
        saved_key = get_secret("api_key", "")
        if saved_key:
            st.caption("API 키: 서버 설정에서 로드됨")
            override = st.text_input("다른 키로 덮어쓰기 (선택)", type="password")
            api_key = override.strip() or saved_key
        else:
            api_key = st.text_input("기상청 API 키", type="password", help="서버의 KMA_API_KEY 시크릿(또는 로컬 secrets.toml 의 api_key)에 저장해 두면 편리합니다.").strip()
            if not api_key:
                st.warning("API 키를 입력해 주세요.")
        if normal_mode == "api" and source != "apihub":
            st.info("평년값 API 는 기상청 API허브 전용입니다. 공공데이터포털 소스에서는 '과거 30년 실측 계산' 또는 '평년 비교 생략'을 선택하세요.")

        can_query = (not warnings) and bool(metric_names) and bool(station_ids) and bool(api_key)
        if normal_mode == "api" and source != "apihub":
            can_query = False
        query = st.button("데이터 조회", type="primary", disabled=not can_query)

        col_a, col_b = st.columns(2)
        if col_a.button("캐시 비우기"):
            st.cache_data.clear()
            st.session_state.pop("result", None)
            st.toast("화면 캐시를 비웠습니다. (Firestore 에 저장된 확정 자료는 유지)")
        if col_b.button("로그아웃"):
            st.session_state.clear()
            st.rerun()
        st.caption(f"저장소: {firestore_cache.status()}")

    # ---------------- 메인 화면 ----------------
    st.title("🌾 제주도 농업 기상 분석 대시보드")
    st.caption("기상청 ASOS 일자료 · 제주(184) 고산(185) 성산(188) 서귀포(189)")
    period_txt = f"{start:%Y년 %m월 %d일} ~ {end:%Y년 %m월 %d일}" if start <= end else "기간 오류"
    st.markdown(f"#### 조회 기간: {period_txt}  |  지표: {', '.join(metric_names) if metric_names else '선택 없음'}")

    # ---------------- Phase 3/4. 조회 실행 및 예외 처리 ----------------
    if query and can_query:
        try:
            with st.spinner("기상청 자료를 조회·가공하고 있습니다..."):
                st.session_state["result"] = run_pipeline(start, end, metric_names, api_key, source,
                                                          station_ids, normal_mode, extreme, rain_zero, ratio_all)
        except WeatherApiTimeoutError as exc:
            st.error(SERVER_DELAY_MSG)
            st.caption(f"상세: {exc}")
        except WeatherApiError as exc:
            st.error(f"기상청 API 오류: {exc}")
        except Exception as exc:  # 예상치 못한 오류에도 앱이 멈추지 않도록
            st.error(f"처리 중 오류가 발생했습니다: {exc}")

    result = st.session_state.get("result")
    if result is None:
        st.info("왼쪽 사이드바에서 기간과 지표를 설정한 뒤 **데이터 조회** 버튼을 눌러 주세요.")
        st.markdown(
            """
            | 열 이름 | 의미 |
            |---|---|
            | 구분 | 기상 지표(단위) |
            | 해당 연도 / 전년 | 선택 기간 및 전년도(직전 해) 동일 기간의 제주 전체(4개 지점 평균) 값 |
            | 최근 5년 평균 | 직전 5개년 동일 기간 값의 평균 |
            | 평년 평균 | 1991~2020년 평년값 기준 동일 기간 값 |
            | 전년 대비 / 최근 5년 대비 / 평년 대비 | 해당 연도 − 비교 기준값 (편차) |
            | 전년도 대비(비율) / 최근5년평균대비(비율) / 평년대비(비율) | 해당 연도 ÷ 비교 기준값 × 100 (%) — 평균·최고·최저기온, 강수량, 일조시간, 풍속 모두 산출 |
            """
        )
        return

    rp = result["params"]
    if (rp["start"], rp["end"], tuple(rp["metrics"]), rp["source"], rp["extreme"], rp["normal_mode"], tuple(rp["stations"]), rp.get("ratio_all", True)) !=             (start, end, tuple(metric_names), source, extreme, normal_mode, tuple(station_ids), ratio_all):
        st.warning("사이드바 설정이 변경되었습니다. 아래 표는 이전 조회 조건 기준입니다. **데이터 조회**를 다시 눌러 주세요.")
    render_results(result)


if __name__ == "__main__":
    main()

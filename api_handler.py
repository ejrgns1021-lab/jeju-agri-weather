# -*- coding: utf-8 -*-
"""
api_handler.py
기상청 ASOS 일자료 API 호출 · 응답 파싱 · 캐싱 모듈

지원 데이터 소스
  1) 기상청 API허브 (기본)  - 일자료 기간조회 : kma_sfcdd3.php
                             - 평년값(1991~2020): sfc_norm1.php
  2) 공공데이터포털          - ASOS 일자료 조회서비스 getWthrDataList (JSON)

관측 지점 : 제주(184) · 고산(185) · 성산(188) · 서귀포(189)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

import firestore_cache as fsc

try:  # Streamlit 환경이면 st.cache_data 사용, 아니면(단위 테스트 등) 무시
    import streamlit as st

    cache_data = st.cache_data
except Exception:  # pragma: no cover

    def cache_data(*_args: Any, **_kwargs: Any):
        def _deco(func):
            return func

        return _deco


# ---------------------------------------------------------------------
# 상수 정의
# ---------------------------------------------------------------------
STATIONS: Dict[str, int] = {"제주": 184, "고산": 185, "성산": 188, "서귀포": 189}
STATION_NAMES: Dict[int, str] = {v: k for k, v in STATIONS.items()}

APIHUB_DAILY_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php"
APIHUB_NORMAL_URL = "https://apihub.kma.go.kr/api/typ01/url/sfc_norm1.php"
DATAGOKR_DAILY_URL = "http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"

REQUEST_TIMEOUT_SEC = 20
MAX_RETRY = 2
MAX_DAYS_PER_CALL = 365

# 지표 정의: 표준 컬럼명, 단위, 집계 방법, 물리적 종류(kind), 후보 컬럼명, 범례 키워드
METRICS: Dict[str, Dict[str, Any]] = {
    "평균 기온": {"col": "avgTa", "unit": "℃", "agg": "mean", "kind": "temp",
                 "daily": ["TA_AVG", "avgTa", "TA", "TAVG", "AVG_TA", "TA_MEAN"],
                 "norm": ["TA_AVG", "TA", "TAVG", "TA_NORM", "AVG_TA", "TA_MEAN", "avgTa"],
                 "desc_in": [["평균", "기온"], ["평균", "온도"], ["기온"]],
                 "desc_ex": ["이슬점", "지면", "지중", "초상", "시각", "최고", "최저", "편차", "표준", "일교차", "극값"]},
    "최고 기온": {"col": "maxTa", "unit": "℃", "agg": "mean", "kind": "temp",
                 "daily": ["TA_MAX", "maxTa", "TX", "TMAX", "MAX_TA"],
                 "norm": ["TA_MAX", "TX", "TMAX", "MAX_TA", "TA_MAX_NORM", "maxTa"],
                 "desc_in": [["최고", "기온"], ["최고", "온도"]],
                 "desc_ex": ["시각", "해면", "기압", "습도", "편차", "극값", "지면", "지중"]},
    "최저 기온": {"col": "minTa", "unit": "℃", "agg": "mean", "kind": "temp",
                 "daily": ["TA_MIN", "minTa", "TN", "TMIN", "MIN_TA"],
                 "norm": ["TA_MIN", "TN", "TMIN", "MIN_TA", "TA_MIN_NORM", "minTa"],
                 "desc_in": [["최저", "기온"], ["최저", "온도"]],
                 "desc_ex": ["시각", "초상", "습도", "기압", "편차", "극값", "지면", "지중"]},
    "누적 강수량": {"col": "sumRn", "unit": "mm", "agg": "sum", "kind": "rain",
                   "daily": ["RN_DAY", "sumRn", "RN", "RN_SUM", "PRCP", "RAIN"],
                   "norm": ["RN_DAY", "RN", "RN_SUM", "RN_NORM", "PRCP", "RAIN", "sumRn"],
                   "desc_in": [["일", "강수량"], ["강수량"], ["강수"]],
                   "desc_ex": ["시각", "최다", "9-9", "강도", "1시간", "10분", "60분", "계속", "일수", "편차", "유무", "확률"]},
    "일조 시간": {"col": "sumSsHr", "unit": "hr", "agg": "sum", "kind": "sun",
                 "daily": ["SS_DUR", "SS_DAY", "sumSsHr", "SS", "SS_SUM", "SS_HR", "SUN"],
                 "norm": ["SS_DUR", "SS_DAY", "SS", "SS_SUM", "SS_NORM", "SS_HR", "SUN", "sumSsHr"],
                 "desc_in": [["일조", "합"], ["일조", "시간"], ["일조"]],
                 "desc_ex": ["가조", "캄벨", "시각", "일사", "편차", "율", "%"]},
    "풍속": {"col": "avgWs", "unit": "m/s", "agg": "mean", "kind": "wind",
            "daily": ["WS_AVG", "avgWs", "WS", "WSPD", "AVG_WS", "WS_MEAN"],
            "norm": ["WS_AVG", "WS", "WSPD", "WS_NORM", "AVG_WS", "WS_MEAN", "avgWs"],
            "desc_in": [["평균", "풍속"], ["풍속"]],
            "desc_ex": ["최대", "순간", "시각", "풍정", "편차", "풍향", "습도"]},
}
METRIC_COLS: List[str] = [m["col"] for m in METRICS.values()]
BASE_COLS: List[str] = ["date", "stn_id", "stn_name"]

# API허브 kma_sfcdd3.php 표준 컬럼 순서(help=0 응답 대비 기본값)
DEFAULT_DAILY_COLS: List[str] = [
    "TM", "STN", "WS_AVG", "WR_DAY", "WD_MAX", "WS_MAX", "WS_MAX_TM", "WD_INS", "WS_INS", "WS_INS_TM",
    "TA_AVG", "TA_MAX", "TA_MAX_TM", "TA_MIN", "TA_MIN_TM", "TD_AVG", "TS_AVG", "TG_MIN", "HM_AVG",
    "HM_MIN", "HM_MIN_TM", "PV_AVG", "EV_S", "EV_L", "FG_DUR", "PA_AVG", "PS_AVG", "PS_MAX", "PS_MAX_TM",
    "PS_MIN", "PS_MIN_TM", "CA_TOT", "SS_DAY", "SS_DUR", "SS_CMB", "SI_DAY", "SI_60M_MAX", "SI_60M_MAX_TM",
    "RN_DAY", "RN_D99", "RN_DUR", "RN_60M_MAX", "RN_60M_MAX_TM", "RN_10M_MAX", "RN_10M_MAX_TM",
    "RN_POW_MAX", "RN_POW_MAX_TM", "SD_NEW", "SD_NEW_TM", "SD_MAX", "SD_MAX_TM",
    "TE_05", "TE_10", "TE_15", "TE_30", "TE_50",
]

# API허브 sfc_norm1.php 표준 컬럼 후보
DEFAULT_NORMAL_COLS: List[str] = [
    "ST", "STN", "MM", "DD", "TA", "TA_MAX", "TA_MIN", "HM", "RN",
    "RN_DUR", "EV", "WS", "SS", "SI", "PV",
]


# ---------------------------------------------------------------------
# 예외 정의
# ---------------------------------------------------------------------
class WeatherApiError(Exception):
    """기상청 API 호출/응답 처리 중 발생한 일반 오류"""


class WeatherApiTimeoutError(WeatherApiError):
    """타임아웃 · 서버 점검(5xx) · 연결 실패 등 서버 응답 지연 오류"""


# ---------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------
def empty_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BASE_COLS + METRIC_COLS)


def decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _http_get(url: str, params: Dict[str, Any], timeout: int = REQUEST_TIMEOUT_SEC) -> requests.Response:
    """타임아웃/서버 점검을 WeatherApiTimeoutError 로, 기타 오류를 WeatherApiError 로 변환"""
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRY + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            time.sleep(1.0)
            continue
        except requests.exceptions.ConnectionError as exc:
            raise WeatherApiTimeoutError(f"기상청 서버에 연결할 수 없습니다. ({exc.__class__.__name__})") from exc
        except requests.exceptions.RequestException as exc:
            raise WeatherApiError(f"요청 오류: {exc}") from exc

        if resp.status_code >= 500:
            raise WeatherApiTimeoutError(f"기상청 서버 점검 또는 장애 (HTTP {resp.status_code})")
        if resp.status_code in (401, 403):
            raise WeatherApiError(f"인증 실패 (HTTP {resp.status_code}) - API 키를 확인해 주세요.")
        if resp.status_code != 200:
            raise WeatherApiError(f"HTTP {resp.status_code} 오류: {resp.text[:200]}")
        return resp

    raise WeatherApiTimeoutError(f"요청 시간 초과 ({timeout}초, {MAX_RETRY + 1}회 시도) - {last_exc}")


def clean_numeric(series: pd.Series, kind: str) -> pd.Series:
    """
    결측 및 물리적 범위 정제 규칙
      - 공백 / '-' / 'null' / 'None' / 변환 불가 문자열 -> NaN
      - 기상청 결측 코드 -9, -99, -999 (및 소수 표기) -> NaN
      - 기온(kind='temp')  : -50℃ 이하 또는 60℃ 이상 -> NaN
      - 풍속(kind='wind')  : 음수 또는 45 m/s 초과 -> NaN (상대습도 66% 등 오매칭 차단)
      - 일조(kind='sun')   : 음수 또는 16 hr/일 초과 -> NaN
      - 강수(kind='rain')  : 음수 또는 1500 mm/일 초과 -> NaN
      - 기타 비음수        : 음수 -> NaN
    """
    if series is None:
        return pd.Series(dtype=float)
    txt = series.astype(str).str.strip()
    blank = txt.isin(["", "-", "null", "None", "NULL", "nan", "NaN"])
    num = pd.to_numeric(txt.mask(blank, None), errors="coerce")
    missing_codes = num.isin([-9.0, -99.0, -999.0, -9.9, -99.9, -999.9])
    num = num.mask(missing_codes, np.nan)
    if kind == "temp":
        num = num.mask((num <= -50.0) | (num >= 60.0), np.nan)
    elif kind == "wind":
        num = num.mask((num < 0.0) | (num > 45.0), np.nan)
    elif kind == "sun":
        num = num.mask((num < 0.0) | (num > 16.0), np.nan)
    elif kind == "rain":
        num = num.mask((num < 0.0) | (num > 1500.0), np.nan)
    else:
        num = num.mask(num < 0.0, np.nan)
    return num.astype(float)


def _find_col(columns: List[str], candidates: List[str]) -> Optional[str]:
    upper = {str(c).upper(): str(c) for c in columns}
    for cand in candidates:
        if cand.upper() in upper:
            return upper[cand.upper()]
    return None


def _pick_sunshine_col(raw: pd.DataFrame, cols: List[str], candidates: List[str],
                       legend: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    일자료/평년값에 SS_DAY(가조시간 또는 일조시간)와 SS_DUR(합계 일조시간 또는 가조시간) 두 컬럼이
    함께 있을 때, 가조시간(낮의 길이, 10~14hr)이 아닌 실제 일조시간(항상 가조시간 이하)을 선택한다.
    """
    if legend:
        # 범례 설명에 '가조'가 없는 일조 컬럼 우선
        for c in ("SS_DUR", "SS_DAY", "SS"):
            found = _find_col(cols, [c])
            if found:
                desc = legend.get(found.upper(), "")
                if "일조" in desc and "가조" not in desc and "캄벨" not in desc and "율" not in desc:
                    return found
    a = _find_col(cols, ["SS_DUR"])
    b = _find_col(cols, ["SS_DAY"])
    if a and b and a in raw.columns and b in raw.columns:
        va = clean_numeric(raw[a], "sun").mean()
        vb = clean_numeric(raw[b], "sun").mean()
        if pd.isna(va):
            return b
        if pd.isna(vb):
            return a
        return a if va <= vb else b
    return None


def _find_metric_col(columns: List[str], meta: Dict[str, Any], key: str,
                     legend: Optional[Dict[str, str]] = None,
                     raw: Optional[pd.DataFrame] = None) -> Optional[str]:
    """
    지표 컬럼 탐색
      1) 일조 시간은 SS_DUR / SS_DAY 중 가조시간이 아닌 실제 일조시간 우선 선택
      2) 후보 컬럼명(meta[key]) 과 대소문자 무시 정확 일치 (단, 범례에 제외 키워드가 있으면 건너뜀)
      3) help=1 범례 설명문 키워드 매칭 (desc_in 의 한 묶음이 모두 포함 & desc_ex 미포함)
    """
    if meta["col"] == "sumSsHr" and raw is not None:
        picked = _pick_sunshine_col(raw, columns, meta.get(key, []), legend)
        if picked is not None:
            return picked

    desc_map = {str(c).upper(): re.sub(r"\s+", "", legend.get(str(c).upper(), "")) for c in columns} if legend else {}
    for cand in meta.get(key, []):
        found = _find_col(columns, [cand])
        if found is not None:
            desc = desc_map.get(found.upper(), "")
            if desc and any(ex in desc for ex in meta.get("desc_ex", [])):
                continue
            return found

    if not legend:
        return None
    for group in meta.get("desc_in", []):
        for c in columns:
            desc = desc_map.get(str(c).upper(), "")
            if not desc:
                continue
            if all(tok in desc for tok in group) and not any(ex in desc for ex in meta.get("desc_ex", [])):
                return str(c)
    return None


# ---------------------------------------------------------------------
# API허브 텍스트 응답 파서
# ---------------------------------------------------------------------
# 번호가 있는 범례('# 1. TM : ...' 또는 '# 7. WS_MAX TM : ...')와 일반 범례('# TA_AVG : ...')
_NUMBERED_LEGEND_PAT = re.compile(r"^#\s*(\d+)\s*[.)]\s*([A-Za-z][A-Za-z0-9_\s-]*?)\s*:\s*(.*)$")
_PLAIN_LEGEND_PAT = re.compile(r"^#\s*([A-Za-z][A-Za-z0-9_]*?)\s*:\s*(.*)$")
_TM_ALIASES = ("YYMMDD", "YYYYMMDD", "DATE", "TM_DAY", "YMD")
_NON_COLUMN_LEGEND_KEYS = {"HELP", "NOTE", "UNIT", "URL", "REQUEST", "SOURCE", "FORMAT", "DESC", "DESCRIPTION"}


def _norm_name(token: str) -> str:
    u = re.sub(r"[\s-]+", "_", token.strip()).upper()
    return "TM" if u in _TM_ALIASES else u


def _legend_entries(comment_lines: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """
    주석에서 (순서 보존 컬럼명 리스트, {컬럼명: 설명} 딕셔너리) 를 추출.
    - 번호 범례('# 1. ...')가 있으면 번호 순서대로 정렬하며, 동일 이름이 중복되어도 리스트 길이를 유지해
      열 밀림(Column Shift)이 발생하지 않도록 한다.
    """
    numbered: List[Tuple[int, str, str]] = []
    plain: List[Tuple[str, str]] = []
    for line in comment_lines:
        s = line.strip()
        m_num = _NUMBERED_LEGEND_PAT.match(s)
        if m_num:
            idx = int(m_num.group(1))
            name = _norm_name(m_num.group(2))
            desc = m_num.group(3).strip()
            numbered.append((idx, name, desc))
            continue
        m_plain = _PLAIN_LEGEND_PAT.match(s)
        if m_plain:
            name = _norm_name(m_plain.group(1))
            if name in _NON_COLUMN_LEGEND_KEYS:
                continue
            desc = m_plain.group(2).strip()
            plain.append((name, desc))

    ordered_names: List[str] = []
    legend_map: Dict[str, str] = {}
    if numbered:
        numbered.sort(key=lambda x: x[0])
        seen_idx: set = set()
        seen_name: Dict[str, int] = {}
        for idx, name, desc in numbered:
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
            if name in seen_name:
                seen_name[name] += 1
                col_name = f"{name}_{seen_name[name]}"
            else:
                seen_name[name] = 0
                col_name = name
            ordered_names.append(col_name)
            legend_map[col_name] = desc
            legend_map.setdefault(name, desc)
    else:
        seen_name = {}
        for name, desc in plain:
            if name in seen_name:
                seen_name[name] += 1
                col_name = f"{name}_{seen_name[name]}"
            else:
                seen_name[name] = 0
                col_name = name
            ordered_names.append(col_name)
            legend_map[col_name] = desc
            legend_map.setdefault(name, desc)
    return ordered_names, legend_map


def _legend_from_comments(comment_lines: List[str]) -> Dict[str, str]:
    """하위 호환용: {컬럼명: 설명} 반환"""
    _, legend_map = _legend_entries(comment_lines)
    return legend_map


def _merge_two_line_header(line1: str, line2: str) -> List[str]:
    """
    2줄로 나뉜 헤더(예: '# YYMMDD STN WS TA ...' / '# KST ID AVG AVG ...')를
    문자 위치 기준으로 병합하여 'WS_AVG', 'TA_AVG' 형태의 컬럼명을 생성한다.
    """
    b1 = line1.lstrip("#")
    b2 = line2.lstrip("#")
    off1 = len(line1) - len(b1)
    off2 = len(line2) - len(b2)
    top = [(m.start() + off1, m.end() + off1, m.group(0).upper()) for m in re.finditer(r"\S+", b1)]
    bottom = [(m.start() + off2, m.end() + off2, m.group(0).upper()) for m in re.finditer(r"\S+", b2)]
    if not top:
        return []
    # 만약 두 줄의 토큰 개수가 정확히 같다면 1:1 위치 결합을 우선 사용
    if len(top) == len(bottom):
        names: List[str] = []
        for i, ((_, _, ttxt), (_, _, btxt)) in enumerate(zip(top, bottom)):
            if i == 0 and ttxt in _TM_ALIASES + ("TM",):
                names.append("TM")
            elif ttxt in ("STN", "STN_ID"):
                names.append("STN")
            elif ttxt in ("MM", "DD", "MONTH", "DAY", "ST", "TMST") and btxt in ("ID", "MON", "DAY", "ST", "YR"):
                names.append(ttxt)
            elif btxt in ("ID", "KST", "-") or btxt == ttxt:
                names.append(_norm_name(ttxt))
            else:
                names.append(f"{ttxt}_{btxt}")
        return names

    parts: List[List[str]] = [[] for _ in top]
    for bs, be, btxt in bottom:
        best_i, best_score = 0, None
        for i, (ts, te, _t) in enumerate(top):
            overlap = min(be, te) - max(bs, ts)
            dist = abs((bs + be) / 2.0 - (ts + te) / 2.0)
            score = (-overlap if overlap > 0 else 0, dist)
            if best_score is None or score < best_score:
                best_i, best_score = i, score
        parts[best_i].append(btxt)
    names = []
    for i, (_ts, _te, ttxt) in enumerate(top):
        if i == 0 and ttxt in _TM_ALIASES + ("TM",):
            names.append("TM")
        elif ttxt in ("STN", "STN_ID"):
            names.append("STN")
        elif ttxt in ("MM", "DD", "MONTH", "DAY", "ST", "TMST"):
            names.append(ttxt)
        else:
            sub = [p for p in parts[i] if p not in ("ID", "KST", "-")]
            names.append("_".join([ttxt] + sub) if sub else ttxt)
    return names


def _header_candidates(comment_lines: List[str]) -> List[Tuple[str, List[str]]]:
    """':' 없이 식별자 토큰만 나열된 주석 행(= 컬럼 헤더 행) 후보. (원문, 토큰목록)"""
    raw_headers: List[Tuple[str, List[str]]] = []
    unit_tokens = {"C", "M/S", "HR", "MM", "%", "HPA", "CM", "MJ/M2", "MJ/M", "1/10", "16", "M", "MM/H"}
    for line in comment_lines:
        body = line.lstrip("#").strip()
        if not body or ":" in body:
            continue
        if "START7777" in body or "7777END" in body:
            continue
        if set(body) <= set("-=_ "):
            continue
        tokens = body.replace(",", " ").split()
        if len(tokens) < 3:
            continue
        uppers = [t.upper() for t in tokens]
        # 단위 행(C, m/s, hr, mm 등이 절반 이상인 줄)은 헤더에서 제외
        if sum(1 for u in uppers if u in unit_tokens) >= max(3, len(uppers) // 2):
            continue
        if all(re.fullmatch(r"[A-Za-z0-9_./()%-]+", t) for t in tokens):
            raw_headers.append((line, [_norm_name(t) for t in tokens]))
    return raw_headers


def _fit(cols: List[str], ncols: int) -> List[str]:
    if len(cols) >= ncols:
        return list(cols[:ncols])
    return list(cols) + [f"COL_{i}" for i in range(len(cols) + 1, ncols + 1)]


def _unique(cols: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    uniq: List[str] = []
    for c in cols:
        if c in seen:
            seen[c] += 1
            uniq.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            uniq.append(c)
    return uniq


def column_strategies(comment_lines: List[str], ncols: int,
                      default_cols: Optional[List[str]]) -> List[Tuple[str, List[str]]]:
    """
    컬럼명 결정 전략을 우선순위 순으로 모두 반환 [(전략명, 컬럼목록)].
      1) 범례 순서(열 수 일치)
      2) 2줄 분할 헤더 병합(열 수 일치)
      3) 단일 헤더 행(열 수 일치 & 중복 토큰 없음)
      4) 기본 컬럼 목록(열 수 일치)
      5) 단일 헤더 행(열 수 일치)
      6) 2줄 분할 헤더 병합(자르기/채우기) · 범례 순서(불일치) · 헤더 행(불일치)
      7) 기본 컬럼 목록 · COL_1.. 임시 이름
    """
    strategies: List[Tuple[str, List[str]]] = []
    headers = _header_candidates(comment_lines)
    legend_names, _ = _legend_entries(comment_lines)

    # 1) 범례 순서(열 수 정확히 일치)
    if legend_names and len(legend_names) == ncols:
        strategies.append(("범례 순서(열 수 일치)", list(legend_names)))

    # 2) 인접한 2줄 헤더 병합
    merged_list: List[List[str]] = []
    for i in range(len(headers) - 1):
        l1, t1 = headers[i]
        l2, _t2 = headers[i + 1]
        if any(k in t1 for k in ("STN", "STN_ID", "TM")):
            mcols = _merge_two_line_header(l1, l2)
            if mcols:
                merged_list.append(mcols)
                if len(mcols) == ncols:
                    strategies.append(("2줄 헤더 병합(열 수 일치)", list(mcols)))

    # 3) 단일 헤더 행(열 수 일치 & 중복 토큰 없음)
    for _raw_line, toks in headers:
        if len(toks) == ncols and len(set(toks)) == len(toks):
            strategies.append(("단일 헤더 행(열 수·고유성 일치)", list(toks)))

    # 4) 기본 컬럼 목록(열 수 정확히 일치)
    if default_cols and len(default_cols) == ncols:
        strategies.append(("기본 컬럼 목록(열 수 일치)", list(default_cols)))

    # 5) 단일 헤더 행(열 수 일치, 중복 토큰 허용 - 첫 행 우선)
    for _raw_line, toks in headers:
        if len(toks) == ncols:
            strategies.append(("헤더 행(열 수 일치)", list(toks)))

    # 6) 열 수 불일치 보정 후보들
    for mcols in merged_list:
        if len(mcols) != ncols:
            strategies.append((f"2줄 헤더 병합({len(mcols)}/{ncols})", _fit(mcols, ncols)))
    if legend_names and len(legend_names) != ncols and len(legend_names) >= 3:
        strategies.append((f"범례 순서({len(legend_names)}/{ncols})", _fit(legend_names, ncols)))
    for _raw_line, toks in headers:
        if len(toks) != ncols:
            strategies.append((f"헤더 행({len(toks)}/{ncols})", _fit(toks, ncols)))
    if default_cols:
        strategies.append(("기본 컬럼 목록", _fit(default_cols, ncols)))
    strategies.append(("임시 이름(COL_n)", [f"COL_{i}" for i in range(1, ncols + 1)]))

    out: List[Tuple[str, List[str]]] = []
    seen_keys = set()
    for name, cols in strategies:
        key = tuple(cols)
        if key not in seen_keys:
            seen_keys.add(key)
            out.append((name, cols))
    return out


def _columns_from_comments(comment_lines: List[str], ncols: int, default_cols: Optional[List[str]]) -> List[str]:
    """(하위 호환) 1순위 전략의 컬럼 목록"""
    return column_strategies(comment_lines, ncols, default_cols)[0][1]


def split_kma_text(text: str) -> Tuple[List[str], List[List[str]]]:
    """API허브 typ01 텍스트를 (주석 행 목록, 데이터 토큰 행 목록) 으로 분리"""
    comments: List[str] = []
    rows: List[List[str]] = []
    if not text or not text.strip():
        return comments, rows
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            comments.append(line)
            continue
        line = line.rstrip("=").strip().rstrip(",")
        if not line:
            continue
        tokens = [t for t in line.replace(",", " ").split() if t != "="]
        if tokens:
            rows.append(tokens)
    return comments, rows


def frame_from_rows(rows: List[List[str]], cols: List[str]) -> pd.DataFrame:
    ncols = len(cols)
    fixed = [r + [None] * (ncols - len(r)) if len(r) < ncols else r[:ncols] for r in rows]
    return pd.DataFrame(fixed, columns=_unique(cols))


def parse_kma_text(text: str, default_cols: Optional[List[str]] = None,
                   strategy_index: int = 0) -> pd.DataFrame:
    """
    API허브 typ01 텍스트(#START7777 ... #7777END)를 문자열 DataFrame 으로 변환.
    df.attrs 에 legend(범례 dict), strategy(사용 전략명), n_strategies 를 담는다.
    """
    comments, rows = split_kma_text(text)
    if not rows:
        return pd.DataFrame()
    ncols = max(len(r) for r in rows)
    strategies = column_strategies(comments, ncols, default_cols)
    idx = min(max(strategy_index, 0), len(strategies) - 1)
    name, cols = strategies[idx]
    df = frame_from_rows(rows, cols)
    df.attrs["legend"] = _legend_from_comments(comments)
    df.attrs["strategy"] = name
    df.attrs["n_strategies"] = len(strategies)
    return df


def _detect_text_error(text: str) -> Optional[str]:
    low = text.lower()
    for kw in ("인증키", "authkey", "unauthorized", "invalid key", "error", "에러", "오류"):
        if kw in low and not re.search(r"^\d{8}\s", text, re.M):
            return text.strip()[:300]
    return None


# ---------------------------------------------------------------------
# 표준 스키마 변환
# ---------------------------------------------------------------------
def standardize_daily(raw: pd.DataFrame, source: str, stn_default: int,
                      rain_missing_as_zero: bool = True,
                      legend: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """원시 응답 -> [date, stn_id, stn_name, avgTa, maxTa, minTa, sumRn, sumSsHr, avgWs]
    df.attrs["mapping"] 에 {지표명: 사용한 원본 컬럼 또는 None} 을 기록."""
    if raw is None or raw.empty:
        return empty_daily_frame()
    if legend is None:
        legend = raw.attrs.get("legend") if hasattr(raw, "attrs") else None
    cols = list(raw.columns)
    out = pd.DataFrame(index=raw.index)

    tm_col = _find_col(cols, ["TM", "tm", "YYMMDD", "YYYYMMDD", "DATE", "YMD"])
    if tm_col is None:
        for c in cols:
            s = raw[c].astype(str).str.strip()
            if s.str.fullmatch(r"(19|20)\d{6}").mean() > 0.8:
                tm_col = c
                break
    if tm_col is None:
        return empty_daily_frame()
    digits = raw[tm_col].astype(str).str.replace(r"[^0-9]", "", regex=True).str[:8]
    out["date"] = pd.to_datetime(digits, format="%Y%m%d", errors="coerce").dt.date

    stn_col = _find_col(cols, ["STN", "stnId", "STN_ID"])
    if stn_col is not None:
        out["stn_id"] = pd.to_numeric(raw[stn_col], errors="coerce").fillna(stn_default).astype(int)
    else:
        out["stn_id"] = int(stn_default)
    out["stn_name"] = out["stn_id"].map(STATION_NAMES).fillna("기타")

    mapping: Dict[str, Optional[str]] = {}
    for name, meta in METRICS.items():
        src_col = _find_metric_col(cols, meta, "daily", legend, raw=raw)
        mapping[name] = src_col
        out[meta["col"]] = clean_numeric(raw[src_col], meta["kind"]) if src_col else np.nan

    # 무강수일 처리: 기온 관측이 정상인 날의 강수 결측(공란/-9)은 0.0mm 로 간주
    if rain_missing_as_zero:
        observed = out["avgTa"].notna() | out["maxTa"].notna() | out["minTa"].notna()
        out.loc[observed & out["sumRn"].isna(), "sumRn"] = 0.0

    out = out.dropna(subset=["date"]).drop_duplicates(subset=["date", "stn_id"])
    out = out.sort_values(["date", "stn_id"]).reset_index(drop=True)
    out.attrs["mapping"] = mapping
    return out


def _score_daily(std: pd.DataFrame) -> float:
    """표준화 결과의 신뢰도 점수: 유효 지표 값 개수 + 물리적 일관성 보너스/페널티"""
    if std is None or std.empty:
        return 0.0
    score = float(std[METRIC_COLS].notna().sum().sum())
    ok = (std["minTa"] <= std["avgTa"]) & (std["avgTa"] <= std["maxTa"])
    score += 2.0 * float(ok.sum())
    bad = (std["minTa"] > std["maxTa"]) | (std["minTa"] > std["avgTa"]) | (std["avgTa"] > std["maxTa"])
    score -= 8.0 * float(bad.sum())
    # 풍속 평균이 20 m/s 를 넘으면 상대습도(HM)·풍정(WR) 등 오매핑일 가능성이 높으므로 감점
    if std["avgWs"].notna().any():
        ws_mean = float(std["avgWs"].mean())
        if ws_mean > 20.0:
            score -= 50.0
    return score


def parse_daily_text_best(text: str, stn_default: int, rain_missing_as_zero: bool = True,
                          default_cols: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    컬럼 전략을 순서대로 시도하여 지표 값이 가장 많이 복원되는 결과를 채택.
    반환: (표준 DataFrame, 진단 dict)
    """
    default_cols = default_cols if default_cols is not None else DEFAULT_DAILY_COLS
    comments, rows = split_kma_text(text)
    diag: Dict[str, Any] = {"n_rows": len(rows), "ncols": 0, "legend_count": 0, "tried": [],
                            "chosen": None, "columns": [], "mapping": {}, "sample": {}}
    if not rows:
        return empty_daily_frame(), diag
    ncols = max(len(r) for r in rows)
    legend = _legend_from_comments(comments)
    diag["ncols"] = ncols
    diag["legend_count"] = len(legend)
    diag["header_lines"] = [ln for ln, _ in _header_candidates(comments)]

    best_df, best_score, best_name, best_cols = empty_daily_frame(), -1e9, None, []
    for name, cols in column_strategies(comments, ncols, default_cols):
        raw = frame_from_rows(rows, cols)
        std = standardize_daily(raw, "apihub", stn_default, rain_missing_as_zero, legend)
        sc = _score_daily(std)
        diag["tried"].append({"strategy": name, "score": sc, "rows": int(len(std))})
        if sc > best_score:
            best_df, best_score, best_name, best_cols = std, sc, name, list(raw.columns)
    diag["chosen"] = best_name
    diag["columns"] = best_cols
    diag["mapping"] = best_df.attrs.get("mapping", {}) if hasattr(best_df, "attrs") else {}
    if not best_df.empty:
        first = best_df.iloc[0]
        diag["sample"] = {"date": str(first["date"]), "stn_id": int(first["stn_id"]),
                          **{n: (None if pd.isna(first[METRICS[n]["col"]]) else float(first[METRICS[n]["col"]])) for n in METRICS}}
    best_df.attrs["diag"] = diag
    return best_df, diag


# ---------------------------------------------------------------------
# 일자료 조회 (소스별) - st.cache_data 적용
# ---------------------------------------------------------------------
def fetch_apihub_daily_text(stn_id: int, start_ymd: str, end_ymd: str, api_key: str) -> str:
    """일자료 API 원문 텍스트 (캐시 없음, 진단용)"""
    params = {"tm1": start_ymd, "tm2": end_ymd, "stn": str(stn_id), "help": "1", "authKey": api_key}
    resp = _http_get(APIHUB_DAILY_URL, params)
    return decode_bytes(resp.content)


@cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_apihub_daily(stn_id: int, start_ymd: str, end_ymd: str, api_key: str,
                       rain_missing_as_zero: bool = True) -> pd.DataFrame:
    cache_key = ("apihub", stn_id, start_ymd, end_ymd, int(rain_missing_as_zero))
    cached = fsc.get_df(fsc.DAILY_COLLECTION, *cache_key)
    if cached is not None:
        return cached
    text = fetch_apihub_daily_text(stn_id, start_ymd, end_ymd, api_key)
    std, _diag = parse_daily_text_best(text, stn_id, rain_missing_as_zero, DEFAULT_DAILY_COLS)
    if std.empty:
        err = _detect_text_error(text)
        if err:
            raise WeatherApiError(f"API허브 응답 오류: {err}")
        return empty_daily_frame()
    if fsc.is_finalized(end_ymd):
        fsc.put_df(fsc.DAILY_COLLECTION, std, *cache_key, meta={"stn_id": stn_id, "start": start_ymd, "end": end_ymd})
    return std


@cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_datagokr_daily(stn_id: int, start_ymd: str, end_ymd: str, api_key: str,
                         rain_missing_as_zero: bool = True) -> pd.DataFrame:
    cache_key = ("datagokr", stn_id, start_ymd, end_ymd, int(rain_missing_as_zero))
    cached = fsc.get_df(fsc.DAILY_COLLECTION, *cache_key)
    if cached is not None:
        return cached
    key = urllib.parse.unquote(api_key) if "%" in api_key else api_key
    items: List[Dict[str, Any]] = []
    page = 1
    while True:
        params = {"serviceKey": key, "pageNo": str(page), "numOfRows": "999", "dataType": "JSON",
                  "dataCd": "ASOS", "dateCd": "DAY", "startDt": start_ymd, "endDt": end_ymd,
                  "stnIds": str(stn_id)}
        resp = _http_get(DATAGOKR_DAILY_URL, params)
        try:
            js = resp.json()
        except ValueError:
            snippet = decode_bytes(resp.content)[:300]
            raise WeatherApiError(f"공공데이터포털 응답이 JSON 이 아닙니다(키 오류 가능): {snippet}")
        header = js.get("response", {}).get("header", {})
        code = str(header.get("resultCode", ""))
        if code == "03":  # NODATA_ERROR
            break
        if code != "00":
            raise WeatherApiError(f"공공데이터포털 오류({code}): {header.get('resultMsg', '')}")
        body = js.get("response", {}).get("body", {})
        got = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
        if isinstance(got, dict):
            got = [got]
        items.extend(got)
        total = int(body.get("totalCount", 0) or 0)
        if not got or page * 999 >= total:
            break
        page += 1
    if not items:
        return empty_daily_frame()
    raw = pd.DataFrame(items)
    std = standardize_daily(raw, "datagokr", stn_id, rain_missing_as_zero)
    if fsc.is_finalized(end_ymd):
        fsc.put_df(fsc.DAILY_COLLECTION, std, *cache_key, meta={"stn_id": stn_id, "start": start_ymd, "end": end_ymd})
    return std


def split_period(start: dt.date, end: dt.date, max_days: int = MAX_DAYS_PER_CALL) -> List[Tuple[dt.date, dt.date]]:
    chunks: List[Tuple[dt.date, dt.date]] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=max_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + dt.timedelta(days=1)
    return chunks


def fetch_all_stations(start: dt.date, end: dt.date, api_key: str, source: str = "apihub",
                       station_ids: Optional[List[int]] = None, rain_missing_as_zero: bool = True,
                       progress: Optional[Callable[[str], None]] = None) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """
    선택 지점의 일자료를 순차 조회하여 하나의 표준 DataFrame 으로 결합.
    """
    station_ids = station_ids or list(STATIONS.values())
    fetch_fn = fetch_apihub_daily if source == "apihub" else fetch_datagokr_daily
    frames: List[pd.DataFrame] = []
    errors: Dict[str, str] = {}
    for stn_id in station_ids:
        name = STATION_NAMES.get(stn_id, str(stn_id))
        parts: List[pd.DataFrame] = []
        for s, e in split_period(start, end):
            if progress:
                progress(f"{name}({stn_id}) {s:%Y-%m-%d} ~ {e:%Y-%m-%d} 조회 중")
            try:
                part = fetch_fn(stn_id, s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), api_key, rain_missing_as_zero)
            except WeatherApiTimeoutError:
                raise
            except WeatherApiError as exc:
                errors[name] = str(exc)
                continue
            if part is not None and not part.empty:
                parts.append(part)
        if parts:
            frames.append(pd.concat(parts, ignore_index=True))
        elif name not in errors:
            errors[name] = "조회된 자료가 없습니다."
    if not frames:
        return empty_daily_frame(), errors
    merged = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["date", "stn_id"])
    return merged.sort_values(["date", "stn_id"]).reset_index(drop=True), errors


# ---------------------------------------------------------------------
# 평년값 (API허브 sfc_norm1.php, 1991~2020 평년)
# ---------------------------------------------------------------------
def _validate_normal_series(series: pd.Series, kind: str) -> pd.Series:
    """
    평년값(1991~2020 30년 평균) 물리적 유효성 검증.
    - 평년 일평균 풍속(wind)은 국내 전 지점에서 0.2 ~ 15.0 m/s 범위이다.
      열 밀림으로 상대습도(HM, 50~85%)나 풍향(WD)이 매핑된 경우(평균 > 15.0) 즉시 NaN 처리한다.
    - 평년 일조시간(sun)은 0.5 ~ 14.0 hr/일 범위이다.
    """
    cleaned = clean_numeric(series, kind)
    valid = cleaned.dropna()
    if valid.empty:
        return cleaned
    mean_val = float(valid.mean())
    if kind == "wind" and (mean_val > 15.0 or mean_val < 0.1):
        return pd.Series(np.nan, index=series.index, dtype=float)
    if kind == "sun" and (mean_val > 14.5 or mean_val < 0.1):
        return pd.Series(np.nan, index=series.index, dtype=float)
    if kind == "temp" and (mean_val > 38.0 or mean_val < -25.0):
        return pd.Series(np.nan, index=series.index, dtype=float)
    return cleaned


def _extract_normals_from_raw(raw: pd.DataFrame, legend: Dict[str, str],
                              station_ids: List[int]) -> Tuple[Optional[pd.DataFrame], Dict[str, Optional[str]], float]:
    """단일 컬럼 전략으로 파싱된 평년 raw DataFrame 에서 지점·월일·6개 지표를 추출하고 신뢰도 점수를 계산"""
    if raw is None or raw.empty:
        return None, {}, -1e9
    cols = list(raw.columns)
    stn_col = _find_col(cols, ["STN", "STN_ID", "stnId", "STN_NO", "STATION"])
    if stn_col is None:
        for c in cols:
            vals = pd.to_numeric(raw[c], errors="coerce")
            if vals.isin(list(STATIONS.values())).any() and vals.dropna().between(90, 300).all():
                stn_col = c
                break
    if stn_col is None:
        return None, {}, -1e9

    work = raw.copy()
    work["stn_id"] = pd.to_numeric(work[stn_col], errors="coerce")
    work = work[work["stn_id"].isin(station_ids)].copy()
    if work.empty:
        return None, {}, -1e9
    work["stn_id"] = work["stn_id"].astype(int)

    mm_col = _find_col(cols, ["MM", "MONTH", "MON", "MM1"])
    dd_col = _find_col(cols, ["DD", "DAY", "DD1"])
    tm_col = _find_col(cols, ["TM", "MMDD", "DATE", "YYMMDD", "TM_DAY"])
    if mm_col and dd_col:
        mm = pd.to_numeric(work[mm_col], errors="coerce")
        dd = pd.to_numeric(work[dd_col], errors="coerce")
    elif tm_col:
        digits = work[tm_col].astype(str).str.replace(r"[^0-9]", "", regex=True).str[-4:]
        mm = pd.to_numeric(digits.str[:2], errors="coerce")
        dd = pd.to_numeric(digits.str[2:4], errors="coerce")
    else:
        return None, {}, -1e9

    valid_md = mm.between(1, 12) & dd.between(1, 31)
    if not valid_md.any():
        return None, {}, -1e9
    work = work[valid_md].copy()
    work["_mmdd"] = mm[valid_md].astype(int) * 100 + dd[valid_md].astype(int)

    norm = pd.DataFrame({"stn_id": work["stn_id"], "_mmdd": work["_mmdd"]})
    mapping: Dict[str, Optional[str]] = {}
    for name, meta in METRICS.items():
        src = _find_metric_col(cols, meta, "norm", legend, raw=work)
        if src is None:
            mapping[name] = None
            norm[meta["col"]] = np.nan
        else:
            s_val = _validate_normal_series(work[src], meta["kind"])
            if s_val.isna().all():
                mapping[name] = None
                norm[meta["col"]] = np.nan
            else:
                mapping[name] = src
                norm[meta["col"]] = s_val

    score = float(norm[METRIC_COLS].notna().sum().sum())
    ok = (norm["minTa"] <= norm["avgTa"]) & (norm["avgTa"] <= norm["maxTa"])
    score += 3.0 * float(ok.sum())
    bad = (norm["minTa"] > norm["maxTa"]) | (norm["minTa"] > norm["avgTa"]) | (norm["avgTa"] > norm["maxTa"])
    score -= 10.0 * float(bad.sum())
    return norm, mapping, score


def parse_normals_text_best(text: str, station_ids: Optional[List[int]] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """평년값 원문에서 모든 컬럼 전략을 시도하여 물리적으로 가장 타당한 결과를 반환"""
    station_ids = station_ids or list(STATIONS.values())
    comments, rows = split_kma_text(text)
    diag: Dict[str, Any] = {"n_rows": len(rows), "ncols": 0, "legend_count": 0, "columns": [],
                            "strategy": None, "mapping": {}, "sample": {}, "header_lines": [],
                            "error_text": _detect_text_error(text) if not rows else None}
    if not rows:
        return pd.DataFrame(), diag
    ncols = max(len(r) for r in rows)
    legend = _legend_from_comments(comments)
    diag.update({"ncols": ncols, "legend_count": len(legend),
                 "header_lines": [ln for ln, _ in _header_candidates(comments)]})

    best_norm, best_raw, best_map, best_score, best_strat = None, pd.DataFrame(), {}, -1e9, None
    for strat_name, cols in column_strategies(comments, ncols, DEFAULT_NORMAL_COLS):
        raw = frame_from_rows(rows, cols)
        raw.attrs["legend"] = legend
        raw.attrs["strategy"] = strat_name
        norm, mapping, sc = _extract_normals_from_raw(raw, legend, station_ids)
        if sc > best_score and norm is not None:
            best_norm, best_raw, best_map, best_score, best_strat = norm, raw, mapping, sc, strat_name

    if best_norm is None:
        best_raw = parse_kma_text(text, DEFAULT_NORMAL_COLS)
        best_strat = best_raw.attrs.get("strategy")
    diag["columns"] = list(best_raw.columns) if not best_raw.empty else []
    diag["strategy"] = best_strat
    diag["mapping"] = best_map
    if not best_raw.empty:
        diag["sample"] = {c: best_raw.iloc[0][c] for c in list(best_raw.columns)[: min(len(best_raw.columns), 12)]}
    if best_norm is None:
        return pd.DataFrame(), diag
    best_norm.attrs["mapping"] = best_map
    best_norm.attrs["strategy"] = best_strat
    best_norm.attrs["columns"] = diag["columns"]
    return best_norm, diag


@cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_apihub_normals_raw(mm1: int, dd1: int, mm2: int, dd2: int, api_key: str, tmst: int = 2021) -> Tuple[pd.DataFrame, str]:
    # 평년값(1991~2020)은 바뀌지 않으므로 Firestore 에 저장해 재사용 (원문 텍스트는 저장하지 않음)
    cache_key = ("normals", tmst, f"{mm1:02d}{dd1:02d}", f"{mm2:02d}{dd2:02d}")
    cached = fsc.get_df(fsc.NORMALS_COLLECTION, *cache_key)
    if cached is not None:
        return cached, ""
    params = {"norm": "D", "tmst": str(tmst), "stn": "0", "MM1": str(mm1), "DD1": str(dd1),
              "MM2": str(mm2), "DD2": str(dd2), "help": "1", "authKey": api_key}
    resp = _http_get(APIHUB_NORMAL_URL, params)
    text = decode_bytes(resp.content)
    norm_df, _diag = parse_normals_text_best(text, list(STATIONS.values()))
    if norm_df.empty:
        err = _detect_text_error(text)
        if err:
            raise WeatherApiError(f"평년값 API 응답 오류: {err}")
    else:
        fsc.put_df(fsc.NORMALS_COLLECTION, norm_df, *cache_key)
    return norm_df, text


def normals_for_period(start: dt.date, end: dt.date, api_key: str,
                       station_ids: Optional[List[int]] = None) -> Tuple[pd.DataFrame, List[str], str]:
    """
    선택 기간의 일별·지점별 평년값 DataFrame 반환 (표준 스키마).
    연도를 넘는 기간(예: 12/20~1/10)은 두 구간으로 나누어 호출.
    반환: (평년 DataFrame, 매핑 실패 지표 목록, 원문 텍스트)
    """
    station_ids = station_ids or list(STATIONS.values())
    if (start.month, start.day) <= (end.month, end.day):
        intervals = [(start.month, start.day, end.month, end.day)]
    else:
        intervals = [(start.month, start.day, 12, 31), (1, 1, end.month, end.day)]

    parts: List[pd.DataFrame] = []
    texts: List[str] = []
    for mm1, dd1, mm2, dd2 in intervals:
        norm_part, text = fetch_apihub_normals_raw(mm1, dd1, mm2, dd2, api_key)
        texts.append(text)
        if norm_part is not None and not norm_part.empty:
            parts.append(norm_part)
    full_text = "\n\n".join(texts)
    if not parts:
        return empty_daily_frame(), [m for m in METRICS], full_text

    norm_all = pd.concat(parts, ignore_index=True)
    norm_all = norm_all[norm_all["stn_id"].isin(station_ids)].copy()
    if norm_all.empty:
        return empty_daily_frame(), [m for m in METRICS], full_text

    norm = norm_all.groupby(["stn_id", "_mmdd"], as_index=False)[METRIC_COLS].mean()
    unmapped = [name for name, meta in METRICS.items() if norm[meta["col"]].isna().all()]

    dates = pd.date_range(start, end, freq="D")
    grid = pd.DataFrame([{"date": d.date(), "_mmdd": d.month * 100 + d.day, "stn_id": s}
                         for d in dates for s in station_ids])
    merged = grid.merge(norm, on=["stn_id", "_mmdd"], how="left").drop(columns=["_mmdd"])
    merged["stn_name"] = merged["stn_id"].map(STATION_NAMES).fillna("기타")
    merged = merged[BASE_COLS + METRIC_COLS].sort_values(["date", "stn_id"]).reset_index(drop=True)
    return merged, unmapped, full_text


# ---------------------------------------------------------------------
# 진단 (원문 헤더 · 컬럼 매핑 확인)
# ---------------------------------------------------------------------
def diagnose_daily_text(text: str, stn_default: int = 184) -> Dict[str, Any]:
    """일자료 원문을 파싱해 전략별 점수·채택 컬럼·지표 매핑·첫 행 샘플을 반환"""
    _, diag = parse_daily_text_best(text, stn_default, True, DEFAULT_DAILY_COLS)
    diag["error_text"] = _detect_text_error(text) if diag["n_rows"] == 0 else None
    return diag


def diagnose_normals_text(text: str) -> Dict[str, Any]:
    """평년값 원문의 컬럼 구성과 지표 매핑 결과를 반환"""
    _, diag = parse_normals_text_best(text, list(STATIONS.values()))
    return diag


def mapping_table(diag: Dict[str, Any]) -> pd.DataFrame:
    """진단 dict 의 지표 매핑을 표로"""
    rows = []
    for name in METRICS:
        col = diag.get("mapping", {}).get(name)
        sample = diag.get("sample", {}).get(name, "")
        rows.append({"지표": name, "매핑된 원본 컬럼": col if col else "(없음)",
                     "첫 행 값": "" if sample is None else sample})
    return pd.DataFrame(rows)


def detected_columns(text: str, default_cols: Optional[List[str]] = None) -> List[str]:
    """원문 응답에서 파서가 최종 채택한 컬럼 이름 목록을 돌려준다(진단 화면용)."""
    if not text or not str(text).strip():
        return ["(원문 응답 없음)"]
    try:
        comment_lines, rows = split_kma_text(text)
    except Exception as exc:  # pragma: no cover
        return [f"(원문 분해 실패: {exc})"]
    if not rows:
        return ["(데이터 행 없음)"]
    ncols = max(len(r) for r in rows)
    try:
        if default_cols is not None:
            diag = diagnose_daily_text(text)
            cols = list(diag.get("columns") or [])
            label = diag.get("chosen")
        else:
            diag = diagnose_normals_text(text)
            cols = list(diag.get("columns") or [])
            label = diag.get("strategy")
        if not cols:
            cols = _columns_from_comments(comment_lines, ncols, default_cols)
            label = None
    except Exception as exc:  # pragma: no cover
        return [f"(컬럼 인식 실패: {exc})"]
    cols = [str(c) for c in cols]
    if not cols:
        return [f"(컬럼 이름 미인식, 데이터 열 {ncols}개)"]
    return ([f"[채택 전략: {label}]"] if label else []) + cols

# -*- coding: utf-8 -*-
"""
archive.py - 관측 일자료 저장소 (v7.5)

조회할 때마다 기상청을 부르지 않고, 미리 저장해 둔 일자료에서 바로 계산합니다.

  · 저장 단위 : 관측망·지점·연도별 문서 1개  obs_daily/asos_184_2024  (1년치 일자료, 약 30KB)
  · 매일 09시 : 최근 10일(놓친 날이 있으면 그만큼 더)을 기상청에서 다시 받아 '덮어쓰기'
                → 기상청이 며칠 전 자료를 고쳐도 다음 날 아침 자동 반영
  · 매주 월요일: 올해 1월 1일~어제 전체를 다시 받아 덮어쓰기 (지난 자료 수정분 반영, 지점당 기상청 1회 호출)
                1월에는 작년 1년치도 함께 (연말 자료 수정분 반영)
  · 빠진 연도 : 1991년~올해 중 저장되지 않은 연도는 예약 작업·관리자 화면에서 채움.
                조회 중 저장되지 않은 연도를 만나면 그 연도만 기상청에서 받아 저장(최초 1회)

지점 번호는 관측망 이름을 붙여 저장합니다(asos_184). 나중에 AWS·농진청 자료를 같은 방식으로
덧붙일 때 번호가 겹치지 않게 하기 위함입니다.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import store
from .kma_api import (
    BASE_COLS,
    DEFAULT_DAILY_COLS,
    METRIC_COLS,
    METRICS,
    STATION_NAMES,
    STATIONS,
    WeatherApiError,
    _detect_text_error,
    add_derived_metrics,
    empty_daily_frame,
    fetch_apihub_daily_text,
    fetch_apihub_normals_raw,
    parse_daily_text_best,
    split_period,
)

log = logging.getLogger(__name__)

NETWORK = "asos"
COLLECTION = "obs_daily"
META_COLLECTION = "obs_meta"
ARCHIVE_VERSION = 1
FIRST_YEAR = 1991                    # 평년(1991~2020) 계산에 필요한 첫 해
SYNC_DAYS = 10                       # 매일 다시 받아 덮어쓰는 최근 일수
MAX_GAP_DAYS = 400                   # 갱신을 오래 못 했을 때 한 번에 다시 받는 최대 일수
WEEKLY_DAY = 0                       # 매주 올해 전체를 다시 받는 요일 (0 = 월요일)
MEM_TTL_SEC = 600                    # 같은 서버 안에서 연도 문서를 기억하는 시간
WORKERS = 4                          # 기상청 동시 요청 수
KST = dt.timezone(dt.timedelta(hours=9))

# 저장하는 지표(파생 지표인 강우일수·열대야 일수는 읽을 때 계산)
STORED_COLS: List[str] = [m["col"] for m in METRICS.values() if not m.get("derived_from")]

_mem: Dict[Tuple[int, int], Tuple[float, Optional[pd.DataFrame]]] = {}
_mem_lock = threading.Lock()


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def _doc_id(stn: int, year: int) -> str:
    return f"{NETWORK}_{stn}_{year}"


# ---------------------------------------------------------------------
# 연도 문서 읽기·쓰기
# ---------------------------------------------------------------------
def _to_payload(df: pd.DataFrame, stn: int, year: int) -> Dict[str, Any]:
    work = df[["date"] + STORED_COLS].copy().sort_values("date")
    work["date"] = work["date"].map(lambda d: d.isoformat())
    last = work.dropna(subset=STORED_COLS, how="all")["date"].max() if not work.empty else None
    return {"v": ARCHIVE_VERSION, "net": NETWORK, "stn": stn, "year": year,
            "data": work.to_json(orient="split", index=False, force_ascii=False, double_precision=10),
            "rows": int(len(work)), "last": last or "", "updated_at": dt.datetime.now(dt.timezone.utc)}


def _from_payload(payload: Dict[str, Any], stn: int) -> Optional[pd.DataFrame]:
    if payload.get("v") != ARCHIVE_VERSION or "data" not in payload:
        return None
    df = pd.read_json(io.StringIO(payload["data"]), orient="split", convert_dates=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    for c in STORED_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan
    df["stn_id"] = stn
    return df.dropna(subset=["date"])


def load_year(stn: int, year: int) -> Optional[pd.DataFrame]:
    """저장된 1년치 자료 (없으면 None). 같은 서버 안에서는 10분간 기억."""
    key = (stn, year)
    now = time.time()
    with _mem_lock:
        hit = _mem.get(key)
        if hit and now - hit[0] < MEM_TTL_SEC:
            return None if hit[1] is None else hit[1].copy()
    client = store._get_client()
    df: Optional[pd.DataFrame] = None
    if client is not None:
        try:
            snap = client.collection(COLLECTION).document(_doc_id(stn, year)).get()
            if snap.exists:
                df = _from_payload(snap.to_dict() or {}, stn)
        except Exception as exc:
            log.warning("저장 자료 읽기 실패 %s: %s", _doc_id(stn, year), exc)
            return None                       # 일시 장애는 기억하지 않음
    elif hit:                                 # 저장소가 없는 환경(테스트): 메모리 보관분 사용
        return None if hit[1] is None else hit[1].copy()
    if df is not None or client is not None:
        with _mem_lock:
            _mem[key] = (now, df)
    return None if df is None else df.copy()


def save_year(stn: int, year: int, df: pd.DataFrame) -> None:
    df = df[df["date"].map(lambda d: d.year == year)].copy()
    df["stn_id"] = stn
    with _mem_lock:
        _mem[(stn, year)] = (time.time(), df.copy())
    client = store._get_client()
    if client is None or df.empty:
        return
    client.collection(COLLECTION).document(_doc_id(stn, year)).set(_to_payload(df, stn, year))


def stored_years(stations: Optional[List[int]] = None) -> Dict[int, List[int]]:
    """지점별 저장된 연도 목록 (문서 이름만 확인)"""
    stations = stations or list(STATIONS.values())
    client = store._get_client()
    out: Dict[int, List[int]] = {s: [] for s in stations}
    if client is None:
        return out
    years = list(range(FIRST_YEAR, today_kst().year + 1))
    refs = [client.collection(COLLECTION).document(_doc_id(s, y)) for s in stations for y in years]
    for i in range(0, len(refs), 100):
        for snap in client.get_all(refs[i:i + 100], field_paths=["stn", "year"]):
            if snap.exists:
                d = snap.to_dict() or {}
                if d.get("stn") in out:
                    out[d["stn"]].append(int(d.get("year")))
    return {s: sorted(v) for s, v in out.items()}


# ---------------------------------------------------------------------
# 기상청에서 받아 저장 (덮어쓰기)
# ---------------------------------------------------------------------
def _fetch_raw(stn: int, start: dt.date, end: dt.date, api_key: str) -> pd.DataFrame:
    """기상청 원자료 (강수 결측은 그대로 — 무강수 처리는 읽을 때 선택에 따라)"""
    parts = []
    for s, e in split_period(start, end):
        text = fetch_apihub_daily_text(stn, s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), api_key)
        std, _ = parse_daily_text_best(text, stn, False, DEFAULT_DAILY_COLS)
        if std.empty:
            err = _detect_text_error(text)
            if err:
                raise WeatherApiError(f"API허브 응답 오류: {err}")
            continue
        parts.append(std[std["stn_id"] == stn])
    if not parts:
        return empty_daily_frame()
    return pd.concat(parts, ignore_index=True).drop_duplicates(subset=["date"], keep="last")


def refresh_range(stn: int, start: dt.date, end: dt.date, api_key: str) -> int:
    """기간 자료를 기상청에서 다시 받아 연도 문서에 덮어씀. 반환: 받은 일수"""
    end = min(end, today_kst() - dt.timedelta(days=1))
    if start > end:
        return 0
    new = _fetch_raw(stn, start, end, api_key)
    if new.empty:
        return 0
    for year in range(start.year, end.year + 1):
        part = new[new["date"].map(lambda d: d.year == year)]
        if part.empty:
            continue
        old = load_year(stn, year)
        if old is not None and not old.empty:
            keep = old[~old["date"].isin(set(part["date"]))]
            merged = pd.concat([keep[["date"] + STORED_COLS], part[["date"] + STORED_COLS]], ignore_index=True)
        else:
            merged = part[["date"] + STORED_COLS]
        save_year(stn, year, merged.sort_values("date").reset_index(drop=True))
    return int(len(new))


def refresh_year(stn: int, year: int, api_key: str) -> None:
    refresh_range(stn, dt.date(year, 1, 1), dt.date(year, 12, 31), api_key)


# ---------------------------------------------------------------------
# 조회용: 저장된 자료에서 기간 자료 만들기
# ---------------------------------------------------------------------
def read_range(start: dt.date, end: dt.date, station_ids: List[int], rain_missing_as_zero: bool,
               api_key: str, progress: Optional[Callable[[str], None]] = None) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """fetch_all_stations 와 같은 형태로 반환 (date, stn_id, stn_name, 지표…), 오류는 지점별 메시지"""
    frames: List[pd.DataFrame] = []
    errors: Dict[str, str] = {}
    for stn in station_ids:
        name = STATION_NAMES.get(stn, str(stn))
        parts = []
        for year in range(start.year, end.year + 1):
            df = load_year(stn, year)
            if df is None and year <= today_kst().year:
                # 아직 저장되지 않은 연도 — 이 연도만 기상청에서 받아 저장(최초 1회)
                if progress:
                    progress(f"{name} {year}년 자료가 저장돼 있지 않아 기상청에서 받는 중")
                try:
                    refresh_year(stn, year, api_key)
                except WeatherApiError as exc:
                    errors[name] = str(exc)
                    continue
                df = load_year(stn, year)
            if df is not None and not df.empty:
                parts.append(df[(df["date"] >= start) & (df["date"] <= end)])
        parts = [p for p in parts if not p.empty]
        if parts:
            frames.append(pd.concat(parts, ignore_index=True))
        elif name not in errors:
            errors[name] = "조회된 자료가 없습니다."
    if not frames:
        return empty_daily_frame(), errors
    out = pd.concat(frames, ignore_index=True)
    out["stn_name"] = out["stn_id"].map(STATION_NAMES).fillna("기타")
    for c in METRIC_COLS:
        if c not in out.columns:
            out[c] = np.nan
    if rain_missing_as_zero:   # 기온 관측이 정상인 날의 강수 결측은 0.0mm (기존과 같은 규칙)
        observed = out["avgTa"].notna() | out["maxTa"].notna() | out["minTa"].notna()
        out.loc[observed & out["sumRn"].isna(), "sumRn"] = 0.0
    add_derived_metrics(out)
    out = out[BASE_COLS + METRIC_COLS].drop_duplicates(subset=["date", "stn_id"])
    return out.sort_values(["date", "stn_id"]).reset_index(drop=True), errors


# ---------------------------------------------------------------------
# 상태 기록 (관리자 화면 · 화면의 '자료 기준일')
# ---------------------------------------------------------------------
def meta_get() -> Dict[str, Any]:
    client = store._get_client()
    if client is None:
        return {}
    try:
        snap = client.collection(META_COLLECTION).document(NETWORK).get()
        return (snap.to_dict() or {}) if snap.exists else {}
    except Exception:
        return {}


def _meta_set(**fields: Any) -> None:
    client = store._get_client()
    if client is None:
        return
    try:
        client.collection(META_COLLECTION).document(NETWORK).set(fields, merge=True)
    except Exception as exc:
        log.warning("저장소 상태 기록 실패: %s", exc)


_asof_cache: Tuple[float, Optional[str]] = (0.0, None)


def as_of() -> Optional[str]:
    """저장된 자료의 마지막 날짜(모든 지점 공통, YYYY-MM-DD). 5분간 기억."""
    global _asof_cache
    if time.time() - _asof_cache[0] < 300:
        return _asof_cache[1]
    value = meta_get().get("asOf")
    _asof_cache = (time.time(), value)
    return value


def _compute_as_of() -> Optional[str]:
    year = today_kst().year
    lasts = []
    for stn in STATIONS.values():
        last = None
        for y in (year, year - 1):
            df = load_year(stn, y)
            if df is not None and not df.empty:
                got = df.dropna(subset=["avgTa"])
                if not got.empty:
                    last = got["date"].max()
                    break
        if last is None:
            return None
        lasts.append(last)
    return min(lasts).isoformat()


# ---------------------------------------------------------------------
# 예약 작업 · 관리자 실행
# ---------------------------------------------------------------------
def sync_recent(api_key: str, days: Optional[int] = None, whole_year: bool = False) -> Dict[str, Any]:
    """최근 자료 덮어쓰기. days 를 주지 않으면 10일(놓친 날이 있으면 마지막 기준일 2일 전부터).
    whole_year=True 면 올해 1월 1일부터(1월에는 작년 1월 1일부터) — 매주 월요일 갱신"""
    today = today_kst()
    if whole_year:
        first = dt.date(today.year - 1 if today.month == 1 else today.year, 1, 1)
        days = (today - first).days
    elif days is None:
        days = SYNC_DAYS
        last = meta_get().get("asOf")
        if last:
            gap = (today - dt.date.fromisoformat(last)).days + 2
            days = max(days, min(gap, MAX_GAP_DAYS))
    start = today - dt.timedelta(days=days)
    end = today - dt.timedelta(days=1)
    t0 = time.time()
    errors: Dict[str, str] = {}

    def one(stn: int) -> int:
        return refresh_range(stn, start, end, api_key)

    counts: Dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {stn: pool.submit(one, stn) for stn in STATIONS.values()}
        for stn, fut in futs.items():
            name = STATION_NAMES[stn]
            try:
                counts[name] = fut.result()
            except Exception as exc:
                errors[name] = f"{exc.__class__.__name__}: {str(exc)[:150]}"
    ok = not errors
    now = dt.datetime.now(KST)
    fields: Dict[str, Any] = {"lastSyncAt": now.strftime("%Y-%m-%d %H:%M"), "lastSyncOk": ok,
                              "lastSyncRange": f"{start.isoformat()}~{end.isoformat()}",
                              "lastSyncErrors": errors, "lastSyncSec": round(time.time() - t0, 1)}
    if ok:
        fields["lastOkDate"] = today.isoformat()
    asof = _compute_as_of()
    if asof:
        fields["asOf"] = asof
    _meta_set(**fields)
    global _asof_cache
    _asof_cache = (0.0, None)
    return {"ok": ok, "range": fields["lastSyncRange"], "days": counts, "errors": errors, "asOf": asof}


def missing_years() -> List[Tuple[int, int]]:
    have = stored_years()
    years = range(FIRST_YEAR, today_kst().year + 1)
    return [(s, y) for s in STATIONS.values() for y in years if y not in have.get(s, [])]


def backfill(api_key: str, time_budget: float = 200.0) -> Dict[str, Any]:
    """저장되지 않은 연도 채우기 (시간 제한 안에서 가능한 만큼, 다음 실행 때 이어서)"""
    t0 = time.time()
    todo = missing_years()
    done: List[str] = []
    errors: Dict[str, str] = {}
    # 평년값(1년 전체)도 한 번 받아 저장 — 이후 평년 조회는 저장분 사용
    try:
        fetch_apihub_normals_raw(1, 1, 12, 31, api_key)
    except Exception as exc:
        errors["평년값"] = f"{exc.__class__.__name__}: {str(exc)[:120]}"
    idx = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while idx < len(todo) and time.time() - t0 < time_budget:
            batch = todo[idx: idx + WORKERS]
            idx += len(batch)
            futs = {pool.submit(refresh_year, s, y, api_key): (s, y) for s, y in batch}
            for fut, (s, y) in futs.items():
                label = f"{STATION_NAMES[s]} {y}"
                try:
                    fut.result()
                    done.append(label)
                except Exception as exc:
                    errors[label] = f"{exc.__class__.__name__}: {str(exc)[:120]}"
    left = len(missing_years()) if done else len(todo)
    _meta_set(backfillAt=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M"), backfillLeft=left)
    return {"filled": len(done), "left": left, "errors": errors, "sec": round(time.time() - t0, 1)}


def scheduled(api_key: str, budget: float = 480.0) -> Dict[str, Any]:
    """예약 작업(09·10·12시): 오늘 갱신이 이미 성공했으면 건너뛰고, 남는 시간에 빠진 연도 채우기"""
    t0 = time.time()
    today = today_kst()
    meta = meta_get()
    out: Dict[str, Any] = {}
    if meta.get("lastOkDate") != today.isoformat():
        out["sync"] = sync_recent(api_key, whole_year=today.weekday() == WEEKLY_DAY)
    else:
        out["sync"] = "오늘 갱신 완료 — 건너뜀"
    remain = budget - (time.time() - t0)
    if remain > 30:
        out["backfill"] = backfill(api_key, remain - 20)
    return out


def status() -> Dict[str, Any]:
    meta = meta_get()
    have = stored_years()
    total = (today_kst().year - FIRST_YEAR + 1) * len(STATIONS)
    stored = sum(len(v) for v in have.values())
    keep = ("asOf", "lastSyncAt", "lastSyncOk", "lastSyncRange", "lastSyncErrors", "lastSyncSec", "backfillAt")
    out = {k: meta.get(k) for k in keep}
    out.update({"storedDocs": stored, "totalDocs": total, "firstYear": FIRST_YEAR,
                "stations": {STATION_NAMES[s]: (f"{v[0]}~{v[-1]} ({len(v)}개 연도)" if v else "없음") for s, v in have.items()}})
    return out

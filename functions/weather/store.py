# -*- coding: utf-8 -*-
"""
store.py  (백엔드)
조회 결과 재사용을 위한 두 단계 캐시

1) memo_cache : 같은 Cloud Functions 인스턴스 안에서 짧게 기억 (메모리)
2) Firestore  : 확정된 과거 일자료와 평년값을 영구 저장 → 다음 조회부터 기상청 API 호출 생략

- Cloud Functions 에서는 Firebase Admin SDK 가 자동으로 인증됩니다.
- Firestore 를 쓸 수 없는 환경(단위 테스트 등)에서는 조용히 꺼지고, 기상청 API 만 사용합니다.
- 환경변수 FIRESTORE_CACHE=0 으로 강제로 끌 수 있습니다.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import io
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)

CACHE_VERSION = 2                  # 저장 형식·지표 구성이 바뀌면 올려서 기존 저장분을 무시 (v2: 풍속 제거)
DAILY_COLLECTION = "kma_daily"
NORMALS_COLLECTION = "kma_normals"
FINALIZED_AFTER_DAYS = 11          # 매일 09시에 최근 10일을 다시 받아 덮어쓰므로(v7.5), 그보다 지난 구간만 '확정'(영구 저장)
RECENT_TTL_SEC = 6 * 3600          # 확정 전 최근 자료·결과는 6시간 동안만 재사용
RESULTS_COLLECTION = "results"
RESULT_SCHEMA = 7                  # v7.5: 저장소 자료 기준일(dataAsOf) 추가 (v7.3: 최근 3년·보고서 표)
KST = dt.timezone(dt.timedelta(hours=9))

_client: Any = None
_disabled_reason: Optional[str] = None
_client_lock = threading.Lock()


# ---------------------------------------------------------------------
# 1) 메모리 캐시
# ---------------------------------------------------------------------
def memo_cache(ttl: int = 3600, maxsize: int = 512) -> Callable:
    """인자 값 기준으로 결과를 ttl 초 동안 기억. DataFrame 은 복사본을 돌려준다."""

    def deco(func: Callable) -> Callable:
        data: Dict[Tuple, Tuple[float, Any]] = {}
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()
            with lock:
                hit = data.get(key)
                if hit and now - hit[0] < ttl:
                    return _copy(hit[1])
            value = func(*args, **kwargs)
            with lock:
                if len(data) >= maxsize:
                    data.pop(next(iter(data)))
                data[key] = (now, value)
            return _copy(value)

        wrapper.cache_clear = data.clear  # type: ignore[attr-defined]
        return wrapper

    return deco


def _copy(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, tuple):
        return tuple(_copy(v) for v in value)
    return value


# ---------------------------------------------------------------------
# 2) Firestore 영구 저장
# ---------------------------------------------------------------------
def _get_client() -> Any:
    global _client, _disabled_reason
    if _client is not None or _disabled_reason is not None:
        return _client
    with _client_lock:
        if _client is not None or _disabled_reason is not None:
            return _client
        if os.environ.get("FIRESTORE_CACHE", "1").strip().lower() in ("0", "false", "off"):
            _disabled_reason = "FIRESTORE_CACHE=0"
            return None
        if os.environ.get("FUNCTIONS_EMULATOR") == "true" and not os.environ.get("FIRESTORE_EMULATOR_HOST"):
            _disabled_reason = "로컬 에뮬레이터 (실제 Firestore 미사용)"
            return None
        try:
            import firebase_admin  # type: ignore
            from firebase_admin import firestore  # type: ignore

            if not firebase_admin._apps:
                firebase_admin.initialize_app()
            _client = firestore.client()
        except Exception as exc:  # 패키지 없음 · 인증 정보 없음 등
            _disabled_reason = f"{exc.__class__.__name__}: {str(exc)[:80]}"
            log.info("Firestore 저장 비활성화: %s", _disabled_reason)
            _client = None
    return _client


def enabled() -> bool:
    return _get_client() is not None


def is_finalized(end_ymd: str) -> bool:
    """종료일이 충분히 지나 자료가 확정된 구간인지 (한국 시간 기준)"""
    try:
        end = dt.datetime.strptime(end_ymd, "%Y%m%d").date()
    except ValueError:
        return False
    return (dt.datetime.now(KST).date() - end).days >= FINALIZED_AFTER_DAYS


def _doc_id(*parts: Any) -> str:
    raw = "_".join(str(p) for p in parts)
    return f"{raw[:80]}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"


def _json_ok(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def _df_to_payload(df: pd.DataFrame) -> Dict[str, Any]:
    work = df.copy()
    date_cols = [c for c in ("date",) if c in work.columns]
    for c in date_cols:
        work[c] = work[c].map(lambda d: d.isoformat() if isinstance(d, dt.date) else None)
    attrs = {k: v for k, v in df.attrs.items() if _json_ok(v)}
    return {
        "v": CACHE_VERSION,
        "data": work.to_json(orient="split", force_ascii=False, double_precision=15),
        "date_cols": date_cols,
        "attrs": json.dumps(attrs, ensure_ascii=False),
        "rows": int(len(df)),
        "saved_at": dt.datetime.now(dt.timezone.utc),
    }


def _payload_to_df(payload: Dict[str, Any]) -> Optional[pd.DataFrame]:
    if payload.get("v") != CACHE_VERSION or "data" not in payload:
        return None
    df = pd.read_json(io.StringIO(payload["data"]), orient="split", convert_dates=False)
    for c in payload.get("date_cols", []):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    for c in ("stn_id", "_mmdd"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(int)
    try:
        df.attrs.update(json.loads(payload.get("attrs") or "{}"))
    except ValueError:
        pass
    return df


def _is_fresh(payload: Dict[str, Any], recent_ttl: Optional[int]) -> bool:
    """확정 자료(final 이 없거나 True)는 항상 유효, 최근 자료는 recent_ttl 초 안에 저장된 것만 유효"""
    if payload.get("final", True) is not False:
        return True                      # 확정 자료 (이전 버전 저장분도 모두 확정 자료만 저장됨)
    if recent_ttl is None:
        return False                     # 최근 자료 재사용을 허용하지 않는 호출
    saved = payload.get("saved_at")
    if not isinstance(saved, dt.datetime):
        return False
    if saved.tzinfo is None:
        saved = saved.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - saved).total_seconds() < recent_ttl


def get_df(collection: str, *key_parts: Any, recent_ttl: Optional[int] = None) -> Optional[pd.DataFrame]:
    client = _get_client()
    if client is None:
        return None
    try:
        snap = client.collection(collection).document(_doc_id(*key_parts)).get()
        if not snap.exists:
            return None
        payload = snap.to_dict() or {}
        if not _is_fresh(payload, recent_ttl):
            return None
        return _payload_to_df(payload)
    except Exception as exc:  # 저장소 장애가 조회를 막지 않도록
        log.warning("Firestore 읽기 실패(%s): %s", collection, exc)
        return None


def put_df(collection: str, df: pd.DataFrame, *key_parts: Any, meta: Optional[Dict[str, Any]] = None) -> None:
    client = _get_client()
    if client is None or df is None or df.empty:
        return
    try:
        payload = _df_to_payload(df)
        payload.update(meta or {})
        client.collection(collection).document(_doc_id(*key_parts)).set(payload)
    except Exception as exc:
        log.warning("Firestore 저장 실패(%s): %s", collection, exc)


# ---------------------------------------------------------------------
# 3) 조회 작업 진행 상황 (화면 로딩창 표시 + 60초 넘는 조회 결과 전달용)
#    jobs/{job_id}: status(running|done|error), pct, stage, detail, result(JSON 문자열), error
# ---------------------------------------------------------------------
JOBS_COLLECTION = "jobs"
JOB_RESULT_MAX_BYTES = 900_000      # Firestore 문서 1MB 제한 여유분
_job_last_write: Dict[str, float] = {}


def job_update(job_id: str, force: bool = False, **fields: Any) -> None:
    """진행 상황 저장. 너무 잦은 쓰기를 막기 위해 0.8초 간격으로만 기록(force=True 면 즉시)."""
    client = _get_client()
    if client is None or not job_id:
        return
    now = time.time()
    if not force and now - _job_last_write.get(job_id, 0) < 0.8:
        return
    _job_last_write[job_id] = now
    try:
        fields["updated_at"] = dt.datetime.now(dt.timezone.utc)
        fields.setdefault("expire_at", dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1))
        client.collection(JOBS_COLLECTION).document(job_id).set(fields, merge=True)
    except Exception as exc:
        log.warning("진행 상황 저장 실패: %s", exc)


def job_get(job_id: str) -> Optional[Dict[str, Any]]:
    client = _get_client()
    if client is None or not job_id:
        return None
    try:
        snap = client.collection(JOBS_COLLECTION).document(job_id).get()
        return snap.to_dict() if snap.exists else None
    except Exception as exc:
        log.warning("진행 상황 읽기 실패: %s", exc)
        return None


def job_finish(job_id: str, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, str]] = None) -> None:
    if error is not None:
        job_update(job_id, force=True, status="error", error=error, pct=100)
        return
    payload = json.dumps(result, ensure_ascii=False, default=str)
    if len(payload.encode("utf-8")) > JOB_RESULT_MAX_BYTES:
        job_update(job_id, force=True, status="done", pct=100, result=None, too_large=True)
    else:
        job_update(job_id, force=True, status="done", pct=100, result=payload)
    _job_last_write.pop(job_id, None)


# ---------------------------------------------------------------------
# 4) 조회 결과 전체 저장 — 같은 조건으로 다시 조회하면 기상청 호출 없이 바로 표시
#    (종료일이 3일 이상 지난 조회는 영구, 최근 기간 조회는 6시간 동안 재사용)
# ---------------------------------------------------------------------
def result_key(query: Dict[str, Any]) -> str:
    raw = json.dumps(query, ensure_ascii=False, sort_keys=True, default=str)
    return f"q{RESULT_SCHEMA}_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def result_get(key: str) -> Optional[Dict[str, Any]]:
    client = _get_client()
    if client is None:
        return None
    try:
        snap = client.collection(RESULTS_COLLECTION).document(key).get()
        if not snap.exists:
            return None
        payload = snap.to_dict() or {}
        if payload.get("v") != CACHE_VERSION or not _is_fresh(payload, RECENT_TTL_SEC):
            return None
        return json.loads(payload["data"])
    except Exception as exc:
        log.warning("저장된 결과 읽기 실패: %s", exc)
        return None


def result_put(key: str, result: Dict[str, Any], final: bool) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        data = json.dumps(result, ensure_ascii=False, default=str)
        if len(data.encode("utf-8")) > JOB_RESULT_MAX_BYTES:
            return
        client.collection(RESULTS_COLLECTION).document(key).set({
            "v": CACHE_VERSION, "data": data, "final": bool(final),
            "saved_at": dt.datetime.now(dt.timezone.utc),
        })
    except Exception as exc:
        log.warning("결과 저장 실패: %s", exc)


# ---------------------------------------------------------------------
# 5) 대시보드 상단 예보 — 단기예보는 3시간마다 바뀌므로 20분만 재사용
#    (일부 예보를 못 받은 경우 3분 뒤 다시 시도)
# ---------------------------------------------------------------------
FORECAST_COLLECTION = "forecast"
FORECAST_TTL_SEC = 20 * 60
FORECAST_ERR_TTL_SEC = 3 * 60
_forecast_mem: Dict[str, Any] = {}


def forecast_get(key: str = "jeju") -> Optional[Dict[str, Any]]:
    now = time.time()
    m = _forecast_mem.get(key)
    if m and now - m[0] < m[1]:
        return _copy(m[2])
    client = _get_client()
    if client is None:
        return None
    try:
        snap = client.collection(FORECAST_COLLECTION).document(key).get()
        if not snap.exists:
            return None
        p = snap.to_dict() or {}
        ttl = FORECAST_ERR_TTL_SEC if p.get("partial") else FORECAST_TTL_SEC
        if now - float(p.get("ts") or 0) >= ttl:
            return None
        data = json.loads(p["data"])
        _forecast_mem[key] = (float(p["ts"]), ttl, data)
        return _copy(data)
    except Exception as exc:
        log.warning("예보 저장분 읽기 실패: %s", exc)
        return None


def forecast_last_good(key: str = "jeju") -> Optional[Dict[str, Any]]:
    """기상청 연결이 안 될 때 대신 보여줄, 마지막으로 정상 수신한 예보 (기간 제한 없음)"""
    client = _get_client()
    if client is None:
        return None
    try:
        snap = client.collection(FORECAST_COLLECTION).document(key + "_last").get()
        return json.loads((snap.to_dict() or {})["data"]) if snap.exists else None
    except Exception as exc:
        log.warning("지난 예보 읽기 실패: %s", exc)
        return None


def forecast_put(data: Dict[str, Any], key: str = "jeju") -> None:
    partial = bool(data.get("errors"))
    ts = time.time()
    _forecast_mem[key] = (ts, FORECAST_ERR_TTL_SEC if partial else FORECAST_TTL_SEC, _copy(data))
    client = _get_client()
    if client is None:
        return
    try:
        doc = {"data": json.dumps(data, ensure_ascii=False, default=str), "ts": ts, "partial": partial}
        client.collection(FORECAST_COLLECTION).document(key).set(doc)
        if not partial:
            client.collection(FORECAST_COLLECTION).document(key + "_last").set(doc)
    except Exception as exc:
        log.warning("예보 저장 실패: %s", exc)


# ---------------------------------------------------------------------
# 6) AI 기상분석 결과 — 같은 조회 조건이면 다시 부르지 않고 재사용
#    (확정 기간은 영구, 최근 기간은 6시간)
# ---------------------------------------------------------------------
AI_COLLECTION = "ai_analysis"


def ai_get(key: str) -> Optional[Dict[str, Any]]:
    client = _get_client()
    if client is None:
        return None
    try:
        snap = client.collection(AI_COLLECTION).document(key).get()
        if not snap.exists:
            return None
        p = snap.to_dict() or {}
        if not p.get("final") and time.time() - float(p.get("ts") or 0) > RECENT_TTL_SEC:
            return None
        return json.loads(p["data"])
    except Exception as exc:
        log.warning("저장된 AI 분석 읽기 실패: %s", exc)
        return None


def ai_put(key: str, data: Dict[str, Any], final: bool) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.collection(AI_COLLECTION).document(key).set({
            "data": json.dumps(data, ensure_ascii=False, default=str), "ts": time.time(), "final": bool(final)})
    except Exception as exc:
        log.warning("AI 분석 저장 실패: %s", exc)

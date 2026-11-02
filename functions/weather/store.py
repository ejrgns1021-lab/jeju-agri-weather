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
FINALIZED_AFTER_DAYS = 3           # 기상청 일자료는 최근 며칠간 수정될 수 있으므로 이보다 지난 구간만 저장
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


def get_df(collection: str, *key_parts: Any) -> Optional[pd.DataFrame]:
    client = _get_client()
    if client is None:
        return None
    try:
        snap = client.collection(collection).document(_doc_id(*key_parts)).get()
        if not snap.exists:
            return None
        return _payload_to_df(snap.to_dict() or {})
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

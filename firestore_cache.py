# -*- coding: utf-8 -*-
"""
firestore_cache.py - 기상청 조회 결과를 Firestore 에 저장해 재사용하는 영구 캐시

- Cloud Run 에서는 서비스 계정(Application Default Credentials)으로 자동 인증됩니다.
- 로컬 실행 등 Firestore 를 쓸 수 없는 환경에서는 조용히 비활성화되어,
  앱은 지금처럼 기상청 API 만 사용해 정상 동작합니다.
- 환경변수 FIRESTORE_CACHE=0 으로 강제로 끌 수 있습니다.

저장 단위: 지점 × 조회 구간 일자료 1건 = 문서 1개 (문서 1MB 제한에 충분히 여유)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import logging
import os
from typing import Any, Dict, Optional

import pandas as pd

log = logging.getLogger(__name__)

CACHE_VERSION = 1                  # 파싱 로직이 바뀌면 올려서 기존 캐시를 무시
DAILY_COLLECTION = "kma_daily"
NORMALS_COLLECTION = "kma_normals"
# 기상청 일자료는 최근 며칠간 수정될 수 있으므로, 이 일수보다 지난 구간만 저장
FINALIZED_AFTER_DAYS = 3
KST = dt.timezone(dt.timedelta(hours=9))

_client: Any = None
_disabled_reason: Optional[str] = None


def _get_client() -> Any:
    """Firestore 클라이언트를 한 번만 만들고, 실패하면 캐시를 끈다."""
    global _client, _disabled_reason
    if _client is not None or _disabled_reason is not None:
        return _client
    if os.environ.get("FIRESTORE_CACHE", "1").strip() in ("0", "false", "off"):
        _disabled_reason = "FIRESTORE_CACHE=0"
        return None
    try:
        from google.cloud import firestore  # type: ignore

        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("FIREBASE_PROJECT_ID") or None
        _client = firestore.Client(project=project)
    except Exception as exc:  # 패키지 없음 · 인증 정보 없음 등
        _disabled_reason = f"{exc.__class__.__name__}: {str(exc)[:80]}"
        log.info("Firestore 캐시 비활성화: %s", _disabled_reason)
        _client = None
    return _client


def status() -> str:
    """화면 표시용 상태 문자열"""
    client = _get_client()
    if client is not None:
        return "Firestore 저장 사용 중"
    return f"Firestore 저장 꺼짐 ({_disabled_reason})" if _disabled_reason else "Firestore 저장 꺼짐"


def is_finalized(end_ymd: str) -> bool:
    """종료일이 충분히 지나 자료가 확정된 구간인지 (한국 시간 기준)"""
    try:
        end = dt.datetime.strptime(end_ymd, "%Y%m%d").date()
    except ValueError:
        return False
    today_kst = dt.datetime.now(KST).date()
    return (today_kst - end).days >= FINALIZED_AFTER_DAYS


def _doc_id(*parts: Any) -> str:
    raw = "_".join(str(p) for p in parts)
    # 사람이 읽을 수 있는 접두어 + 충돌 방지용 짧은 해시
    return f"{raw[:80]}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"


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
    if "stn_id" in df.columns:
        df["stn_id"] = pd.to_numeric(df["stn_id"], errors="coerce").astype(int)
    if "_mmdd" in df.columns:
        df["_mmdd"] = pd.to_numeric(df["_mmdd"], errors="coerce").astype(int)
    try:
        df.attrs.update(json.loads(payload.get("attrs") or "{}"))
    except ValueError:
        pass
    return df


def _json_ok(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def get_df(collection: str, *key_parts: Any) -> Optional[pd.DataFrame]:
    client = _get_client()
    if client is None:
        return None
    try:
        snap = client.collection(collection).document(_doc_id(*key_parts)).get()
        if not snap.exists:
            return None
        return _payload_to_df(snap.to_dict() or {})
    except Exception as exc:  # 캐시 장애가 조회를 막지 않도록
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

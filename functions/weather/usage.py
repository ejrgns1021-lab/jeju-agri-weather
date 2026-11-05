# -*- coding: utf-8 -*-
"""
usage.py - 이용 현황 기록·집계 (v7.2, 관리자 화면용)

- 브라우저마다 화면이 만든 무작위 번호(cid)만 받습니다. 이름·이메일·IP 등 개인정보는 저장하지 않으며,
  그 번호도 한 번 더 해시(sha256 앞 16자리)해서 저장합니다.
- Firestore usage/{YYYY-MM-DD} 문서에 종류별 횟수와 브라우저 번호 목록을 누적합니다.
- 기록이 실패해도 로그인·조회 등 본 기능에는 영향이 없습니다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
from typing import Any, Dict, List

from . import store

# 프리뷰(…dev 함수)의 테스트 이용은 라이브 통계와 섞이지 않게 따로 기록
USAGE_COLLECTION = "usage_dev" if os.environ.get("K_SERVICE", "").endswith("dev") else "usage"
KINDS = ("login", "query", "brief", "forecast")
KST = dt.timezone(dt.timedelta(hours=9))
CID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
log = logging.getLogger(__name__)


def _today() -> str:
    return dt.datetime.now(KST).strftime("%Y-%m-%d")


def record(kind: str, cid: Any) -> None:
    if kind not in KINDS:
        return
    client = store._get_client()
    if client is None:
        return
    try:
        from google.cloud import firestore  # firebase-admin 에 포함
        fields: Dict[str, Any] = {f"n_{kind}": firestore.Increment(1), "date": _today()}
        cid = str(cid or "")
        if CID_RE.match(cid):
            h = hashlib.sha256(f"jeju-usage:{cid}".encode()).hexdigest()[:16]
            fields["browsers"] = firestore.ArrayUnion([h])
            if kind == "query":
                fields["query_browsers"] = firestore.ArrayUnion([h])
        client.collection(USAGE_COLLECTION).document(_today()).set(fields, merge=True)
    except Exception as exc:   # 기록 실패는 무시
        log.warning("이용 기록 실패: %s", exc)


def stats(days: int = 30) -> Dict[str, Any]:
    """최근 days일 날짜별 횟수·브라우저 수 + 기간 합계(서로 다른 브라우저 수)"""
    client = store._get_client()
    out: List[Dict[str, Any]] = []
    all_browsers: set = set()
    if client is None:
        return {"days": out, "total": {}, "uniqueBrowsers": 0, "note": "저장소에 연결할 수 없습니다."}
    end = dt.datetime.now(KST).date()
    start = end - dt.timedelta(days=days - 1)
    ids = [(start + dt.timedelta(days=i)).isoformat() for i in range(days)]
    refs = [client.collection(USAGE_COLLECTION).document(d) for d in ids]
    snaps = {s.id: (s.to_dict() or {}) for s in client.get_all(refs) if s.exists}
    total = {k: 0 for k in KINDS}
    for d in ids:
        doc = snaps.get(d)
        if not doc:
            continue
        b = set(doc.get("browsers") or [])
        all_browsers |= b
        row = {"date": d, "browsers": len(b), "queryBrowsers": len(set(doc.get("query_browsers") or []))}
        for k in KINDS:
            row[k] = int(doc.get(f"n_{k}") or 0)
            total[k] += row[k]
        out.append(row)
    return {"days": out, "total": total, "uniqueBrowsers": len(all_browsers), "since": ids[0]}


CACHE_KINDS = {
    "results": ("results", "조회 결과 저장분"),
    "ai": ("ai_analysis", "AI 기상분석 저장분"),
    "forecast": ("forecast", "기상 예보 저장분"),
    "jobs": ("jobs", "조회 진행 기록"),
}


def cache_counts() -> Dict[str, int]:
    client = store._get_client()
    out: Dict[str, int] = {}
    if client is None:
        return out
    for key, (col, _) in CACHE_KINDS.items():
        try:
            agg = client.collection(col).count().get()
            out[key] = int(agg[0][0].value)
        except Exception:
            out[key] = -1
    return out


def clear_cache(kind: str) -> int:
    """저장분 비우기 (관측 원자료 kma_daily·평년 kma_normals 는 지우지 않음 — 다시 받는 데 오래 걸림)"""
    if kind not in CACHE_KINDS:
        raise ValueError("알 수 없는 저장 종류입니다.")
    client = store._get_client()
    if client is None:
        return 0
    col = client.collection(CACHE_KINDS[kind][0])
    n = 0
    while True:
        docs = list(col.limit(300).stream())
        if not docs:
            break
        batch = client.batch()
        for d in docs:
            batch.delete(d.reference)
        batch.commit()
        n += len(docs)
        if n >= 20000:
            break
    store._forecast_mem.clear() if kind == "forecast" else None
    return n

# -*- coding: utf-8 -*-
"""
settings.py - 관리자 화면에서 바꾸는 설정 (v7.4)

- AI 모델 선택: Firestore settings/ai (프리뷰 함수는 settings/ai_dev — 테스트 선택이 라이브에 영향 없음)
- AI Studio API 키(젬마용): Firestore settings/ai_keys (라이브·프리뷰 공용)
  · Firestore 규칙이 브라우저 직접 접근을 모두 막으므로 서버 함수만 읽을 수 있습니다.
  · 관리자 화면에는 키 전체를 돌려주지 않고 끝 4자리만 보여 줍니다.
  · 환경 변수 AISTUDIO_API_KEY 가 있으면 그것을 우선 사용합니다(Secret Manager 로 옮길 때).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional

from . import ai
from . import store

COLLECTION = "settings"
MODEL_DOC = "ai_dev" if os.environ.get("K_SERVICE", "").endswith("dev") else "ai"
KEY_DOC = "ai_keys"
CACHE_SEC = 60                      # 같은 서버 인스턴스에서 설정을 1분간 기억 (매 요청 Firestore 읽기 방지)
KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{20,300}$")   # AIza… · AQ.… 형식 모두 허용
log = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: Dict[str, Any] = {}


def _read(doc: str) -> Dict[str, Any]:
    now = time.time()
    with _lock:
        hit = _cache.get(doc)
        if hit and now - hit[0] < CACHE_SEC:
            return dict(hit[1])
    data: Dict[str, Any] = {}
    client = store._get_client()
    if client is not None:
        try:
            snap = client.collection(COLLECTION).document(doc).get()
            data = (snap.to_dict() or {}) if snap.exists else {}
        except Exception as exc:     # 설정을 못 읽어도 기본값으로 계속 동작
            log.warning("설정 읽기 실패(%s): %s", doc, exc.__class__.__name__)
    with _lock:
        _cache[doc] = (now, data)
    return dict(data)


def _write(doc: str, fields: Dict[str, Any]) -> None:
    client = store._get_client()
    if client is None:
        raise RuntimeError("저장소(Firestore)에 연결할 수 없습니다.")
    client.collection(COLLECTION).document(doc).set(fields, merge=True)
    with _lock:
        _cache.pop(doc, None)


# ------------------------------------------------------------------ AI 모델
def get_model() -> str:
    m = str(_read(MODEL_DOC).get("model") or "")
    return m if m in ai.MODELS else ai.DEFAULT_MODEL


def set_model(model: str) -> str:
    if model not in ai.MODELS:
        raise ValueError("알 수 없는 모델입니다.")
    _write(MODEL_DOC, {"model": model, "updatedAt": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600))})
    return model


# ------------------------------------------------------------------ AI Studio 키
def get_aistudio_key() -> str:
    env = (os.environ.get("AISTUDIO_API_KEY") or "").strip()
    return env or str(_read(KEY_DOC).get("aistudio") or "").strip()


def set_aistudio_key(key: str) -> None:
    key = (key or "").strip()
    if not key:
        _write(KEY_DOC, {"aistudio": "", "updatedAt": ""})
        return
    if not KEY_RE.match(key):
        raise ValueError("API 키 형식이 올바르지 않습니다. AI Studio에서 복사한 키 전체를 붙여넣어 주세요.")
    _write(KEY_DOC, {"aistudio": key, "updatedAt": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600))})


def key_status() -> Dict[str, Any]:
    key = get_aistudio_key()
    doc = _read(KEY_DOC)
    return {"set": bool(key), "tail": key[-4:] if key else "", "updatedAt": doc.get("updatedAt") or "",
            "fromEnv": bool((os.environ.get("AISTUDIO_API_KEY") or "").strip())}


def ai_status() -> Dict[str, Any]:
    """관리자 화면용: 모델 목록·현재 선택·키 상태 (키 값 자체는 보내지 않음)"""
    return {
        "models": [{"id": k, "label": v["label"], "provider": v["provider"], "note": v.get("note", "")} for k, v in ai.MODELS.items()],
        "current": get_model(),
        "default": ai.DEFAULT_MODEL,
        "key": key_status(),
        "scope": "프리뷰" if MODEL_DOC.endswith("_dev") else "라이브",
    }


def api_key_for(model: str) -> Optional[str]:
    """해당 모델 호출에 필요한 키 (Vertex AI 는 서버 계정 인증이라 None)"""
    return get_aistudio_key() if ai.MODELS.get(model, {}).get("provider") == "aistudio" else None

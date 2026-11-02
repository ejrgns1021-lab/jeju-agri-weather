# -*- coding: utf-8 -*-
"""
auth.py  (백엔드)
공용 비밀번호 로그인 → 서명된 접속 토큰 발급 · 검증

- 토큰 = "만료시각.서명"  (서명 = HMAC-SHA256(비밀번호, 만료시각))
- 비밀번호를 바꾸면 기존 토큰은 모두 자동으로 무효가 됩니다.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

TOKEN_TTL_SEC = 12 * 3600


def check_password(given: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest((given or "").encode("utf-8"), expected.encode("utf-8"))


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"jeju-agri-weather:{payload}".encode("utf-8"), hashlib.sha256).hexdigest()


def issue_token(secret: str, now: Optional[float] = None) -> tuple[str, int]:
    exp = int((now or time.time()) + TOKEN_TTL_SEC)
    return f"{exp}.{_sign(secret, str(exp))}", exp


def verify_token(token: str, secret: str, now: Optional[float] = None) -> bool:
    if not token or not secret or "." not in token:
        return False
    exp_str, sig = token.split(".", 1)
    if not exp_str.isdigit() or int(exp_str) < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _sign(secret, exp_str))

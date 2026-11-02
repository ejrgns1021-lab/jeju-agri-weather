# -*- coding: utf-8 -*-
"""
main.py - Firebase Cloud Functions (Python) 진입점

  login    : 공용 비밀번호 확인 → 12시간짜리 접속 토큰 발급
  analyze  : 토큰 확인 → 기상청 자료 조회·가공 → 화면용 표(JSON) 반환

두 함수 모두 '호출 가능 함수(callable)' 이며, 화면(public/js/api.js)이 호출합니다.
비밀번호와 기상청 API 키는 Secret Manager 에 저장된 값을 사용합니다.
  firebase functions:secrets:set APP_PASSWORD
  firebase functions:secrets:set KMA_API_KEY
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from firebase_functions import https_fn, options
from firebase_functions.params import SecretParam

from weather import auth
from weather.kma_api import WeatherApiError, WeatherApiTimeoutError
from weather.pipeline import InputError, run_analysis, validate_request

APP_PASSWORD = SecretParam("APP_PASSWORD")
KMA_API_KEY = SecretParam("KMA_API_KEY")

REGION = "asia-northeast3"          # 서울
SERVER_DELAY_MSG = "기상청 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."

options.set_global_options(region=REGION, max_instances=5)
log = logging.getLogger("jeju-agri-weather")


def _fail(code: https_fn.FunctionsErrorCode, message: str) -> https_fn.HttpsError:
    return https_fn.HttpsError(code=code, message=message)


@https_fn.on_call(secrets=[APP_PASSWORD], memory=options.MemoryOption.MB_256, timeout_sec=30)
def login(req: https_fn.CallableRequest) -> Dict[str, Any]:
    data = req.data or {}
    expected = APP_PASSWORD.value or ""
    if not expected:
        raise _fail(https_fn.FunctionsErrorCode.FAILED_PRECONDITION,
                    "접속 비밀번호가 설정되지 않았습니다. 관리자에게 문의하세요.")
    if not auth.check_password(str(data.get("password", "")), expected):
        time.sleep(1.0)  # 무작위 대입 지연
        raise _fail(https_fn.FunctionsErrorCode.UNAUTHENTICATED, "비밀번호가 올바르지 않습니다.")
    token, exp = auth.issue_token(expected)
    return {"token": token, "expiresAt": exp}


@https_fn.on_call(secrets=[APP_PASSWORD, KMA_API_KEY], memory=options.MemoryOption.GB_1,
                  timeout_sec=540, concurrency=4, cpu=1)
def analyze(req: https_fn.CallableRequest) -> Dict[str, Any]:
    data = req.data or {}
    if not auth.verify_token(str(data.get("token", "")), APP_PASSWORD.value or ""):
        raise _fail(https_fn.FunctionsErrorCode.UNAUTHENTICATED, "로그인이 만료되었습니다. 다시 로그인해 주세요.")
    api_key = (KMA_API_KEY.value or "").strip()
    if not api_key:
        raise _fail(https_fn.FunctionsErrorCode.FAILED_PRECONDITION,
                    "기상청 API 키가 설정되지 않았습니다. 관리자에게 문의하세요.")
    try:
        query = validate_request(data.get("query") or {})
        return run_analysis(query, api_key)
    except InputError as exc:
        raise _fail(https_fn.FunctionsErrorCode.INVALID_ARGUMENT, str(exc))
    except WeatherApiTimeoutError as exc:
        log.warning("기상청 지연: %s", exc)
        raise _fail(https_fn.FunctionsErrorCode.UNAVAILABLE, f"{SERVER_DELAY_MSG} (상세: {exc})")
    except WeatherApiError as exc:
        raise _fail(https_fn.FunctionsErrorCode.FAILED_PRECONDITION, f"기상청 API 오류: {exc}")
    except Exception as exc:  # 예상치 못한 오류도 화면에 알림
        log.exception("처리 중 오류")
        raise _fail(https_fn.FunctionsErrorCode.INTERNAL, f"처리 중 오류가 발생했습니다: {exc}")

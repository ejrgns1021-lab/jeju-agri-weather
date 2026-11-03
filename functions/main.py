# -*- coding: utf-8 -*-
"""
main.py - Firebase Cloud Functions (Python) 진입점

  login    : 공용 비밀번호 확인 → 12시간짜리 접속 토큰 발급
  jejuweather : 토큰 확인 → 기상청 자료 조회·가공 → 화면용 표(JSON) 반환
               (이름에 'analyze' 등을 쓰면 브라우저 광고·추적 차단 기능이 요청을 막으므로 중립적인 이름 사용)
  jejuprogress: 조회 진행 상황(단계·진행률) 확인, 60초가 넘는 조회는 완료 후 결과도 여기서 전달

두 함수 모두 '호출 가능 함수(callable)' 이며, 화면(public/js/api.js)이 호출합니다.
비밀번호와 기상청 API 키는 Secret Manager 에 저장된 값을 사용합니다.
  firebase functions:secrets:set APP_PASSWORD
  firebase functions:secrets:set KMA_API_KEY
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict

from firebase_functions import https_fn, options
from firebase_functions.params import SecretParam

from weather import auth
from weather import store
from weather.kma_api import WeatherApiError, WeatherApiTimeoutError
from weather.pipeline import InputError, run_analysis, validate_request

APP_PASSWORD = SecretParam("APP_PASSWORD")
KMA_API_KEY = SecretParam("KMA_API_KEY")

REGION = "asia-northeast3"          # 서울
SERVER_DELAY_MSG = "기상청 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."

options.set_global_options(region=REGION, max_instances=5)
log = logging.getLogger("jeju-agri-weather")


JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _fail(code: https_fn.FunctionsErrorCode, message: str) -> https_fn.HttpsError:
    return https_fn.HttpsError(code=code, message=message)


def _redact(text: str, secret: str) -> str:
    """오류 문구·로그에 기상청 API 키가 노출되지 않도록 가림"""
    text = str(text)
    if secret:
        text = text.replace(secret, "****")
    return re.sub(r"(authKey|serviceKey)=[^&\s'\")]+", r"\1=****", text)


@https_fn.on_call(secrets=[APP_PASSWORD], invoker="public", memory=options.MemoryOption.MB_256, timeout_sec=30)
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


@https_fn.on_call(secrets=[APP_PASSWORD, KMA_API_KEY], invoker="public", memory=options.MemoryOption.GB_1,
                  timeout_sec=540, concurrency=4, cpu=1)
def jejuweather(req: https_fn.CallableRequest) -> Dict[str, Any]:
    data = req.data or {}
    if not auth.verify_token(str(data.get("token", "")), APP_PASSWORD.value or ""):
        raise _fail(https_fn.FunctionsErrorCode.UNAUTHENTICATED, "로그인이 만료되었습니다. 다시 로그인해 주세요.")
    api_key = (KMA_API_KEY.value or "").strip()
    if not api_key:
        raise _fail(https_fn.FunctionsErrorCode.FAILED_PRECONDITION,
                    "기상청 API 키가 설정되지 않았습니다. 관리자에게 문의하세요.")
    job_id = str(data.get("jobId") or "")
    if not JOB_ID_RE.match(job_id):
        job_id = ""
    started = time.time()

    def progress(pct: int, stage: str, detail: str) -> None:
        store.job_update(job_id, status="running", pct=int(pct), stage=stage, detail=_redact(detail, api_key),
                         elapsed=round(time.time() - started, 1))

    def fail(code: https_fn.FunctionsErrorCode, message: str) -> https_fn.HttpsError:
        store.job_finish(job_id, error={"status": code.value if hasattr(code, "value") else str(code), "message": message})
        return _fail(code, message)

    store.job_update(job_id, force=True, status="running", pct=0, stage="조회 준비", detail="요청을 받았습니다", elapsed=0)
    try:
        query = validate_request(data.get("query") or {})
        result = run_analysis(query, api_key, progress=progress)
        store.job_finish(job_id, result=result)
        return result
    except InputError as exc:
        raise fail(https_fn.FunctionsErrorCode.INVALID_ARGUMENT, str(exc))
    except WeatherApiTimeoutError as exc:
        detail = _redact(exc, api_key)
        log.warning("기상청 지연: %s", detail)
        raise fail(https_fn.FunctionsErrorCode.UNAVAILABLE, f"{SERVER_DELAY_MSG} (상세: {detail})")
    except WeatherApiError as exc:
        raise fail(https_fn.FunctionsErrorCode.FAILED_PRECONDITION, f"기상청 API 오류: {_redact(exc, api_key)}")
    except Exception as exc:  # 예상치 못한 오류도 화면에 알림
        log.error("처리 중 오류: %s", _redact(repr(exc), api_key))
        raise fail(https_fn.FunctionsErrorCode.INTERNAL, f"처리 중 오류가 발생했습니다: {_redact(exc, api_key)}")


@https_fn.on_call(secrets=[APP_PASSWORD], invoker="public", memory=options.MemoryOption.MB_256, timeout_sec=30, concurrency=20)
def jejuprogress(req: https_fn.CallableRequest) -> Dict[str, Any]:
    """화면이 1~2초마다 호출: 진행 상황, 완료 시 결과(60초가 넘어 본 요청이 끊긴 경우 대비)"""
    data = req.data or {}
    if not auth.verify_token(str(data.get("token", "")), APP_PASSWORD.value or ""):
        raise _fail(https_fn.FunctionsErrorCode.UNAUTHENTICATED, "로그인이 만료되었습니다. 다시 로그인해 주세요.")
    job_id = str(data.get("jobId") or "")
    if not JOB_ID_RE.match(job_id):
        raise _fail(https_fn.FunctionsErrorCode.INVALID_ARGUMENT, "잘못된 작업 번호입니다.")
    job = store.job_get(job_id)
    if not job:
        return {"status": "pending"}
    out = {k: job.get(k) for k in ("status", "pct", "stage", "detail", "elapsed", "error", "too_large")}
    if job.get("status") == "done" and job.get("result") and data.get("withResult"):
        out["result"] = json.loads(job["result"])
    return out

# -*- coding: utf-8 -*-
"""
백엔드 동작 확인 (기상청 서버 없이 가짜 응답으로 실행)
  cd functions && python -m pytest -q tests      또는   python tests/test_backend.py
"""
from __future__ import annotations

import datetime as dt
import os
import random
import sys

os.environ["FIRESTORE_CACHE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weather import auth, kma_api, pipeline  # noqa: E402
from weather.kma_api import DEFAULT_DAILY_COLS, DEFAULT_NORMAL_COLS, METRICS, STATIONS  # noqa: E402

CALLS = {"daily": 0, "normal": 0}


class FakeResp:
    def __init__(self, text: str):
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.text = text


def _daily_text(stn: int, tm1: str, tm2: str) -> str:
    d = dt.datetime.strptime(tm1, "%Y%m%d").date()
    e = dt.datetime.strptime(tm2, "%Y%m%d").date()
    lines = ["#START7777", "# " + " ".join(DEFAULT_DAILY_COLS)]
    while d <= e:
        rnd = random.Random(f"{stn}{d}")
        ta = 15 + 8 * rnd.random()
        vals = {c: "-9" for c in DEFAULT_DAILY_COLS}
        vals.update({"TM": d.strftime("%Y%m%d"), "STN": str(stn), "WS_AVG": "3.2", "TA_AVG": f"{ta:.1f}",
                     "TA_MAX": f"{ta + 4:.1f}", "TA_MIN": f"{ta - 4:.1f}", "HM_AVG": "70.0",
                     "SS_DAY": f"{rnd.random() * 9:.1f}", "SS_DUR": "12.5",
                     "RN_DAY": f"{rnd.random() * 20:.1f}" if rnd.random() < 0.3 else "-9"})
        lines.append(" ".join(vals[c] for c in DEFAULT_DAILY_COLS))
        d += dt.timedelta(days=1)
    lines.append("#7777END")
    return "\n".join(lines)


def _normal_text(mm1: int, dd1: int, mm2: int, dd2: int) -> str:
    lines = ["#START7777", "# " + " ".join(DEFAULT_NORMAL_COLS)]
    d = dt.date(2021, mm1, dd1)
    end = dt.date(2021, mm2, dd2)
    while d <= end:
        for stn in STATIONS.values():
            v = {"ST": "1", "STN": str(stn), "MM": str(d.month), "DD": str(d.day), "TA": "18.0", "TA_MAX": "22.0",
                 "TA_MIN": "14.5", "HM": "72.0", "RN": "4.0", "RN_DUR": "3.0", "EV": "2.5", "WS": "3.5",
                 "SS": "5.5", "SI": "14.0", "PV": "15.0"}
            lines.append(" ".join(v[c] for c in DEFAULT_NORMAL_COLS))
        d += dt.timedelta(days=1)
    lines.append("#7777END")
    return "\n".join(lines)


def fake_http_get(url, params, timeout=20):
    if url == kma_api.APIHUB_DAILY_URL:
        CALLS["daily"] += 1
        return FakeResp(_daily_text(int(params["stn"]), params["tm1"], params["tm2"]))
    if url == kma_api.APIHUB_NORMAL_URL:
        CALLS["normal"] += 1
        return FakeResp(_normal_text(int(params["MM1"]), int(params["DD1"]), int(params["MM2"]), int(params["DD2"])))
    raise AssertionError(url)


kma_api._http_get = fake_http_get


def test_wind_removed():
    assert "풍속" not in METRICS
    assert "avgWs" not in kma_api.METRIC_COLS


def test_auth_token():
    tok, exp = auth.issue_token("pw", now=1000)
    assert auth.verify_token(tok, "pw", now=1001)
    assert not auth.verify_token(tok, "other", now=1001)
    assert not auth.verify_token(tok, "pw", now=exp + 1)
    assert not auth.verify_token("garbage", "pw")
    assert auth.check_password("pw", "pw") and not auth.check_password("x", "pw")


def test_validation():
    today = pipeline.today_kst()
    bad = [
        {"start": "2025-05-10", "end": "2025-05-01"},
        {"start": "2024-01-01", "end": "2025-03-01"},
        {"start": today.isoformat(), "end": (today + dt.timedelta(days=1)).isoformat()},
    ]
    for b in bad:
        try:
            pipeline.validate_request({**b, "metrics": ["평균 기온"], "stations": ["제주"]})
            raise AssertionError("검증 실패")
        except pipeline.InputError:
            pass
    try:
        pipeline.validate_request({"start": "2025-05-01", "end": "2025-05-07", "metrics": ["풍속"], "stations": ["제주"]})
        raise AssertionError("풍속이 허용됨")
    except pipeline.InputError:
        pass


def test_full_pipeline():
    req = pipeline.validate_request({
        "start": "2025-05-01", "end": "2025-05-07", "metrics": list(METRICS), "stations": list(STATIONS),
        "source": "apihub", "normalMode": "api", "extreme": False, "rainZero": True})
    res = pipeline.run_analysis(req, "TESTKEY")
    assert res["ok"], res["notices"]
    assert res["summary"]["gotDays"] == 7
    cmp_all = res["comparison"]["all"]
    labels = [r[0] for r in cmp_all["rows"]]
    assert labels == ["평균 기온(℃)", "최고 기온(℃)", "최저 기온(℃)", "누적 강수량(mm)", "일조 시간(hr)"], labels
    assert set(res["comparison"]) == set(pipeline.LAYOUTS)
    # 평년값: 평균 기온 18.0 (7일 평균), 강수 4.0*7=28.0, 일조 5.5*7=38.5
    row = dict(zip(cmp_all["columns"], cmp_all["rows"][0]))
    assert row["평년 평균"] == "18.0", row
    assert dict(zip(cmp_all["columns"], cmp_all["rows"][3]))["평년 평균"] == "28.0"
    assert dict(zip(cmp_all["columns"], cmp_all["rows"][4]))["평년 평균"] == "38.5"
    # 편차·비율 검산 (표시 숫자 기준)
    cur, nor = float(row["해당 연도(2025)"]), float(row["평년 평균"])
    assert abs(float(row["평년 대비"]) - round(cur - nor, 1)) < 1e-9
    assert abs(float(row["평년대비(비율)"]) - round(cur / nor * 100, 1)) < 1e-9
    assert all(not any("풍속" in c for c in t["columns"]) for t in [cmp_all, res["station"], res["raw"]])
    assert set(res["daily"]) == set(METRICS)
    assert res["recent"] and len(res["recent"]["rows"]) == 5
    assert res["station"]["rows"][-1][0] == "제주 전체 평균"


def test_compute_mode_uses_cache():
    CALLS["daily"] = 0
    req = pipeline.validate_request({
        "start": "2025-06-01", "end": "2025-06-03", "metrics": ["평균 기온", "누적 강수량"], "stations": ["제주", "고산"],
        "source": "apihub", "normalMode": "compute"})
    res = pipeline.run_analysis(req, "TESTKEY")
    first = CALLS["daily"]
    assert res["ok"] and res["normal30"] and len(res["normal30"]["rows"]) == 30
    pipeline.run_analysis(req, "TESTKEY")
    assert CALLS["daily"] - first <= 1, "두 번째 조회는 캐시를 써야 함 (진단용 원문 1회 제외)"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("통과:", name)

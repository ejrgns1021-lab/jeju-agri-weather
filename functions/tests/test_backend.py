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
import pandas as pd  # noqa: E402
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
        ta = 15 + 8 * rnd.random() + (10 if d.month in (7, 8) else 0)   # 여름은 열대야가 생기도록
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
    d = dt.date(2020, mm1, dd1)
    end = dt.date(2020, mm2, dd2)
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
    assert labels == ["평균 기온(℃)", "최고 기온(℃)", "최저 기온(℃)", "누적 강수량(mm)", "일조 시간(hr)", "강우일수(일)", "열대야 일수(일)"], labels
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


def test_rain_days():
    """강우일수 = 일강수량 0.1mm 이상인 날 수, 제주 전체는 지점별 강우일수의 평균"""
    df, _ = kma_api.fetch_all_stations(dt.date(2025, 7, 1), dt.date(2025, 7, 20), "K", "apihub", [184, 189])
    for stn in (184, 189):
        sub = df[df["stn_id"] == stn]
        assert int(sub["rainDays"].sum()) == int((sub["sumRn"] >= 0.1).sum())
    from weather.processing import jeju_daily_mean, aggregate_period
    daily = jeju_daily_mean(df)
    jeju = aggregate_period(daily, ["강우일수"])["강우일수"]
    per_station = df.groupby("stn_id")["rainDays"].sum().mean()
    assert abs(jeju - per_station) < 1e-9, (jeju, per_station)
    # 평년 강우일수: 평년값 API 에 없으므로 30년 실측으로 대체 계산되어야 함
    req = pipeline.validate_request({"start": "2025-07-01", "end": "2025-07-05", "metrics": ["강우일수"],
                                     "stations": ["제주"], "normalMode": "api"})
    res = pipeline.run_analysis(req, "K")
    row = dict(zip(res["comparison"]["all"]["columns"], res["comparison"]["all"]["rows"][0]))
    assert row["평년 평균"] != "-", row
    assert any("강우일수" in n["text"] and "30년 실측" in n["text"] for n in res["notices"]), res["notices"]


def test_dekad_periods():
    from weather.dekad import dekad_periods
    D = dt.date
    ps = dekad_periods(D(2024, 2, 5), D(2024, 3, 12))           # 윤년 2월, 부분 순 포함
    assert [p["label"] for p in ps] == ["2024년 2월 상순", "2024년 2월 중순", "2024년 2월 하순", "2024년 3월 상순", "2024년 3월 중순"]
    assert (ps[0]["start"], ps[0]["days"], ps[0]["partial"]) == (D(2024, 2, 5), 6, True)
    assert (ps[2]["end"], ps[2]["days"], ps[2]["partial"]) == (D(2024, 2, 29), 9, False)
    assert (ps[4]["end"], ps[4]["days"], ps[4]["partial"]) == (D(2024, 3, 12), 2, True)
    assert dekad_periods(D(2025, 2, 21), D(2025, 2, 28))[0]["days"] == 8       # 평년 2월 하순
    assert [p["days"] for p in dekad_periods(D(2025, 1, 1), D(2025, 1, 31))] == [10, 10, 11]
    ys = dekad_periods(D(2025, 12, 25), D(2026, 1, 3))           # 연도를 넘는 기간
    assert [p["label"] for p in ys] == ["2025년 12월 하순", "2026년 1월 상순"]
    assert dekad_periods(D(2025, 5, 3), D(2025, 5, 1)) == []                   # 잘못된 기간


def test_dekad_tables_consistent():
    """순별 합계·평균이 기간 전체 값과 일치해야 함"""
    req = pipeline.validate_request({"start": "2025-07-05", "end": "2025-08-14", "metrics": list(METRICS),
                                     "stations": list(STATIONS), "normalMode": "api"})
    res = pipeline.run_analysis(req, "TESTKEY")
    dk = res["dekad"]
    assert dk and dk["count"] == 5 and dk["partial"] == ["2025년 7월 상순", "2025년 8월 중순"], dk and dk["partial"]
    ov = dk["overview"]
    col = lambda name: ov["columns"].index(name)
    rain_total = sum(float(r[col("누적 강수량(mm)")]) for r in ov["rows"])
    cmp_row = {r[0]: r for r in res["comparison"]["all"]["rows"]}
    tol = 0.05 * len(ov["rows"]) + 0.051   # 순별 값은 소수 첫째 자리 반올림 후 합산되므로 반올림 오차 허용
    assert abs(rain_total - float(cmp_row["누적 강수량(mm)"][1])) <= tol, rain_total
    tn_total = sum(float(r[col("열대야 일수(일)")]) for r in ov["rows"])
    assert abs(tn_total - float(cmp_row["열대야 일수(일)"][1])) <= tol
    # 일수 가중 평균 기온 = 전체 평균 기온
    days = [int(r[col("일수")].split("일")[0]) for r in ov["rows"]]
    tavg = sum(float(r[col("평균 기온(℃)")]) * d for r, d in zip(ov["rows"], days)) / sum(days)
    assert abs(tavg - float(cmp_row["평균 기온(℃)"][1])) <= 0.06
    for lay in ("combined", "all"):
        tb = dk["byMetric"]["평균 기온"][lay]
        assert all("nan" not in r for r in tb["rows"]), (lay, tb["columns"])
    t = dk["byMetric"]["평균 기온"]["all"]
    row = dict(zip(t["columns"], t["rows"][0]))
    assert row["평년 평균"] != "-" and row["전년(2024)"] != "-", row
    assert abs(float(row["평년 대비"]) - round(float(row["해당 연도(2025)"]) - float(row["평년 평균"]), 1)) < 1e-9


def test_tropical_nights():
    df, _ = kma_api.fetch_all_stations(dt.date(2025, 8, 1), dt.date(2025, 8, 20), "K", "apihub", [184])
    assert int(df["tropicalNights"].sum()) == int((df["minTa"] >= 25.0).sum())
    edge = pd.DataFrame({"minTa": [25.0, 24.9, None], "sumRn": [0.0, 0.1, None]})
    kma_api.add_derived_metrics(edge)
    assert edge["tropicalNights"].tolist()[:2] == [1.0, 0.0] and pd.isna(edge["tropicalNights"][2])
    assert edge["rainDays"].tolist()[:2] == [0.0, 1.0] and pd.isna(edge["rainDays"][2])


# ---------------------------------------------------------------- 예보 (v6.9)
def _fake_forecast_get(url, params=None, timeout=None):
    import json as _j
    from weather import forecast as fc
    class R:
        def __init__(self, obj, code=200): self.status_code = code; self.text = _j.dumps(obj, ensure_ascii=False); self._o = obj
        def json(self): return self._o
    ok = lambda items: R({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}, "totalCount": len(items)}}})
    if url == fc.VILAGE_URL:
        items = []
        for d in ("20261105", "20261106", "20261107"):
            for h in range(0, 24):
                hh = f"{h:02d}00"
                items += [{"fcstDate": d, "fcstTime": hh, "category": "TMP", "fcstValue": str(10 + h % 8)},
                          {"fcstDate": d, "fcstTime": hh, "category": "SKY", "fcstValue": "4" if d == "20261106" else "1"},
                          {"fcstDate": d, "fcstTime": hh, "category": "PTY", "fcstValue": "1" if d == "20261106" and 12 <= h < 18 else "0"},
                          {"fcstDate": d, "fcstTime": hh, "category": "POP", "fcstValue": "70" if d == "20261106" else "10"},
                          {"fcstDate": d, "fcstTime": hh, "category": "PCP", "fcstValue": "1.0mm" if d == "20261106" and 12 <= h < 18 else "강수없음"}]
            if d != "20261105":
                items += [{"fcstDate": d, "fcstTime": "0600", "category": "TMN", "fcstValue": "9.0"},
                          {"fcstDate": d, "fcstTime": "1500", "category": "TMX", "fcstValue": "19.0"}]
        return ok(items)
    if url == fc.MID_LAND_URL:
        it = {"regId": "11G00000"}
        for n in range(4, 8):
            it.update({f"rnSt{n}Am": 20, f"rnSt{n}Pm": 30, f"wf{n}Am": "맑음", f"wf{n}Pm": "구름많음"})
        for n in range(8, 11):
            it.update({f"rnSt{n}": 60, f"wf{n}": "흐리고 비"})
        return ok([it])
    if url == fc.MID_TA_URL:
        it = {"regId": params["regId"]}
        for n in range(4, 11):
            it.update({f"taMin{n}": 12, f"taMax{n}": 18})
        return ok([it])
    raise AssertionError(url)


def test_forecast():
    import datetime as _dt
    from weather import forecast as fc
    orig = fc.requests.get
    fc.requests.get = _fake_forecast_get
    try:
        now = _dt.datetime(2026, 11, 5, 8, 20, tzinfo=fc.KST)
        assert fc.vilage_base(now) == ("20261105", "0800") and fc.vilage_base(now.replace(hour=8, minute=5)) == ("20261105", "0500")
        assert fc.mid_tmfc(now).hour == 6 and fc.mid_tmfc(now.replace(hour=6, minute=10)).hour == 18
        assert fc.latlon_to_grid(33.5141, 126.5297) == (53, 38)
        res = fc.build_forecast("K", now=now)
        assert [s["name"] for s in res["short"]["stations"]] == ["제주", "서귀포", "성산", "고산"]
        days = res["short"]["jeju"]
        assert days[0]["label"].startswith("오늘") and days[0]["minMaxPartial"]
        d2 = days[1]
        assert d2["tmin"] == 9.0 and d2["tmax"] == 19.0 and d2["pop"] == 70 and "비" in d2["pm"] and d2["am"] == "흐림"
        assert d2["icon"] == "cloud-rain" and d2["rainMm"] == 6.0
        mid = res["mid"]["days"]
        assert mid[0]["date"] == "2026-11-09" and mid[0]["pop"] == 30 and mid[0]["am"] == "맑음"
        assert mid[-1]["date"] == "2026-11-15" and mid[-1]["pm"] is None and mid[-1]["pop"] == 60
        assert [p["name"] for p in mid[0]["stations"]] == ["제주", "서귀포", "성산", "고산"]
        assert res["errors"] == {} and "warn" not in res
    finally:
        fc.requests.get = orig


# ---------------------------------------------------------------- AI 기상분석 (v7.0)
def test_ai_digest():
    from weather import ai
    req = pipeline.validate_request({
        "start": "2025-07-01", "end": "2025-08-31", "metrics": list(METRICS), "stations": list(STATIONS),
        "source": "apihub", "normalMode": "api", "extreme": False, "rainZero": True})
    res = pipeline.run_analysis(req, "TESTKEY")
    dg = ai.build_digest(res)
    assert "기간 전체 비교표" in dg and "순별" in dg and "폭염일" in dg and "열대야" in dg and "무강수" in dg
    assert "2025년 7월 상순" in dg and len(dg) < 40000
    assert "전년" not in dg.split("## 4.")[0] and "평년" not in dg.split("## 4.")[0] and "최근 5년" in dg
    assert ai.valid_result(res, req) and not ai.valid_result({"params": {"start": "2024-01-01"}}, req) and not ai.valid_result(None, req)
    # 연속 구간 찾기
    d = [f"2025-07-{i:02d}" for i in range(1, 11)]
    assert ai._spells(d, [True] * 3 + [False] + [True] * 6, 3) == [("2025-07-01", "2025-07-03", 3), ("2025-07-05", "2025-07-10", 6)]
    assert ai._spells(d, [False] * 10, 1) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("통과:", name)

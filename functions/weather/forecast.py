# -*- coding: utf-8 -*-
"""
forecast.py - 대시보드 상단 기상 예보 (v6.9)

  단기예보 : 기상청 API허브 VilageFcstInfoService_2.0/getVilageFcst (격자 좌표, 오늘~모레/글피)
  중기예보 : MidFcstInfoService/getMidLandFcst(제주도 날씨·강수확률) + getMidTa(지점별 기온), 3~10일 뒤
  기상특보 : WthrWrnInfoService/getPwnStatus (전국 특보 현황 중 '제주' 관련 줄만)

기존 관측 자료 조회·계산 로직(kma_api / processing / pipeline)과는 완전히 별개입니다.
세 종류는 따로 받아서, 하나가 실패해도 나머지는 표시합니다.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .kma_api import STATIONS

KST = dt.timezone(dt.timedelta(hours=9))
BASE = "https://apihub.kma.go.kr/api/typ02/openApi"
VILAGE_URL = f"{BASE}/VilageFcstInfoService_2.0/getVilageFcst"
MID_LAND_URL = f"{BASE}/MidFcstInfoService/getMidLandFcst"
MID_TA_URL = f"{BASE}/MidFcstInfoService/getMidTa"
WRN_URL = f"{BASE}/WthrWrnInfoService/getPwnStatus"

CONNECT_SEC, READ_SEC, RETRY = 5, 15, 2

# 지점 위·경도(기상청 ASOS) → 단기예보 격자
STATION_LATLON: Dict[str, Tuple[float, float]] = {
    "제주": (33.5141, 126.5297), "서귀포": (33.2462, 126.5653),
    "성산": (33.3868, 126.8802), "고산": (33.2938, 126.1628),
}
MID_LAND_REG = "11G00000"            # 중기 육상예보: 제주도
MID_TA_REG = {"제주": "11G00201", "서귀포": "11G00401", "성산": "11G00101", "고산": "11G00501"}  # 중기 기온

SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY = {"1": "비", "2": "비/눈", "3": "눈", "4": "소나기", "5": "빗방울", "6": "빗방울눈날림", "7": "눈날림"}
DAY_LABEL = ["오늘", "내일", "모레", "글피"]
WEEKDAY = "월화수목금토일"


class ForecastError(Exception):
    """화면에 그대로 보여줄 수 있는 짧은 문구"""


# ------------------------------------------------------------------ 공통
def _get(url: str, params: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """기상청 typ02(JSON) 호출 → body. 활용신청 필요(403)·오류 문구를 구분해 ForecastError로"""
    p = dict(params, authKey=api_key, dataType="JSON")
    last = None
    for _ in range(RETRY + 1):
        try:
            r = requests.get(url, params=p, timeout=(CONNECT_SEC, READ_SEC))
        except requests.exceptions.RequestException as exc:
            last = exc
            time.sleep(0.7)
            continue
        text = r.text or ""
        if r.status_code == 403 or "활용신청" in text:
            raise ForecastError("기상청 API허브에서 이 예보 API 활용신청이 필요합니다.")
        if r.status_code in (401,):
            raise ForecastError("API 키 인증에 실패했습니다.")
        if r.status_code >= 500:
            last = ForecastError(f"기상청 서버 응답 오류 (HTTP {r.status_code})")
            time.sleep(0.7)
            continue
        try:
            js = r.json()
        except ValueError:
            raise ForecastError(f"예보 응답을 읽을 수 없습니다. (HTTP {r.status_code})")
        resp = js.get("response") or {}
        head = resp.get("header") or {}
        code = str(head.get("resultCode", ""))
        if code == "03":                      # NODATA_ERROR: 아직 발표 전
            return {"items": []}
        if code and code != "00":
            raise ForecastError(f"기상청 예보 오류: {head.get('resultMsg', code)}")
        body = resp.get("body") or {}
        items = (body.get("items") or {})
        items = items.get("item", []) if isinstance(items, dict) else (items or [])
        if isinstance(items, dict):
            items = [items]
        return {"items": items, "total": int(body.get("totalCount") or len(items))}
    raise ForecastError("기상청 예보 서버에 연결할 수 없습니다.") if not isinstance(last, ForecastError) else last


def latlon_to_grid(lat: float, lon: float) -> Tuple[int, int]:
    """기상청 단기예보 격자(Lambert Conformal Conic) 변환 — 기상청 제공 공식"""
    RE, GRID, S1, S2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    d = math.pi / 180.0
    re_ = RE / GRID
    s1, s2, olon, olat = S1 * d, S2 * d, OLON * d, OLAT * d
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(math.tan(math.pi * 0.25 + s2 * 0.5) / math.tan(math.pi * 0.25 + s1 * 0.5))
    sf = math.tan(math.pi * 0.25 + s1 * 0.5) ** sn * math.cos(s1) / sn
    ro = re_ * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn
    ra = re_ * sf / math.tan(math.pi * 0.25 + lat * d * 0.5) ** sn
    th = lon * d - olon
    if th > math.pi:
        th -= 2 * math.pi
    if th < -math.pi:
        th += 2 * math.pi
    th *= sn
    return int(ra * math.sin(th) + XO + 0.5), int(ro - ra * math.cos(th) + YO + 0.5)


def vilage_base(now: dt.datetime) -> Tuple[str, str]:
    """가장 최근 단기예보 발표 시각 (02·05·…·23시, 발표 후 약 10분 뒤 제공)"""
    t = now - dt.timedelta(minutes=15)
    hours = [h for h in (2, 5, 8, 11, 14, 17, 20, 23) if h <= t.hour]
    if hours:
        return t.strftime("%Y%m%d"), f"{hours[-1]:02d}00"
    return (t - dt.timedelta(days=1)).strftime("%Y%m%d"), "2300"


def mid_tmfc(now: dt.datetime) -> dt.datetime:
    """가장 최근 중기예보 발표 시각 (06·18시, 발표 후 여유 40분)"""
    t = now - dt.timedelta(minutes=40)
    if t.hour >= 18:
        return t.replace(hour=18, minute=0, second=0, microsecond=0)
    if t.hour >= 6:
        return t.replace(hour=6, minute=0, second=0, microsecond=0)
    return (t - dt.timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)


def _date_label(d: dt.date, today: dt.date) -> str:
    n = (d - today).days
    base = f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"
    return f"{DAY_LABEL[n]} {base}" if 0 <= n < len(DAY_LABEL) else base


def _num(v: Any) -> Optional[float]:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return None if x <= -900 or x >= 900 else x


def _rain_mm(v: Any) -> float:
    """PCP 문자열 → mm ('강수없음'=0, '1mm 미만'/'1.0mm 미만'=0.5, '30.0~50.0mm'=30, '50.0mm 이상'=50)"""
    s = str(v or "").strip()
    if not s or "없음" in s:
        return 0.0
    if "미만" in s:
        return 0.5
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else 0.0


def _weather_text(skies: List[str], ptys: List[str]) -> str:
    """구간(오전/오후) 대표 날씨: 강수형태가 있으면 그것(가장 흔한 것), 없으면 가장 흔한 하늘상태"""
    rain = [p for p in ptys if p and p != "0"]
    if rain:
        kind = PTY.get(Counter(rain).most_common(1)[0][0], "비")
        sky = Counter(skies).most_common(1)[0][0] if skies else "4"
        prefix = "흐리고 " if sky == "4" else "구름많고 " if sky == "3" else ""
        return prefix + kind
    if not skies:
        return "-"
    # 같은 빈도면 더 흐린 쪽
    c = Counter(skies)
    best = max(c.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return SKY.get(best, "-")


def weather_icon(text: str) -> str:
    """화면 아이콘 이름 (icons.js)"""
    if "눈" in text:
        return "snowflake"
    if "비" in text or "소나기" in text or "빗방울" in text:
        return "cloud-rain"
    if "흐림" in text or text.startswith("흐리"):
        return "cloud"
    if "구름" in text:
        return "cloud-sun"
    return "sun"


# ------------------------------------------------------------------ 단기예보
def summarize_vilage(items: List[Dict[str, Any]], today: dt.date) -> List[Dict[str, Any]]:
    """시간별 항목 → 날짜별 요약 (오전 06~11시 / 오후 12~17시 날씨, 최고·최저, 강수확률, 예상 강수량)"""
    by_day: Dict[str, Dict[str, Any]] = {}
    for it in items:
        d, h, cat, val = str(it.get("fcstDate")), str(it.get("fcstTime", "0000"))[:2], it.get("category"), it.get("fcstValue")
        day = by_day.setdefault(d, {"TMP": [], "TMN": None, "TMX": None, "POP": [], "PCP": 0.0,
                                    "am": {"SKY": [], "PTY": [], "POP": []}, "pm": {"SKY": [], "PTY": [], "POP": []}})
        hh = int(h) if h.isdigit() else 0
        part = "am" if 6 <= hh < 12 else "pm" if 12 <= hh < 18 else None
        if cat == "TMP":
            x = _num(val)
            if x is not None:
                day["TMP"].append(x)
        elif cat in ("TMN", "TMX"):
            day[cat] = _num(val)
        elif cat == "POP":
            x = _num(val)
            if x is not None:
                day["POP"].append(x)
                if part:
                    day[part]["POP"].append(x)
        elif cat == "PCP":
            day["PCP"] += _rain_mm(val)
        elif cat in ("SKY", "PTY") and part:
            day[part][cat].append(str(val))
    out = []
    for d in sorted(by_day):
        try:
            date = dt.datetime.strptime(d, "%Y%m%d").date()
        except ValueError:
            continue
        if date < today:
            continue
        v = by_day[d]
        tmin = v["TMN"] if v["TMN"] is not None else (min(v["TMP"]) if v["TMP"] else None)
        tmax = v["TMX"] if v["TMX"] is not None else (max(v["TMP"]) if v["TMP"] else None)
        am = _weather_text(v["am"]["SKY"], v["am"]["PTY"]) if v["am"]["SKY"] or v["am"]["PTY"] else None
        pm = _weather_text(v["pm"]["SKY"], v["pm"]["PTY"]) if v["pm"]["SKY"] or v["pm"]["PTY"] else None
        if am is None and pm is None:
            continue                      # 발표 끝자락의 몇 시간만 있는 날(날씨 없음)은 중기예보와 겹치므로 제외
        main = pm or am or "-"
        out.append({
            "date": date.isoformat(), "label": _date_label(date, today),
            "am": am, "pm": pm, "wx": main, "icon": weather_icon(main),
            "tmin": None if tmin is None else round(tmin, 1),
            "tmax": None if tmax is None else round(tmax, 1),
            "minMaxPartial": v["TMN"] is None or v["TMX"] is None,   # 오늘처럼 발표에서 빠진 경우 시간별 값으로 대신함
            "popAm": max(v["am"]["POP"]) if v["am"]["POP"] else None,
            "popPm": max(v["pm"]["POP"]) if v["pm"]["POP"] else None,
            "pop": max(v["POP"]) if v["POP"] else None,
            "rainMm": round(v["PCP"], 1),
        })
    return out


def fetch_vilage(api_key: str, now: dt.datetime) -> Dict[str, Any]:
    base_date, base_time = vilage_base(now)
    stations = []
    for name in STATIONS:
        nx, ny = latlon_to_grid(*STATION_LATLON[name])
        res = _get(VILAGE_URL, {"pageNo": 1, "numOfRows": 1500, "base_date": base_date,
                                "base_time": base_time, "nx": nx, "ny": ny}, api_key)
        items = res["items"]
        if res.get("total", 0) > len(items):     # 1500행을 넘으면 다음 쪽도 받음
            res2 = _get(VILAGE_URL, {"pageNo": 2, "numOfRows": 1500, "base_date": base_date,
                                     "base_time": base_time, "nx": nx, "ny": ny}, api_key)
            items = items + res2["items"]
        stations.append({"name": name, "grid": [nx, ny], "days": summarize_vilage(items, now.date())})
    return {"base": f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]} {base_time[:2]}:00",
            "stations": stations, "jeju": combine_days(stations)}


def combine_days(stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """제주 전체 한 줄용: 날짜별로 4개 지점 최저의 최저·최고의 최고, 강수확률 최대, 날씨는 제주 지점 기준(없으면 첫 지점)"""
    dates: Dict[str, List[Dict[str, Any]]] = {}
    for s in stations:
        for d in s["days"]:
            dates.setdefault(d["date"], []).append((s["name"], d))
    out = []
    for date in sorted(dates):
        rows = dates[date]
        ref = next((d for n, d in rows if n == "제주"), rows[0][1])
        tmins = [d["tmin"] for _, d in rows if d["tmin"] is not None]
        tmaxs = [d["tmax"] for _, d in rows if d["tmax"] is not None]
        pops = [d["pop"] for _, d in rows if d["pop"] is not None]
        rainy = [n for n, d in rows if d["wx"] and ("비" in d["wx"] or "눈" in d["wx"] or "소나기" in d["wx"])]
        out.append({**ref, "tmin": min(tmins) if tmins else None, "tmax": max(tmaxs) if tmaxs else None,
                    "pop": max(pops) if pops else None, "rainAt": rainy})
    return out


# ------------------------------------------------------------------ 중기예보
def _pick(item: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in item and item[k] not in (None, ""):
            return item[k]
    return None


def fetch_mid(api_key: str, now: dt.datetime) -> Dict[str, Any]:
    tm = mid_tmfc(now)
    tmfc = tm.strftime("%Y%m%d%H%M")
    land = _get(MID_LAND_URL, {"pageNo": 1, "numOfRows": 10, "regId": MID_LAND_REG, "tmFc": tmfc}, api_key)["items"]
    land = land[0] if land else {}
    temps: Dict[str, Dict[str, Any]] = {}
    for name in STATIONS:
        rows = _get(MID_TA_URL, {"pageNo": 1, "numOfRows": 10, "regId": MID_TA_REG[name], "tmFc": tmfc}, api_key)["items"]
        temps[name] = rows[0] if rows else {}
    days = []
    for n in range(3, 11):                       # 발표일 기준 n일 뒤 (기상청은 4~10일 또는 3~10일 제공)
        date = (tm + dt.timedelta(days=n)).date()
        if date <= now.date() + dt.timedelta(days=2):
            continue                              # 단기예보와 겹치는 날은 생략
        wf_am = _pick(land, f"wf{n}Am", f"wf{n}")          # 8일 뒤부터는 하루 한 값(wf8)만 발표
        wf_pm = _pick(land, f"wf{n}Pm")
        pop_am = _num(_pick(land, f"rnSt{n}Am", f"rnSt{n}"))
        pop_pm = _num(_pick(land, f"rnSt{n}Pm"))
        per = []
        for name in STATIONS:
            t = temps.get(name) or {}
            per.append({"name": name, "tmin": _num(t.get(f"taMin{n}")), "tmax": _num(t.get(f"taMax{n}"))})
        if wf_am is None and wf_pm is None and all(p["tmin"] is None and p["tmax"] is None for p in per):
            continue
        main = wf_pm or wf_am or "-"
        tmins = [p["tmin"] for p in per if p["tmin"] is not None]
        tmaxs = [p["tmax"] for p in per if p["tmax"] is not None]
        pops = [x for x in (pop_am, pop_pm) if x is not None]
        days.append({"date": date.isoformat(), "label": _date_label(date, now.date()),
                     "am": wf_am, "pm": wf_pm,
                     "wx": main, "icon": weather_icon(main),
                     "popAm": pop_am, "popPm": pop_pm, "pop": max(pops) if pops else None,
                     "tmin": min(tmins) if tmins else None, "tmax": max(tmaxs) if tmaxs else None,
                     "stations": per})
    return {"base": tm.strftime("%Y-%m-%d %H:%M"), "days": days}


# ------------------------------------------------------------------ 기상특보
_LINE = re.compile(r"^\s*o\s*(?P<kind>[^:：]+?)\s*[:：]\s*(?P<regions>.+)$")


def parse_warnings(text: str) -> List[Dict[str, str]]:
    """'o 강풍주의보 : 제주도(제주도남부, 제주도산지), 전라남도(…)' 형식에서 제주 관련 부분만"""
    out = []
    if not text:
        return out
    for raw in re.split(r"[\r\n]+", text):
        m = _LINE.match(raw)
        if not m:
            continue
        kind, regions = m.group("kind").strip(), m.group("regions").strip()
        if kind in ("없음",) or not regions or regions == "없음":
            continue
        parts = re.findall(r"[^,(]+(?:\([^)]*\))?", regions)
        jeju = [p.strip(" ,") for p in parts if "제주" in p or "추자" in p]
        if jeju:
            out.append({"kind": kind, "regions": ", ".join(jeju)})
    return out


def fetch_warnings(api_key: str) -> Dict[str, Any]:
    items = _get(WRN_URL, {"pageNo": 1, "numOfRows": 10}, api_key)["items"]
    it = items[0] if items else {}
    tm = str(it.get("tmFc") or "")
    return {
        "base": f"{tm[:4]}-{tm[4:6]}-{tm[6:8]} {tm[8:10]}:{tm[10:12]}" if len(tm) >= 12 else "",
        "active": parse_warnings(str(it.get("t6") or "")),
        "prelim": parse_warnings(str(it.get("t7") or "")),
    }


# ------------------------------------------------------------------ 묶음
def build_forecast(api_key: str, now: Optional[dt.datetime] = None,
                   parts: Tuple[str, ...] = ("short", "mid", "warn")) -> Dict[str, Any]:
    now = now or dt.datetime.now(KST)
    out: Dict[str, Any] = {"fetchedAt": now.strftime("%Y-%m-%d %H:%M"), "errors": {}}
    jobs: Dict[str, Callable[[], Any]] = {
        "short": lambda: fetch_vilage(api_key, now),
        "mid": lambda: fetch_mid(api_key, now),
        "warn": lambda: fetch_warnings(api_key),
    }
    with ThreadPoolExecutor(max_workers=len(parts) or 1) as pool:      # 세 종류를 동시에 받음
        futures = {key: pool.submit(jobs[key]) for key in parts}
        for key, fut in futures.items():
            try:
                out[key] = fut.result()
            except ForecastError as exc:
                out[key] = None
                out["errors"][key] = str(exc)
            except Exception as exc:   # 예보 실패가 화면 전체를 막지 않도록
                out[key] = None
                out["errors"][key] = f"예보를 불러오지 못했습니다. ({exc.__class__.__name__})"
    return out

#!/usr/bin/env bash
# 그래프 자료 점검 (Cloud Shell): 실제 저장 자료로 조회 1회 → chart 자료가 만들어지는지
cd "$(dirname "$0")/../functions"
K="$(gcloud secrets versions access latest --secret=KMA_API_KEY --project jeju-agri-weather)"
K="$K" GOOGLE_CLOUD_PROJECT=jeju-agri-weather ./venv/bin/python - <<'PY'
import os, sys, json, traceback, datetime as dt
sys.path.insert(0, ".")
from weather import pipeline, chart
from weather.processing import jeju_daily_mean
q = pipeline.validate_request({"start": "2026-10-01", "end": "2026-10-21", "metrics": ["평균 기온", "누적 강수량", "일조 시간"],
    "stations": ["제주", "서귀포", "성산", "고산"], "source": "apihub", "normalMode": "api", "extreme": False, "rainZero": True})
r = pipeline.run_analysis(q, os.environ["K"])
ch = r.get("chart")
print("chart:", "없음" if not ch else f"{len(ch['dates'])}일 {list(ch['metrics'])}")
print("결과 크기(KB):", round(len(json.dumps(r, ensure_ascii=False, default=str).encode()) / 1024))
if not ch:
    try:
        from weather.kma_api import fetch_all_stations
        cur, _ = fetch_all_stations(q["start"], q["end"], os.environ["K"], "apihub", q["stations"], True)
        chart.build_chart(q["start"], q["end"], q["metrics"], jeju_daily_mean(cur),
                          lambda s, e: fetch_all_stations(s, e, os.environ["K"], "apihub", q["stations"], True)[0],
                          [2025, 2024, 2023, 2022, 2021], "api", None, list(range(1991, 2021)))
    except Exception:
        traceback.print_exc()
else:
    m = ch["metrics"]["평균 기온"]; print("평균 기온 본년 앞 3일:", m["cur"][:3], "평년:", m["normal"][:3])
PY

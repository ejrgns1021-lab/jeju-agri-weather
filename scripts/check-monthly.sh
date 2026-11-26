#!/usr/bin/env bash
# 월별 집계 점검 (Cloud Shell): 실제 저장 자료로 월별 요약·강수 비교 출력
cd "$(dirname "$0")/../functions"
START="${START:-2026-09-16}"; END="${END:-2026-11-24}"
K="$(gcloud secrets versions access latest --secret=KMA_API_KEY --project jeju-agri-weather)"
START="$START" END="$END" K="$K" GOOGLE_CLOUD_PROJECT=jeju-agri-weather ./venv/bin/python - <<'PY' 2>&1 | grep -v Warning
import os, sys; sys.path.insert(0, ".")
from weather import pipeline
q = pipeline.validate_request({"start": os.environ["START"], "end": os.environ["END"], "metrics": ["평균 기온", "누적 강수량", "일조 시간"],
    "stations": ["제주", "서귀포", "성산", "고산"], "source": "apihub", "normalMode": "api", "extreme": False, "rainZero": True})
r = pipeline.run_analysis(q, os.environ["K"])
mo = r["monthly"]
for t in (mo["overview"], mo["byMetric"]["누적 강수량"]["combined"]):
    print(" | ".join(t["columns"]))
    for row in t["rows"]: print(" | ".join(row))
    print()
print("기간 합계 강수(선택 지점 평균):", r["stationReport"]["average"]["누적 강수량"]["cur"])
PY

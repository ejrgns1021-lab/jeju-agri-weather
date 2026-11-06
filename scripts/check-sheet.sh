#!/usr/bin/env bash
# 보고서 표 값 점검 (Cloud Shell): 실제 기상청 자료로 지점별 본년·전년·최근3년·최근5년·평년 출력
#   START=2026-10-01 END=2026-10-21 bash scripts/check-sheet.sh
set -euo pipefail
cd "$(dirname "$0")/../functions"
START="${START:-2026-10-01}"; END="${END:-2026-10-21}"
K="$(gcloud secrets versions access latest --secret=KMA_API_KEY --project jeju-agri-weather)"
START="$START" END="$END" K="$K" ./venv/bin/python - <<'PY'
import os, sys
sys.path.insert(0, ".")
from weather import pipeline
q = pipeline.validate_request({"start": os.environ["START"], "end": os.environ["END"],
    "metrics": ["평균 기온", "누적 강수량", "일조 시간"], "stations": ["제주", "서귀포", "성산", "고산"],
    "source": "apihub", "normalMode": "api", "extreme": False, "rainZero": True})
r = pipeline.run_analysis(q, os.environ["K"])
sr = r["stationReport"]
print("지점  지표          본년   전년  최근3년 최근5년  평년")
for s in sr["stations"]:
    for m, v in s["values"].items():
        f = lambda x: "-" if x is None else f"{x:.1f}"
        print(f"{s['name']:<4} {m:<8} {f(v['cur']):>7} {f(v['prev']):>6} {f(v['r3']):>7} {f(v['r5']):>7} {f(v['normal']):>6}")
PY

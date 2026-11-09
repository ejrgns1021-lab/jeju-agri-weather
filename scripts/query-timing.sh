#!/usr/bin/env bash
# 조회 1회 단계별 경과 시간 (Cloud Shell)
cd "$(dirname "$0")/../functions"
K="$(gcloud secrets versions access latest --secret=KMA_API_KEY --project jeju-agri-weather)"
K="$K" GOOGLE_CLOUD_PROJECT=jeju-agri-weather ./venv/bin/python - <<'PY'
import os, sys, time
sys.path.insert(0, ".")
from weather import pipeline
t0 = time.time(); last = [t0]
def prog(p, stage, detail):
    now = time.time()
    if now - last[0] > 0.5:
        print(f"{now-t0:6.1f}s (+{now-last[0]:.1f}) {p:3d}% {stage} | {detail[:70]}")
    last[0] = now
q = pipeline.validate_request({"start": "2026-10-01", "end": "2026-10-21", "metrics": ["평균 기온", "누적 강수량", "일조 시간"],
    "stations": ["제주", "서귀포", "성산", "고산"], "source": "apihub", "normalMode": "api", "extreme": False, "rainZero": True})
r = pipeline.run_analysis(q, os.environ["K"], progress=prog)
print(f"합계 {time.time()-t0:.1f}s ok={r['ok']} 기준일={r['summary'].get('dataAsOf')}")
PY

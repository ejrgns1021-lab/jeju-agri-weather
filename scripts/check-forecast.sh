#!/usr/bin/env bash
# 예보 함수 점검 (Cloud Shell): 접속 토큰을 만들어 /api/jejuforecast 를 실제로 호출하고 요약만 출력
#   bash scripts/check-forecast.sh                # 프리뷰
#   URL=https://jeju-agri-weather.web.app bash scripts/check-forecast.sh   # 라이브
set -euo pipefail
cd "$(dirname "$0")/../functions"
URL="${URL:-https://jeju-agri-weather--preview-test-h21yjnk4.web.app}"
P="$(gcloud secrets versions access latest --secret=APP_PASSWORD --project jeju-agri-weather)"
TOKEN="$(P="$P" python3 -c "import os,sys; sys.path.insert(0,'.'); from weather import auth; print(auth.issue_token(os.environ['P'])[0])")"
curl -s -X POST "$URL/api/jejuforecast" -H 'Content-Type: application/json' \
  -d "{\"data\":{\"token\":\"$TOKEN\",\"refresh\":true}}" | python3 -c '
import json,sys
r=json.load(sys.stdin)
if "error" in r: print("오류:", r["error"]); sys.exit()
r=r["result"]; print("받은 시각", r["fetchedAt"], "| 오류", r["errors"])
s=r.get("short")
if s:
    print("단기", s["base"])
    for d in s["jeju"]: print("  ", d["label"], d["am"], "→", d["pm"], d["tmin"], "/", d["tmax"], "강수", d["pop"], "%", d["rainMm"], "mm")
m=r.get("mid")
if m:
    print("중기", m["base"])
    for d in m["days"]: print("  ", d["label"], d["am"], "→", d["pm"], d["tmin"], "/", d["tmax"], "강수", d["pop"])
print("특보", r.get("warn"))'

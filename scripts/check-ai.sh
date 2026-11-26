#!/usr/bin/env bash
# AI 기상분석 점검 (Cloud Shell): 토큰을 만들어 jejuai 함수를 직접 호출 (Hosting 60초 제한을 피해 함수 주소로)
#   bash scripts/check-ai.sh                 # 프리뷰(jejubriefdev)
#   FN=jejubrief bash scripts/check-ai.sh       # 라이브
#   START=2025-07-01 END=2025-08-31 bash scripts/check-ai.sh
#   PROMPT="마늘 생육 관점에서 분석해 줘" bash scripts/check-ai.sh   # 요청(프롬프트) 넣어 분석 (v7.6)
set -euo pipefail
cd "$(dirname "$0")/../functions"
FN="${FN:-jejubriefdev}"; START="${START:-2026-10-01}"; END="${END:-2026-10-31}"
P="$(gcloud secrets versions access latest --secret=APP_PASSWORD --project jeju-agri-weather)"
TOKEN="$(P="$P" python3 -c "import os,sys; sys.path.insert(0,'.'); from weather import auth; print(auth.issue_token(os.environ['P'])[0])")"
Q="{\"start\":\"$START\",\"end\":\"$END\",\"metrics\":[\"평균 기온\",\"최고 기온\",\"최저 기온\",\"누적 강수량\",\"일조 시간\",\"강우일수\",\"열대야 일수\"],\"stations\":[\"제주\",\"서귀포\",\"성산\",\"고산\"],\"source\":\"apihub\",\"normalMode\":\"api\",\"extreme\":false,\"rainZero\":true}"
# 화면처럼 조회 결과를 함께 보냄: Cloud Shell 에서 직접 계산(기상청 연결은 Cloud Shell 이 더 안정적)
K="$(gcloud secrets versions access latest --secret=KMA_API_KEY --project jeju-agri-weather)"
RES="$(Q="$Q" K="$K" ./venv/bin/python -c "
import os, json, sys; sys.path.insert(0, '.')
from weather import pipeline
r = pipeline.run_analysis(pipeline.validate_request(json.loads(os.environ['Q'])), os.environ['K'])
keep = {k: r[k] for k in ('params', 'summary', 'stationBrief', 'daily')}
print(json.dumps(keep, ensure_ascii=False, default=str))")"
TOKEN="$TOKEN" Q="$Q" RES="$RES" python3 -c '
import json, os
d = {"token": os.environ["TOKEN"], "query": json.loads(os.environ["Q"]), "result": json.loads(os.environ["RES"])}
if os.environ.get("REFRESH"): d["refresh"] = True
if os.environ.get("PROMPT"): d["prompt"] = os.environ["PROMPT"]
json.dump({"data": d}, open(os.path.expanduser("~/ai-req.json"), "w"), ensure_ascii=False)' 
echo "보낼 자료 $(wc -c < ~/ai-req.json) bytes"
time curl -s -m 320 -X POST "https://asia-northeast3-jeju-agri-weather.cloudfunctions.net/$FN" -H 'Content-Type: application/json' \
  -d @"$HOME/ai-req.json" > ~/ai.json
python3 -c '
import json; r=json.load(open("'"$HOME"'/ai.json"))
if "error" in r: print("오류:", r["error"])
else: r=r["result"]; print(r["model"], r["createdAt"], "저장분" if r.get("cached") else "새 분석", len(r["text"]), "자"); print(r["text"])'

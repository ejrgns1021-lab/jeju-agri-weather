#!/usr/bin/env bash
# 관리자 함수 점검 (Cloud Shell): 관리자 비밀값으로 토큰을 만들어 이용 현황·연결 점검을 호출
#   bash scripts/check-manage.sh               # 프리뷰(jejumanagedev)
#   FN=jejumanage bash scripts/check-manage.sh  # 라이브
set -euo pipefail
cd "$(dirname "$0")/../functions"
FN="${FN:-jejumanagedev}"
P="$(gcloud secrets versions access latest --secret=ADMIN_PASSWORD --project jeju-agri-weather)"
TOKEN="$(P="$P" python3 -c "import os,sys; sys.path.insert(0,'.'); from weather import auth; print(auth.issue_token('admin:' + os.environ['P'].strip())[0])")"
for A in stats health; do
  echo "== $A"
  curl -s -m 120 -X POST "https://asia-northeast3-jeju-agri-weather.cloudfunctions.net/$FN" -H 'Content-Type: application/json' \
    -d "{\"data\":{\"token\":\"$TOKEN\",\"action\":\"$A\",\"days\":7}}" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False)[:900])'
done

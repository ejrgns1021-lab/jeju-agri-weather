#!/usr/bin/env bash
# =====================================================================
# 테스트(프리뷰) 배포 — 실제 서비스(라이브 사이트·라이브 함수)는 건드리지 않음
#
#   1) 현재 코드의 서버 함수를 '…dev' 이름(logindev, jejuweatherdev, jejuprogressdev)으로 별도 배포
#      (Firebase codebase "dev" — 라이브 함수 login/jejuweather/jejuprogress 와 완전히 분리)
#   2) 웹페이지를 Hosting 임시 채널(preview-test)에 올리고, /api/* 를 dev 함수로 연결
#
# 사용:  bash scripts/deploy-preview.sh            (Cloud Shell, dev 브랜치에서)
# 결과:  https://jeju-agri-weather--preview-test-XXXX.web.app  (7일 후 자동 만료)
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${PROJECT:-jeju-agri-weather}"
CHANNEL="${CHANNEL:-preview-test}"
PYTHON="${PYTHON:-python3.12}"   # GitHub Actions 에서는 PYTHON=python 으로 실행
WORK=".preview-build"

rm -rf "$WORK" && mkdir -p "$WORK"
cp -r functions "$WORK/functions-dev"
rm -rf "$WORK/functions-dev/venv" "$WORK/functions-dev/tests"
# 함수 이름만 바꿔 라이브 함수와 분리
sed -i -E 's/^def (login|jejuweather|jejuprogress|jejuforecast|jejubrief|managelogin|jejumanage)\(/def \1dev(/' "$WORK/functions-dev/main.py"
grep -E "^def (login|jejuweather|jejuprogress|jejuforecast|jejubrief|managelogin|jejumanage)dev\(" "$WORK/functions-dev/main.py" | wc -l | grep -q 7 \
  || { echo "함수 이름 변경 실패" >&2; exit 1; }
cp -r public "$WORK/public"

cat > "$WORK/firebase.json" <<JSON
{
  "functions": [{ "source": "functions-dev", "codebase": "dev", "runtime": "python312",
                  "ignore": ["venv", "__pycache__", "*.local"] }],
  "hosting": {
    "public": "public",
    "ignore": ["firebase.json", "**/.*"],
    "headers": [
      { "source": "**/*.@(js|css|html)", "headers": [{ "key": "Cache-Control", "value": "no-cache" }] },
      { "source": "/api/**", "headers": [{ "key": "Cache-Control", "value": "no-store" }] }
    ],
    "rewrites": [
      { "source": "/api/login", "function": { "functionId": "logindev", "region": "asia-northeast3" } },
      { "source": "/api/jejuweather", "function": { "functionId": "jejuweatherdev", "region": "asia-northeast3" } },
      { "source": "/api/jejuprogress", "function": { "functionId": "jejuprogressdev", "region": "asia-northeast3" } },
      { "source": "/api/jejuforecast", "function": { "functionId": "jejuforecastdev", "region": "asia-northeast3" } },
      { "source": "/api/jejubrief", "function": { "functionId": "jejubriefdev", "region": "asia-northeast3" } },
      { "source": "/api/managelogin", "function": { "functionId": "managelogindev", "region": "asia-northeast3" } },
      { "source": "/api/jejumanage", "function": { "functionId": "jejumanagedev", "region": "asia-northeast3" } }
    ]
  }
}
JSON

cd "$WORK"
echo "▶ 1/2 dev 함수 배포 (라이브 함수는 변경되지 않음)"
"$PYTHON" -m venv functions-dev/venv >/dev/null
functions-dev/venv/bin/pip install -q -r functions-dev/requirements.txt
firebase deploy --only functions:dev --project "$PROJECT" --force --non-interactive

echo "▶ 2/2 웹페이지 임시 채널 배포 ($CHANNEL)"
firebase hosting:channel:deploy "$CHANNEL" --project "$PROJECT" --expires 7d --non-interactive --json > channel.json
URL="$("$PYTHON" -c "import json; r=json.load(open('channel.json'))['result']; print(next(iter(r.values()))['url'])")"
echo "PREVIEW_URL=$URL"
[ -n "${GITHUB_OUTPUT:-}" ] && echo "url=$URL" >> "$GITHUB_OUTPUT" || true

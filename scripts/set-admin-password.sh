#!/usr/bin/env bash
# 관리자 화면(/manage.html) 비밀번호 설정 (Cloud Shell)
#   bash scripts/set-admin-password.sh
# 1) 입력창에 새 관리자 비밀번호를 붙여넣고 Enter (화면에 표시되지 않음)
# 2) 관리자 함수(라이브·프리뷰)를 다시 배포해 새 비밀번호를 적용 (약 2~3분)
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=jeju-agri-weather
read -r -s -p "새 관리자 비밀번호: " PW; echo
[ ${#PW} -ge 8 ] || { echo "8자 이상으로 정해 주세요." >&2; exit 1; }
printf '%s' "$PW" | gcloud secrets versions add ADMIN_PASSWORD --data-file=- --project "$PROJECT" >/dev/null
unset PW
echo "비밀번호 저장 완료 → 관리자 함수 다시 배포"
BR="$(git rev-parse --abbrev-ref HEAD)"
git fetch -q origin
if git show origin/main:functions/main.py | grep -q "def managelogin"; then   # 라이브에 관리자 화면이 있을 때만
  git checkout -q main && git pull -q origin main
  firebase deploy --only functions:default:managelogin,functions:default:jejumanage --project "$PROJECT" --force --non-interactive 2>&1 | grep -E "Successful|Error|complete" || true
  git checkout -q "$BR"
fi
echo "완료: https://jeju-agri-weather.web.app/manage.html 에서 새 비밀번호로 접속"
echo "(프리뷰에도 적용하려면 dev 브랜치에서 bash scripts/deploy-preview.sh 를 한 번 더 실행)"

#!/usr/bin/env bash
# 관리자 비밀번호 비밀값(ADMIN_PASSWORD)을 처음 만들 때 한 번만 (Cloud Shell)
# 아무도 모르는 무작위 값으로 만들어 두므로, 실제 사용 전 set-admin-password.sh 로 바꿔야 함
set -euo pipefail
PROJECT=jeju-agri-weather
if gcloud secrets describe ADMIN_PASSWORD --project "$PROJECT" >/dev/null 2>&1; then
  echo "ADMIN_PASSWORD 이미 있음"; exit 0
fi
head -c 32 /dev/urandom | base64 | tr -d '\n/+=' | head -c 32 | \
  gcloud secrets create ADMIN_PASSWORD --data-file=- --replication-policy=automatic --project "$PROJECT" >/dev/null
echo "ADMIN_PASSWORD 생성 완료(무작위 값) — set-admin-password.sh 로 원하는 비밀번호로 바꾸세요"

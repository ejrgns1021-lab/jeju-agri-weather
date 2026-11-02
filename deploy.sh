#!/usr/bin/env bash
# =====================================================================
# deploy.sh - 제주도 농업 기상 분석 대시보드를 Firebase(Cloud Run + Hosting + Firestore)에 배포
#
# 사용법:  ./deploy.sh <파이어베이스_프로젝트_ID>
#   예)    ./deploy.sh jeju-agri-weather-12345
#
# 필요:    gcloud CLI, firebase CLI (Cloud Shell 에는 둘 다 설치되어 있음)
#          프로젝트는 Blaze(종량제) 요금제여야 합니다.
# 처음 실행할 때 접속 비밀번호와 기상청 API 키를 물어보고 Secret Manager 에 저장합니다.
# =====================================================================
set -euo pipefail

PROJECT_ID="${1:-${PROJECT_ID:-}}"
if [[ -z "${PROJECT_ID}" ]]; then
  echo "사용법: ./deploy.sh <파이어베이스_프로젝트_ID>" >&2
  exit 1
fi
REGION="${REGION:-asia-northeast3}"          # 서울
SERVICE="${SERVICE:-jeju-agri-weather}"
RUN_SA_NAME="jeju-weather-run"
RUN_SA="${RUN_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")"
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "▶ 1/6 필요한 Google Cloud 서비스 사용 설정"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com iam.googleapis.com

echo "▶ 2/6 Firestore 데이터베이스 확인"
if ! gcloud firestore databases describe --database="(default)" >/dev/null 2>&1; then
  gcloud firestore databases create --database="(default)" --location="${REGION}" --type=firestore-native
fi

echo "▶ 3/6 비밀번호 · API 키 (Secret Manager)"
ensure_secret() {
  local name="$1" prompt="$2" value
  if gcloud secrets describe "${name}" >/dev/null 2>&1; then
    echo "  - ${name}: 이미 있음 (바꾸려면: gcloud secrets versions add ${name} --data-file=-)"
    return
  fi
  read -r -s -p "  ${prompt}: " value; echo
  if [[ -z "${value}" ]]; then echo "  값이 비어 있습니다." >&2; exit 1; fi
  printf '%s' "${value}" | gcloud secrets create "${name}" --replication-policy=automatic --data-file=-
}
ensure_secret app-password "대시보드 접속 비밀번호"
ensure_secret kma-api-key  "기상청 API 키"

echo "▶ 4/6 실행용 서비스 계정 및 권한"
if ! gcloud iam service-accounts describe "${RUN_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUN_SA_NAME}" --display-name="제주 기상 대시보드 (Cloud Run)"
fi
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${RUN_SA}" \
  --role="roles/datastore.user" --condition=None >/dev/null
for s in app-password kma-api-key; do
  gcloud secrets add-iam-policy-binding "${s}" --member="serviceAccount:${RUN_SA}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
done

echo "▶ 5/6 Cloud Run 배포 (빌드 포함, 수 분 소요)"
# - timeout 3600: Streamlit 은 화면 연결(WebSocket)을 길게 유지하므로 최대치로 설정
# - session-affinity: 같은 사용자를 같은 인스턴스로 보내 로그인 상태 유지
gcloud run deploy "${SERVICE}" \
  --source . \
  --region "${REGION}" \
  --service-account "${RUN_SA}" \
  --allow-unauthenticated \
  --set-secrets "APP_PASSWORD=app-password:latest,KMA_API_KEY=kma-api-key:latest" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --timeout 3600 \
  --session-affinity \
  --memory 1Gi \
  --min-instances 0 \
  --max-instances 3

RUN_URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')"

echo "▶ 6/6 Firebase Hosting(주소 연결) · Firestore 보안 규칙 배포"
sed "s#__CLOUD_RUN_URL__#${RUN_URL}#" firebase.template.json > firebase.json
firebase deploy --only hosting,firestore:rules --project "${PROJECT_ID}"

echo
echo "✅ 배포 완료"
echo "   접속 주소:  https://${PROJECT_ID}.web.app   (→ ${RUN_URL} 로 이동)"

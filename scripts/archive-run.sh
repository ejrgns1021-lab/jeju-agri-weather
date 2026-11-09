#!/usr/bin/env bash
# 관측 자료 저장소 예약 작업을 지금 한 번 실행 (빠진 연도 채우기 포함, 약 8분)
#   bash scripts/archive-run.sh        → 프리뷰(jejusyncdev)
#   FN=jejusync bash scripts/archive-run.sh → 라이브
FN="${FN:-jejusyncdev}"
gcloud scheduler jobs run "firebase-schedule-$FN-asia-northeast3" --location asia-northeast3 && echo "실행 요청: $FN"

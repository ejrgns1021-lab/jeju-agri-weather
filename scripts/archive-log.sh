#!/usr/bin/env bash
# 저장소 예약 작업 기록 (최근 6시간)
FN="${FN:-jejusyncdev}"
SINCE="$(date -u -d '-6 hours' +%Y-%m-%dT%H:%M:%SZ)"
gcloud logging read "resource.type=\"cloud_run_revision\" AND timestamp>=\"$SINCE\" AND resource.labels.service_name=\"$FN\" AND (textPayload:\"저장소\" OR severity>=WARNING)" \
  --limit "${N:-6}" --format="value(timestamp.date('%m-%d %H:%M:%S',tz='Asia/Seoul'),severity,textPayload)" 2>&1 | cut -c1-900

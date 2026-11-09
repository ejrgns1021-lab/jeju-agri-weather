#!/usr/bin/env bash
# 저장소 예약 작업 기록 (최근 6시간)
FN="${FN:-jejusyncdev}"
SINCE="$(date -u -d '-6 hours' +%Y-%m-%dT%H:%M:%SZ)"
gcloud logging read "timestamp>=\"$SINCE\" AND (resource.labels.service_name=\"$FN\" OR resource.labels.function_name=\"$FN\" OR resource.labels.job_id:\"$FN\")" \
  --limit "${N:-12}" --format="value(timestamp.date('%m-%d %H:%M:%S',tz='Asia/Seoul'),resource.type,severity,httpRequest.status,httpRequest.latency,textPayload,jsonPayload.message,jsonPayload.status)" 2>&1 | cut -c1-600

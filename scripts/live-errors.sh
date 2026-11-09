#!/usr/bin/env bash
# 라이브 조회 함수의 최근 오류·느린 요청 (기본: 최근 3시간)
SINCE="${SINCE:-$(date -u -d '-3 hours' +%Y-%m-%dT%H:%M:%SZ)}"
gcloud logging read "resource.type=\"cloud_run_revision\" AND timestamp>=\"$SINCE\" AND resource.labels.service_name=\"${FN:-jejuweather}\" AND (severity>=WARNING OR httpRequest.status>=400)" \
  --limit "${N:-15}" --format="value(timestamp.date('%m-%d %H:%M:%S',tz='Asia/Seoul'),severity,httpRequest.status,httpRequest.latency,textPayload,jsonPayload.message)" 2>&1 | cut -c1-220

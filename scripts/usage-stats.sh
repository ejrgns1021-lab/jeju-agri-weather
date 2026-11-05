#!/usr/bin/env bash
# 이용 현황 (Cloud Shell): 라이브 함수 요청 기록에서 날짜별 로그인·조회·AI 분석 횟수와 접속 IP 수를 셈
#   bash scripts/usage-stats.sh        # 최근 30일
#   DAYS=7 bash scripts/usage-stats.sh
# ※ 공용 비밀번호 방식이라 '사람 수'는 알 수 없고, 접속 IP 수로 추정 (같은 사무실은 IP 하나로 보일 수 있음)
set -euo pipefail
DAYS="${DAYS:-30}"
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name=("login" OR "jejuweather" OR "jejubrief") AND httpRequest.requestMethod="POST"' \
  --project jeju-agri-weather --freshness "${DAYS}d" --limit 20000 \
  --format='value(timestamp,resource.labels.service_name,httpRequest.status,httpRequest.remoteIp)' > /tmp/usage.tsv
python3 - <<'PY'
import collections, datetime as dt
KST = dt.timezone(dt.timedelta(hours=9))
NAME = {"login": "로그인", "jejuweather": "조회", "jejubrief": "AI분석"}
day = collections.defaultdict(lambda: collections.Counter()); ips = collections.defaultdict(set); all_ips = set()
for line in open("/tmp/usage.tsv"):
    p = line.rstrip("\n").split("\t")
    if len(p) < 4 or not p[0]:
        continue
    ts, svc, status, ip = p[:4]
    if status and status != "200":
        continue
    d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST).strftime("%m-%d")
    day[d][svc] += 1
    if svc == "login" and ip:
        ips[d].add(ip); all_ips.add(ip)
print("날짜   로그인  조회  AI분석  로그인IP수")
for d in sorted(day):
    c = day[d]
    print(f"{d}  {c['login']:6d} {c['jejuweather']:5d} {c['jejubrief']:6d}  {len(ips[d]):6d}")
tot = collections.Counter()
for c in day.values(): tot.update(c)
print(f"합계   {tot['login']:6d} {tot['jejuweather']:5d} {tot['jejubrief']:6d}  서로 다른 IP {len(all_ips)}개")
PY

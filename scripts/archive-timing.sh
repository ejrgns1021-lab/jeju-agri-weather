#!/usr/bin/env bash
# 조회 단계별 소요 시간 점검 (Cloud Shell)
cd "$(dirname "$0")/../functions"
K="$(gcloud secrets versions access latest --secret=KMA_API_KEY --project jeju-agri-weather)"
K="$K" GOOGLE_CLOUD_PROJECT=jeju-agri-weather ./venv/bin/python - <<'PY'
import os, sys, time, datetime as dt
sys.path.insert(0, ".")
from weather import archive, kma_api, store
t = time.time(); store._get_client(); print(f"저장소 연결 {time.time()-t:.1f}s")
t = time.time(); n = archive.warm([184, 189, 188, 185], list(range(1990, 2027))); print(f"연도 문서 {n}개 묶음 읽기 {time.time()-t:.1f}s")
t = time.time(); df, err = archive.read_range(dt.date(2026, 10, 1), dt.date(2026, 10, 21), [184, 189, 188, 185], True, os.environ["K"]); print(f"기간 자료 {len(df)}행 {time.time()-t:.2f}s {err}")
t = time.time(); full = store.get_df(store.NORMALS_COLLECTION, "normals", 2021, "0101", "1231"); print(f"평년 1년치 저장분: {'있음 '+str(len(full))+'행' if full is not None else '없음'} {time.time()-t:.1f}s")
t = time.time()
try:
    kma_api.fetch_apihub_normals_raw(1, 1, 12, 31, os.environ["K"]); print(f"평년 1년치 기상청 요청 성공 {time.time()-t:.1f}s")
except Exception as e:
    print(f"평년 1년치 기상청 요청 실패 {time.time()-t:.1f}s {e.__class__.__name__}: {str(e)[:120]}")
PY

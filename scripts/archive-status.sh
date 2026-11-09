#!/usr/bin/env bash
# 관측 자료 저장소 상태 (Cloud Shell): 자료 기준일, 마지막 갱신, 저장된 연도
cd "$(dirname "$0")/../functions"
GOOGLE_CLOUD_PROJECT=jeju-agri-weather ./venv/bin/python - <<'PY'
import json, sys
sys.path.insert(0, ".")
from weather import archive
print(json.dumps(archive.status(), ensure_ascii=False, indent=1, default=str))
PY

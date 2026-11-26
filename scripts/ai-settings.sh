#!/usr/bin/env bash
# AI 모델 설정 확인 (읽기만): 라이브(ai)·프리뷰(ai_dev)에서 고른 모델, AI Studio 키 저장 여부(값은 표시 안 함)
cd "$(dirname "$0")/../functions"
GOOGLE_CLOUD_PROJECT=jeju-agri-weather ./venv/bin/python - <<'PY' 2>&1 | grep -v Warning
import sys; sys.path.insert(0, ".")
from weather import store
c = store._get_client()
for d in ("ai", "ai_dev"):
    print(f"{d}: model =", (c.collection("settings").document(d).get().to_dict() or {}).get("model") or "(기본: gemini)")
k = c.collection("settings").document("ai_keys").get().to_dict() or {}
print("AI Studio 키 저장:", "있음" if k else "없음", "· 항목:", sorted(k.keys()))
PY

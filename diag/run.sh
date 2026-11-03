#!/usr/bin/env bash
cd "$(dirname "$0")"
python3.12 -m venv venv >/dev/null && ./venv/bin/pip install -q -r requirements.txt
firebase deploy --only functions --project jeju-agri-weather --non-interactive 2>&1 | grep -E "✔  functions\[|Error" | tail -5
for r in asia-northeast3/diagseoul asia-east1/diagtaiwan asia-northeast1/diagtokyo; do
  echo "== ${r}"; curl -s -m 70 "https://${r%%/*}-jeju-agri-weather.cloudfunctions.net/${r##*/}"; echo
done

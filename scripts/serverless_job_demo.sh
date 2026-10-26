#!/bin/sh
# Container command for the Serverless Job demo run: seed the graph, change the labeled demo funder page,
# serve the demo pages inside the container, then run the nightly check once (python -m donor_lookback.job).
# Needs TOKEN_FACTORY_BASE_URL and TOKEN_FACTORY_API_KEY in the environment; PUBLIC_BASE_URL must be http://127.0.0.1:PORT.
set -eu
PORT="${PORT:-8000}"
export PUBLIC_BASE_URL="http://127.0.0.1:${PORT}"
python - <<'PY'
from donor_lookback.config import get_settings
from donor_lookback.seed import edit_funder_deadline, reset_demo
from donor_lookback.store import Store
s = get_settings()
reset_demo(Store(s.db_path), s)
edit_funder_deadline(s, "November 9, 2026")
print("seeded; demo funder page now says November 9, 2026")
PY
uvicorn donor_lookback.api:app_from_env --factory --host 127.0.0.1 --port "${PORT}" --log-level warning &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:${PORT}/demo/funder', timeout=1)" 2>/dev/null && break
  sleep 0.2
done
python -m donor_lookback.job

#!/usr/bin/env bash
# Local board on http://127.0.0.1:8000 (reads .env if present).
set -euo pipefail
ROOT="${BASH_SOURCE[0]%/*}/.."
cd "$ROOT"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec .venv/bin/uvicorn donor_lookback.api:app_from_env --factory --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"

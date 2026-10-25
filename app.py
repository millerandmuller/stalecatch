"""Vercel entry point for the hosted judge board (replay, proof and the seed; no Token Factory key on this host).
The container image (Dockerfile) stays the general way to run the board; this file only sets judge-mode defaults for a serverless host."""
import os

# Serverless filesystems are read-only except /tmp; the seed is rebuilt on every cold start, so nothing here has to persist.
os.environ.setdefault("DONOR_LOOKBACK_DB", "/tmp/lookback.db")
os.environ.setdefault("DONOR_LOOKBACK_DEMO_STATE", "/tmp/demo_page.json")
os.environ.setdefault("JUDGE_MODE", "1")
if os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
    os.environ.setdefault("PUBLIC_BASE_URL", f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}")

from donor_lookback.api import app_from_env  # noqa: E402 - the environment above must be set before settings are read

app = app_from_env()

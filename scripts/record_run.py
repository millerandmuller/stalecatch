"""Plan B: capture one full LIVE reveal as frames the board can replay read-only (judge mode, camera fallback).
Needs a running board with real Token Factory credentials. Refuses to record simulated models.
   python scripts/record_run.py [--base http://127.0.0.1:8000] [--deadline "November 9, 2026"] [--public-url https://stalecatch.vercel.app]
--public-url rewrites the board address inside the saved frames (demo page URLs) so the hosted replay shows the hosted path.
   python scripts/record_run.py --rewrite-only --public-url <URL>   rewrites the existing recording without any model call."""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent.parent / "data" / "recorded_run.json"


def rewrite_base(text: str, base: str, public_url: str) -> str:
    """Swap the local board address for the hosted one in a serialized recording (no model call involved)."""
    return text.replace(base.rstrip("/"), public_url.rstrip("/"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--deadline", default="November 9, 2026")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--public-url", default="")
    ap.add_argument("--rewrite-only", action="store_true")
    args = ap.parse_args()
    if args.rewrite_only:
        if not args.public_url:
            print("--rewrite-only needs --public-url.", file=sys.stderr)
            return 2
        out = Path(args.out)
        out.write_text(rewrite_base(out.read_text(), args.base, args.public_url))
        print(f"Rewrote {args.base} to {args.public_url} in {out}")
        return 0
    http = httpx.Client(base_url=args.base, timeout=30)
    cfg = http.get("/api/config").json()
    if cfg.get("dev_fake"):
        print("Refusing to record: this board runs simulated models.", file=sys.stderr)
        return 2
    if not cfg.get("live_ready"):
        print("Refusing to record: the board has no Token Factory credentials.", file=sys.stderr)
        return 2
    http.post("/api/demo/reset").raise_for_status()
    frames = [{"state": http.get("/api/state").json(), "ticker": "Seed: three items and one sent message built on the funder page.", "hold_ms": 1500}]
    http.post("/api/demo/edit", json={"deadline": args.deadline}).raise_for_status()
    resp = http.post("/api/check/run")
    resp.raise_for_status()
    run_id = resp.json()["run_id"]
    seq, last_state = 0, None
    deadline = time.time() + 600
    while time.time() < deadline:
        ev = http.get(f"/api/run/{run_id}/events", params={"after": seq}).json()
        state = http.get("/api/state").json()
        for e in ev["events"]:
            seq = e["seq"]
        key = json.dumps([[p["id"], p["status"]] for p in state["products"]] + [d["state"] for d in state["donors"]])
        if ev["events"] or key != last_state:
            last = ev["events"][-1] if ev["events"] else None
            ticker = f"{last['step']}: {last['detail']}" if last else ""
            frame = {"state": state, "ticker": ticker, "hold_ms": 900}
            if last:
                frame["event"] = {"step": last["step"], "detail": last["detail"]}  # the board words the ticker line from this
            frames.append(frame)
            last_state = key
        if not ev["running"] and ev["run"] and ev["run"]["finished_at"]:
            break
        time.sleep(0.3)
    else:
        print("Run did not finish within 10 minutes; nothing recorded.", file=sys.stderr)
        return 3
    final = http.get("/api/state").json()
    if not (final["run"] and final["run"]["complete"]):
        print("The run was incomplete; not recording a broken reveal.", file=sys.stderr)
        return 3
    frames.append({"state": final, "ticker": "Run finished.", "hold_ms": 2500})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "models": cfg["models"], "run_complete": bool(final["run"] and final["run"]["complete"]),
                          "frames": frames})
    Path(args.out).write_text(rewrite_base(payload, args.base, args.public_url) if args.public_url else payload)
    print(f"Recorded {len(frames)} frames to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

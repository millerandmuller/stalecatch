"""F9 Archive replay bench: real before/after page pairs from the Internet Archive.
Nano alone vs. Nano + Ultra confirm: recall on real changes, false alarms on unchanged pages, cost from usage fields.
Run: python -m bench.run_bench [--pairs bench/archive_pairs.json] [--out bench/report.json]"""
import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from donor_lookback.config import Settings, get_settings
from donor_lookback.gateway import Gateway
from donor_lookback.intake import INTAKE_SCHEMA
from donor_lookback.prompts import INTAKE_SYSTEM
from donor_lookback.receipt import build_receipt
from donor_lookback.store import Store
from donor_lookback.textutil import html_to_text, is_verbatim, relevant_window
from donor_lookback.watch import nano_compare, ultra_confirm

HERE = Path(__file__).resolve().parent
ALERTS = ("changed", "superseded")


def wayback_raw(url: str) -> str:
    """Snapshot URLs look like https://web.archive.org/web/20240101000000/https://example.org/ ; id_ asks for the original bytes."""
    if "/web/" not in url or "id_/" in url:
        return url
    head, tail = url.split("/web/", 1)
    stamp, rest = tail.split("/", 1)
    return f"{head}/web/{stamp}id_/{rest}"


def fetch_snapshot(url: str, http: Optional[httpx.Client] = None) -> str:
    http = http or httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": "DonorLookbackBench/0.1"})
    r = http.get(wayback_raw(url))
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    return html_to_text(r.text) if "html" in ctype else r.text


def cached_fetch(snapshot_url: str) -> str:
    """Snapshot text from the shared disk cache (data/wayback_cache, same key as scripts/curate_pairs.py); on a miss one polite
    network fetch, then cached. Keeps a rerun from hitting the Internet Archive again."""
    stamp, original = snapshot_url.split("/web/", 1)[1].split("/", 1)
    cache = HERE.parent / "data" / "wayback_cache" / (hashlib.sha1(f"{stamp}|{original}".encode()).hexdigest() + ".txt")
    if cache.exists():
        return cache.read_text()
    time.sleep(15)
    text = fetch_snapshot(snapshot_url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    return text


def _extract_donor(gateway: Gateway, settings: Settings, before: str, field: str, run_id: str) -> Optional[dict]:
    window = relevant_window(before, field or "deadline due date", settings.page_char_limit)
    hint = f" Focus on: {field}." if field else " Focus on the application deadline."
    out = gateway.chat_json("nano", INTAKE_SYSTEM + hint, f"PAGE TEXT:\n{window}", purpose="bench-intake",
                            run_id=run_id, schema=INTAKE_SCHEMA)
    excerpt, claim = str(out.get("excerpt", "")).strip(), str(out.get("claim", "")).strip()
    if claim and excerpt and is_verbatim(excerpt, before):
        return {"claim": claim, "excerpt": excerpt}
    return None


def run_pairs(pairs: list[dict], gateway: Gateway, settings: Settings, store: Store,
              fetch: Callable[[str], str] = fetch_snapshot) -> dict:
    run_id = store.start_run("bench")
    rows = []
    for pair in pairs:
        row = {"label": pair["label"], "url": pair["url"], "before_url": pair["before_snapshot"],
               "after_url": pair["after_snapshot"], "changed": bool(pair["changed"]), "field": pair.get("field", ""), "what_changed": pair.get("what_changed", ""),
               "expected_verdict": pair.get("expected_verdict", ""),
               "nano_verdict": "error", "final_verdict": "error", "note": ""}
        try:
            before, after = fetch(pair["before_snapshot"]), fetch(pair["after_snapshot"])
            donor = _extract_donor(gateway, settings, before, pair.get("field", ""), run_id)
            if donor is None:
                row["note"] = "no verbatim excerpt on the before page (counted as a miss on changed pairs)"
                row["nano_verdict"] = row["final_verdict"] = "unchanged"
            else:
                alarm = nano_compare(gateway, settings, donor, after, run_id)
                row["nano_verdict"] = "changed" if alarm.changed else "unchanged"
                if alarm.changed:
                    confirmed = ultra_confirm(gateway, settings, donor, after, alarm, run_id)
                    row["final_verdict"] = confirmed.kind  # changed | superseded | unchanged
                    row["note"] = confirmed.reason
                else:
                    row["final_verdict"] = "unchanged"
        except Exception as exc:  # noqa: BLE001 - one bad snapshot must not sink the report; it is shown as an error row
            row["note"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return {"rows": rows, "summary": summarize(rows, build_receipt(store, settings, run_id))}


def summarize(rows: list[dict], receipt: dict) -> dict:
    changed = [r for r in rows if r["changed"]]
    unchanged = [r for r in rows if not r["changed"]]
    return {
        "changed_pairs": len(changed), "unchanged_pairs": len(unchanged),
        "nano_recall": sum(r["nano_verdict"] == "changed" for r in changed),
        # both alerting verdicts count as found: a superseded cycle tells the user the saved date is stale
        "confirmed_recall": sum(r["final_verdict"] in ALERTS for r in changed),
        "verdict_changed": sum(r["final_verdict"] == "changed" for r in changed),
        "verdict_superseded": sum(r["final_verdict"] == "superseded" for r in changed),
        "nano_false_alarms": sum(r["nano_verdict"] == "changed" for r in unchanged),
        "false_alarms": sum(r["final_verdict"] in ALERTS for r in unchanged),
        "errors": sum("error" in (r["nano_verdict"], r["final_verdict"]) for r in rows),
        "misses": [r["label"] for r in changed if r["final_verdict"] not in ALERTS],
        "total_usd": receipt["total_usd"], "receipt": receipt,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=str(HERE / "archive_pairs.json"))
    ap.add_argument("--out", default=str(HERE / "report.json"))
    args = ap.parse_args()
    pairs = json.loads(Path(args.pairs).read_text())
    settings = get_settings()
    store = Store(":memory:")
    report = run_pairs(pairs, Gateway(settings, recorder=store.log_call), settings, store, fetch=cached_fetch)
    Path(args.out).write_text(json.dumps(report, indent=2))
    s = report["summary"]
    print(f"Nano alone {s['nano_recall']}/{s['changed_pairs']} | Nano+Ultra {s['confirmed_recall']}/{s['changed_pairs']} "
          f"(changed {s['verdict_changed']}, superseded {s['verdict_superseded']}) | "
          f"false alarms {s['false_alarms']}/{s['unchanged_pairs']} | errors {s['errors']} | ${s['total_usd']:.4f}")
    for m in s["misses"]:
        print("MISS:", m)


if __name__ == "__main__":
    main()

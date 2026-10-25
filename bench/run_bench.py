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

from donor_lookback.config import Settings, get_settings, load_dotenv
from donor_lookback.gateway import Gateway
from donor_lookback.intake import INTAKE_SCHEMA, excerpt_problem
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
    """Snapshot text from bench/snapshots (committed, same key as scripts/curate_pairs.py); on a miss one polite network fetch,
    then stored there. Keeps a rerun from hitting the Internet Archive again."""
    stamp, original = snapshot_url.split("/web/", 1)[1].split("/", 1)
    stamp = stamp.removesuffix("id_")  # the raw-bytes and the plain snapshot URL are the same page
    cache = HERE / "snapshots" / (hashlib.sha1(f"{stamp}|{original}".encode()).hexdigest() + ".txt")
    if cache.exists() and cache.stat().st_size > 0:  # an empty file is a failed earlier write, not a page
        return cache.read_text()
    time.sleep(15)
    text = fetch_snapshot(snapshot_url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(cache)  # atomic: a killed run never leaves a truncated snapshot behind
    return text


def _extract_donor(gateway: Gateway, settings: Settings, before: str, field: str, run_id: str) -> Optional[dict]:
    """Same acceptance rule and one retry as product intake: verbatim and carrying the date itself."""
    window = relevant_window(before, field or "deadline due date", settings.page_char_limit)
    hint = f" Focus on: {field}." if field else " Focus on the application deadline."
    problem = ""
    for _ in range(2):
        retry = f"\n\nYour previous excerpt was rejected: {problem} Copy it exactly." if problem else ""
        out = gateway.chat_json("nano", INTAKE_SYSTEM + hint, f"PAGE TEXT:\n{window}{retry}", purpose="bench-intake",
                                run_id=run_id, schema=INTAKE_SCHEMA)
        excerpt, claim = str(out.get("excerpt", "")).strip(), str(out.get("claim", "")).strip()
        problem = excerpt_problem(claim, excerpt, before)
        if not problem:
            return {"claim": claim, "excerpt": excerpt}
    return None


def pair_flags(pair: dict) -> dict:
    """Pair attributes that travel into the report rows (the board and the headline read them)."""
    flags = {"verified": pair.get("verified", True)}
    for key in ("scope", "note", "listing_page"):
        if pair.get(key):
            flags[f"pair_{key}" if key == "note" else key] = pair[key]
    return flags


def load_pairs(path: Path) -> tuple[dict, list[dict]]:
    """The pairs file is {"meta": {...}, "pairs": [...]}; a bare list (older files) is accepted."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        return {}, data
    if not isinstance(data.get("pairs"), list):
        raise SystemExit(f"{path}: expected a 'pairs' list")
    return data.get("meta") or {}, data["pairs"]


def report_meta(meta: dict, summary: dict) -> dict:
    """Text the board shows above the table; N is the number of verified real changes the headline counts."""
    out = dict(meta)
    if out.get("subtitle"):
        out["subtitle"] = out["subtitle"].replace("{n}", str(summary["changed_pairs"]))
    return out


def run_pairs(pairs: list[dict], gateway: Gateway, settings: Settings, store: Store,
              fetch: Callable[[str], str] = fetch_snapshot, meta: Optional[dict] = None) -> dict:
    run_id = store.start_run("bench")
    rows = []
    for pair in pairs:
        row = {"label": pair["label"], "url": pair["url"], "before_url": pair["before_snapshot"],
               "after_url": pair["after_snapshot"], "changed": bool(pair["changed"]), "field": pair.get("field", ""), "what_changed": pair.get("what_changed", ""),
               "expected_verdict": pair.get("expected_verdict", ""), **pair_flags(pair),
               "nano_verdict": "error", "final_verdict": "error", "note": ""}
        try:
            before, after = fetch(pair["before_snapshot"]), fetch(pair["after_snapshot"])
            donor = _extract_donor(gateway, settings, before, pair.get("field", ""), run_id)
            if donor is None:
                row["note"] = "no usable dated excerpt on the before page (counted as a miss on changed pairs)"
                row["no_donor"] = True
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
    summary = summarize(rows, build_receipt(store, settings, run_id))
    return {"meta": report_meta(meta or {}, summary), "rows": rows, "summary": summary}


def resummarize(report: dict, pairs: list[dict], meta: dict) -> dict:
    """Rebuild the headline from the stored rows (no model calls), after pair attributes such as `verified` changed."""
    by_key = {(p["url"], p["before_snapshot"]): p for p in pairs}
    rows = []
    for row in report["rows"]:
        pair = by_key.get((row["url"], row["before_url"]))
        if pair is None:
            raise SystemExit(f"Report row without a matching pair in the pairs file: {row['label']}")
        row = {k: v for k, v in row.items() if k not in ("verified", "scope", "pair_note", "listing_page")}
        row.update(label=pair["label"], what_changed=pair.get("what_changed", row.get("what_changed", "")), **pair_flags(pair))
        rows.append(row)
    summary = summarize(rows, report["summary"]["receipt"])
    return {"meta": report_meta(meta, summary), "rows": rows, "summary": summary}


def summarize(rows: list[dict], receipt: dict) -> dict:
    # The headline counts verified real changes only; an unverified pair is still run and listed (summary["unverified"]).
    changed = [r for r in rows if r["changed"] and r.get("verified", True) is not False]
    unverified = [{"label": r["label"], "nano_verdict": r["nano_verdict"], "final_verdict": r["final_verdict"]}
                  for r in rows if r["changed"] and not r.get("verified", True) is not False]
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
        "verdict_differs_from_expected": [r["label"] for r in rows if r.get("expected_verdict") and r["final_verdict"] in ALERTS + ("unchanged",)
                                          and r["final_verdict"] != r["expected_verdict"]],
        "changes_compared": sum(not r.get("no_donor") for r in changed),
        "controls_compared": sum(not r.get("no_donor") for r in unchanged),
        "no_donor": [r["label"] for r in rows if r.get("no_donor")],  # pairs the models never compared; shown, never hidden
        "misses": [r["label"] for r in changed if r["final_verdict"] not in ALERTS],
        "unverified": unverified,
        "total_usd": receipt["total_usd"], "receipt": receipt,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=str(HERE / "archive_pairs.json"))
    ap.add_argument("--out", default=str(HERE / "report.json"))
    ap.add_argument("--resummarize", action="store_true",
                    help="rebuild the headline of the existing report from its stored rows and the pairs file (no model calls)")
    args = ap.parse_args()
    meta, pairs = load_pairs(Path(args.pairs))
    if args.resummarize:
        out = Path(args.out)
        out.write_text(json.dumps(resummarize(json.loads(out.read_text()), pairs, meta), indent=2))
        print(f"Rebuilt the headline of {out.name} from its stored rows.")
        return
    if not Path(args.out).resolve().parent.is_dir():
        raise SystemExit(f"Output directory does not exist: {Path(args.out).resolve().parent}")
    load_dotenv()
    settings = get_settings()
    if not (settings.base_url and settings.api_key):
        raise SystemExit("Token Factory is not configured: set TOKEN_FACTORY_BASE_URL and TOKEN_FACTORY_API_KEY (or fill .env).")
    store = Store(":memory:")
    report = run_pairs(pairs, Gateway(settings, recorder=store.log_call), settings, store, fetch=cached_fetch, meta=meta)
    s = report["summary"]
    out = Path(args.out)
    if s["errors"]:
        out = out.with_suffix(".incomplete.json")  # never replace a good report with a run that had errors
        print(f"{s['errors']} pairs ended in an error; wrote {out.name} and kept {Path(args.out).name} as it was.")
    out.write_text(json.dumps(report, indent=2))
    print(f"Nano alone {s['nano_recall']}/{s['changed_pairs']} | Nano+Ultra {s['confirmed_recall']}/{s['changed_pairs']} "
          f"(changed {s['verdict_changed']}, superseded {s['verdict_superseded']}) | "
          f"false alarms {s['false_alarms']}/{s['unchanged_pairs']} | errors {s['errors']} | ${s['total_usd']:.4f}")
    for m in s["misses"]:
        print("MISS:", m)
    for u in s["unverified"]:
        print("UNVERIFIED (not in the headline):", u["label"], u["nano_verdict"], u["final_verdict"])
    for n in s["no_donor"]:
        print("NOT COMPARED (no usable excerpt on the before page):", n)
    if s["errors"]:
        raise SystemExit(3)  # scripts can tell an incomplete run from a finished one


if __name__ == "__main__":
    main()

"""Curate candidate before/after pairs for the archive bench from Internet Archive snapshots, politely.

Protocol (the Wayback Machine refuses bursts):
  plan   one CDX call per URL (5 s apart) -> picks ONE pair of captures per page, 14-150 days apart, different content digest
  fetch  fetches only those two snapshots per page, 15 s between requests, exponential backoff on refusal, disk cache
A fetched pair is labeled by what its date lines show: 'changed' (date lines differ) or 'unchanged' (same date lines, different
markup). A human reviews every printed pair before it goes into bench/archive_pairs.json.

   python scripts/curate_pairs.py plan  urls.txt  plan.json
   python scripts/curate_pairs.py fetch plan.json candidates.json
"""
import argparse
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench.run_bench import wayback_raw  # noqa: E402
from donor_lookback.textutil import html_to_text  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / "data" / "wayback_cache"
CDX = "https://web.archive.org/cdx/search/cdx"
MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_LINE = re.compile(rf"(?i)(deadline|due|apply by|submit by|closes?|close on|application period|open until|opens?|received by|no later than).*({MONTH}\.? \d{{1,2}}|\d{{1,2}} {MONTH}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})")
http = httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": "DonorLookbackBench/0.2 (hackathon research; polite)"})


def polite_get(url: str, params=None, *, tries: int = 4, base_delay: float = 60.0) -> httpx.Response:
    """GET with exponential backoff when the archive refuses (connection errors, 429, 5xx)."""
    delay = base_delay
    for attempt in range(1, tries + 1):
        try:
            r = http.get(url, params=params)
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
            why = f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            why = type(exc).__name__
        if attempt == tries:
            raise RuntimeError(f"archive refused {tries} times ({why}): {url[:90]}")
        print(f"  backoff {delay:.0f}s after {why}", file=sys.stderr)
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


def pick_pair(rows: list[list[str]], rng: random.Random):
    """rows: [timestamp, digest] with distinct digests. Choose consecutive captures 14-150 days apart."""
    stamps = [datetime.strptime(r[0], "%Y%m%d%H%M%S") for r in rows]
    options = [(i, i + 1) for i in range(len(rows) - 1) if 14 <= (stamps[i + 1] - stamps[i]).days <= 150]
    if not options:
        return None
    i, j = rng.choice(options)
    return rows[i][0], rows[j][0]


def cmd_plan(urls_file: str, out: str) -> None:
    rng = random.Random(7)
    plan = []
    for url in [u.strip() for u in Path(urls_file).read_text().splitlines() if u.strip() and not u.startswith("#")]:
        try:
            r = polite_get(CDX, params={"url": url, "output": "json", "fl": "timestamp,digest", "filter": "statuscode:200",
                                        "collapse": "digest", "limit": 4000})
            rows = r.json()[1:]
        except Exception as exc:  # noqa: BLE001
            print(f"# {url}: {exc}", file=sys.stderr)
            continue
        finally:
            time.sleep(5)
        pair = pick_pair(rows, rng)
        print(f"{url}: {len(rows)} distinct captures -> {pair}", file=sys.stderr)
        if pair:
            plan.append({"url": url, "before": pair[0], "after": pair[1]})
    Path(out).write_text(json.dumps(plan, indent=1))
    print(f"planned {len(plan)} pairs", file=sys.stderr)


def snapshot_text(url: str, stamp: str) -> tuple[str, bool]:
    """Returns (text, fetched_from_network). Cached on disk so a rerun never refetches."""
    key = hashlib.sha1(f"{stamp}|{url}".encode()).hexdigest()
    f = CACHE / f"{key}.txt"
    if f.exists():
        return f.read_text(), False
    r = polite_get(wayback_raw(f"https://web.archive.org/web/{stamp}/{url}"))
    r.raise_for_status()
    text = html_to_text(r.text) if "html" in r.headers.get("content-type", "") else r.text
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return text, True


def date_lines(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n") if DATE_LINE.search(ln) and len(ln) < 400})


def cmd_fetch(plan_file: str, out: str) -> None:
    results = []
    for item in json.loads(Path(plan_file).read_text()):
        try:
            texts = []
            for stamp in (item["before"], item["after"]):
                text, fetched = snapshot_text(item["url"], stamp)
                texts.append(text)
                if fetched:
                    time.sleep(15)
        except Exception as exc:  # noqa: BLE001
            print(f"# skip {item['url']}: {exc}", file=sys.stderr)
            continue
        b, a = date_lines(texts[0]), date_lines(texts[1])
        if not b or not a:
            print(f"# no date lines: {item['url']}", file=sys.stderr)
            continue
        results.append({**item, "candidate": "changed" if b != a else "unchanged", "before_lines": b[:5], "after_lines": a[:5],
                        "before_snapshot": f"https://web.archive.org/web/{item['before']}/{item['url']}",
                        "after_snapshot": f"https://web.archive.org/web/{item['after']}/{item['url']}"})
        print(f"{results[-1]['candidate']:9s} {item['url']}", file=sys.stderr)
        Path(out).write_text(json.dumps(results, indent=1))
    print(f"{len(results)} candidates -> {out}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "fetch"])
    ap.add_argument("src")
    ap.add_argument("out")
    args = ap.parse_args()
    (cmd_plan if args.cmd == "plan" else cmd_fetch)(args.src, args.out)


if __name__ == "__main__":
    main()

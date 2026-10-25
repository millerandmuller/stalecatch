"""Find CANDIDATE before/after pairs for the archive bench from Internet Archive snapshots (read-only GETs).
A candidate is only a candidate: a human reviews each printed diff before it goes into bench/archive_pairs.json.
   python scripts/curate_pairs.py URL [URL ...] [--max-snaps 14] > candidates.json"""
import argparse
import json
import re
import sys
import time

import httpx

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from bench.run_bench import fetch_snapshot  # noqa: E402

MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_LINE = re.compile(rf"(?i)(deadline|due|apply by|submit by|closes?|application period|open until).*({MONTH}\.? \d{{1,2}}|\d{{1,2}} {MONTH}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})")
http = httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": "DonorLookbackBench/0.1"})


def snapshots(url: str, limit: int) -> list[str]:
    r = http.get("https://web.archive.org/cdx/search/cdx", params={
        "url": url, "output": "json", "filter": "statuscode:200", "collapse": "timestamp:6", "limit": 400})
    rows = r.json()[1:]
    stamps = [row[1] for row in rows]
    if len(stamps) > limit:
        step = len(stamps) / limit
        stamps = [stamps[int(i * step)] for i in range(limit)]
    return stamps


def date_lines(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n") if DATE_LINE.search(ln) and len(ln) < 400})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--max-snaps", type=int, default=14)
    args = ap.parse_args()
    out = []
    for url in args.urls:
        try:
            stamps = snapshots(url, args.max_snaps)
        except Exception as exc:  # noqa: BLE001
            print(f"# {url}: CDX failed {exc}", file=sys.stderr)
            continue
        prev = None
        for st in stamps:
            snap = f"https://web.archive.org/web/{st}/{url}"
            try:
                text = fetch_snapshot(snap)
            except Exception as exc:  # noqa: BLE001
                print(f"# {snap}: {type(exc).__name__}", file=sys.stderr)
                continue
            finally:
                time.sleep(0.6)
            lines = date_lines(text)
            if prev and prev["lines"] and lines:
                kind = "changed" if lines != prev["lines"] else ("unchanged" if text != prev["text"] else None)
                if kind:
                    out.append({"url": url, "before_snapshot": prev["snap"], "after_snapshot": snap, "candidate": kind,
                                "before_lines": prev["lines"][:4], "after_lines": lines[:4]})
            prev = {"snap": snap, "text": text, "lines": lines}
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()

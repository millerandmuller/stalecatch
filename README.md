# Donor Lookback

> Your assistant doesn't just remember. It recalls.

Blood banks trace every unit from donor to patient and recall it when a donation turns out bad. Personal AI assistants cannot tell you which of their answers rest on a page that changed last night. Donor Lookback gives every memory a **donor record** (source URL, fetch time, verbatim excerpt), records every **product** built from it (calendar entry, plan, sent message), checks the donors nightly with a small model, and when a donor changes it quarantines and re-derives everything downstream with a big model. What already went out rises to the top, with a correction drafted for you to approve. Nothing is sent automatically.

## Run it (about 10 minutes)

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env        # fill TOKEN_FACTORY_BASE_URL, TOKEN_FACTORY_API_KEY, optionally TAVILY_API_KEY
.venv/bin/python -m pytest -q          # offline tests, no credentials needed
scripts/serve.sh                       # board on http://127.0.0.1:8000 (seeds the demo on first start)
```

The reveal: open the board, click **Funder moves the deadline** (it edits our own labeled demo page at `/demo/funder`), then **Run tonight's check now**. Nano flags the changed excerpt, Ultra confirms, three products re-derive, and the message already sent to a colleague moves to row 1 of Recipients with a correction draft. **Reset demo** (or `python scripts/reset_demo.py`) restores the seed.

The funder pages are ours and are labeled as demo pages. Detection quality is measured separately on real page changes (below).

## How it works

```
Agent (Hermes on NemoClaw) --MCP--> Memory server (FastAPI/MCP + SQLite: donors, products, edges)
                                          ^
Nightly check (Nebius Serverless Job) --> re-fetch (Tavily) --> Nano compares --> Ultra confirms + re-derives
                                          Lookback board (Donors | Products | Recipients, receipt, bench)
```

- `donor_lookback/store.py`: `derive` rejects calls without parents in code, not by prompt; the graph walk is a recursive SQL query.
- `donor_lookback/watch.py`, `cascade.py`, `runner.py`: Nano compare, Ultra confirm of every alarm, quarantine, Ultra re-derive of only the quarantined products.
- `donor_lookback/mcp_server.py`: the memory MCP server (`python -m donor_lookback.mcp_server`, Streamable HTTP on port 8765).
- `donor_lookback/gateway.py`: the only module that talks to Token Factory; models, base URL and prices live in `config.py`.
- `donor_lookback/agent.py`: fallback agent loop (tool calling on Nano with the same memory tools) in case Hermes cannot be made reliable.
- `scripts/record_run.py`: captures one live reveal for the read-only replay in judge mode (refuses simulated models).
- `donor_lookback/job.py`: entry point of the nightly check (`python -m donor_lookback.job`).

## How we used Nemotron, Token Factory, Serverless Jobs and Tavily

- **Nemotron on Token Factory:** two tiers behind one gateway. Nemotron 3 Nano watches every donor every night; Nemotron 3 Ultra double-checks every Nano alarm and re-derives only quarantined products. Every call logs the API's own usage fields; the cost receipt multiplies them by list prices and shows what "Ultra checks everything" would have cost.
- **Nebius Serverless Jobs:** the nightly check is a container job (`Dockerfile`, command `python -m donor_lookback.job`). The same code runs from the board button.
- **Tavily:** extract re-fetches public donor URLs; a changed page is what triggers the recall.
- **Agent shell:** Hermes on NVIDIA NemoClaw calls the memory server over MCP.

Measured numbers (receipt for a full reveal, archive bench results) are filled in from live runs; until a live run exists the board shows no receipt rather than an invented one.

## Archive replay bench

`python -m bench.run_bench` replays real before/after page pairs from the Internet Archive (`bench/archive_pairs.json`, every pair with both snapshot URLs) and writes `bench/report.json`: Nano alone vs. Nano + Ultra recall, Ultra's three verdicts as separate columns (changed / superseded by a newer cycle / unchanged), false alarms on controls, misses, and cost from logged token usage. Snapshots are cached in `data/wayback_cache`, so a rerun does not touch the archive; a cache miss makes one fetch after a 15 s wait.

**Result (last run, 19 real changes, 13 controls):** Nano alone found 17/19. Nano + Ultra found 17/19 (13 as "changed", 4 as "superseded by a newer cycle") and raised 0/13 false alarms; Nano alone raised 4/13, and Ultra cleared all four. Cost $0.15 for 32 pairs. Runs vary a little even at temperature 0 (an earlier run found 16/19 with one false alarm), so treat the figures as one measured sample, not a guarantee.

**The two misses** are `RFA-RM-22-016` and `RFA-MH-22-175`. Both are newer NIH pages whose "Key Dates" table loses its label-to-date association when flattened to text, so intake picked the sentence "All applications are due by 5:00 PM local time" instead of a date and Nano then saw nothing to compare. This is a text-extraction limit on table layouts, not a model judgement, and it is not fixed.

**How the pairs were selected (stated so nobody has to guess):**
- *Changes (19):* hand-picked from official NIH Guide notices that extend or move the application due date of one funding opportunity (43 notices from 2012 to 2023 collected; 34 had archived captures on both sides of the notice date). For each, the last capture before the notice date and the first capture after it were fetched, and the due date was read off both archived pages by hand. 15 pairs qualified because the archived page shows a different due date after the notice; the rest were dropped because the archived "after" page did not yet show the change, the page had expired, or the notice only moved open or expiration dates. No qualified pair was left out for being hard for the models. Added to those 15: three earlier pairs found by hand (NEH, Marshall Scholarship and NSF 503144, where a yearly rollover replaced the saved date, so "superseded" is the expected verdict) and one NSF pair (program 5467, Aug 9 to Sep 5, 2017) that surfaced when a randomly sampled control turned out to contain a real change. No NSF extension announcements were searched; the NSF pairs are the program pages that happened to change.
- *Controls (13):* drawn at random (seed 11) from 40 archived grant-program pages whose deadline lines are the same on both snapshots (NSF program pages, Marshall, Gates Cambridge, Schwarzman and similar). The pool was rechecked with the improved date detection after one control proved to be a change; that control moved to the changes list and the draw was repeated. Controls were not reviewed one by one beyond that.
- *Limits:* 19 changes is a small sample, the NIH pairs are mostly short extensions of one cycle, and the hand reading of due dates was done by one person. `scripts/curate_pairs.py` (`plan`, `notices`, `fetch`) reproduces the candidate search; it makes one Internet Archive index call per page and two snapshot fetches 15 s apart, with backoff on refusal.

## Limits

No real email, chat or calendar sending (outbox and calendar are internal views). Nightly cadence plus a manual button, no real-time monitoring. Only registered donor URLs are fetched.

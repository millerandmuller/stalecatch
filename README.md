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

`python -m bench.run_bench` replays real before/after page pairs from the Internet Archive (`bench/archive_pairs.json`, every pair with both snapshot URLs) and writes `bench/report.json` (committed, so the board's Proof panel works on a fresh clone). It reports Nano alone vs. Nano + Ultra recall, Ultra's three verdicts as separate columns (changed / superseded by a newer cycle / unchanged), false alarms on controls, misses and cost from logged token usage. The 64 snapshot texts are committed in `bench/snapshots`, so a rerun needs only Token Factory access; a snapshot that is missing is fetched once after a 15 s wait. The command reads `.env` itself, stops before doing anything if Token Factory is not configured, and never replaces a good report with a run that had errors (it writes `report.incomplete.json` instead).

**Result of the last run (19 real changes, 13 controls):**

| | Found / flagged |
|---|---|
| Real changes found by Nano alone | 18 / 19 |
| Real changes found by Nano + Ultra | 18 / 19 (15 as "changed", 3 as "superseded by a newer cycle") |
| False alarms on 13 controls, Nano alone | 4 / 13 |
| False alarms on 13 controls, after Ultra's second look | 0 / 13 |
| Cost for all 32 pairs | $0.13 |

Across the several runs made while the intake step was being fixed, Nano + Ultra found 16 to 18 of 19 and the false alarms after Ultra ranged from 0 to 2 (temperature 0 does not make these models repeatable), so read the table as one measured sample.

**The one miss** is `RFA-MH-22-175` (due date Mar 18 became Apr 19, 2022). The NIH page lays its "Key Dates" out as a table whose cells flatten to separate lines, so the labels and the dates are far apart in the text. Intake could not produce a verbatim excerpt that contains the date (it needs a real date, not just "5:00 PM"), so no donor was stored and the pair counts as a miss. An earlier miss of the same kind (`RFA-RM-22-016`) was fixed by keeping date lines and their surrounding block in the text window sent to the model.

**Verdict labels:** each pair carries an `expected_verdict`. The three yearly rollovers (NEH, Marshall, NSF 503144) are expected as "superseded" and came back that way. The summary lists any pair whose verdict differs from its expected one (`verdict_differs_from_expected`); in the last run that is only the miss above.

**How the pairs were selected:**
- *Changes (19):* hand-picked from official NIH Guide notices that extend or move the application due date of one funding opportunity. 43 notices from 2012 to 2023 were collected; 34 had archived captures on both sides of the notice date. For each, the last capture before the notice date and the first capture after it were fetched, and the due date was read off both archived pages by hand. 15 pairs qualified because the archived page shows a different due date after the notice. The rest were dropped because the archived "after" page did not yet show the change, the page had expired, or the notice only moved open or expiration dates. No qualified pair was left out for being hard for the models. Added to the 15: three yearly rollovers found earlier by hand (NEH, Marshall Scholarship, NSF 503144) and one NSF pair (program 5467, deadline Aug 9 to Sep 5, 2017) that surfaced when a sampled control turned out to contain a real change. No NSF extension announcements were searched; the NSF pairs are program pages that happened to change.
- *Controls (13):* `bench/control_pool.json` lists 40 archived grant-program pages (31 NSF program pages plus Gates Cambridge, Schwarzman, IMLS, Soros, Open Society, Yaddo, Creative Capital, ERC and UKRI) whose deadline lines are the same on two snapshots 14 to 150 days apart. The controls are `random.Random(11).shuffle(sorted(pool))[:13]`; that gave 12 NSF pages and one soros.org page. The pool was rechecked with the improved date detection after one control proved to be a real change; that pair moved to the changes list and the draw was repeated. Controls were otherwise not reviewed one by one.
- *Limits:* 19 changes is a small sample; the NIH pairs are mostly short extensions of one cycle; one person read the due dates by hand. `scripts/curate_pairs.py` (`plan`, `notices`, `fetch`) reproduces the candidate search; it makes one Internet Archive index call per page and fetches two snapshots 15 s apart, with backoff on refusal.

## Judge mode and the recorded run

`JUDGE_MODE=1 scripts/serve.sh` runs the board read-only for visitors: the live "Run tonight's check now" button is rate limited and the **Replay recorded run** button plays `data/recorded_run.json`, a real run captured with `scripts/record_run.py` (it refuses simulated models). The replay ends on the finished state: red donor, three re-derived products, the sent message in row 1 with its correction draft.

## Limits

No real email, chat or calendar sending (outbox and calendar are internal views). Nightly cadence plus a manual button, no real-time monitoring. Only registered donor URLs are fetched.

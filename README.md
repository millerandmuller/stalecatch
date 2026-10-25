# StaleCatch

> Your assistant doesn't just remember. It recalls.

Hosted judge mode: https://stalecatch.vercel.app, free until 2026-12-15.

StaleCatch catches the moment a page your assistant relied on goes stale. Every memory keeps a **source record** (URL, fetch time, verbatim excerpt) and every **item built from it** (calendar entry, plan, sent message). A small model re-checks the sources every night. When one changes, the items built from it go on hold, a large model updates only those, and the message you already sent rises to the top of "Who's been told" with a correction drafted for you to approve. Nothing is sent automatically.

The method comes from blood-bank look-back: when a donation turns out bad, trace everything made from it and recall it.

(The Python package and the repository keep the name `donor_lookback`; in the code a source is a "donor" and an item built from it is a "product".)

## Run it (about 10 minutes)

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env        # fill TOKEN_FACTORY_BASE_URL, TOKEN_FACTORY_API_KEY, optionally TAVILY_API_KEY
.venv/bin/python -m pytest -q          # offline tests, no credentials needed
scripts/serve.sh                       # board on http://127.0.0.1:8000 (seeds the demo on first start)
```

The reveal: open the board (columns **Sources**, **Built from them**, **Who's been told**), click **Funder moves the deadline** (it edits our own labeled demo page at `/demo/funder`), then **Run tonight's check now**. Nano flags the changed excerpt, Ultra confirms, three products re-derive, and the message already sent to a colleague moves to row 1 of Recipients with a correction draft. **Reset demo** (or `python scripts/reset_demo.py`) restores the seed.

The funder pages are ours and are labeled as demo pages. Detection quality is measured separately on real page changes (below).

## How it works

```
Agent (Hermes on NemoClaw) --MCP--> Memory server (FastAPI/MCP + SQLite: donors, products, edges)
                                          ^
Nightly check (Nebius Serverless Job) --> re-fetch (Tavily) --> Nano compares --> Ultra confirms + re-derives
                                          Board (Sources | Built from them | Who's been told, cost panel, bench)
```

- `donor_lookback/store.py`: `derive` rejects calls without parents in code, not by prompt; the graph walk is a recursive SQL query.
- `donor_lookback/watch.py`, `cascade.py`, `runner.py`: Nano compare, Ultra confirm of every alarm, quarantine, Ultra re-derive of only the quarantined products.
- `donor_lookback/dates.py`: reads dates in a text and checks every re-derived date: it must fall after today and on or before the new deadline. An update outside that window (or one that says an earlier date "still stands") is retried once with the reason and otherwise leaves the run incomplete, never silently stored. A sent message's old dates may be quoted only to replace them ("not Nov 12", "instead of Nov 3").
- `donor_lookback/mcp_server.py`: the memory MCP server (`python -m donor_lookback.mcp_server`, Streamable HTTP on port 8765).
- `donor_lookback/gateway.py`: the only module that talks to Token Factory; models, base URL and prices live in `config.py`.
- `donor_lookback/agent.py`: fallback agent loop (tool calling on Nano with the same memory tools) in case Hermes cannot be made reliable.
- `scripts/record_run.py`: captures one live reveal for the read-only replay in judge mode (refuses simulated models).
- `donor_lookback/job.py`: entry point of the nightly check (`python -m donor_lookback.job`).

## How we used Nemotron, Token Factory, Serverless Jobs and Tavily

- **Nemotron on Token Factory:** two tiers behind one gateway. Nemotron 3 Nano watches every donor every night; Nemotron 3 Ultra double-checks every Nano alarm and re-derives only quarantined products. Every call logs the API's own usage fields; the cost receipt multiplies them by list prices and shows what "Ultra checks everything" would have cost. Nano answers in about 1.7 s per call with reasoning off (measured; 16 s with reasoning on).
- **Nebius Serverless Jobs:** the nightly check is a container job (`Dockerfile`, command `python -m donor_lookback.job`). The same code runs from the board button.
- **Tavily:** extract re-fetches public donor URLs; a changed page is what triggers the recall.
- **Agent shell:** Hermes on NVIDIA NemoClaw calls the memory server over MCP.

## Archive replay bench

`python -m bench.run_bench` replays real before/after page pairs from the Internet Archive (`bench/archive_pairs.json`, every pair with both snapshot URLs) and writes `bench/report.json` (committed, so the board's Proof panel works on a fresh clone). It reports Nano alone vs. Nano + Ultra recall, Ultra's three verdicts as separate columns (changed / superseded by a newer cycle / unchanged), false alarms on controls, misses and cost from logged token usage. The 64 snapshot texts are committed in `bench/snapshots`, so a rerun needs only Token Factory access; a snapshot that is missing is fetched once after a 15 s wait. The command reads `.env` itself, stops before doing anything if Token Factory is not configured, and never replaces a good report with a run that had errors (it writes `report.incomplete.json` instead).

**Result of the last run (18 verified real changes, 13 controls):**

| | Result |
|---|---|
| Verified real changes compared by the models | 17 of 18 (one page gave no usable dated excerpt, see below) |
| Found by the quick scan (Nano) alone | 17 of 18 |
| Found with deep review (Ultra) | 17 of 18 (14 as "changed", 3 as "Next cycle posted") |
| Controls compared by the models | 10 of 13 (three gave no usable dated excerpt) |
| False alarms among the compared controls, quick scan alone | 8 of 10 |
| False alarms among the compared controls, after deep review | 1 of 10 |
| Cost for all 32 pairs | $0.17 |

**The unverified pair.** NSF 17-565 (program 5467, deadline Aug 9 to Sep 5, 2017) is marked `"verified": false` in `bench/archive_pairs.json`: it was found in the archive when a sampled control differed, and no official notice was checked. It is still run and shown in the table (tagged "unverified"), but the headline counts only the 18 verified changes; with it the earlier headline read 18 of 19. The numbers above were not re-measured for this: the headline was rebuilt from the stored rows (`python -m bench.run_bench --resummarize`).

**Next cycle posted.** Deep review's third verdict (`superseded` in the data) means: old date already passed at the after-snapshot, new date in a later cycle. The board shows it as "Next cycle posted" with the line "The page now shows the next cycle's deadline. The date you saved belonged to a cycle that has closed. Nothing you sent is wrong."

**What "not compared" means.** For a pair, the model first has to quote a dated line from the *before* page. When it cannot produce a line that appears verbatim on the page and states a date, no donor is stored and Nano and Ultra never see the pair. Such pairs are listed in the report (`no_donor`), counted as misses when they are real changes, and left out of the false-alarm denominator when they are controls. In the last run this hit `RFA-RM-22-016` (a verified real change, counted as a miss) and the controls `pims_id=5228`, `5741` and `504793` (archived NSF pages whose text names a "Due Dates" heading but no date). An earlier run printed "0 false alarms on 13 controls", which overstated the test: only 10 controls were ever compared.

**The one false alarm** after deep review is `ukri.org/opportunity/`, a rotating listing of many funding calls rather than one program page. Ultra judged that the stored line had been replaced by a newer set of calls ("superseded"). That is a defensible reading of a listing page, and it is counted as a false alarm here.

**Run-to-run variance is large.** Temperature 0 does not make these models repeatable. Over the runs made while the intake step was being fixed, Nano + Ultra found 16 to 18 of 19, and the pairs that ended as "not compared" or as a miss changed from run to run (`RFA-MH-22-175` was a miss in earlier runs and was found in the last one). Nano alone raises many false alarms on unchanged pages (4 to 8 per run); Ultra's second look removes almost all of them, which is the argument for the two tiers. Read every number here as one measured sample, not a guarantee.

**Verdict labels:** each pair carries an `expected_verdict`. The three yearly rollovers (NEH, Marshall, NSF 503144) are expected as "superseded" and came back that way. The summary lists any pair whose verdict differs from its expected one (`verdict_differs_from_expected`). Two pairs carry `scope: "NIH-wide notice"` (RFA-RM-12-005, RFA-RM-14-006): one NIH notice moved the date of several announcements and the page is one of them. RFA-HG-14-009 is noted as "third receipt date removed; earlier date Sep 18, 2015 added".

**How the pairs were selected:**
- *Changes (19 in the file, 18 verified):* hand-picked from official NIH Guide notices that extend or move the application due date of one funding opportunity. 43 notices from 2012 to 2023 were collected; 34 had archived captures on both sides of the notice date. For each, the last capture before the notice date and the first capture after it were fetched, and the due date was read off both archived pages by hand. 15 pairs qualified because the archived page shows a different due date after the notice. The rest were dropped because the archived "after" page did not yet show the change, the page had expired, or the notice only moved open or expiration dates. No qualified pair was left out for being hard for the models. Added to the 15: three yearly rollovers found earlier by hand (NEH, Marshall Scholarship, NSF 503144) and one NSF pair (program 5467, deadline Aug 9 to Sep 5, 2017) that surfaced when a sampled control turned out to contain a real change. No NSF extension announcements were searched; the NSF pairs are program pages that happened to change.
- *Controls (13):* `bench/control_pool.json` lists 40 archived grant pages (31 NSF program pages plus Gates Cambridge, Schwarzman, IMLS, Soros, Open Society, Yaddo, Creative Capital, ERC and UKRI) whose deadline lines are the same on two snapshots 14 to 150 days apart. The controls are `random.Random(11).shuffle(eligible pool)[:13]`, where eligible means the page states a labelled deadline with a date on both snapshots (`states_deadline_date_on_both` in the pool file, 24 of 40). The draw was repeated three times, and each repeat came after looking at results, so it is described here: the first pool used a looser date detector and hid a real change (NSF 5467, moved to the changes list); the second let the verb 'may' count as the month May, which admitted pages with no deadline date. Controls were not reviewed one by one beyond that.
- *Limits:* 19 changes is a small sample; the NIH pairs are mostly short extensions of one cycle; one person read the due dates by hand. `scripts/curate_pairs.py` (`plan`, `notices`, `fetch`) reproduces the candidate search; it makes one Internet Archive index call per page and fetches two snapshots 15 s apart, with backoff on refusal.

## Judge mode and the recorded run

`JUDGE_MODE=1 scripts/serve.sh` runs the board read-only for visitors: the live "Run tonight's check now" button is limited to `LIVE_RUNS_PER_DAY` runs a day (default 10, shared by all visitors, counted in the database so a demo reset does not refill it; once used up the board says "Live checks used up for today, the replay shows a real run.") and the **Replay recorded run** button plays `data/recorded_run.json`, a real run captured with `scripts/record_run.py` (it refuses simulated models). The replay ends on the finished state and stays there (reload to leave it): red donor with its old and new excerpt open, three re-derived products, the sent message in row 1 with its correction draft. That last frame is also the Cold Open.

The hosted judge board at https://stalecatch.vercel.app runs `app.py` on Vercel **without a Token Factory key**, so visitors get the seed, the replay and the proof panel, and the live button explains that it needs credentials. Its SQLite file lives in `/tmp` and is re-seeded on a cold start. The same image (`Dockerfile`) runs on any container host with a persistent `/data` volume.

## Limits

No real email, chat or calendar sending (outbox and calendar are internal views). Nightly cadence plus a manual button, no real-time monitoring. Only registered donor URLs are fetched.

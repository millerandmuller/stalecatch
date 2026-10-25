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

`scripts/curate_pairs.py` finds candidate pairs (a human reviews each diff). `python -m bench.run_bench` replays real before/after page pairs from the Internet Archive listed in `bench/archive_pairs.json` (each with snapshot URLs) plus unchanged control pairs, and reports Nano alone vs. Nano + Ultra recall, false alarms and cost. Misses are shown as misses.

## Limits

No real email, chat or calendar sending (outbox and calendar are internal views). Nightly cadence plus a manual button, no real-time monitoring. Only registered donor URLs are fetched.

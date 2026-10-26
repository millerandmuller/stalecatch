# Serverless Job run, 2026-10-26

One real run of the nightly check as a Nebius Serverless Job (project region eu-west2).

| | |
|---|---|
| Job | `stalecatch-nightly-1` (`aijob-e04mbdq8w350twpw32`), created 2026-10-26 12:36 UTC, state COMPLETED |
| Image | `cr.eu-west2.nebius.cloud/e04d1skd88s9ca8fhs/stalecatch:job` (the repository `Dockerfile`, linux/amd64) |
| Compute | `cpu-d3`, preset `2vcpu-8gb`, no GPU |
| Command | `sh /app/scripts/serverless_job_demo.sh`: seeds the graph, sets the labeled demo funder page to November 9, 2026, serves the demo pages inside the container, then runs `python -m donor_lookback.job` |
| Secrets | `TOKEN_FACTORY_API_KEY` from a MysteryBox secret; nothing else is injected |

The demo pages are served inside the job container from the same seed as the hosted board. The job did not read the hosted board's database.

## Job log (stdout, as printed)

```
Provisioning resources for the workload
Workload initialization started
Downloading container image
Starting workload container
seeded; demo funder page now says November 9, 2026
{
  "donors": 2,
  "changed": 1,
  "unchanged": 1,
  "gone": 0,
  "rejected_alarms": 0,
  "superseded": 0,
  "rederived": 3,
  "errors": [],
  "receipt": {
    "run_id": "run_06b6bf97",
    "nano": {
      "calls": 2,
      "prompt_tokens": 737,
      "completion_tokens": 280,
      "usd": 0.00011142,
      "latency_ms": 10692
    },
    "ultra": {
      "calls": 5,
      "prompt_tokens": 3829,
      "completion_tokens": 4253,
      "usd": 0.016588,
      "latency_ms": 12794
    },
    "total_usd": 0.01669942,
    "ultra_everything_usd": 0.018165,
    "failed_calls": 0,
    "calls_without_usage": 0,
    "models": {
      "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
      "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b"
    },
    "price_basis": "Token Factory list prices (USD per 1M tokens), multiplied by the usage fields the API returned."
  }
}
```

## Receipt

2 sources checked, 1 changed, 3 items re-derived, 0 errors. Nano 2 calls, Ultra 5 calls, $0.0167 in total (the same check with Ultra on everything: $0.0182). Figures come from the usage fields the API returned.

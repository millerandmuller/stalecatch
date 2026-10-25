from bench.run_bench import run_pairs, summarize, wayback_raw
from tests.fakes import FakeModels
from donor_lookback.gateway import Gateway
from donor_lookback.store import Store

PAGES = {
    "b1": "Intro\nApplications are due by 5 PM on March 1, 2024.\nFooter",
    "a1": "Intro\nApplications are due by 5 PM on April 15, 2024.\nFooter",
    "b2": "Applications are due by 5 PM on May 1, 2024.\nOld footer",
    "a2": "Applications are due by 5 PM on May 1, 2024.\nNew footer wording",
}


def test_wayback_raw():
    assert wayback_raw("https://web.archive.org/web/20240101000000/https://x.org/a") == \
        "https://web.archive.org/web/20240101000000id_/https://x.org/a"


def test_bench_counts_recall_and_false_alarms(env):
    store = Store(":memory:")
    gw = Gateway(env.settings, client=FakeModels(), recorder=store.log_call, sleep=lambda s: None)
    pairs = [
        {"label": "changed", "url": "u1", "before_snapshot": "b1", "after_snapshot": "a1", "changed": True, "field": "deadline"},
        {"label": "same", "url": "u2", "before_snapshot": "b2", "after_snapshot": "a2", "changed": False, "field": "deadline"},
    ]
    rep = run_pairs(pairs, gw, env.settings, store, fetch=PAGES.__getitem__)
    s = rep["summary"]
    assert (s["nano_recall"], s["confirmed_recall"], s["false_alarms"], s["errors"]) == (1, 1, 0, 0)
    assert s["total_usd"] > 0 and s["misses"] == []


def test_bench_shows_miss_and_error_rows(env):
    store = Store(":memory:")
    gw = Gateway(env.settings, client=FakeModels(), recorder=store.log_call, sleep=lambda s: None)
    pairs = [{"label": "dead", "url": "u", "before_snapshot": "zz", "after_snapshot": "a1", "changed": True}]
    rep = run_pairs(pairs, gw, env.settings, store, fetch=PAGES.__getitem__)
    assert rep["rows"][0]["nano_verdict"] == "error" and rep["summary"]["misses"] == ["dead"]

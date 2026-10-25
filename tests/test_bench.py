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


def test_bench_reports_superseded_verdict_in_its_own_count(env):
    store = Store(":memory:")
    gw = Gateway(env.settings, client=FakeModels(ultra_says_superseded=True), recorder=store.log_call, sleep=lambda s: None)
    pairs = [
        {"label": "rollover", "url": "u1", "before_snapshot": "b1", "after_snapshot": "a1", "changed": True, "what_changed": "next cycle"},
        {"label": "same", "url": "u2", "before_snapshot": "b2", "after_snapshot": "a2", "changed": False},
    ]
    rep = run_pairs(pairs, gw, env.settings, store, fetch=PAGES.__getitem__)
    s = rep["summary"]
    assert rep["rows"][0]["final_verdict"] == "superseded" and rep["rows"][0]["what_changed"] == "next cycle"
    assert (s["verdict_changed"], s["verdict_superseded"], s["confirmed_recall"], s["false_alarms"]) == (0, 1, 1, 0)
    assert s["misses"] == []


def test_bench_superseded_on_an_unchanged_control_is_a_false_alarm(env):
    rows = [{"label": "c", "changed": False, "nano_verdict": "changed", "final_verdict": "superseded"}]
    s = summarize(rows, {"total_usd": 0.0})
    assert s["false_alarms"] == 1 and s["verdict_superseded"] == 0


def test_cached_fetch_reads_the_shared_cache_without_the_network(tmp_path, monkeypatch):
    import hashlib
    import bench.run_bench as rb
    monkeypatch.setattr(rb, "HERE", tmp_path / "bench")
    url = "https://web.archive.org/web/20200101000000/https://example.org/a"
    cache = tmp_path / "bench" / "snapshots"
    cache.mkdir(parents=True)
    (cache / (hashlib.sha1(b"20200101000000|https://example.org/a").hexdigest() + ".txt")).write_text("cached text")
    monkeypatch.setattr(rb, "fetch_snapshot", lambda u: (_ for _ in ()).throw(AssertionError("network used")))
    assert rb.cached_fetch(url) == "cached text"


def test_cached_fetch_miss_fetches_once_then_caches(tmp_path, monkeypatch):
    import bench.run_bench as rb
    monkeypatch.setattr(rb, "HERE", tmp_path / "bench")
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(rb, "fetch_snapshot", lambda u: calls.append(u) or "fresh")
    url = "https://web.archive.org/web/20210101000000/https://example.org/b"
    assert rb.cached_fetch(url) == "fresh" and rb.cached_fetch(url) == "fresh" and len(calls) == 1


def test_summary_lists_rows_whose_verdict_differs_from_the_expected_one():
    rows = [{"label": "a", "changed": True, "nano_verdict": "changed", "final_verdict": "superseded", "expected_verdict": "changed"},
            {"label": "b", "changed": True, "nano_verdict": "changed", "final_verdict": "changed", "expected_verdict": "changed"}]
    assert summarize(rows, {"total_usd": 0.0})["verdict_differs_from_expected"] == ["a"]


def test_bench_without_credentials_stops_before_touching_the_report(tmp_path, monkeypatch):
    import pytest
    import bench.run_bench as rb
    monkeypatch.setattr(rb, "load_dotenv", lambda: None)
    for name in ("TOKEN_FACTORY_BASE_URL", "TOKEN_FACTORY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    out = tmp_path / "report.json"
    out.write_text("good report")
    monkeypatch.setattr("sys.argv", ["run_bench", "--out", str(out)])
    with pytest.raises(SystemExit):
        rb.main()
    assert out.read_text() == "good report"


def test_summary_counts_pairs_the_models_never_compared():
    rows = [{"label": "c1", "changed": True, "nano_verdict": "unchanged", "final_verdict": "unchanged", "no_donor": True},
            {"label": "u1", "changed": False, "nano_verdict": "unchanged", "final_verdict": "unchanged", "no_donor": True},
            {"label": "u2", "changed": False, "nano_verdict": "unchanged", "final_verdict": "unchanged"}]
    s = summarize(rows, {"total_usd": 0.0})
    assert (s["changes_compared"], s["controls_compared"], s["no_donor"]) == (0, 1, ["c1", "u1"])

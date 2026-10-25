import time

import pytest
from fastapi.testclient import TestClient

from donor_lookback.api import create_app
from donor_lookback.services import Services


@pytest.fixture
def client(env, tmp_path):
    svc = Services(env.settings, env.store, env.gateway, env.web)
    app = create_app(svc, judge_mode=True, cooldown_s=0.0, recorded_run_path=tmp_path / "none.json", bench_report_path=tmp_path / "none2.json")
    return TestClient(app), env


def wait_done(c, run_id):
    for _ in range(100):
        r = c.get(f"/api/run/{run_id}/events").json()
        if not r["running"] and r["run"]["finished_at"]:
            return r
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_demo_page_is_labeled_and_editable(client):
    c, env = client
    html = c.get("/demo/funder").text
    assert "Not a real funder" in html and "November 19, 2026" in html
    c.post("/api/demo/edit", json={"deadline": "November 9, 2026"})
    assert "November 9, 2026" in c.get("/demo/funder").text


def test_state_recipients_sent_first_after_run(client):
    c, env = client
    c.post("/api/demo/edit", json={"deadline": "November 9, 2026"})
    env.web.state["funder_deadline"] = "November 9, 2026"
    run_id = c.post("/api/check/run").json()["run_id"]
    wait_done(c, run_id)
    st = c.get("/api/state").json()
    assert st["recipients"][0]["id"] == "prod_msg" and st["recipients"][0]["correction_draft"]
    assert st["receipt"]["ultra"]["calls"] == 4


def test_run_requires_configuration(env, tmp_path):
    from dataclasses import replace
    from donor_lookback.gateway import Gateway
    unconfigured = replace(env.settings, base_url="", api_key="")  # explicit, so a sourced .env cannot configure it
    svc = Services(unconfigured, env.store, Gateway(unconfigured), env.web)
    c = TestClient(create_app(svc))
    r = c.post("/api/check/run")
    assert r.status_code == 503


def test_judge_mode_rate_limit(env, tmp_path):
    svc = Services(env.settings, env.store, env.gateway, env.web)
    c = TestClient(create_app(svc, judge_mode=True, cooldown_s=60.0))
    first = c.post("/api/check/run")
    assert first.status_code == 200
    wait_done(c, first.json()["run_id"])
    assert c.post("/api/check/run").status_code == 429


def test_approve_and_reset(client):
    c, env = client
    assert c.post("/api/products/prod_msg/approve").status_code == 409  # nothing to approve before a lookback
    c.post("/api/demo/edit", json={"deadline": "November 9, 2026"})
    env.web.state["funder_deadline"] = "November 9, 2026"
    wait_done(c, c.post("/api/check/run").json()["run_id"])
    assert c.post("/api/products/prod_msg/approve").json()["correction_approved"] == 1
    assert c.post("/api/demo/reset").json()["products"] == 5
    assert c.get("/api/state").json()["products"][0]["correction_approved"] == 0
    assert c.post("/api/products/nope/approve").status_code == 404


def test_state_keeps_the_receipt_of_the_run_that_changed_something(client):
    c, env = client
    c.post("/api/demo/edit", json={"deadline": "November 9, 2026"})
    env.web.state["funder_deadline"] = "November 9, 2026"
    wait_done(c, c.post("/api/check/run").json()["run_id"])
    wait_done(c, c.post("/api/check/run").json()["run_id"])  # quiet second check
    st = c.get("/api/state").json()
    assert st["run"]["id"] != st["headline_run"]["id"]
    assert st["receipt"]["ultra"]["calls"] == 4 and st["headline_run"]["summary"]["changed"] == 1


def _cap_client(env, cap):
    from dataclasses import replace
    settings = replace(env.settings, live_runs_per_day=cap)
    svc = Services(settings, env.store, env.gateway, env.web)
    return TestClient(create_app(svc, judge_mode=True, cooldown_s=0.0))


def test_judge_mode_daily_cap_is_global_and_survives_reset(env):
    c = _cap_client(env, cap=2)
    assert c.get("/api/config").json()["live_runs_left"] == 2
    for _ in range(2):
        r = c.post("/api/check/run")
        assert r.status_code == 200
        wait_done(c, r.json()["run_id"])
    assert c.get("/api/config").json()["live_runs_left"] == 0
    capped = c.post("/api/check/run")
    assert capped.status_code == 429 and "replay shows a real run" in capped.json()["detail"]
    assert c.post("/api/demo/reset").status_code == 200  # a reset must not hand out fresh live runs
    assert c.post("/api/check/run").status_code == 429


def test_daily_cap_counts_per_utc_day_and_blocked_requests_cost_nothing(env):
    env.store.claim_live_run("2026-10-24", 1)
    assert env.store.live_runs_used("2026-10-24") == 1 and env.store.live_runs_used("2026-10-25") == 0
    assert env.store.claim_live_run("2026-10-24", 1) is False
    assert env.store.live_runs_used("2026-10-24") == 1  # a refused claim is not counted


def test_unconfigured_server_does_not_use_up_the_cap(env):
    from dataclasses import replace
    from donor_lookback.gateway import Gateway
    unconfigured = replace(env.settings, base_url="", api_key="", live_runs_per_day=1)
    svc = Services(unconfigured, env.store, Gateway(unconfigured), env.web)
    c = TestClient(create_app(svc, judge_mode=True, cooldown_s=0.0))
    assert c.post("/api/check/run").status_code == 503
    assert c.get("/api/config").json()["live_runs_left"] == 1


def test_busy_run_and_cap_each_refuse_without_a_second_run(env):
    c = _cap_client(env, cap=1)
    first = c.post("/api/check/run")
    assert first.status_code == 200
    busy = c.post("/api/check/run")  # while the first still runs: 409 or, if it already ended, the cap
    assert busy.status_code in (409, 429)
    wait_done(c, first.json()["run_id"])
    assert c.get("/api/config").json()["live_runs_left"] == 0


def test_no_cap_outside_judge_mode(env):
    svc = Services(env.settings, env.store, env.gateway, env.web)
    c = TestClient(create_app(svc, judge_mode=False, cooldown_s=0.0))
    assert c.get("/api/config").json()["live_runs_left"] is None


def test_index_answers_head_requests(client):
    c, _ = client
    assert c.head("/").status_code == 200 and c.get("/").status_code == 200

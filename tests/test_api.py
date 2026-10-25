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
    assert "Not a real funder" in html and "November 12, 2027" in html
    c.post("/api/demo/edit", json={"deadline": "October 28, 2027"})
    assert "October 28, 2027" in c.get("/demo/funder").text


def test_state_recipients_sent_first_after_run(client):
    c, env = client
    c.post("/api/demo/edit", json={"deadline": "October 28, 2027"})
    env.web.state["funder_deadline"] = "October 28, 2027"
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
    c.post("/api/demo/edit", json={"deadline": "October 28, 2027"})
    env.web.state["funder_deadline"] = "October 28, 2027"
    wait_done(c, c.post("/api/check/run").json()["run_id"])
    assert c.post("/api/products/prod_msg/approve").json()["correction_approved"] == 1
    assert c.post("/api/demo/reset").json()["products"] == 5
    assert c.get("/api/state").json()["products"][0]["correction_approved"] == 0
    assert c.post("/api/products/nope/approve").status_code == 404


def test_state_keeps_the_receipt_of_the_run_that_changed_something(client):
    c, env = client
    c.post("/api/demo/edit", json={"deadline": "October 28, 2027"})
    env.web.state["funder_deadline"] = "October 28, 2027"
    wait_done(c, c.post("/api/check/run").json()["run_id"])
    wait_done(c, c.post("/api/check/run").json()["run_id"])  # quiet second check
    st = c.get("/api/state").json()
    assert st["run"]["id"] != st["headline_run"]["id"]
    assert st["receipt"]["ultra"]["calls"] == 4 and st["headline_run"]["summary"]["changed"] == 1

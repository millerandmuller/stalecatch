"""Regression tests: one per defect found in review of the check pipeline, store, gateway, fetcher and API."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from donor_lookback import cascade
from donor_lookback.api import create_app
from donor_lookback.fetcher import FetchResult, donor_url_problem
from donor_lookback.gateway import Gateway, GatewayError, parse_json, redact
from donor_lookback.runner import run_check
from donor_lookback.seed import edit_funder_deadline
from donor_lookback.services import Services
from donor_lookback.store import Store
from tests.fakes import FakeModels, _resp

FUNDER = "http://127.0.0.1:8000/demo/funder"


def change(env, text="October 28, 2026"):
    edit_funder_deadline(env.settings, text)
    env.web.state["funder_deadline"] = text


def check(env):
    return run_check(env.store, env.gateway, env.web, env.settings, trigger="test")


def override(env, purpose_marker, obj):
    real = env.models._create

    def create(model, messages, **kw):
        if purpose_marker in messages[0]["content"]:
            return _resp(obj)
        return real(model, messages, **kw)
    env.models.chat.completions.create = create


def test_ultra_confirm_without_excerpt_is_incomplete_not_green(env):
    change(env)
    override(env, "second, careful check", {"confirmed": True, "field": "d", "new_claim": "c", "reason": "yes"})
    run = check(env)
    assert run["complete"] == 0 and run["summary"]["errors"]
    assert env.store.get_donor("donor_funder")["state"] == "ok"


def test_nano_incomplete_verdict_is_incomplete_not_unchanged(env):
    override(env, "You watch a web page", {})
    run = check(env)
    assert run["complete"] == 0 and len(run["summary"]["errors"]) == 2


def test_ultra_confirming_the_old_excerpt_is_rejected(env):
    env.models.nano_false_alarm = True
    old = env.store.get_donor("donor_funder")["excerpt"]
    override(env, "second, careful check", {"confirmed": True, "field": "d", "new_claim": "c", "new_excerpt": old, "reason": "yes"})
    run = check(env)
    assert run["summary"]["changed"] == 0
    assert all(p["status"] == "released" for p in env.store.list_products())


def test_ultra_confirming_an_unrelated_line_is_loud_not_silent(env):
    env.models.nano_false_alarm = True
    override(env, "second, careful check", {"confirmed": True, "field": "d", "new_claim": "c",
                                            "new_excerpt": "Decisions are announced in the first week of January.", "reason": "yes"})
    run = check(env)
    assert run["complete"] == 0 and run["summary"]["changed"] == 0  # flagged for review, not "Checked twice, no change"
    assert all(p["status"] == "released" for p in env.store.list_products())


def test_confirmed_string_false_is_not_confirmed(env):
    env.models.nano_false_alarm = True
    override(env, "second, careful check", {"confirmed": "false", "field": "d", "new_claim": "c", "new_excerpt": "", "reason": "no"})
    assert check(env)["summary"]["changed"] == 0


def test_non_dict_json_fails_that_donor_only(env):
    change(env)
    override(env, "You watch a web page", [1, 2])
    run = check(env)
    assert run["complete"] == 0 and len(run["summary"]["errors"]) == 2  # both donors reported, run finished normally
    assert run["finished_at"]


def test_crash_after_change_resumes_next_run(env, monkeypatch):
    change(env)

    def die(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cascade, "rederive", die)
    with pytest.raises(KeyboardInterrupt):
        check(env)
    donor = env.store.get_donor("donor_funder")
    assert donor["state"] == "changed" and donor["cascade_pending"] == 1
    assert {p["status"] for p in env.store.descendants("donor_funder")} == {"quarantined"}  # atomic: never "changed + released"
    monkeypatch.undo()
    run = check(env)
    assert run["complete"] == 1
    assert {p["status"] for p in env.store.descendants("donor_funder")} == {"rederived"}
    assert env.store.get_donor("donor_funder")["cascade_pending"] == 0


def test_partial_cascade_then_source_gone_then_back_still_finishes(env):
    change(env)
    real = env.models._create
    n = {"i": 0}

    def flaky(model, messages, **kw):
        if "re-derive one item" in messages[0]["content"]:
            n["i"] += 1
            if n["i"] >= 2:
                raise ConnectionError("down")
        return real(model, messages, **kw)
    env.models.chat.completions.create = flaky
    env.gateway.settings = env.settings.__class__(**{**env.settings.__dict__, "max_retries": 1})
    assert check(env)["complete"] == 0
    env.models.chat.completions.create = real
    env.web.override[FUNDER] = FetchResult("unreachable", "", "fake", "timeout")
    check(env)
    del env.web.override[FUNDER]
    check(env)
    assert {p["status"] for p in env.store.descendants("donor_funder")} == {"rederived"}


@pytest.mark.parametrize("parent_id,child_id", [("a_parent", "z_child"), ("z_parent", "a_child")])
def test_diamond_rederives_parent_before_child(parent_id, child_id):
    s = Store(":memory:")
    d = s.add_donor(url="http://a", claim="c", excerpt="e")
    s.derive(product_id=parent_id, parents=[d["id"]], kind="note", content="p")
    s.derive(product_id=child_id, parents=[d["id"], parent_id], kind="note", content="c")
    order = [p["id"] for p in s.descendants(d["id"])]
    assert order.index(parent_id) < order.index(child_id)


def test_demo_page_escapes_html_and_rejects_markup(env, tmp_path):
    c = TestClient(create_app(Services(env.settings, env.store, env.gateway, env.web)))
    assert c.post("/api/demo/edit", json={"deadline": "<script>alert(1)</script>"}).status_code == 422
    from donor_lookback import demo_pages
    assert "<script>" not in demo_pages.render_html("funder", {"funder_deadline": "<script>x</script>"})


def test_mark_sent_keeps_what_was_actually_sent(env):
    change(env)
    check(env)
    env.store.mark_sent("prod_msg", "Sam")
    assert "Nov 12" in env.store.get_product("prod_msg")["sent_content"]


def test_gateway_format_400_on_last_attempt_still_falls_back(env):
    class Err(Exception):
        status_code = 400

    calls = []

    def create(model, messages, response_format=None, **kw):
        calls.append(response_format)
        if response_format:
            raise Err("response_format json_schema not supported")
        return _resp({"ok": True})
    gw = Gateway(env.settings.__class__(**{**env.settings.__dict__, "max_retries": 1}),
                 client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), sleep=lambda s: None)
    assert gw.chat_json("nano", "s", "u", purpose="t") == {"ok": True}
    assert calls[0] is not None and calls[1] is None


def test_unrelated_400_does_not_disable_structured_output(env):
    class Err(Exception):
        status_code = 400
    state = {"n": 0}

    def create(model, messages, response_format=None, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise Err("context length exceeded")
        return _resp({"ok": True})
    gw = Gateway(env.settings, client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), sleep=lambda s: None)
    with pytest.raises(GatewayError):
        gw.chat_json("nano", "s", "u", purpose="t")
    assert not gw._schema_unsupported


def test_parse_json_rejects_non_objects_and_unclosed_think():
    with pytest.raises(GatewayError):
        parse_json("[1,2]")
    assert parse_json('<think>reasoning {"confirmed": false}</think>{"confirmed": true}') == {"confirmed": True}


def test_error_text_is_redacted():
    fake = "sk-" + "abcdef123456"  # built at runtime: a fake credential, kept out of the source text on purpose
    assert fake not in redact(f"Incorrect API key provided: {fake} bad")


def test_donor_url_guard():
    base = "http://127.0.0.1:8000"
    assert donor_url_problem("file:///etc/passwd", base)
    assert donor_url_problem("http://169.254.169.254/latest", base)
    assert donor_url_problem("http://127.0.0.1:9999/secret", base)
    assert not donor_url_problem("http://127.0.0.1:8000/demo/funder", base)
    assert not donor_url_problem("https://funder.example.org/apply", base)


def test_orphan_runs_are_closed_on_startup(env):
    rid = env.store.start_run("board")
    create_app(Services(env.settings, env.store, env.gateway, env.web))
    run = env.store.get_run(rid)
    assert run["finished_at"] and run["complete"] == 0


def test_receipt_flags_calls_without_usage(env):
    from donor_lookback.receipt import build_receipt
    env.store.log_call(run_id="r", tier="nano", model="m", purpose="x", prompt_tokens=0, completion_tokens=0, latency_ms=5, usd=0.0)
    assert build_receipt(env.store, env.settings, "r")["calls_without_usage"] == 1


@pytest.mark.parametrize("old,new", [
    ("Applications for the 2026 Small Grants round are due by 5:00 PM Pacific on November 12, 2026.", "Deadline: October 28, 2026"),
    ("Applications for the 2026 Small Grants round are due by 5:00 PM Pacific on November 12, 2026.", "Due October 28, 2026, 5:00 PM PT"),
    ("Who can apply: registered nonprofits and fiscally sponsored groups based in the county.", "Who can apply: individuals and for-profit companies nationwide."),
    ("The Small Grants Program supports neighbourhood projects with awards of up to $15,000.", "Awards of up to $50,000 are available."),
])
def test_legitimate_rewrites_are_not_flagged_unrelated(old, new):
    from donor_lookback.textutil import looks_unrelated
    assert not looks_unrelated(old, new)


def test_null_rederive_fields_are_an_error_not_the_string_none(env):
    change(env)
    override(env, "re-derive one item", {"content": None, "correction_draft": None})
    run = check(env)
    assert run["complete"] == 0
    assert all(p["content"] != "None" for p in env.store.list_products())


def test_restore_waits_for_a_successful_compare(env):
    env.web.override[FUNDER] = FetchResult("unreachable", "", "fake", "timeout")
    check(env)
    del env.web.override[FUNDER]
    override(env, "You watch a web page", {})  # models misbehave on the comeback run
    run = check(env)
    assert run["complete"] == 0
    assert env.store.get_product("prod_msg")["status"] == "source_unavailable"  # not falsely green


def test_malformed_donor_url_does_not_abort_other_donors(env):
    env.store._conn.execute("UPDATE donors SET url='http://bad.invalid/x' WHERE id='donor_funder'")
    class Boom:
        def fetch(self, url):
            if "bad" in url:
                raise ValueError("invalid url")
            return env.web.fetch(url)
    run = run_check(env.store, env.gateway, Boom(), env.settings, trigger="test")
    assert run["finished_at"] and run["summary"]["unchanged"] == 1 and len(run["summary"]["errors"]) == 1


def test_redact_leaves_ordinary_words_alone():
    assert redact("task-management-system risk-adjusted api.dev1.example.com") == "task-management-system risk-adjusted api.dev1.example.com"
    fake = "abcd+efgh" + "/ijkl=="  # fake value, assembled at runtime
    assert fake not in redact("api" + "_key=" + fake + " failed")


@pytest.mark.parametrize("url", ["http://127.1:9/x", "http://localhost.:9/x", "http://foo.localhost/x", "http://printer.lan/x",
                                 "http://metadata.google.internal/x", "http://2130706433/x", "http://[::1]:9/x"])
def test_donor_url_guard_blocks_numeric_and_suffix_forms(url):
    assert donor_url_problem(url, "http://127.0.0.1:8000")


def test_redirect_to_internal_host_is_refused():
    import httpx
    from donor_lookback.config import Settings
    from donor_lookback.fetcher import Fetcher

    def handler(request):
        if request.url.host == "public.example.org":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:9/secret"})
        return httpx.Response(200, text="secret")
    f = Fetcher(Settings(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    res = f._http_fetch("http://public.example.org/start")
    assert res.status == "unreachable" and "refused" in res.detail

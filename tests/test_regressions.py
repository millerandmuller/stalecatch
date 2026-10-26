"""Regression tests: one per defect found in review of the check pipeline, store, gateway, fetcher and API."""
import json
from pathlib import Path
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


def change(env, text="November 9, 2026"):
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
    override(env, "second, careful check", {"verdict": "changed", "field": "d", "new_claim": "c", "reason": "yes"})
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
    override(env, "second, careful check", {"verdict": "changed", "field": "d", "new_claim": "c", "new_excerpt": old, "reason": "yes"})
    run = check(env)
    assert run["summary"]["changed"] == 0
    assert all(p["status"] == "released" for p in env.store.list_products())


def test_ultra_confirming_an_unrelated_line_is_loud_not_silent(env):
    env.models.nano_false_alarm = True
    override(env, "second, careful check", {"verdict": "changed", "field": "d", "new_claim": "c",
                                            "new_excerpt": "Decisions are announced in the first week of January.", "reason": "yes"})
    run = check(env)
    assert run["complete"] == 0 and run["summary"]["changed"] == 0  # flagged for review, not "Checked twice, no change"
    assert all(p["status"] == "released" for p in env.store.list_products())


def test_ultra_verdict_outside_the_three_is_incomplete_not_unchanged(env):
    env.models.nano_false_alarm = True
    override(env, "second, careful check", {"verdict": "false", "field": "d", "new_claim": "c", "new_excerpt": "", "reason": "no"})
    run = check(env)
    assert run["complete"] == 0 and run["summary"]["changed"] == 0


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
    assert "Nov 19" in env.store.get_product("prod_msg")["sent_content"]


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
    ("Applications for the 2026 Small Grants round are due by 5:00 PM Pacific on November 19, 2026.", "Deadline: November 9, 2026"),
    ("Applications for the 2026 Small Grants round are due by 5:00 PM Pacific on November 19, 2026.", "Due November 9, 2026, 5:00 PM PT"),
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


def test_nano_sends_enable_thinking_false_and_ultra_does_not(env):
    seen = {}

    def create(model, messages, **kw):
        seen[model] = kw.get("extra_body")
        return _resp({"ok": True})
    gw = Gateway(env.settings, client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), sleep=lambda s: None)
    gw.chat_json("nano", "s", "u", purpose="t")
    gw.chat_json("ultra", "s", "u", purpose="t")
    assert seen[env.settings.nano.model_id] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert seen[env.settings.ultra.model_id] is None


def test_superseded_verdict_alerts_and_cascades_but_is_labeled(env):
    change(env)
    env.models.ultra_says_superseded = True
    run = check(env)
    assert run["summary"]["changed"] == 1 and run["summary"]["superseded"] == 1
    assert env.store.get_donor("donor_funder")["state"] == "changed"
    details = [e["detail"] for e in env.store.events(run["id"]) if e["step"] == "donor_red"]
    assert len(details) == 1 and details[0].startswith("Next cycle posted")


def test_superseded_without_verbatim_excerpt_is_incomplete(env):
    change(env)
    override(env, "second, careful check", {"verdict": "superseded", "field": "d", "new_claim": "c", "reason": "next year"})
    run = check(env)
    assert run["complete"] == 0 and run["summary"]["errors"] and run["summary"]["superseded"] == 0
    assert env.store.get_donor("donor_funder")["state"] == "ok"


def test_chat_json_retries_once_with_more_room_when_json_is_cut_off(env):
    seen = []
    real = env.models._create

    def create(model, messages, **kw):
        seen.append(kw.get("max_tokens"))
        if len(seen) == 1:
            return _resp('{"verdict": "chan')  # cut off mid-object
        return real(model, messages, **kw)
    env.models.chat.completions.create = create
    out = env.gateway.chat_json("nano", "You watch a web page", "OLD excerpt: x\n\nCURRENT PAGE TEXT:\nDeadline: May 1, 2026", purpose="compare",
                                max_tokens=100)
    assert out["verdict"] in ("changed", "unchanged") and seen == [100, 200]


def test_chat_json_gives_up_after_one_retry(env):
    env.models.chat.completions.create = lambda model, messages, **kw: _resp("not json at all")
    with pytest.raises(GatewayError):
        env.gateway.chat_json("nano", "s", "u", purpose="x")


def test_replay_frames_stop_when_reset_or_run_takes_the_board_back(tmp_path):
    """A replay must not paint frames after Reset or Run (web/replay.js)."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    script = tmp_path / "t.js"
    web = Path(__file__).resolve().parent.parent / "web" / "replay.js"
    script.write_text(f"""
const {{ createReplay, playFrames }} = require({json.dumps(str(web))});
(async () => {{
  const replay = createReplay(), shown = [];
  const mine = replay.start();
  const done = playFrames([1, 2, 3, 4], replay, mine, (f) => shown.push(f), async () => {{
    if (shown.length === 2) replay.stop();        // Reset or Run clicked mid-replay
  }});
  const finished = await done;
  // a second replay started after the stop owns the board again; the first stays dead
  const second = replay.start();
  const ok2 = await playFrames([9], replay, second, (f) => shown.push(f), async () => {{}});
  console.log(JSON.stringify({{ finished, shown, ok2, firstOwns: replay.owns(mine) }}));
}})();
""")
    out = json.loads(subprocess.run([node, str(script)], capture_output=True, text=True, check=True).stdout)
    assert out == {"finished": False, "shown": [1, 2, 9], "ok2": True, "firstOwns": False}


def test_run_clicked_mid_replay_stops_the_replay_frames():
    """board.js runCheck() must take the board back: no replay frame is painted after the click."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    harness = Path(__file__).resolve().parent / "board_replay_harness.js"
    out = json.loads(subprocess.run([node, str(harness)], capture_output=True, text=True, check=True, timeout=30).stdout)
    assert out["errors"] == []
    assert out["before"][0] == "f1" and len(out["before"]) < 4  # the replay was under way when Run was clicked
    assert out["replayFramesAfterRun"] == []


def test_recording_urls_point_at_the_hosted_board():
    """data/recorded_run.json carries the hosted demo page paths, not the recording machine's 127.0.0.1."""
    rec = (Path(__file__).resolve().parent.parent / "data" / "recorded_run.json").read_text()
    assert "127.0.0.1" not in rec and "localhost" not in rec
    urls = {d["url"] for f in json.loads(rec)["frames"] for d in f["state"]["donors"]}
    assert urls == {"https://stalecatch.vercel.app/demo/funder", "https://stalecatch.vercel.app/demo/control"}


def test_rewrite_base_swaps_only_the_board_address():
    import importlib.util
    spec = importlib.util.spec_from_file_location("record_run", Path(__file__).resolve().parent.parent / "scripts" / "record_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.rewrite_base('{"url": "http://127.0.0.1:8000/demo/funder"}', "http://127.0.0.1:8000/", "https://x.example/") \
        == '{"url": "https://x.example/demo/funder"}'


def test_phone_widths_get_a_normal_radius_on_the_pill_surfaces():
    """Below 640px the header and ticker wrap to several lines; a 999px radius would turn them into an oval."""
    import re
    css = (Path(__file__).resolve().parent.parent / "web" / "board.css").read_text()
    assert re.search(r"\.pill\s*\{\s*border-radius:\s*999px", css)  # wide screens keep the pill
    phone = re.search(r"@media \(max-width: 640px\)\s*\{(.*)\}", css)
    assert phone, "no 640px media block"
    assert re.search(r"\.pill\s*\{\s*border-radius:\s*24px", phone.group(1))


def test_public_board_phone_behaviour_visible_reason_and_replay_approve():
    """Judge mode without credentials: a visible line explains the disabled buttons (phones show no title tooltip),
    and Approve pressed during a replay greens only the replayed card with no server call (the seed has no draft, so it would 409)."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    harness = Path(__file__).resolve().parent / "board_phone_harness.js"
    out = json.loads(subprocess.run([node, str(harness)], capture_output=True, text=True, check=True, timeout=30).stdout)
    assert out["noteBeforeReplay"] == {"text": "Live checks run locally; this public demo replays a recorded run.", "hidden": False}
    assert out["before"]["card"] == "card s-changed has-rule" and out["before"]["count"] == "1 needs correction"
    assert out["approveCalls"] == []
    assert out["afterCard"] == "card s-rederived has-rule"
    assert out["afterCount"] == "1 current"
    assert out["approvedLine"].startswith("✓ Approved.") and out["approvedLine"].endswith("(replay: nothing sent or stored)")
    assert out["stillHasApproveButton"] is False
    assert out["approvedAfterStop"] is False  # Reset or Run takes the board back and forgets the replay approval

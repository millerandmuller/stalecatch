from donor_lookback import cascade
from donor_lookback.fetcher import FetchResult
from donor_lookback.gateway import GatewayError
from donor_lookback.intake import IntakeError, intake_url
from donor_lookback.receipt import build_receipt
from donor_lookback.runner import run_check
from donor_lookback.seed import edit_funder_deadline, reset_demo
from donor_lookback.store import Store

FUNDER = "http://127.0.0.1:8000/demo/funder"


def check(env):
    return run_check(env.store, env.gateway, env.web, env.settings, trigger="test")


def set_deadline(env, text):
    edit_funder_deadline(env.settings, text)
    env.web.state["funder_deadline"] = text


def test_unchanged_run_makes_no_alarm_and_no_ultra(env):
    run = check(env)
    assert run["complete"] == 1
    assert run["summary"]["changed"] == 0 and run["summary"]["unchanged"] == 2
    assert not any(m == env.models.ultra for m, _ in env.models.calls)
    assert all(p["status"] == "released" for p in env.store.list_products())


def test_full_reveal_rederives_exactly_the_three_descendants(env):
    before_ctl = {p["id"]: p for p in env.store.list_products() if p["id"].startswith("prod_ctl")}
    set_deadline(env, "October 28, 2026")
    run = check(env)
    assert run["summary"]["changed"] == 1 and run["summary"]["rederived"] == 3
    products = {p["id"]: p for p in env.store.list_products()}
    for pid in ("prod_cal", "prod_plan", "prod_msg"):
        assert products[pid]["status"] == "rederived"
        assert "October 28, 2026" in products[pid]["content"]
        assert products[pid]["prev_content"]
    for pid, old in before_ctl.items():  # control donor untouched
        assert products[pid]["status"] == "released" and products[pid]["content"] == old["content"]
    donor = env.store.get_donor("donor_funder")
    assert donor["state"] == "changed" and "November 12, 2026" in donor["prev_excerpt"] and "October 28, 2026" in donor["excerpt"]
    assert env.store.get_donor("donor_control")["state"] == "ok"


def test_sent_message_has_correction_draft_never_auto_sent(env):
    set_deadline(env, "October 28, 2026")
    check(env)
    msg = env.store.get_product("prod_msg")
    assert msg["sent"] == 1 and msg["correction_draft"].startswith("Correction")
    assert msg["sent_content"].count("Nov 12") == 1  # what actually went out is preserved
    assert msg["correction_approved"] == 0


def test_ultra_only_on_alarms_and_double_checks_nano(env):
    set_deadline(env, "October 28, 2026")
    check(env)
    purposes = [p for _, p in env.models.calls]
    assert purposes.count("compare") == 2 and purposes.count("confirm") == 1 and purposes.count("rederive") == 3


def test_nano_false_alarm_is_rejected_by_ultra(env):
    env.models.nano_false_alarm = True
    run = check(env)
    assert run["summary"]["changed"] == 0 and run["summary"]["rejected_alarms"] == 2
    assert all(p["status"] == "released" for p in env.store.list_products())
    assert any("Checked twice, no change" in e["detail"] for e in env.store.events(run["id"]))


def test_source_gone_greys_donor_and_flags_not_quarantines(env):
    env.web.override[FUNDER] = FetchResult("gone", "", "fake", "HTTP 404")
    run = check(env)
    assert env.store.get_donor("donor_funder")["state"] == "gone"
    statuses = {p["id"]: p["status"] for p in env.store.list_products()}
    assert statuses["prod_cal"] == statuses["prod_plan"] == statuses["prod_msg"] == "source_unavailable"
    assert statuses["prod_ctl_cal"] == "released"
    assert run["summary"]["gone"] == 1
    assert env.models.calls.count((env.models.ultra, "rederive")) == 0


def test_source_returning_restores_flagged_products(env):
    env.web.override[FUNDER] = FetchResult("unreachable", "", "fake", "timeout")
    check(env)
    del env.web.override[FUNDER]
    check(env)
    assert env.store.get_donor("donor_funder")["state"] == "ok"
    assert all(p["status"] == "released" for p in env.store.list_products())


def test_layout_only_change_does_not_cascade(env):
    # Same deadline, so the fake Nano sees an identical date line: unchanged, no cascade.
    run = check(env)
    assert run["summary"]["changed"] == 0
    assert env.store.get_product("prod_msg")["status"] == "released"


def test_gateway_failure_marks_run_incomplete_not_green(env):
    set_deadline(env, "October 28, 2026")

    def boom(**kw):
        raise ConnectionError("down")
    env.models.chat.completions.create = boom
    run = check(env)
    assert run["complete"] == 0 and run["summary"]["errors"]
    assert all(p["status"] == "released" for p in env.store.list_products())  # nothing changed, nothing falsely green


def test_interrupted_cascade_resumes_on_next_run(env):
    set_deadline(env, "October 28, 2026")
    real = env.models._create
    state = {"n": 0}

    def flaky(model, messages, **kw):
        if "re-derive one item" in messages[0]["content"]:
            state["n"] += 1
            if state["n"] == 2:
                raise ConnectionError("rate limited")
        return real(model, messages, **kw)
    env.models.chat.completions.create = flaky
    env.gateway.settings = env.settings.__class__(**{**env.settings.__dict__, "max_retries": 1})
    run = check(env)
    assert run["complete"] == 0
    assert any(p["status"] == "quarantined" for p in env.store.list_products())
    env.models.chat.completions.create = real
    run2 = check(env)
    assert run2["complete"] == 1
    assert {p["status"] for p in env.store.list_products() if not p["id"].startswith("prod_ctl")} == {"rederived"}


def test_receipt_comes_from_logged_usage_fields(env):
    set_deadline(env, "October 28, 2026")
    run = check(env)
    r = run["summary"]["receipt"]
    # 2 compare (400/60) on nano; 1 confirm (900/80) + 3 rederive (1200/90) on ultra
    assert r["nano"]["calls"] == 2 and r["nano"]["prompt_tokens"] == 800 and r["nano"]["completion_tokens"] == 120
    assert r["ultra"]["calls"] == 4 and r["ultra"]["prompt_tokens"] == 900 + 3600
    s = env.settings
    expected_nano = (800 * s.nano.usd_per_m_input + 120 * s.nano.usd_per_m_output) / 1e6
    assert abs(r["nano"]["usd"] - expected_nano) < 1e-8
    assert r["ultra_everything_usd"] > r["total_usd"]
    assert build_receipt(env.store, env.settings, run["id"]) == r


def test_intake_stores_verbatim_excerpt(env):
    s = Store(":memory:")
    d = intake_url(s, env.gateway, env.web, env.settings, FUNDER)
    assert "due by" in d["excerpt"] and d["fetch_via"] == "fake"


def test_intake_rejects_paraphrase(env):
    s = Store(":memory:")
    real = env.models._create

    def paraphrase(model, messages, **kw):
        r = real(model, messages, **kw)
        r.choices[0].message.content = '{"claim":"c","excerpt":"the grant closes in november","date_fields":[]}'
        return r
    env.models.chat.completions.create = paraphrase
    try:
        intake_url(s, env.gateway, env.web, env.settings, FUNDER)
        raise AssertionError("expected IntakeError")
    except IntakeError:
        pass
    assert s.list_donors() == []


def test_reset_restores_seed(env):
    set_deadline(env, "October 28, 2026")
    check(env)
    out = reset_demo(env.store, env.settings)
    assert out["donors"] == 2 and out["products"] == 5
    assert all(p["status"] == "released" for p in env.store.list_products())
    assert env.store.calls() == []


def test_intake_rejects_a_dateless_excerpt_and_retries_with_the_reason(env):
    s = Store(":memory:")
    real = env.models._create
    prompts = []

    def create(model, messages, **kw):
        prompts.append(messages[-1]["content"])
        if len(prompts) == 1:  # a verbatim sentence about the deadline that states no date
            page = messages[-1]["content"].split("PAGE TEXT:\n", 1)[1]
            page_line = next(l for l in page.split("\n") if l.strip() and not any(c.isdigit() for c in l))
            from tests.fakes import _resp
            return _resp({"claim": "c", "excerpt": page_line.strip(), "date_fields": []})
        return real(model, messages, **kw)
    env.models.chat.completions.create = create
    d = intake_url(s, env.gateway, env.web, env.settings, FUNDER)
    assert any(c.isdigit() for c in d["excerpt"])
    assert len(prompts) == 2 and "contains no date" in prompts[1]


def test_excerpt_problem_wants_a_date_not_just_a_digit():
    from donor_lookback.intake import excerpt_problem
    page = "Due by 5:00 PM local time. Deadline: November 12, 2026. Rolling from 3 March."
    assert "no date" in excerpt_problem("c", "Due by 5:00 PM local time.", page)
    assert excerpt_problem("c", "Deadline: November 12, 2026.", page) == ""
    assert excerpt_problem("c", "Rolling from 3 March.", page) == ""
    assert "verbatim" in excerpt_problem("c", "Deadline: May 5, 2026.", page)
    assert "empty" in excerpt_problem("", "x", page)


def test_relevant_window_keeps_bare_date_lines_on_a_long_page():
    from donor_lookback.textutil import relevant_window
    def filler(a, b):
        return "\n".join(f"Application due statement number {i} about the deadline for applicants." for i in range(a, b))
    page = filler(0, 300) + "\nKey Dates\nApplication Due Dates\nJuly 19, 2022\n" + filler(300, 600)
    window = relevant_window(page, "application due date", 3000)
    assert "Application Due Dates\nJuly 19, 2022" in window and len(window) <= 3100  # the label stays next to its date
    assert "[...]" in window  # a gap is marked, so a model cannot quote across it


def test_date_pattern_accepts_ordinals_and_rejects_month_lookalikes():
    from donor_lookback.textutil import DATE_IN_TEXT
    for ok in ("12th of November", "Nov 12th", "12-Nov-26", "Nov. 12", "2026-11-12", "12.11.2026", "Closes on 12/11", "3 March"):
        assert DATE_IN_TEXT.search(ok), ok
    for bad in ("Due by 5:00 PM local time.", "Decision 3 weeks later.", "Mayor 5 said so", "NSF 16-1 may apply to proposals", "the guide, 1 may be used", "a decade of 1 grant", "Rolling basis."):
        assert not DATE_IN_TEXT.search(bad), bad


def test_states_deadline_needs_a_labelled_date():
    from donor_lookback.textutil import states_deadline
    assert states_deadline("Full Proposal Deadline Date\nAugust 9, 2017")
    assert states_deadline("Application deadline: May 15, 2022")
    assert not states_deadline("A revised guide was issued on October 4, 2012 and is effective for proposals due on or after January 14, 2013.")
    assert not states_deadline("Due Dates\nsee the solicitation")


def test_window_respects_the_limit_with_markers_and_survives_one_huge_line():
    from donor_lookback.textutil import relevant_window
    lines = [f"Posted {i}: Oct {i % 28 + 1}, 2026 update" if i % 2 else f"Applications are due statement {i}" for i in range(3000)]
    window = relevant_window("\n".join(lines), "applications are due", 2000)
    assert 0 < len(window) <= 2000
    assert relevant_window("x" * 50000, "anything", 1000)  # one huge line still gives the model something


def test_changed_undated_statement_stays_in_view_on_a_date_heavy_page():
    from donor_lookback.textutil import relevant_window
    noise = [f"Posted {i}: Oct {i % 28 + 1}, 2026 update" for i in range(2000)]
    page = "\n".join(noise[:1000] + ["Applications are now accepted on a rolling basis"] + noise[1000:])
    assert "rolling basis" in relevant_window(page, "Applications are due by 5:00 PM on Nov 12, 2026", 3000)

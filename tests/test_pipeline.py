from donor_lookback import cascade
from donor_lookback.fetcher import FetchResult
from donor_lookback.gateway import GatewayError
from donor_lookback.intake import IntakeError, intake_url
from donor_lookback.receipt import build_receipt
from donor_lookback.runner import run_check
from donor_lookback.seed import edit_funder_deadline, reset_demo
from donor_lookback.store import Store
from tests.fakes import _resp

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
    set_deadline(env, "November 9, 2026")
    run = check(env)
    assert run["summary"]["changed"] == 1 and run["summary"]["rederived"] == 3
    products = {p["id"]: p for p in env.store.list_products()}
    for pid in ("prod_cal", "prod_plan", "prod_msg"):
        assert products[pid]["status"] == "rederived"
        assert products[pid]["prev_content"]
    assert "November 9, 2026" in products["prod_cal"]["content"]
    assert products["prod_plan"]["content"].startswith("Moved earlier:")
    assert products["prod_msg"]["content"] == "Replaced by correction."
    for pid, old in before_ctl.items():  # control donor untouched
        assert products[pid]["status"] == "released" and products[pid]["content"] == old["content"]
    donor = env.store.get_donor("donor_funder")
    assert donor["state"] == "changed" and "November 19, 2026" in donor["prev_excerpt"] and "November 9, 2026" in donor["excerpt"]
    assert env.store.get_donor("donor_control")["state"] == "ok"


def test_sent_message_has_correction_draft_never_auto_sent(env):
    set_deadline(env, "November 9, 2026")
    check(env)
    msg = env.store.get_product("prod_msg")
    assert msg["sent"] == 1 and msg["correction_draft"].startswith("Hi Sam")
    assert msg["sent_content"].count("Nov 19") == 1 and msg["recipient"] == "Sam (Executive Director, client)"  # what went out is preserved
    assert msg["correction_approved"] == 0


def test_ultra_only_on_alarms_and_double_checks_nano(env):
    set_deadline(env, "November 9, 2026")
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
    set_deadline(env, "November 9, 2026")

    def boom(**kw):
        raise ConnectionError("down")
    env.models.chat.completions.create = boom
    run = check(env)
    assert run["complete"] == 0 and run["summary"]["errors"]
    assert all(p["status"] == "released" for p in env.store.list_products())  # nothing changed, nothing falsely green


def test_interrupted_cascade_resumes_on_next_run(env):
    set_deadline(env, "November 9, 2026")
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
    set_deadline(env, "November 9, 2026")
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
    set_deadline(env, "November 9, 2026")
    check(env)
    out = reset_demo(env.store, env.settings)
    assert out["donors"] == 2 and out["products"] == 6
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
    page = "Due by 5:00 PM local time. Deadline: November 19, 2026. Rolling from 3 March."
    assert "no date" in excerpt_problem("c", "Due by 5:00 PM local time.", page)
    assert excerpt_problem("c", "Deadline: November 19, 2026.", page) == ""
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


def test_every_rederived_date_falls_on_or_before_the_new_deadline(env):
    from datetime import date
    from donor_lookback.dates import parse_dates
    set_deadline(env, "November 9, 2026")
    check(env)
    deadline = date(2026, 11, 9)
    for p in env.store.list_products():
        if p["status"] != "rederived":
            continue
        texts = [p["content"]] + ([p["correction_draft"]] if p["correction_draft"] else [])
        for text in texts:
            for d, start, end in parse_dates(text, deadline):
                quoted_old = p["correction_draft"] == text and (d.month, d.day) in {(11, 10), (11, 12), (11, 19)}
                assert d <= deadline or quoted_old, (p["id"], text)
    draft = env.store.get_product("prod_msg")["correction_draft"]
    assert "still stand" not in draft.lower() and "Oct 26" in draft and "Nov 2" in draft  # earlier dates are proposed


def test_ultra_update_that_lands_after_the_deadline_is_retried_then_refused(env):
    from donor_lookback.cascade import rederive
    from donor_lookback.gateway import GatewayError
    set_deadline(env, "November 9, 2026")
    real = env.models._create
    seen = []

    def create(model, messages, **kw):
        if "re-derive one item" in messages[0]["content"] and "ITEM kind: plan" in messages[-1]["content"]:
            seen.append(messages[-1]["content"])
            return _resp({"content": "Moved earlier: draft by Nov 12, submit by Nov 8.", "correction_draft": ""})
        return real(model, messages, **kw)
    env.models.chat.completions.create = create
    run = check(env)
    assert run["complete"] == 0 and any("does not fit the new deadline" in e["error"] for e in run["summary"]["errors"])
    assert len(seen) == 2 and "previous answer was rejected" in seen[1]


def test_late_dates_rules():
    from datetime import date
    from donor_lookback.dates import late_dates
    deadline = date(2027, 10, 28)
    sent = "Could you send the budget by Nov 3? I'll have the draft to you by Nov 5."
    assert late_dates("Draft by Oct 21, submit Oct 28.", deadline) == []
    assert late_dates("The Nov 5 draft still stands.", deadline, sent)  # never allowed
    assert late_dates("Draft by Nov 5.", deadline, sent)  # a later date as a plan, not a replacement
    assert late_dates("Please send it by Oct 14 instead of Nov 3.", deadline, sent) == []
    assert late_dates("Please send it by Dec 1 instead of Nov 3.", deadline, sent)  # Dec 1 is not an old value


def test_late_dates_catches_abbreviations_loose_cues_and_other_formats():
    from datetime import date
    from donor_lookback.dates import late_dates
    deadline = date(2027, 10, 28)
    sent = "Could you send the budget by Nov 3? I'll have the draft to you by Nov 5."
    assert late_dates("The deadline is Oct 28. Nov. 5 for the draft.", deadline, sent)  # "Nov." does not end a sentence
    assert late_dates("The deadline moved to Oct 28. I'll still send the draft by Nov 5, not later.", deadline, sent)
    assert late_dates("I'll send the draft on Nov 5 as planned, since I was ahead.", deadline, sent)
    assert late_dates("Draft by 2027-11-05.", deadline, sent) and late_dates("Draft by 11/5.", deadline, sent)
    assert late_dates("Nov 5 remains the plan.", deadline, sent)
    assert late_dates("Draft by Oct 21, not Nov 5.", deadline, sent) == []
    assert late_dates("Draft by Oct 21 instead of the Nov 5 date.", deadline, sent) == []


def test_year_less_dates_resolve_next_to_the_deadline():
    from datetime import date
    from donor_lookback.dates import late_dates, deadline_of
    assert late_dates("Plan: documents Dec 20, submit Jan 10.", date(2028, 1, 15)) == []  # Dec 20 is 2027, before the deadline
    assert late_dates("Plan: draft by Nov 5.", date(2027, 10, 28)) == ["Nov 5 is after the new deadline"]
    assert deadline_of("Due by 5:00 PM Pacific on October 28.", date(2027, 11, 12)) == date(2027, 10, 28)


def test_deadline_of_picks_the_deadline_not_an_opening_or_old_date():
    from datetime import date
    from donor_lookback.dates import deadline_of
    assert deadline_of("Applications open September 1, 2027 and are due October 28, 2027.") == date(2027, 10, 28)
    assert deadline_of("Extended from October 28, 2027 to November 12, 2027.") == date(2027, 11, 12)
    assert deadline_of("Rolling basis, no date.") is None


def test_funder_page_treats_the_default_date_in_another_form_as_unchanged():
    from donor_lookback import demo_pages
    for typed in ("Nov 19, 2026", "november 19, 2026", "November 19 2026"):
        line = demo_pages.funder_deadline_line({"funder_deadline": typed})
        assert line.startswith("Applications for the 2026") and "Correction" not in line, typed
    assert not demo_pages.valid_deadline("tomorrow") and not demo_pages.valid_deadline("October 28") and demo_pages.valid_deadline("November 9, 2026")


def test_rederived_dates_must_fall_after_today_too():
    from datetime import date
    from donor_lookback.dates import late_dates
    deadline, today = date(2026, 11, 9), date(2026, 10, 20)
    assert late_dates("Draft by Oct 26, submit Nov 8.", deadline, after=today) == []
    assert late_dates("Documents by Oct 6.", deadline, after=today) == ["Oct 6 is not after today"]
    assert late_dates("Documents by Oct 20.", deadline, after=today) == ["Oct 20 is not after today"]  # today itself is not a plan date
    sent = "Could you send the budget by Oct 6? I'll have the draft by Nov 12."
    assert late_dates("Please send it by Oct 26 instead of Oct 6.", deadline, sent, after=today) == []  # an old value, quoted to replace it
    assert late_dates("Please send it by Oct 6.", deadline, sent, after=today)  # the same date as a new plan is not allowed
    assert late_dates("Draft by Nov 12.", deadline, sent, after=today) == ["Nov 12 is after the new deadline"]


def test_ultra_update_dated_before_today_is_retried_then_refused(env):
    set_deadline(env, "November 9, 2026")
    real = env.models._create
    seen = []

    def create(model, messages, **kw):
        if "re-derive one item" in messages[0]["content"] and "ITEM kind: plan" in messages[-1]["content"]:
            seen.append(messages[-1]["content"])
            return _resp({"content": "Moved earlier: documents by Oct 6, submit by Nov 8.", "correction_draft": ""})
        return real(model, messages, **kw)
    env.models.chat.completions.create = create
    run = check(env)
    assert run["complete"] == 0 and any("not after today" in e["error"] for e in run["summary"]["errors"])
    assert len(seen) == 2 and "TODAY: October 20, 2026" in seen[0] and "after today" in seen[1]


def test_when_the_new_deadline_is_already_past_only_the_upper_bound_applies(env, monkeypatch):
    from datetime import date
    from donor_lookback import dates
    monkeypatch.setattr(dates, "today", lambda: date(2026, 11, 20))  # after the new deadline: no valid window
    set_deadline(env, "November 9, 2026")
    assert check(env)["summary"]["rederived"] == 3

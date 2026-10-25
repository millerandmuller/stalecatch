import time

import pytest

from donor_lookback.store import ParentError, Store


def test_derive_without_parents_errors():
    s = Store(":memory:")
    with pytest.raises(ParentError):
        s.derive(parents=[], kind="calendar", content="x")


def test_derive_with_unknown_parent_errors():
    s = Store(":memory:")
    with pytest.raises(ParentError):
        s.derive(parents=["nope"], kind="calendar", content="x")


def test_donor_requires_verbatim_fields():
    s = Store(":memory:")
    with pytest.raises(ValueError):
        s.add_donor(url="http://x", claim="c", excerpt="")


def test_product_with_two_donors_is_found_from_either():
    s = Store(":memory:")
    a = s.add_donor(url="http://a", claim="c", excerpt="e")
    b = s.add_donor(url="http://b", claim="c", excerpt="e")
    p = s.derive(parents=[a["id"], b["id"]], kind="plan", content="p")
    assert [x["id"] for x in s.descendants(a["id"])] == [p["id"]]
    assert [x["id"] for x in s.descendants(b["id"])] == [p["id"]]


def test_graph_walk_200_nodes_under_100ms():
    s = Store(":memory:")
    d = s.add_donor(url="http://a", claim="c", excerpt="e")
    prev = d["id"]
    ids = []
    for i in range(199):
        parent = prev if i % 3 == 0 else d["id"]
        p = s.derive(parents=[parent], kind="note", content=f"n{i}")
        ids.append(p["id"])
        prev = p["id"]
    t = time.perf_counter()
    desc = s.descendants(d["id"])
    elapsed = time.perf_counter() - t
    assert len(desc) == 199
    assert elapsed < 0.1


def test_lookback_puts_sent_first():
    s = Store(":memory:")
    d = s.add_donor(url="http://a", claim="c", excerpt="e")
    s.derive(parents=[d["id"]], kind="calendar", content="cal")
    m = s.derive(parents=[d["id"]], kind="message", content="msg", sent=True, recipient="Sam")
    assert s.lookback(d["id"])["sent_first"][0]["id"] == m["id"]


def test_latest_run_with_change_survives_a_later_quiet_run():
    st = Store(":memory:")
    first, second = st.start_run("a"), st.start_run("b")
    st.log_event(first, "donor_red", "changed")
    st.log_event(second, "unchanged", "quiet")
    assert st.latest_run()["id"] == second and st.latest_run_with_change()["id"] == first
    assert Store(":memory:").latest_run_with_change() is None


def test_load_dotenv_handles_comments_quotes_export_and_keeps_existing(tmp_path, monkeypatch):
    import os
    from donor_lookback.config import load_dotenv
    f = tmp_path / ".env"
    f.write_text('A=plain # note\nB="quoted # kept" # gone\nexport C=exp\nD=x=y\nE=keep\r\n# comment\n\nF=\n')
    monkeypatch.setenv("E", "already")
    for k in "ABCDF":
        monkeypatch.delenv(k, raising=False)
    load_dotenv(f)
    assert (os.environ["A"], os.environ["B"], os.environ["C"], os.environ["D"], os.environ["E"], os.environ["F"]) == \
        ("plain", "quoted # kept", "exp", "x=y", "already", "")
    for k in "ABCDF":
        monkeypatch.delenv(k, raising=False)

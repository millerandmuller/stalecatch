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

import asyncio

import pytest

from donor_lookback.fetcher import Fetcher
from donor_lookback.mcp_server import create_mcp
from donor_lookback.services import Services


def call(mcp, name, args):
    return asyncio.run(mcp.call_tool(name, args))


@pytest.fixture
def mcp(env):
    return create_mcp(Services(env.settings, env.store, env.gateway, env.web))


def test_tools_listed(mcp):
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"remember_from_url", "add_donor", "derive", "lookback", "mark_sent", "list_memory"} <= names


def test_derive_without_parents_errors_via_mcp(mcp):
    with pytest.raises(Exception) as e:
        call(mcp, "derive", {"parents": [], "kind": "calendar", "content": "x"})
    assert "parent" in str(e.value).lower()


def test_remember_then_derive_then_lookback(env, mcp):
    from donor_lookback.store import Store
    env.store.reset_all()
    call(mcp, "remember_from_url", {"url": "http://127.0.0.1:8000/demo/funder"})
    donor = env.store.list_donors()[0]
    call(mcp, "derive", {"parents": [donor["id"]], "kind": "calendar", "content": "Nov 12"})
    call(mcp, "derive", {"parents": [donor["id"]], "kind": "message", "content": "m", "sent": True, "recipient": "Sam"})
    lb = env.store.lookback(donor["id"])
    assert len(lb["products"]) == 2 and lb["sent_first"][0]["kind"] == "message"

import json
from types import SimpleNamespace

from donor_lookback.agent import chat
from donor_lookback.services import Services
from donor_lookback.store import Store


def tc(i, name, args):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class ScriptedAgent:
    """Plays a model that makes a mistake (derive without parents), reads the error, then does it right."""

    def __init__(self):
        self.step = 0
        self.seen_errors = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools=None, **kw):
        self.step += 1
        donor = next((json.loads(m["content"])["id"] for m in messages if m["role"] == "tool" and "donor_" in m["content"]), None)
        errs = [m["content"] for m in messages if m["role"] == "tool" and "error" in m["content"]]
        self.seen_errors = errs
        plan = {
            1: [tc(1, "remember_from_url", {"url": "http://127.0.0.1:8000/demo/funder"})],
            2: [tc(2, "derive", {"parents": [], "kind": "calendar", "content": "x"})],
            3: [tc(3, "derive", {"parents": [donor], "kind": "calendar", "content": "Nov 12 deadline", "title": "Calendar"}),
                tc(4, "derive", {"parents": [donor], "kind": "message", "content": "told Sam", "sent": True, "recipient": "Sam"})],
        }
        if self.step in plan:
            msg = SimpleNamespace(content="", tool_calls=plan[self.step])
        else:
            msg = SimpleNamespace(content="Saved.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


def test_agent_loop_uses_memory_tools_and_recovers_from_parent_error(env):
    store = Store(":memory:")
    from donor_lookback.gateway import Gateway
    gw = Gateway(env.settings, client=ScriptedAgent(), recorder=store.log_call, sleep=lambda s: None)
    # intake needs Nano JSON too: wrap the scripted client so extraction prompts get the fake models' answer
    from tests.fakes import FakeModels
    fake, agent = FakeModels(), gw.client
    real = agent.chat.completions.create

    def route(model, messages, **kw):
        if kw.get("tools"):
            return real(model, messages, **kw)
        return fake._create(model, messages, **kw)
    agent.chat.completions.create = route
    svc = Services(env.settings, store, gw, env.web)
    out = chat(svc, "Remember this deadline http://127.0.0.1:8000/demo/funder and I told Sam.")
    names = [c["name"] for c in out["tool_calls"]]
    assert names[:2] == ["remember_from_url", "derive"] and out["tool_calls"][1]["ok"] is False
    donor = store.list_donors()[0]
    assert len(store.lookback(donor["id"])["products"]) == 2
    assert store.lookback(donor["id"])["sent_first"][0]["sent"] == 1


def test_agent_tool_arguments_are_validated(env):
    from donor_lookback.agent import run_tool
    svc = Services(env.settings, env.store, env.gateway, env.web)
    assert "error" in run_tool(svc, "derive", {"parents": 5, "kind": "x", "content": "y"})
    assert "error" in run_tool(svc, "derive", {"parents": ["donor_funder"], "kind": {"a": 1}, "content": "y"})
    made = run_tool(svc, "derive", {"parents": ["donor_funder"], "kind": "note", "content": "y", "sent": "false"})
    assert made["sent"] == 0

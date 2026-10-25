"""F2 fallback agent loop: a minimal tool-calling assistant on Nano that uses the SAME memory tools as the MCP server.
Used if Hermes on NemoClaw cannot be made to call tools reliably (day-1 spike decides). CLI: python -m donor_lookback.agent "..." """
import json
import sys
from typing import Optional

from .fetcher import donor_url_problem
from .intake import IntakeError, intake_url
from .services import Services, build_services
from .gateway import GatewayError
from .store import NotFoundError, ParentError

SYSTEM = (
    "You are a personal assistant with a memory that keeps a donor record for every fact. "
    "When the user gives a web page with a deadline, call remember_from_url first. Then save what you build from it with "
    "derive, always passing the parent ids (the donor id, or the id of the item it was built from): a calendar entry, then a plan. "
    "If the user says they told someone, save that message with derive and sent=true and the recipient. "
    "Use only ids returned by tools. Finish with one short sentence for the user."
)

TOOLS = [
    {"type": "function", "function": {"name": "remember_from_url", "description": "Fetch a page, extract the deadline claim with a verbatim excerpt, store it as a donor.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "label": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "derive", "description": "Save something built from donors/products. parents are required.",
     "parameters": {"type": "object", "properties": {
         "parents": {"type": "array", "items": {"type": "string"}}, "kind": {"type": "string", "description": "calendar | plan | message"},
         "content": {"type": "string"}, "title": {"type": "string"}, "sent": {"type": "boolean"}, "recipient": {"type": "string"}},
         "required": ["parents", "kind", "content"]}}},
    {"type": "function", "function": {"name": "lookback", "description": "Everything built on a donor, sent items first.",
     "parameters": {"type": "object", "properties": {"donor_id": {"type": "string"}}, "required": ["donor_id"]}}},
]


def run_tool(svc: Services, name: str, args: dict) -> dict:
    """Same behaviour as the MCP tools; errors come back as data so the model can correct itself."""
    try:
        if name == "remember_from_url":
            problem = donor_url_problem(args["url"], svc.settings.public_base_url)
            if problem:
                return {"error": problem}
            return intake_url(svc.store, svc.gateway, svc.fetcher, svc.settings, args["url"], label=args.get("label") or None)
        if name == "derive":
            parents = args.get("parents")
            if not isinstance(parents, list) or not all(isinstance(x, str) and x for x in parents):
                return {"error": "parents must be a list of donor/product ids"}
            if not all(isinstance(args.get(k), str) and args.get(k) for k in ("kind", "content")):
                return {"error": "kind and content must be non-empty strings"}
            sent = args.get("sent", False)
            if isinstance(sent, str):
                sent = sent.strip().lower() == "true"
            return svc.store.derive(parents=parents, kind=args["kind"], content=args["content"],
                                    title=args.get("title") or None, sent=bool(sent) if isinstance(sent, bool) else False,
                                    recipient=args.get("recipient") or None)
        if name == "lookback":
            return svc.store.lookback(args["donor_id"])
        return {"error": f"unknown tool {name}"}
    except (ParentError, NotFoundError, IntakeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {"error": str(exc)}


def chat(svc: Services, user_text: str, max_steps: int = 8, run_id: Optional[str] = None) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_text}]
    calls = []
    for _ in range(max_steps):
        try:
            msg = svc.gateway.chat_tools("nano", messages, TOOLS, purpose="agent", run_id=run_id)
        except GatewayError as exc:
            return {"reply": f"The model call failed: {exc}", "tool_calls": calls}
        tool_calls = (getattr(msg, "tool_calls", None) or [])[:6]  # cap parallel calls per step
        messages.append({"role": "assistant", "content": msg.content or "",
                         **({"tool_calls": [{"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}}
                                            for t in tool_calls]} if tool_calls else {})})
        if not tool_calls:
            return {"reply": msg.content or "", "tool_calls": calls}
        for t in tool_calls:
            try:
                args = json.loads(t.function.arguments or "{}")
            except json.JSONDecodeError:
                result = {"error": "arguments were not valid JSON"}
            else:
                result = run_tool(svc, t.function.name, args) if isinstance(args, dict) else {"error": "arguments must be an object"}
            calls.append({"name": t.function.name, "ok": "error" not in result})
            messages.append({"role": "tool", "tool_call_id": t.id, "content": json.dumps(result, default=str)})
    return {"reply": "Stopped after too many steps.", "tool_calls": calls}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('usage: python -m donor_lookback.agent "Remember this deadline: <url>"')
    out = chat(build_services(), " ".join(sys.argv[1:]))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

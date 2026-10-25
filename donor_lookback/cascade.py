"""F5 Lookback cascade: quarantine all descendants of a changed donor, then Ultra re-derives only those."""
import json
from typing import Optional

from .gateway import Gateway
from .prompts import REDERIVE_SYSTEM
from .store import Store

REDERIVE_SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}, "correction_draft": {"type": "string"}},
    "required": ["content", "correction_draft"], "additionalProperties": False,
}


def quarantine_descendants(store: Store, donor_id: str) -> list[dict]:
    """Amber for every product downstream of the donor (ordered by depth). Returns them."""
    desc = store.descendants(donor_id)
    for p in desc:
        store.set_product_status(p["id"], "quarantined")
    return desc


def mark_source_unavailable(store: Store, donor_id: str) -> list[dict]:
    """Source gone or unreachable: flag descendants, do not quarantine, do not re-derive."""
    desc = store.descendants(donor_id)
    for p in desc:
        if p["status"] == "released":
            store.set_product_status(p["id"], "source_unavailable")
    return desc


def restore_source_available(store: Store, donor_id: str) -> None:
    for p in store.descendants(donor_id):
        if p["status"] == "source_unavailable":
            store.set_product_status(p["id"], "released")


def _parent_context(store: Store, product: dict) -> str:
    lines = []
    for pid in product["parents"]:
        try:
            d = store.get_donor(pid)
            lines.append(f"- donor {pid} ({d['url']}) claim: {d['claim']} | excerpt: {d['excerpt']}")
        except KeyError:
            p = store.get_product(pid)
            lines.append(f"- product {pid} ({p['kind']}) current content: {p['content']}")
    return "\n".join(lines)


def rederive(store: Store, gateway: Gateway, donor: dict, quarantined: list[dict], run_id: Optional[str] = None) -> list[dict]:
    """Ultra re-derives each quarantined product, parents before children, from the new donor record."""
    done = []
    for p in sorted(quarantined, key=lambda x: (x["depth"], x["id"])):
        fresh = store.get_product(p["id"])  # parents may have been re-derived a moment ago
        sent_note = (f"This item was already SENT to {fresh['recipient'] or 'a recipient'}.\n"
                     f"What was sent: {fresh['sent_content']}\n") if fresh["sent"] else "This item was not sent.\n"
        user = (f"ITEM kind: {fresh['kind']}\nITEM title: {fresh['title']}\nITEM old content: {fresh['content']}\n{sent_note}\n"
                f"CHANGED DONOR {donor['id']}:\n  OLD claim: {donor['prev_claim']}\n  OLD excerpt: {donor['prev_excerpt']}\n"
                f"  NEW claim: {donor['claim']}\n  NEW excerpt: {donor['excerpt']}\n\n"
                f"ALL PARENTS NOW:\n{_parent_context(store, fresh)}")
        out = gateway.chat_json("ultra", REDERIVE_SYSTEM, user, purpose="rederive", run_id=run_id,
                                schema=REDERIVE_SCHEMA, max_tokens=900)
        content = out.get("content").strip() if isinstance(out.get("content"), str) else ""
        draft = out.get("correction_draft").strip() if isinstance(out.get("correction_draft"), str) else ""
        if not content:
            raise ValueError(f"Ultra returned empty content for {fresh['id']}")
        if fresh["sent"] and not draft:
            raise ValueError(f"Ultra returned no correction draft for sent item {fresh['id']}")
        done.append(store.set_product_rederived(fresh["id"], content=content, correction_draft=draft or None))
    return done

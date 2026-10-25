"""F5 Lookback cascade: quarantine all descendants of a changed donor, then Ultra re-derives only those."""
import json
from typing import Optional

from .dates import deadline_of, late_dates
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


def _check_dates(fresh: dict, deadline, content: str, draft: str) -> list[str]:
    """No re-derived text may land after the new deadline, and a correction may quote an old sent date only to replace it."""
    problems = late_dates(content, deadline)
    if draft:
        problems += late_dates(draft, deadline, quoted_from=fresh["sent_content"] or "")
    return sorted(set(problems))


def rederive(store: Store, gateway: Gateway, donor: dict, quarantined: list[dict], run_id: Optional[str] = None) -> list[dict]:
    """Ultra re-derives each quarantined product, parents before children, from the new donor record.
    Every date it writes must fall on or before the new deadline (one retry that names the problem, then the run is incomplete)."""
    done = []
    previous = deadline_of(donor["prev_excerpt"] or "")
    deadline = deadline_of(donor["excerpt"], previous)  # a year-less new date takes the nearest year to the old deadline
    if deadline is None and run_id:
        store.log_event(run_id, "note", "The new source states no readable date, so updated dates could not be checked against a deadline.")
    for p in sorted(quarantined, key=lambda x: (x["depth"], x["id"])):
        fresh = store.get_product(p["id"])  # parents may have been re-derived a moment ago
        sent_note = (f"This item was already SENT to {fresh['recipient'] or 'a recipient'}.\n"
                     f"What was sent: {fresh['sent_content']}\n") if fresh["sent"] else "This item was not sent.\n"
        deadline_note = f"NEW DEADLINE: {deadline.strftime('%B')} {deadline.day}, {deadline.year}\n" if deadline else ""
        user = (f"ITEM kind: {fresh['kind']}\nITEM title: {fresh['title']}\nITEM old content: {fresh['content']}\n{sent_note}\n"
                f"CHANGED DONOR {donor['id']}:\n  OLD claim: {donor['prev_claim']}\n  OLD excerpt: {donor['prev_excerpt']}\n"
                f"  NEW claim: {donor['claim']}\n  NEW excerpt: {donor['excerpt']}\n{deadline_note}\n"
                f"ALL PARENTS NOW:\n{_parent_context(store, fresh)}")
        retry = ""
        for attempt in (1, 2):
            out = gateway.chat_json("ultra", REDERIVE_SYSTEM, user + retry, purpose="rederive", run_id=run_id,
                                    schema=REDERIVE_SCHEMA, max_tokens=1200)
            content = out.get("content").strip() if isinstance(out.get("content"), str) else ""
            draft = out.get("correction_draft").strip() if isinstance(out.get("correction_draft"), str) else ""
            if not content:
                raise ValueError(f"Ultra returned empty content for {fresh['id']}")
            if fresh["sent"] and not draft:
                raise ValueError(f"Ultra returned no correction draft for sent item {fresh['id']}")
            if fresh["sent"]:
                content = "Replaced by correction."  # the correction draft is the message; this line is what the item now says
            problems = _check_dates(fresh, deadline, content, draft) if deadline else []
            if not problems:
                break
            if attempt == 2:
                raise ValueError(f"Ultra's update for {fresh['id']} does not fit the new deadline: {'; '.join(problems)}")
            retry = f"\n\nYour previous answer was rejected: {'; '.join(problems)}. Move every date on or before the new deadline."
        done.append(store.set_product_rederived(fresh["id"], content=content, correction_draft=draft or None))
    return done

"""F3 Intake: fetch the URL, Nano extracts claim + verbatim excerpt, store a donor."""
import re
from typing import Optional

from .config import Settings
from .fetcher import Fetcher
from .gateway import Gateway, GatewayError
from .prompts import INTAKE_SYSTEM
from .store import Store, now_iso
from .textutil import DATE_IN_TEXT, is_verbatim, relevant_window

INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "excerpt": {"type": "string"},
        "date_fields": {"type": "array", "items": {
            "type": "object",
            "properties": {"field": {"type": "string"}, "value": {"type": "string"}},
            "required": ["field", "value"], "additionalProperties": False}},
    },
    "required": ["claim", "excerpt", "date_fields"],
    "additionalProperties": False,
}


class IntakeError(RuntimeError):
    pass


def excerpt_problem(claim: str, excerpt: str, page_text: str) -> str:
    """Why an extracted excerpt cannot be stored as a donor, or "" when it can. Shared by intake and the bench."""
    if not (claim and excerpt):
        return "claim or excerpt was empty."
    if not is_verbatim(excerpt, page_text):
        return "it was not a verbatim substring of the page."
    if not DATE_IN_TEXT.search(excerpt):
        return "it contains no date; copy the line that states the date itself (label and date lines together)."
    return ""


def intake_url(store: Store, gateway: Gateway, fetcher: Fetcher, settings: Settings, url: str,
               label: Optional[str] = None, run_id: Optional[str] = None) -> dict:
    page = fetcher.fetch(url)
    if page.status != "ok":
        raise IntakeError(f"Could not read {url}: {page.status} {page.detail}".strip())
    window = relevant_window(page.text, "deadline due date apply by eligibility", settings.page_char_limit)
    user = f"URL: {url}\n\nPAGE TEXT:\n{window}"
    last_problem = ""
    for attempt in range(2):
        prompt = user if not last_problem else f"{user}\n\nYour previous excerpt was rejected: {last_problem} Copy it exactly."
        try:
            out = gateway.chat_json("nano", INTAKE_SYSTEM, prompt, purpose="intake", run_id=run_id, schema=INTAKE_SCHEMA)
        except GatewayError as exc:
            raise IntakeError(str(exc)) from exc
        excerpt, claim = str(out.get("excerpt", "")).strip(), str(out.get("claim", "")).strip()
        last_problem = excerpt_problem(claim, excerpt, page.text)
        if not last_problem:
            donor = store.add_donor(url=url, claim=claim, excerpt=excerpt, label=label,
                                    fetched_at=now_iso(), fetch_via=page.via)
            donor["date_fields"] = out.get("date_fields", [])
            return donor
    raise IntakeError(f"No usable excerpt could be extracted from {url} ({last_problem}) "
                      "Donor Lookback tracks statements that carry a date; a page that only says \"rolling basis\" cannot be watched.")

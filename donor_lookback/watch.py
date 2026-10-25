"""Nano watches (compare), Ultra confirms. Excerpts must be verbatim substrings of the fetched page."""
from dataclasses import dataclass
from typing import Optional

from .config import Settings
from .gateway import Gateway, GatewayError
from .prompts import COMPARE_SYSTEM, CONFIRM_SYSTEM
from .textutil import is_verbatim, looks_unrelated, normalize, relevant_window


@dataclass
class Verdict:
    changed: bool
    field: str = ""
    new_claim: str = ""
    new_excerpt: str = ""
    reason: str = ""
    verbatim: bool = False


COMPARE_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["changed", "unchanged"]}, "field": {"type": "string"},
                   "new_claim": {"type": "string"}, "new_excerpt": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["verdict", "field", "new_claim", "new_excerpt", "reason"], "additionalProperties": False,
}
CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"confirmed": {"type": "boolean"}, "field": {"type": "string"}, "new_claim": {"type": "string"},
                   "new_excerpt": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["confirmed", "field", "new_claim", "new_excerpt", "reason"], "additionalProperties": False,
}


def _user_prompt(donor: dict, window: str) -> str:
    return (f"OLD claim: {donor['claim']}\nOLD excerpt: {donor['excerpt']}\n\nCURRENT PAGE TEXT:\n{window}")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strict_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise GatewayError(f"model returned no usable {name!r} (got {value!r})")


def nano_compare(gateway: Gateway, settings: Settings, donor: dict, page_text: str,
                 run_id: Optional[str] = None) -> Verdict:
    window = relevant_window(page_text, donor["excerpt"], settings.page_char_limit)
    out = gateway.chat_json("nano", COMPARE_SYSTEM, _user_prompt(donor, window), purpose="compare",
                            run_id=run_id, schema=COMPARE_SCHEMA)
    verdict = str(out.get("verdict", "")).strip().lower()
    if verdict not in ("changed", "unchanged"):
        raise GatewayError(f"Nano returned no usable verdict (got {out.get('verdict')!r})")  # incomplete run, never "unchanged"
    changed = verdict == "changed"
    new_excerpt = _text(out.get("new_excerpt"))
    return Verdict(changed=changed, field=_text(out.get("field")), new_claim=_text(out.get("new_claim")),
                   new_excerpt=new_excerpt, reason=_text(out.get("reason")),
                   verbatim=bool(new_excerpt) and is_verbatim(new_excerpt, page_text))


def ultra_confirm(gateway: Gateway, settings: Settings, donor: dict, page_text: str, alarm: Verdict,
                  run_id: Optional[str] = None) -> Verdict:
    """Second look at every Nano alarm. A confirmation without a verbatim new excerpt does not count."""
    window = relevant_window(page_text, donor["excerpt"], settings.page_char_limit)
    user = _user_prompt(donor, window) + f"\n\nThe cheaper model's alarm: {alarm.reason}"
    out = gateway.chat_json("ultra", CONFIRM_SYSTEM, user, purpose="confirm", run_id=run_id, schema=CONFIRM_SCHEMA)
    said_yes = _strict_bool(out.get("confirmed"), "confirmed")
    new_excerpt = _text(out.get("new_excerpt"))
    verbatim = bool(new_excerpt) and is_verbatim(new_excerpt, page_text)
    reason = _text(out.get("reason"))
    confirmed = said_yes
    if said_yes and not verbatim:
        # A real change the model could not quote: surface it as an error so the run is incomplete, never "no change".
        raise GatewayError("Ultra confirmed a change but gave no verbatim new excerpt")
    if said_yes and normalize(new_excerpt) == normalize(donor["excerpt"]):
        confirmed, reason = False, f"{reason} (rejected: new excerpt equals the recorded excerpt)".strip()
    elif said_yes and looks_unrelated(donor["excerpt"], new_excerpt):
        # Never a silent "no change": a human has to look at an excerpt that has nothing to do with the recorded one.
        raise GatewayError("Ultra's new excerpt looks unrelated to the recorded statement; needs review")
    return Verdict(changed=confirmed, field=_text(out.get("field")), new_claim=_text(out.get("new_claim")),
                   new_excerpt=new_excerpt, reason=reason, verbatim=verbatim)

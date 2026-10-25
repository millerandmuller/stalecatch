"""The two labeled demo funder pages. They are ours: the banner says so, and the board never calls them a real funder."""
import html
import json
import re
from pathlib import Path

DEFAULT_STATE = {"funder_deadline": "November 12, 2027"}
BANNER = "DEMO PAGE: written by the StaleCatch team for this demo. Not a real funder."

def funder_deadline_line(st: dict) -> str:
    """The seeded page states its deadline plainly; once the funder moves it the page carries a correction notice instead."""
    date = st["funder_deadline"]
    if date == DEFAULT_STATE["funder_deadline"]:
        return f"Applications for the 2027 Small Grants round are due by 5:00 PM Pacific on {date}."
    return (f"Correction: Applications for the 2027 Small Grants round are due by 5:00 PM Pacific on {date}, "
            "so that awards can be approved at the December board meeting.")


PAGES = {
    "funder": {
        "title": "Example Community Foundation: Small Grants (demo page)",
        "lines": lambda st: [
            "Example Community Foundation",
            "Small Grants Program 2027",
            "The Small Grants Program supports neighbourhood projects with awards of up to $15,000.",
            "Who can apply: registered nonprofits and fiscally sponsored groups based in the county.",
            funder_deadline_line(st),
            "Decisions are announced in the first week of January.",
            "Questions? Write to grants@example.invalid. Office hours are Tuesdays and Thursdays.",
        ],
    },
    "control": {
        "title": "Example Regional Arts Council: Travel Grants (demo page)",
        "lines": lambda st: [
            "Example Regional Arts Council",
            "Travel Grants 2027",
            "Travel Grants cover up to $2,500 for artists presenting work outside the region.",
            "Applications for Travel Grants are due by 5:00 PM Eastern on December 3, 2027.",
            "Notification follows within six weeks of the deadline.",
        ],
    },
}


DEADLINE_RE = re.compile(r"^[A-Za-z0-9 ,.\-]{3,40}$")


def valid_deadline(text: str) -> bool:
    return bool(DEADLINE_RE.match(text or ""))


def load_state(path: str) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return {**DEFAULT_STATE, **json.loads(p.read_text())}
        except (json.JSONDecodeError, OSError):
            pass  # a half-written file must not take the board down; fall back to the default page
    return dict(DEFAULT_STATE)


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(p)  # atomic swap so a reader never sees a partial file


def page_lines(slug: str, state: dict) -> list[str]:
    return PAGES[slug]["lines"](state)


def render_html(slug: str, state: dict) -> str:
    page = PAGES[slug]
    lines = page_lines(slug, state)
    body = "".join(f"<p>{html.escape(ln)}</p>" for ln in lines[2:])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{page['title']}</title></head><body>"
            f"<div style='background:#fff4c2;padding:8px;font:14px sans-serif'>{BANNER}</div>"
            f"<h1>{html.escape(lines[0])}</h1><h2>{html.escape(lines[1])}</h2>{body}</body></html>")

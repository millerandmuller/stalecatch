"""The two labeled demo funder pages. They are ours: the banner says so, and the board never calls them a real funder."""
import html
import json
import re
from pathlib import Path

from .dates import deadline_of

DEFAULT_STATE = {"funder_deadline": "November 19, 2026"}
BANNER = "DEMO PAGE: written by the StaleCatch team for this demo. Not a real funder."

def funder_deadline_line(st: dict) -> str:
    """The seeded page states its deadline plainly; once the funder moves it the page carries a correction notice instead."""
    date = st["funder_deadline"]
    if deadline_of(date) == deadline_of(DEFAULT_STATE["funder_deadline"]):  # "Nov 19, 2026" is the default date written differently
        return f"Applications for the 2026 Small Grants round are due by 5:00 PM Pacific on {date}."
    return (f"Correction: Applications for the 2026 Small Grants round are due by 5:00 PM Pacific on {date}, "
            "so that awards can be approved at the December board meeting.")


PAGES = {
    "funder": {
        "title": "Example Community Foundation: Small Grants (demo page)",
        "lines": lambda st: [
            "Example Community Foundation",
            "Small Grants Program 2026",
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
            "Travel Grants 2026",
            "Travel Grants cover up to $2,500 for artists presenting work outside the region.",
            "Applications for Travel Grants are due by 5:00 PM Eastern on December 3, 2026.",
            "Notification follows within six weeks of the deadline.",
        ],
    },
}


DEADLINE_RE = re.compile(r"^[A-Za-z0-9 ,.\-]{3,40}$")


def valid_deadline(text: str) -> bool:
    """A short date such as 'November 9, 2026': plain characters and a full date with a year."""
    return bool(DEADLINE_RE.match(text or "")) and deadline_of(text or "") is not None


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


# One small stylesheet so the pages read as a foundation's site on camera. It adds no text (the extractor skips <head>), and the
# header band is the existing <h1>/<h2>, so the page text the seed excerpts and the recorded run depend on stays word for word.
PAGE_CSS = """
body { margin: 0; background: #faf8f4; color: #2b2a28; font: 16px/1.65 Georgia, 'Times New Roman', serif; }
.demo-banner { background: #fff4c2; color: #4a3b00; padding: 8px 16px; font: 14px/1.4 -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif; text-align: center; border-bottom: 1px solid #e6d58a; }
header { background: #20443c; color: #f4f1ea; padding: 28px 16px 22px; }
header > div, main { max-width: 640px; margin: 0 auto; }
h1 { margin: 0; font-size: 28px; line-height: 1.2; letter-spacing: .01em; }
h2 { margin: 6px 0 0; font-size: 17px; font-weight: normal; color: #cfe0d8; }
main { padding: 28px 16px 56px; }
main p { margin: 0 0 18px; }
"""


def render_html(slug: str, state: dict) -> str:
    page = PAGES[slug]
    lines = page_lines(slug, state)
    body = "".join(f"<p>{html.escape(ln)}</p>" for ln in lines[2:])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{page['title']}</title><style>{PAGE_CSS}</style></head><body>"
            f"<div class='demo-banner'>{BANNER}</div>"
            f"<header><div><h1>{html.escape(lines[0])}</h1><h2>{html.escape(lines[1])}</h2></div></header><main>{body}</main></body></html>")

"""Pure text helpers: HTML to text, whitespace normalisation, verbatim check, relevant-window selection."""
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "header", "footer", "dt", "dd", "main"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "head"}


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    s = _Stripper()
    s.feed(html)
    lines = [re.sub(r"[ \t\r\f\v ]+", " ", ln).strip() for ln in "".join(s.parts).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_verbatim(excerpt: str, page_text: str) -> bool:
    """True if the excerpt appears in the page text (whitespace-insensitive, otherwise exact)."""
    e = normalize(excerpt)
    return bool(e) and e in normalize(page_text)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May)|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
DAY = r"\d{1,2}(?:st|nd|rd|th)?"
# a four-digit year, a day-month pair in either order (ordinals and "of" allowed), or a numeric date; "5:00 PM" is not a date
DATE_IN_TEXT = re.compile(rf"(?i)\b(?:19|20)\d{{2}}\b|\b{MONTHS}\s+{DAY}\b|\b{DAY}(?:\s+of)?[\s-]+{MONTHS}(?![a-z])|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b")
DEADLINE_LABEL = re.compile(r"(?i)deadline|due date|target date|closing date|receipt date|submission date")


def states_deadline(text: str) -> bool:
    """True if a deadline-labelled line carries a date itself or has one in the next three lines (a label above a date)."""
    lines = text.split("\n")
    return any(DEADLINE_LABEL.search(ln) and any(DATE_IN_TEXT.search(x) for x in lines[i:i + 4]) for i, ln in enumerate(lines))


def relevant_window(page_text: str, excerpt: str, limit: int) -> str:
    """Keep the page whole if it fits; otherwise keep the lines most similar to the excerpt, in page order.
    Lines that carry a date, and the block around one, rank above lines that merely share a word, because grant pages often put a
    bare date under a label (a flattened table), the date line shares no words with the question, and a model quotes across a gap
    it cannot see. Words shared with the question still win over a date alone, so a changed undated statement stays in view.
    Runs of kept lines that were not adjacent on the page are separated by a [...] line (counted in the limit)."""
    if len(page_text) <= limit:
        return page_text
    want = _tokens(excerpt)
    lines = [ln[:max(limit - 8, 1)] for ln in page_text.split("\n")]  # a single huge line must not empty the window
    dated = [bool(DATE_IN_TEXT.search(ln)) for ln in lines]
    near_dated = [any(dated[max(0, i - 12):i + 13]) for i in range(len(lines))]

    def score(i: int) -> float:
        return 3 * len(want & _tokens(lines[i])) + (2 if dated[i] else 0) + (1.5 if near_dated[i] else 0)
    keep, used = set(), 0
    for i in sorted(range(len(lines)), key=lambda i: -score(i)):
        cost = len(lines[i]) + 1 + 6  # 6 leaves room for a "[...]\n" marker in front of it
        if used + cost > limit:
            continue
        keep.add(i)
        used += cost
    out, prev = [], None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("[...]")
        out.append(lines[i])
        prev = i
    return "\n".join(out)


_STOP = {"the", "are", "and", "for", "with", "from", "that", "this", "will", "can", "you", "our", "your", "all", "any", "has", "was", "not"}


def looks_unrelated(old: str, new: str) -> bool:
    """True when the new excerpt shares too little with the recorded one to be the same statement.
    Related = two shared content words, or one shared word plus a digit (a date or amount). Short rewrites such as
    'Deadline: October 28, 2026' pass; an unrelated page line does not. Callers must treat True as 'needs review', never as 'no change'."""
    shared = (_tokens(old) & _tokens(new)) - _STOP
    if len(shared) >= 2:
        return False
    return not (len(shared) >= 1 and re.search(r"\d", new or ""))

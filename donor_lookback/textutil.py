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


def relevant_window(page_text: str, excerpt: str, limit: int) -> str:
    """Keep the page whole if it fits; otherwise keep the lines most similar to the excerpt, in page order."""
    if len(page_text) <= limit:
        return page_text
    want = _tokens(excerpt)
    lines = page_text.split("\n")
    scored = sorted(range(len(lines)), key=lambda i: -len(want & _tokens(lines[i])))
    keep, used = set(), 0
    for i in scored:
        if used + len(lines[i]) + 1 > limit:
            continue
        keep.add(i)
        used += len(lines[i]) + 1
    return "\n".join(lines[i] for i in sorted(keep))


_STOP = {"the", "are", "and", "for", "with", "from", "that", "this", "will", "can", "you", "our", "your", "all", "any", "has", "was", "not"}


def looks_unrelated(old: str, new: str) -> bool:
    """True when the new excerpt shares too little with the recorded one to be the same statement.
    Related = two shared content words, or one shared word plus a digit (a date or amount). Short rewrites such as
    'Deadline: October 28, 2026' pass; an unrelated page line does not. Callers must treat True as 'needs review', never as 'no change'."""
    shared = (_tokens(old) & _tokens(new)) - _STOP
    if len(shared) >= 2:
        return False
    return not (len(shared) >= 1 and re.search(r"\d", new or ""))

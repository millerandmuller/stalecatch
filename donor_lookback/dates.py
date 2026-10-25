"""Date reading for the recall: find the dates a text mentions and check that nothing re-derived lands after the new deadline."""
import re
from datetime import date
from typing import Optional

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_MONTH_BY_PREFIX = {name[:3]: i + 1 for i, name in enumerate(_MONTH_NAMES)}
_MONTH_WORD = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May)|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MONTH = rf"(?P<m>{_MONTH_WORD})\.?"
_YEAR = r"(?P<y>(?:19|20)\d{2})"
_MONTH_DAY = re.compile(rf"(?i)\b{_MONTH}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?!\d)(?:,?\s+{_YEAR}\b)?")
_DAY_MONTH = re.compile(rf"(?i)\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+{_MONTH}(?![a-z])(?:,?\s+{_YEAR}\b)?")
_ISO = re.compile(r"\b(?P<y>(?:19|20)\d{2})-(?P<mo>\d{2})-(?P<d>\d{2})\b")
_NUMERIC = re.compile(r"\b(?P<mo>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{4}|\d{2}))?\b")  # US order, month first
_ABBREVIATION_DOT = re.compile(rf"(?i)\b({_MONTH_WORD})\.(?=\s*\d)")


def _closest(month: int, day: int, near: date) -> Optional[date]:
    """A date written without a year means the occurrence nearest the reference date (work-back dates sit close to a deadline)."""
    options = []
    for year in (near.year - 1, near.year, near.year + 1):
        try:
            options.append(date(year, month, day))
        except ValueError:
            continue
    return min(options, key=lambda d: abs((d - near).days)) if options else None


def parse_dates(text: str, near: Optional[date] = None) -> list[tuple[date, int, int]]:
    """Every date in the text as (date, start, end), in reading order. A date without a year resolves to the occurrence nearest
    `near` (None means such dates are skipped). Impossible dates such as Feb 31 are skipped."""
    found = []
    text = text or ""
    for pattern in (_MONTH_DAY, _DAY_MONTH, _ISO, _NUMERIC):
        for m in pattern.finditer(text):
            g = m.groupdict()
            month = int(g["mo"]) if g.get("mo") else _MONTH_BY_PREFIX[g["m"][:3].lower()]
            day = int(g["d"])
            if g.get("y"):
                year = int(g["y"]) + (2000 if len(g["y"]) == 2 else 0)
                try:
                    d = date(year, month, day)
                except ValueError:
                    continue
            elif near is None:
                continue
            else:
                d = _closest(month, day, near)
                if d is None:
                    continue
            found.append((d, m.start(), m.end()))
    found.sort(key=lambda x: x[1])
    return found


_DEADLINE_CUE = re.compile(r"(?i)\b(due|deadline|closes?|closing|received by|submit(?:ted)?|apply by|no later than|by)\b[^.]{0,40}$")


def deadline_of(excerpt: str, near: Optional[date] = None) -> Optional[date]:
    """The deadline a source excerpt states. When several dates appear, the last one that follows a deadline cue ("due by",
    "closes") wins, so an opening date or an old date in "extended from X to Y" is not mistaken for it; without any cue the last
    date wins. `near` resolves dates written without a year (use the previous deadline)."""
    dated = parse_dates(excerpt, near)
    if not dated:
        return None
    cued = [d for d, start, _ in dated if _DEADLINE_CUE.search(excerpt[:start])]
    return cued[-1] if cued else dated[-1][0]


def today() -> date:
    """The day the check runs. One function so tests can pin it."""
    return date.today()


_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
# a cue that comes right before a date and says the date is being replaced ("not Nov 12", "instead of Nov 3", "was Nov 5")
_REPLACING_BEFORE = re.compile(r"(?i)\b(instead of|rather than|not|no longer|was|were|from|previously|originally|replac(?:es|ing)|moved from)\s+(?:the\s+)?(?:\w+\s+){0,2}$")
_STILL_STANDS = re.compile(r"(?i)\bstill\s+(stands?|holds?|applies|works?|fits?|valid|good)\b|\b(remains?|stays?)\s+(the plan|as planned|unchanged)\b")


def late_dates(text: str, deadline: date, quoted_from: str = "", after: Optional[date] = None) -> list[str]:
    """Problems in a re-derived text: dates after the new deadline, dates on or before `after` (today), and 'still stands' phrasing.
    quoted_from is the message that was already sent: one of its dates may be quoted right after a replacing cue
    ("not Nov 12", "instead of Nov 3"), never as something that still holds."""
    problems = []
    text = _ABBREVIATION_DOT.sub(r"\1", text or "")  # "Nov. 5" must not be cut into two sentences
    if _STILL_STANDS.search(text):
        problems.append("it says an earlier date still stands")
    old_values = {(d.month, d.day) for d, _, _ in parse_dates(_ABBREVIATION_DOT.sub(r"\1", quoted_from), deadline)}
    for sentence in _SENTENCE.findall(text):
        for d, start, _ in parse_dates(sentence, deadline):
            too_late, too_early = d > deadline, after is not None and d <= after
            if not (too_late or too_early):
                continue
            if (d.month, d.day) in old_values and _REPLACING_BEFORE.search(sentence[:start]):
                continue
            problems.append(f"{d.strftime('%b')} {d.day} is after the new deadline" if too_late else f"{d.strftime('%b')} {d.day} is not after today")
    return sorted(set(problems))

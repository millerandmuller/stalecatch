"""Date reading for the recall: find the dates a text mentions and check that nothing re-derived lands after the new deadline."""
import re
from datetime import date
from typing import Optional

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_MONTH_BY_PREFIX = {name[:3]: i + 1 for i, name in enumerate(_MONTH_NAMES)}
_MONTH = r"(?P<m>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May)|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_MONTH_DAY = re.compile(rf"(?i)\b{_MONTH}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>(?:19|20)\d{{2}}))?\b")
_DAY_MONTH = re.compile(rf"(?i)\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+{_MONTH}(?:,?\s+(?P<y>(?:19|20)\d{{2}}))?(?![a-z])")


def parse_dates(text: str, default_year: Optional[int] = None) -> list[tuple[date, int, int]]:
    """Every date in the text as (date, start, end), in reading order. A date without a year takes default_year
    (None means such dates are skipped). Impossible dates such as Feb 31 are skipped."""
    found = []
    for pattern in (_MONTH_DAY, _DAY_MONTH):
        for m in pattern.finditer(text or ""):
            year = int(m.group("y")) if m.group("y") else default_year
            if year is None:
                continue
            try:
                found.append((date(year, _MONTH_BY_PREFIX[m.group("m")[:3].lower()], int(m.group("d"))), m.start(), m.end()))
            except ValueError:
                continue
    found.sort(key=lambda x: x[1])
    return found


def deadline_of(excerpt: str) -> Optional[date]:
    """The deadline a source excerpt states: its first date that carries a year."""
    dated = parse_dates(excerpt)
    return dated[0][0] if dated else None


_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
# a sentence that quotes an old value in order to replace it
_REPLACING = re.compile(r"(?i)\b(instead of|rather than|not|earlier|previous(?:ly)?|originally|was|were|no longer|moved|changed|replaces?|from|old)\b")
_STILL_STANDS = re.compile(r"(?i)\bstill\s+(stands?|holds?|applies|works?|fits?|valid|good)\b")


def late_dates(text: str, deadline: date, quoted_from: str = "") -> list[str]:
    """Problems in a re-derived text: dates after the new deadline, and 'still stands' phrasing.
    quoted_from is the message that was already sent: a date from it may be quoted in a sentence that replaces it
    ("not the November 12 date I sent"), never as something that still holds."""
    problems = []
    if _STILL_STANDS.search(text or ""):
        problems.append("it says an earlier date still stands")
    old_values = {(d.month, d.day) for d, _, _ in parse_dates(quoted_from, deadline.year)}
    for sentence in _SENTENCE.findall(text or ""):
        for d, _, _ in parse_dates(sentence, deadline.year):
            if d <= deadline:
                continue
            if quoted_from and (d.month, d.day) in old_values and _REPLACING.search(sentence):
                continue
            problems.append(f"{d.strftime('%b')} {d.day} is after the new deadline")
    return sorted(set(problems))

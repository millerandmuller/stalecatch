"""Deterministic stand-ins for Token Factory and the web, so the pipeline is testable offline."""
import json
import re
from types import SimpleNamespace

from donor_lookback import demo_pages
from donor_lookback.fetcher import FetchResult


def _resp(obj, pt=400, ct=60):
    msg = SimpleNamespace(content=json.dumps(obj))
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct))


class FakeModels:
    """Behaves like a competent Nano/Ultra pair. `calls` records (model, purpose) for assertions."""

    def __init__(self, nano="nano-m", ultra="ultra-m", nano_false_alarm=False, ultra_rejects=False, ultra_says_superseded=False):
        self.calls = []
        self.nano, self.ultra = nano, ultra
        self.nano_false_alarm, self.ultra_rejects = nano_false_alarm, ultra_rejects
        self.ultra_says_superseded = ultra_says_superseded
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _date_line(text):
        for ln in text.split("\n"):
            if "due by" in ln:
                return ln.strip()
        return ""

    def _create(self, model, messages, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if "extract one dated commitment" in system:
            self.calls.append((model, "intake"))
            line = self._date_line(user)
            return _resp({"claim": "Deadline: " + line, "excerpt": line, "date_fields": [{"field": "deadline", "value": line}]})
        if "You watch a web page" in system:
            self.calls.append((model, "compare"))
            old = re.search(r"OLD excerpt: (.*)", user).group(1).strip()
            page = user.split("CURRENT PAGE TEXT:\n", 1)[1]
            new = self._date_line(page)
            changed = new != old or self.nano_false_alarm
            return _resp({"verdict": "changed" if changed else "unchanged", "field": "deadline",
                          "new_claim": "Deadline: " + new, "new_excerpt": new, "reason": "date differs" if changed else "same"})
        if "second, careful check" in system:
            self.calls.append((model, "confirm"))
            old = re.search(r"OLD excerpt: (.*)", user).group(1).strip()
            page = user.split("CURRENT PAGE TEXT:\n", 1)[1]
            new = self._date_line(page)
            ok = new != old and not self.ultra_rejects
            kind = "unchanged"
            if ok:
                kind = "superseded" if self.ultra_says_superseded else "changed"
            return _resp({"verdict": kind, "field": "deadline", "new_claim": "Deadline: " + new, "new_excerpt": new, "reason": "confirmed" if ok else "no real change"}, 900, 80)
        if "re-derive one item" in system:
            self.calls.append((model, "rederive"))
            new_line = re.search(r"NEW excerpt: (.*)", user).group(1).strip()
            m = re.search(r"on ([A-Z][a-z]+ \d+, \d{4})", new_line)
            date = m.group(1) if m else "the new date"
            sent = "already SENT" in user
            return _resp({"content": f"Updated: due {date}.",
                          "correction_draft": f"Correction: the deadline is now {date}, not the date I sent earlier." if sent else ""}, 1200, 90)
        raise AssertionError("unexpected prompt: " + system[:60])


class FakeWeb:
    """Serves the demo pages from state, plus arbitrary overrides (gone / unreachable)."""

    def __init__(self, state, base="http://127.0.0.1:8000"):
        self.state, self.base, self.override = state, base, {}

    def fetch(self, url):
        if url in self.override:
            return self.override[url]
        slug = url.rsplit("/", 1)[-1]
        text = "\n".join(demo_pages.page_lines(slug, self.state))
        return FetchResult("ok", text, "fake")

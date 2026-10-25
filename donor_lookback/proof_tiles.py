"""The three headline tiles above the Proof table. Every figure is read from the bench report's summary, never typed in."""
from typing import Optional

NEEDED = ("confirmed_recall", "changed_pairs", "nano_false_alarms", "false_alarms", "controls_compared", "total_usd")


def _tile(label: str, value: str, note: str = "") -> dict:
    return {"label": label, "value": value, "note": note, "text": f"{label}: {value}{' ' + note if note else ''}"}


def proof_tiles(report: dict) -> list[dict]:
    """Empty when the report lacks a field, so an unfinished bench shows no tiles rather than wrong ones."""
    s = report.get("summary") or {}
    if any(k not in s for k in NEEDED) or not isinstance(report.get("rows"), list):
        return []
    pages = len(report["rows"])
    return [
        _tile("Real changes found", f"{s['confirmed_recall']} of {s['changed_pairs']}"),
        _tile(f"False alarms on {s['controls_compared']} controls", f"{s['nano_false_alarms']} → {s['false_alarms']}", "after deep review"),
        _tile(f"Cost for {pages} pages", f"${s['total_usd']:.2f}"),
    ]

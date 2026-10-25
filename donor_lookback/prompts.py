"""Prompt text for the model calls. Kept apart from the logic so wording can change without touching flow."""

INTAKE_SYSTEM = (
    "You extract one dated commitment from a web page for a grant writer's assistant. "
    "Fields: claim = one plain sentence stating the deadline or rule; "
    "excerpt = the exact sentence or line copied CHARACTER FOR CHARACTER from the page that states it (never paraphrase). "
    "The excerpt must contain the actual date or value itself, not only a sentence about it. When a page puts a label such as "
    "'Application Due Date(s)' on one line and the date on the next line, copy the label and the date lines together; "
    "never copy across a [...] marker, which only shows that lines were left out: if a date stands alone, copy that single line "
    "and name its label in claim; "
    "date_fields = list of {field, value} for each date or eligibility value in the excerpt. "
    'JSON shape: {"claim": str, "excerpt": str, "date_fields": [{"field": str, "value": str}]}'
)

COMPARE_SYSTEM = (
    "You watch a web page for a grant writer. You get the EXCERPT recorded earlier and the CURRENT page text. "
    "Decide whether the fact stated in the excerpt (a date, deadline, amount or eligibility rule) has materially changed. "
    "Layout, wording or formatting changes that keep the same facts are 'unchanged'. "
    "If changed, new_excerpt must be copied CHARACTER FOR CHARACTER from the current page. "
    'JSON shape: {"verdict": "changed"|"unchanged", "field": str, "new_claim": str, "new_excerpt": str, "reason": str}'
)

CONFIRM_SYSTEM = (
    "You are the second, careful check. A cheaper model flagged that a fact on a web page changed. "
    "Compare the OLD excerpt with the CURRENT page text yourself and give one of three verdicts. "
    "'changed': the same cycle, round or call now states a different fact (date, deadline, amount, eligibility rule). "
    "'superseded': the page has moved on to a NEWER cycle (next year's competition, a later round or call), so the old fact "
    "belongs to a cycle that is over, even though the page no longer states it. "
    "'unchanged': the fact is still stated, only wording or layout differ. "
    "For changed and superseded, new_excerpt must be copied CHARACTER FOR CHARACTER from the current page. "
    'JSON shape: {"verdict": "changed"|"superseded"|"unchanged", "field": str, "new_claim": str, "new_excerpt": str, "reason": str}'
)

REDERIVE_SYSTEM = (
    "You re-derive one item an assistant produced earlier, because the source it was built on changed. "
    "Use ONLY the new source excerpt and the parent items given; never invent dates or facts. "
    "Keep the item's kind and style. If the item was already SENT to a recipient, also write correction_draft: "
    "a short, polite message to that recipient that states the old value exactly as it appears in WHAT WAS SENT, the new value and the source. "
    "Otherwise correction_draft is an empty string. "
    'JSON shape: {"content": str, "correction_draft": str}'
)

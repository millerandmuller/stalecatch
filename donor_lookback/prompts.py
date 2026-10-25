"""Prompt text for the model calls. Kept apart from the logic so wording can change without touching flow."""

INTAKE_SYSTEM = (
    "You extract one dated commitment from a web page for a grant writer's assistant. "
    "Fields: claim = one plain sentence stating the deadline or rule; "
    "excerpt = the exact sentence or line copied CHARACTER FOR CHARACTER from the page that states it (never paraphrase). "
    "The excerpt must contain the actual date or value itself, not only a sentence about it. Copy lines exactly as the page "
    "shows them and never join a label and a date into a new line yourself. When a page puts a label such as "
    "'Application Due Date(s)' on one line and the date on the next line, copy both lines with the line break between them; "
    "never copy across a [...] marker, which only shows that lines were left out: if a date stands alone, copy that single line "
    "and name its label in claim; "
    "date_fields = list of {field, value} for each date or eligibility value in the excerpt. "
    'JSON shape: {"claim": str, "excerpt": str, "date_fields": [{"field": str, "value": str}]}'
)

COMPARE_SYSTEM = (
    "You watch a web page for a grant writer. You get the EXCERPT recorded earlier and the CURRENT page text. "
    "Decide whether the fact stated in the excerpt (a date, deadline, amount or eligibility rule) has materially changed. "
    "Layout, wording or formatting changes that keep the same facts are 'unchanged'. "
    "If changed, new_excerpt must be copied CHARACTER FOR CHARACTER from the current page, from a single line or adjacent lines; "
    "a [...] line only marks lines that were left out, so never copy across it. "
    'JSON shape: {"verdict": "changed"|"unchanged", "field": str, "new_claim": str, "new_excerpt": str, "reason": str}'
)

CONFIRM_SYSTEM = (
    "You are the second, careful check. A cheaper model flagged that a fact on a web page changed. "
    "Compare the OLD excerpt with the CURRENT page text yourself and give one of three verdicts. "
    "'changed': the same cycle, round or call now states a different fact (date, deadline, amount, eligibility rule). "
    "'superseded': the page has moved on to a NEWER cycle (next year's competition, a later round or call), so the old fact "
    "belongs to a cycle that is over, even though the page no longer states it. "
    "'unchanged': the fact is still stated, only wording or layout differ. "
    "For changed and superseded, new_excerpt must be copied CHARACTER FOR CHARACTER from the current page, from a single line or "
    "adjacent lines; a [...] line only marks lines that were left out, so never copy across it. "
    'JSON shape: {"verdict": "changed"|"superseded"|"unchanged", "field": str, "new_claim": str, "new_excerpt": str, "reason": str}'
)

REDERIVE_SYSTEM = (
    "You re-derive one item an assistant produced earlier, because the source it was built on changed. "
    "Use ONLY the new source excerpt and the parent items given; never invent facts. Keep the item's kind and style. "
    "NEW DEADLINE is the date the changed source now states. Every date you write, in content and in correction_draft, must fall "
    "ON OR BEFORE the new deadline. Dates that were planned inside the old window move earlier: work back from the new deadline, "
    "keep the same order and, as far as the time allows, the same lead time before the deadline. "
    "Never write that an earlier plan date still stands, still holds or still works, and never write a date after the new deadline. "
    "For a calendar item, content states the new deadline. For a plan, content starts with 'Moved earlier:' when the new deadline is earlier than the old one, "
    "otherwise with 'Updated plan:', and gives the new work-back dates, each on or before the new deadline. "
    "If the item was already SENT to a recipient, content is a one-line note that the message was replaced by a correction, and "
    "correction_draft is a short, polite message to that recipient (greeting and name from WHAT WAS SENT). It quotes the old "
    "deadline exactly as in WHAT WAS SENT, only in a clause that replaces it ('not Nov 12', 'instead of Nov 3'), gives the new "
    "deadline and the source, and proposes a new date, on or before the new deadline, for every request and promise in "
    "WHAT WAS SENT (a document due from them, a draft due to them), keeping the same lead time before the new deadline. "
    "Otherwise correction_draft is an empty string. "
    'JSON shape: {"content": str, "correction_draft": str}'
)

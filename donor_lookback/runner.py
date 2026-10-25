"""The nightly check: fetch, Nano compares, Ultra confirms, cascade. Same code for the Serverless Job and the board button."""
from typing import Optional

from . import cascade
from .config import Settings
from .fetcher import Fetcher
from .gateway import Gateway, GatewayError, redact
from .receipt import build_receipt
from .store import Store
from .watch import nano_compare, ultra_confirm


def run_check(store: Store, gateway: Gateway, fetcher: Fetcher, settings: Settings, *,
              trigger: str = "manual", run_id: Optional[str] = None) -> dict:
    run_id = run_id or store.start_run(trigger)
    donors = store.list_donors()
    summary = {"donors": len(donors), "changed": 0, "unchanged": 0, "gone": 0, "rejected_alarms": 0, "superseded": 0,
               "rederived": 0, "errors": []}
    store.log_event(run_id, "start", f"Tonight's check: {len(donors)} donors")
    for donor in donors:
        did = donor["id"]
        try:
            _check_one(store, gateway, fetcher, settings, donor, run_id, summary)
        except Exception as exc:  # noqa: BLE001 - one bad donor (even a malformed URL) must not abort the other donors
            if isinstance(exc, KeyboardInterrupt):
                raise  # run continues; it is marked incomplete, never silently green
            msg = redact(str(exc))
            summary["errors"].append({"donor_id": did, "error": msg})
            store.log_event(run_id, "error", f"{donor['label']}: {msg}")
    complete = not summary["errors"]
    receipt = build_receipt(store, settings, run_id)
    summary["receipt"] = receipt
    store.log_event(run_id, "done",
                    f"Tonight's check: {summary['donors']} donors, {summary['changed']} changed, ${receipt['total_usd']:.4f}"
                    + ("" if complete else " (incomplete)"))
    store.finish_run(run_id, complete=complete, summary=summary)
    return store.get_run(run_id)


def _check_one(store, gateway, fetcher, settings, donor, run_id, summary) -> None:
    did = donor["id"]
    if donor["cascade_pending"]:
        _resume_pending(store, gateway, donor, run_id, summary)  # an earlier run died mid-cascade; finish it first
        donor = store.get_donor(did)
    page = fetcher.fetch(donor["url"])
    store.log_event(run_id, "fetch", f"{donor['label']}: {page.status} via {page.via or 'n/a'}")
    if page.status != "ok":
        store.update_donor_state(did, state="gone")
        flagged = cascade.mark_source_unavailable(store, did)
        summary["gone"] += 1
        store.log_event(run_id, "source_unavailable", f"{donor['label']}: {len(flagged)} products flagged, not quarantined")
        return

    alarm = nano_compare(gateway, settings, donor, page.text, run_id)
    if donor["state"] == "gone":
        cascade.restore_source_available(store, did)  # only after a successful compare, never before
    if not alarm.changed:
        store.touch_donor(did, fetched_at=_now(), fetch_via=page.via)
        summary["unchanged"] += 1
        store.log_event(run_id, "unchanged", f"{donor['label']}: Nano sees no change")
        return
    store.log_event(run_id, "nano_alarm", f"{donor['label']}: Nano flags a change ({alarm.field})")

    confirmed = ultra_confirm(gateway, settings, donor, page.text, alarm, run_id)
    if not confirmed.changed:
        store.touch_donor(did, fetched_at=_now(), fetch_via=page.via)
        summary["rejected_alarms"] += 1
        store.log_event(run_id, "alarm_rejected", f"{donor['label']}: Checked twice, no change")
        return

    quarantined = store.change_donor_and_quarantine(
        did, claim=confirmed.new_claim or alarm.new_claim or confirmed.new_excerpt, excerpt=confirmed.new_excerpt, fetch_via=page.via)
    changed = store.get_donor(did)
    summary["changed"] += 1
    if confirmed.kind == "superseded":
        summary["superseded"] += 1  # counted in "changed" too: the saved date belongs to a cycle that is over, so it alerts
    what = "Donor superseded by a newer cycle" if confirmed.kind == "superseded" else "Donor changed"
    store.log_event(run_id, "donor_red", f"{what}: {changed['prev_excerpt']!r} -> {changed['excerpt']!r}")
    store.log_event(run_id, "quarantine", f"{len(quarantined)} products quarantined.")
    redone = cascade.rederive(store, gateway, changed, quarantined, run_id)
    store.finish_cascade(did)
    summary["rederived"] += len(redone)
    store.log_event(run_id, "rederived", "Re-derived from the new excerpt.")
    if any(p["sent"] for p in redone):
        store.log_event(run_id, "sent_rises", "Sent items rise to the top of Recipients; corrections prepared as drafts.")


def _now() -> str:
    from .store import now_iso
    return now_iso()


def _resume_pending(store, gateway, donor, run_id, summary) -> None:
    """cascade_pending stays set until every descendant is re-derived, whatever happened to the donor meanwhile."""
    pending = [p for p in store.descendants(donor["id"]) if p["status"] != "rederived"]
    store.log_event(run_id, "resume", f"{donor['label']}: finishing {len(pending)} products from an earlier run")
    for p in pending:
        store.set_product_status(p["id"], "quarantined")
    redone = cascade.rederive(store, gateway, donor, pending, run_id)
    store.finish_cascade(donor["id"])
    summary["rederived"] += len(redone)
    store.log_event(run_id, "rederived", "Re-derived from the new excerpt.")

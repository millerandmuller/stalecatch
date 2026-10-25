"""F10 Demo seed and reset: restores the labeled demo pages and the seed graph in one call."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import demo_pages
from .config import ROOT, Settings
from .store import Store

SEED_FILE = ROOT / "seed" / "seed_data.json"


def _excerpt_from_page(slug: str, state: dict, match: str) -> str:
    for line in demo_pages.page_lines(slug, state):
        if match in line:
            return line
    raise ValueError(f"seed line {match!r} not found on demo page {slug!r}")


def reset_demo(store: Store, settings: Settings, seed_file: Path = SEED_FILE) -> dict:
    data = json.loads(Path(seed_file).read_text())
    state = dict(demo_pages.DEFAULT_STATE)
    demo_pages.save_state(settings.demo_state_path, state)
    store.reset_all()
    for d in data["donors"]:
        fetched = (datetime.now(timezone.utc) - timedelta(days=d["days_ago"])).replace(microsecond=0).isoformat()
        store.add_donor(donor_id=d["id"], url=f"{settings.public_base_url}/demo/{d['slug']}", label=d["label"],
                        claim=d["claim"], excerpt=_excerpt_from_page(d["slug"], state, d["match"]),
                        fetched_at=fetched, fetch_via="seed")
    for p in data["products"]:
        store.derive(product_id=p["id"], parents=p["parents"], kind=p["kind"], title=p["title"], content=p["content"],
                     sent=p.get("sent", False), recipient=p.get("recipient"))
    return {"donors": len(data["donors"]), "products": len(data["products"]), "demo_state": state}


def edit_funder_deadline(settings: Settings, new_deadline: str) -> dict:
    state = demo_pages.load_state(settings.demo_state_path)
    state["funder_deadline"] = new_deadline
    demo_pages.save_state(settings.demo_state_path, state)
    return state

"""Cost receipt built only from logged API usage fields."""
from typing import Optional

from .config import Settings
from .gateway import usd_for
from .store import Store


def _tier_totals(calls: list[dict], tier: str) -> dict:
    rows = [c for c in calls if c["tier"] == tier and c["ok"]]
    return {
        "calls": len(rows),
        "prompt_tokens": sum(c["prompt_tokens"] for c in rows),
        "completion_tokens": sum(c["completion_tokens"] for c in rows),
        "usd": round(sum(c["usd"] for c in rows), 8),
        "latency_ms": sum(c["latency_ms"] for c in rows),
    }


def build_receipt(store: Store, settings: Settings, run_id: Optional[str] = None) -> dict:
    calls = store.calls(run_id)
    nano, ultra = _tier_totals(calls, "nano"), _tier_totals(calls, "ultra")
    # Counterfactual: the same tokens the Nano calls used, priced at Ultra list prices, on top of the real Ultra spend.
    nano_at_ultra = usd_for(settings.ultra, nano["prompt_tokens"], nano["completion_tokens"])
    return {
        "run_id": run_id,
        "nano": nano,
        "ultra": ultra,
        "total_usd": round(nano["usd"] + ultra["usd"], 8),
        "ultra_everything_usd": round(nano_at_ultra + ultra["usd"], 8),
        "failed_calls": sum(1 for c in calls if not c["ok"]),
        # Calls whose response carried no usage fields: their cost is unknown, so the total is a lower bound.
        "calls_without_usage": sum(1 for c in calls if c["ok"] and c["prompt_tokens"] == 0 and c["completion_tokens"] == 0),
        "models": {"nano": settings.nano.model_id, "ultra": settings.ultra.model_id},
        "price_basis": "Token Factory list prices (USD per 1M tokens), multiplied by the usage fields the API returned.",
    }

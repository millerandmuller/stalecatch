"""All provider, model, price and path settings live here (one place to change)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class ModelTier:
    name: str  # "nano" | "ultra"
    model_id: str
    usd_per_m_input: float
    usd_per_m_output: float
    thinking: bool = True  # False sends chat_template_kwargs enable_thinking=false (measured: Nano 16 s -> 1.7 s per call)


@dataclass(frozen=True)
class Settings:
    # VERIFY at the day-1 spike: base URL from the Token Factory console, model IDs via GET /v1/models.
    base_url: str = field(default_factory=lambda: _env("TOKEN_FACTORY_BASE_URL"))
    api_key: str = field(default_factory=lambda: _env("TOKEN_FACTORY_API_KEY"))
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    db_path: str = field(default_factory=lambda: _env("DONOR_LOOKBACK_DB", str(ROOT / "data" / "lookback.db")))
    demo_state_path: str = field(default_factory=lambda: _env("DONOR_LOOKBACK_DEMO_STATE", str(ROOT / "data" / "demo_page.json")))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://127.0.0.1:8000"))
    nano: ModelTier = field(default_factory=lambda: ModelTier(
        "nano", _env("NANO_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"),
        float(_env("NANO_USD_IN", "0.06")), float(_env("NANO_USD_OUT", "0.24")), _env("NANO_THINKING", "0") == "1"))
    ultra: ModelTier = field(default_factory=lambda: ModelTier(
        "ultra", _env("ULTRA_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b"),
        float(_env("ULTRA_USD_IN", "1.00")), float(_env("ULTRA_USD_OUT", "3.00")), _env("ULTRA_THINKING", "1") == "1"))
    max_retries: int = 4
    page_char_limit: int = 12000  # how much page text a model call may see


def get_settings() -> Settings:
    return Settings()

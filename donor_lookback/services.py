"""Wires settings, store, gateway and fetcher once, so the board, the MCP server and the job share one setup."""
from dataclasses import dataclass

from .config import Settings, get_settings
from .fetcher import Fetcher
from .gateway import Gateway
from .store import Store


@dataclass
class Services:
    settings: Settings
    store: Store
    gateway: Gateway
    fetcher: Fetcher


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or get_settings()
    store = Store(settings.db_path)
    gateway = Gateway(settings, recorder=store.log_call)
    return Services(settings, store, gateway, Fetcher(settings))

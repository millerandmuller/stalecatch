import pytest

from donor_lookback import demo_pages
from donor_lookback.config import Settings
from donor_lookback.gateway import Gateway
from donor_lookback.seed import reset_demo
from donor_lookback.store import Store
from tests.fakes import FakeModels, FakeWeb


@pytest.fixture
def env(tmp_path):
    settings = Settings(db_path=":memory:", demo_state_path=str(tmp_path / "demo.json"))
    store = Store(":memory:")
    models = FakeModels()
    gateway = Gateway(settings, client=models, recorder=store.log_call, sleep=lambda s: None)
    reset_demo(store, settings)
    web = FakeWeb(demo_pages.load_state(settings.demo_state_path))
    return type("Env", (), dict(settings=settings, store=store, models=models, gateway=gateway, web=web))

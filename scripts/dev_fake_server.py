"""Dev only: run the board against simulated models so the UI flow can be viewed without credentials.
The badge on the board says so. Never use this for the demo, the bench, or the receipt."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from donor_lookback import demo_pages  # noqa: E402
from donor_lookback.api import create_app  # noqa: E402
from donor_lookback.config import Settings  # noqa: E402
from donor_lookback.fetcher import Fetcher  # noqa: E402
from donor_lookback.gateway import Gateway  # noqa: E402
from donor_lookback.seed import reset_demo  # noqa: E402
from donor_lookback.services import Services  # noqa: E402
from donor_lookback.store import Store  # noqa: E402
from tests.fakes import FakeModels  # noqa: E402

port = int(os.environ.get("PORT", "8000"))
settings = Settings(db_path=":memory:", demo_state_path="/tmp/dl_fake_demo.json", public_base_url=f"http://127.0.0.1:{port}")
store = Store(":memory:")
gateway = Gateway(settings, client=FakeModels(), recorder=store.log_call, sleep=lambda s: None)
reset_demo(store, settings)
app = create_app(Services(settings, store, gateway, Fetcher(settings)), dev_fake=True, cooldown_s=0)
uvicorn.run(app, host="127.0.0.1", port=port)

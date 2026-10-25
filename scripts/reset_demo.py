"""One command: restore the labeled demo page and the seed graph.   python scripts/reset_demo.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from donor_lookback.config import get_settings  # noqa: E402
from donor_lookback.seed import reset_demo  # noqa: E402
from donor_lookback.store import Store  # noqa: E402

settings = get_settings()
print(reset_demo(Store(settings.db_path), settings))

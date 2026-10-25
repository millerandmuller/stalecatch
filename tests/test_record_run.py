import subprocess
import sys

from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "record_run.py"


def test_record_run_refuses_simulated_models(env, tmp_path):
    import threading
    import uvicorn
    from donor_lookback.api import create_app
    from donor_lookback.services import Services
    app = create_app(Services(env.settings, env.store, env.gateway, env.web), dev_fake=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765 + 1, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    import time
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)
    try:
        r = subprocess.run([sys.executable, str(SCRIPT), "--base", "http://127.0.0.1:8766", "--out", str(tmp_path / "x.json")],
                           capture_output=True, text=True)
    finally:
        server.should_exit = True
        t.join(timeout=3)
    assert r.returncode == 2 and "simulated" in r.stderr and not (tmp_path / "x.json").exists()

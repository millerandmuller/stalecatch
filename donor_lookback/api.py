"""Board API + static board + the labeled demo pages. Orchestration only; logic lives in the other modules."""
import json
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import demo_pages
from .config import ROOT
from .gateway import GatewayError, redact
from .receipt import build_receipt
from .runner import run_check
from .seed import edit_funder_deadline, reset_demo
from .services import Services, build_services
from .store import NotFoundError

WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
AFFECTED = {"quarantined": 0, "rederived": 1, "source_unavailable": 2, "released": 3}


class EditBody(BaseModel):
    deadline: str


def create_app(svc: Optional[Services] = None, *, judge_mode: bool = False, dev_fake: bool = False, cooldown_s: float = 45.0,
               recorded_run_path: Optional[Path] = None, bench_report_path: Optional[Path] = None) -> FastAPI:
    svc = svc or build_services()
    app = FastAPI(title="Donor Lookback")
    run_lock = threading.Lock()
    cur = {"run_id": None, "started": 0.0, "last_started": 0.0}
    recorded_run_path = recorded_run_path or DATA_DIR / "recorded_run.json"
    bench_report_path = bench_report_path or ROOT / "bench" / "report.json"

    def live_ready() -> bool:
        s = svc.settings
        return bool(s.base_url and s.api_key) or svc.gateway._client is not None

    @app.get("/api/config")
    def config():
        s = svc.settings
        return {"judge_mode": judge_mode, "dev_fake": dev_fake, "live_ready": live_ready(), "models": {"nano": s.nano.model_id, "ultra": s.ultra.model_id},
                "has_recorded_run": recorded_run_path.exists(), "has_bench": bench_report_path.exists(),
                "tavily": bool(s.tavily_api_key)}

    @app.get("/api/state")
    def state():
        donors = svc.store.list_donors()
        products = svc.store.list_products()
        recipients = sorted((p for p in products if p["sent"]), key=lambda p: (AFFECTED.get(p["status"], 9), p["id"]))
        run = svc.store.latest_run()
        return {"donors": donors, "products": products, "recipients": recipients,
                "demo_page": demo_pages.load_state(svc.settings.demo_state_path),
                "run": run, "running": cur["run_id"] is not None and run_lock.locked(),
                "receipt": build_receipt(svc.store, svc.settings, run["id"]) if run else None}

    @app.get("/api/run/{run_id}/events")
    def events(run_id: str, after: int = 0):
        return {"events": svc.store.events(run_id, after), "run": svc.store.get_run(run_id),
                "running": cur["run_id"] == run_id and run_lock.locked()}

    @app.post("/api/check/run")
    def start_run():
        if not live_ready():
            raise HTTPException(503, "Token Factory is not configured on this server (TOKEN_FACTORY_BASE_URL / TOKEN_FACTORY_API_KEY).")
        wait = cooldown_s - (time.monotonic() - cur["last_started"])
        if judge_mode and wait > 0 and cur["last_started"]:
            raise HTTPException(429, f"Live check is rate limited in judge mode. Try again in {int(wait) + 1}s.")
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "A check is already running.")
        try:
            run_id = svc.store.start_run("board")
        except Exception:
            run_lock.release()  # never leave the lock held if the run row could not be written
            raise
        cur.update(run_id=run_id, started=time.monotonic(), last_started=time.monotonic())

        def work():
            try:
                run_check(svc.store, svc.gateway, svc.fetcher, svc.settings, trigger="board", run_id=run_id)
            except Exception as exc:  # noqa: BLE001 - the run must end visibly, never hang as "running"
                svc.store.log_event(run_id, "error", f"Run aborted: {redact(str(exc))}")
                svc.store.finish_run(run_id, complete=False, summary={"errors": [{"error": redact(str(exc))}]})
            finally:
                run_lock.release()
        threading.Thread(target=work, daemon=True).start()
        return {"run_id": run_id}

    @app.post("/api/demo/edit")
    def edit(body: EditBody):
        deadline = body.deadline.strip()
        if not demo_pages.valid_deadline(deadline):
            raise HTTPException(422, "deadline must be a short date such as 'October 28, 2026' (letters, digits, spaces, commas)")
        return {"demo_page": edit_funder_deadline(svc.settings, deadline)}

    @app.post("/api/demo/reset")
    def reset():
        if not run_lock.acquire(blocking=False):  # holding the lock closes the run-start / reset race
            raise HTTPException(409, "A check is running; wait for it to finish.")
        try:
            out = reset_demo(svc.store, svc.settings)
            cur["run_id"] = None
            return out
        finally:
            run_lock.release()

    @app.post("/api/products/{product_id}/approve")
    def approve(product_id: str):
        try:
            return svc.store.approve_correction(svc.store.get_product(product_id)["id"])
        except NotFoundError:
            raise HTTPException(404, "unknown product")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/donors/{donor_id}/acknowledge")
    def acknowledge(donor_id: str):
        try:
            return svc.store.acknowledge_donor(donor_id)
        except NotFoundError:
            raise HTTPException(404, "unknown donor")

    @app.get("/api/lookback/{donor_id}")
    def lookback(donor_id: str):
        try:
            return svc.store.lookback(donor_id)
        except NotFoundError:
            raise HTTPException(404, "unknown donor")

    @app.get("/api/recorded-run")
    def recorded():
        if not recorded_run_path.exists():
            raise HTTPException(404, "No recorded run has been captured yet.")
        return json.loads(recorded_run_path.read_text())

    @app.get("/api/bench")
    def bench():
        if not bench_report_path.exists():
            raise HTTPException(404, "No bench report yet. Run: python -m bench.run_bench")
        return json.loads(bench_report_path.read_text())

    @app.get("/demo/{slug}", response_class=HTMLResponse)
    def demo_page(slug: str):
        if slug not in demo_pages.PAGES:
            raise HTTPException(404, "unknown demo page")
        return demo_pages.render_html(slug, demo_pages.load_state(svc.settings.demo_state_path))

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    svc.store.close_orphan_runs()
    app.state.services = svc
    return app


def app_from_env() -> FastAPI:
    import os
    svc = build_services()
    if not svc.store.list_donors():
        reset_demo(svc.store, svc.settings)
    return create_app(svc, judge_mode=os.environ.get("JUDGE_MODE") == "1")

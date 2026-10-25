"""SQLite memory: donors, products, edges. Parent enforcement and the graph walk live here, in code."""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('donor','product')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS donors (
  id TEXT PRIMARY KEY REFERENCES nodes(id),
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  claim TEXT NOT NULL,
  excerpt TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  fetch_via TEXT NOT NULL DEFAULT 'unknown',
  state TEXT NOT NULL DEFAULT 'ok' CHECK (state IN ('ok','changed','gone')),
  prev_claim TEXT,
  prev_excerpt TEXT,
  prev_fetched_at TEXT,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  cascade_pending INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY REFERENCES nodes(id),
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  prev_content TEXT,
  status TEXT NOT NULL DEFAULT 'released' CHECK (status IN ('released','quarantined','rederived','source_unavailable')),
  sent INTEGER NOT NULL DEFAULT 0,
  recipient TEXT,
  sent_content TEXT,
  correction_draft TEXT,
  correction_approved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS edges (
  parent_id TEXT NOT NULL REFERENCES nodes(id),
  child_id TEXT NOT NULL REFERENCES nodes(id),
  PRIMARY KEY (parent_id, child_id)
);
CREATE INDEX IF NOT EXISTS edges_parent ON edges(parent_id);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  trigger TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  complete INTEGER NOT NULL DEFAULT 0,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  step TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS model_calls (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  tier TEXT NOT NULL,
  model TEXT NOT NULL,
  purpose TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  usd REAL NOT NULL,
  ok INTEGER NOT NULL DEFAULT 1
);
"""


class ParentError(ValueError):
    """Raised when a derived item has no parents, or names a parent that does not exist."""


class NotFoundError(KeyError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _all(self, sql: str, args: Iterable = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(args)).fetchall()]

    def _one(self, sql: str, args: Iterable = ()) -> Optional[dict]:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # ---- donors -------------------------------------------------------
    def add_donor(self, *, url: str, claim: str, excerpt: str, label: Optional[str] = None,
                  fetched_at: Optional[str] = None, fetch_via: str = "unknown",
                  donor_id: Optional[str] = None) -> dict:
        if not url or not claim or not excerpt:
            raise ValueError("A donor needs url, claim and a verbatim excerpt.")
        did = donor_id or new_id("donor")
        ts = fetched_at or now_iso()
        with self.tx() as c:
            c.execute("INSERT INTO nodes(id,type,created_at) VALUES (?,?,?)", (did, "donor", now_iso()))
            c.execute(
                "INSERT INTO donors(id,label,url,claim,excerpt,fetched_at,fetch_via) VALUES (?,?,?,?,?,?,?)",
                (did, label or url, url, claim, excerpt, ts, fetch_via))
        return self.get_donor(did)

    def get_donor(self, donor_id: str) -> dict:
        row = self._one("SELECT * FROM donors WHERE id=?", (donor_id,))
        if not row:
            raise NotFoundError(donor_id)
        return row

    def list_donors(self) -> list[dict]:
        return self._all("SELECT d.* FROM donors d JOIN nodes n ON n.id=d.id ORDER BY n.rowid")

    def update_donor_state(self, donor_id: str, *, state: str, claim: Optional[str] = None,
                           excerpt: Optional[str] = None, fetched_at: Optional[str] = None,
                           fetch_via: Optional[str] = None) -> dict:
        """Mark a donor changed/gone. On change, the old claim/excerpt move to prev_* for the side-by-side view."""
        cur = self.get_donor(donor_id)
        with self.tx() as c:
            if state == "changed":
                c.execute(
                    "UPDATE donors SET state='changed', prev_claim=?, prev_excerpt=?, prev_fetched_at=?, "
                    "claim=?, excerpt=?, fetched_at=?, fetch_via=COALESCE(?,fetch_via), acknowledged=0 WHERE id=?",
                    (cur["claim"], cur["excerpt"], cur["fetched_at"], claim or cur["claim"],
                     excerpt or cur["excerpt"], fetched_at or now_iso(), fetch_via, donor_id))
            else:
                c.execute("UPDATE donors SET state=?, fetched_at=? WHERE id=?",
                          (state, fetched_at or now_iso(), donor_id))
        return self.get_donor(donor_id)

    def change_donor_and_quarantine(self, donor_id: str, *, claim: str, excerpt: str, fetch_via: str) -> list[dict]:
        """One transaction: donor turns changed, cascade_pending is set, every descendant is quarantined.
        A crash can no longer leave a changed donor with released (stale) products."""
        cur = self.get_donor(donor_id)
        with self.tx() as c:
            c.execute(
                "UPDATE donors SET state='changed', prev_claim=?, prev_excerpt=?, prev_fetched_at=?, claim=?, excerpt=?, "
                "fetched_at=?, fetch_via=?, acknowledged=0, cascade_pending=1 WHERE id=?",
                (cur["claim"], cur["excerpt"], cur["fetched_at"], claim, excerpt, now_iso(), fetch_via, donor_id))
            desc = self.descendants(donor_id)
            for p in desc:
                c.execute("UPDATE products SET status='quarantined' WHERE id=?", (p["id"],))
        return desc

    def finish_cascade(self, donor_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE donors SET cascade_pending=0 WHERE id=?", (donor_id,))

    def close_orphan_runs(self) -> int:
        """Runs left open by a killed process are closed as incomplete so no client polls them forever."""
        rows = self._all("SELECT id FROM runs WHERE finished_at IS NULL")
        for r in rows:
            self.log_event(r["id"], "error", "Run interrupted (process restarted)")
            self.finish_run(r["id"], complete=False, summary={"errors": [{"error": "interrupted"}]})
        return len(rows)

    def touch_donor(self, donor_id: str, fetched_at: str, fetch_via: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE donors SET fetched_at=?, fetch_via=?, state=CASE WHEN state='gone' THEN 'ok' ELSE state END WHERE id=?",
                      (fetched_at, fetch_via, donor_id))

    def acknowledge_donor(self, donor_id: str) -> dict:
        with self.tx() as c:
            c.execute("UPDATE donors SET acknowledged=1 WHERE id=?", (donor_id,))
        return self.get_donor(donor_id)

    # ---- products -----------------------------------------------------
    def derive(self, *, parents: list[str], kind: str, content: str, title: Optional[str] = None,
               sent: bool = False, recipient: Optional[str] = None, product_id: Optional[str] = None) -> dict:
        """Create a product. Parents are enforced here, never by a prompt."""
        if not parents:
            raise ParentError("derive requires at least one parent (donor or product).")
        if not kind or not content:
            raise ValueError("A product needs a kind and content.")
        unique = list(dict.fromkeys(parents))
        known = {r["id"] for r in self._all(
            f"SELECT id FROM nodes WHERE id IN ({','.join('?' * len(unique))})", unique)}
        missing = [p for p in unique if p not in known]
        if missing:
            raise ParentError(f"Unknown parent id(s): {', '.join(missing)}")
        pid = product_id or new_id("prod")
        with self.tx() as c:
            c.execute("INSERT INTO nodes(id,type,created_at) VALUES (?,?,?)", (pid, "product", now_iso()))
            c.execute(
                "INSERT INTO products(id,kind,title,content,sent,recipient,sent_content) VALUES (?,?,?,?,?,?,?)",
                (pid, kind, title or kind, content, 1 if sent else 0, recipient, content if sent else None))
            for p in unique:
                c.execute("INSERT INTO edges(parent_id,child_id) VALUES (?,?)", (p, pid))
        return self.get_product(pid)

    def mark_sent(self, product_id: str, recipient: str) -> dict:
        prod = self.get_product(product_id)
        with self.tx() as c:
            c.execute("UPDATE products SET sent=1, recipient=?, sent_content=COALESCE(sent_content, ?) WHERE id=?",
                      (recipient, prod["content"], product_id))
        return self.get_product(product_id)

    def get_product(self, product_id: str) -> dict:
        row = self._one("SELECT * FROM products WHERE id=?", (product_id,))
        if not row:
            raise NotFoundError(product_id)
        row["parents"] = self.parents_of(product_id)
        return row

    def list_products(self) -> list[dict]:
        rows = self._all("SELECT p.* FROM products p JOIN nodes n ON n.id=p.id ORDER BY n.rowid")
        for r in rows:
            r["parents"] = self.parents_of(r["id"])
        return rows

    def parents_of(self, node_id: str) -> list[str]:
        return [r["parent_id"] for r in self._all(
            "SELECT parent_id FROM edges WHERE child_id=? ORDER BY rowid", (node_id,))]

    def set_product_status(self, product_id: str, status: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE products SET status=? WHERE id=?", (status, product_id))

    def set_product_rederived(self, product_id: str, *, content: str, correction_draft: Optional[str] = None) -> dict:
        cur = self.get_product(product_id)
        with self.tx() as c:
            c.execute(
                "UPDATE products SET prev_content=?, content=?, status='rederived', correction_draft=?, "
                "correction_approved=0 WHERE id=?",
                (cur["content"], content, correction_draft, product_id))
        return self.get_product(product_id)

    def approve_correction(self, product_id: str) -> dict:
        if not self.get_product(product_id)["correction_draft"]:
            raise ValueError("This product has no correction draft to approve.")
        with self.tx() as c:
            c.execute("UPDATE products SET correction_approved=1 WHERE id=?", (product_id,))
        return self.get_product(product_id)

    # ---- graph walk ---------------------------------------------------
    def descendants(self, node_id: str) -> list[dict]:
        """All products downstream of node_id, ordered so every parent comes before its children (longest-path depth)."""
        reach = {r["id"] for r in self._all(
            """
            WITH RECURSIVE walk(id) AS (
              SELECT child_id FROM edges WHERE parent_id=?
              UNION
              SELECT e.child_id FROM edges e JOIN walk ON e.parent_id=walk.id
            )
            SELECT id FROM walk
            """, (node_id,))}
        if not reach:
            return []
        marks = ",".join("?" * len(reach))
        edges = self._all(f"SELECT parent_id, child_id FROM edges WHERE child_id IN ({marks})", tuple(reach))
        parents = {n: [e["parent_id"] for e in edges if e["child_id"] == n and (e["parent_id"] in reach)] for n in reach}
        depth: dict[str, int] = {}

        def d(n: str, seen=()) -> int:
            if n in depth:
                return depth[n]
            if n in seen:  # cycle guard
                return 1
            depth[n] = 1 + max((d(p, seen + (n,)) for p in parents[n]), default=0)
            return depth[n]
        out = []
        for n in sorted(reach, key=lambda x: (d(x), x)):
            prod = self._one("SELECT * FROM products WHERE id=?", (n,))
            if prod:
                prod["depth"] = depth[n]
                prod["parents"] = self.parents_of(n)
                out.append(prod)
        return out

    def lookback(self, donor_id: str) -> dict:
        self.get_donor(donor_id)
        desc = self.descendants(donor_id)
        return {
            "donor_id": donor_id,
            "products": desc,
            "sent_first": sorted(desc, key=lambda p: (not p["sent"], p["depth"], p["id"])),
        }

    # ---- runs, events, calls -----------------------------------------
    def start_run(self, trigger: str) -> str:
        rid = new_id("run")
        with self.tx() as c:
            c.execute("INSERT INTO runs(id,trigger,started_at) VALUES (?,?,?)", (rid, trigger, now_iso()))
        return rid

    def finish_run(self, run_id: str, *, complete: bool, summary: dict) -> None:
        with self.tx() as c:
            c.execute("UPDATE runs SET finished_at=?, complete=?, summary=? WHERE id=?",
                      (now_iso(), 1 if complete else 0, json.dumps(summary), run_id))

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM runs WHERE id=?", (run_id,))
        if row and row["summary"]:
            row["summary"] = json.loads(row["summary"])
        return row

    def latest_run(self) -> Optional[dict]:
        row = self._one("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1")
        return self.get_run(row["id"]) if row else None

    def latest_run_with_change(self) -> Optional[dict]:
        """The most recent run that turned a donor red. The receipt shown on the board belongs to it, so a later quiet check
        does not replace the Nano-versus-Ultra numbers."""
        row = self._one("SELECT run_id FROM run_events WHERE step='donor_red' ORDER BY seq DESC LIMIT 1")
        return self.get_run(row["run_id"]) if row else None

    def log_event(self, run_id: str, step: str, detail: str = "") -> None:
        with self.tx() as c:
            c.execute("INSERT INTO run_events(run_id,ts,step,detail) VALUES (?,?,?,?)",
                      (run_id, now_iso(), step, detail))

    def events(self, run_id: str, after_seq: int = 0) -> list[dict]:
        return self._all("SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after_seq))

    def log_call(self, *, run_id: Optional[str], tier: str, model: str, purpose: str, prompt_tokens: int,
                 completion_tokens: int, latency_ms: int, usd: float, ok: bool = True) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO model_calls(run_id,tier,model,purpose,prompt_tokens,completion_tokens,latency_ms,usd,ok) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, tier, model, purpose, prompt_tokens, completion_tokens, latency_ms, usd, 1 if ok else 0))

    def calls(self, run_id: Optional[str] = None) -> list[dict]:
        if run_id is None:
            return self._all("SELECT * FROM model_calls ORDER BY seq")
        return self._all("SELECT * FROM model_calls WHERE run_id=? ORDER BY seq", (run_id,))

    def reset_all(self) -> None:
        with self.tx() as c:
            for t in ("run_events", "model_calls", "runs", "edges", "products", "donors", "nodes"):
                c.execute(f"DELETE FROM {t}")

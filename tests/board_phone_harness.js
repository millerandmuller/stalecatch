// Drives web/board.js in a minimal fake DOM on the public judge board (no credentials, recorded run only).
// Checks what a phone user sees: the visible reason under the disabled buttons, and that "Approve correction"
// pressed during a replay changes only the replayed card without calling the server.
// Prints one JSON line for tests/test_regressions.py.
const fs = require("fs"), path = require("path"), vm = require("vm");
const web = path.join(__dirname, "..", "web");

class El {
  constructor(tag) {
    this.tag = tag; this.className = ""; this.children = []; this.handlers = {}; this.dataset = {};
    this.hidden = false; this.disabled = false; this.title = ""; this._text = "";
    this.classList = { add: (c) => { this.className = `${this.className} ${c}`.trim(); } };
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  append(...nodes) { for (const n of nodes) this.children.push(typeof n === "string" ? Object.assign(new El("#text"), { _text: n }) : n); }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  getBoundingClientRect() { return { top: 0 }; }
  animate() {}
  querySelector() { return new El("x"); }
  showModal() {}
  find(pred) { if (pred(this)) return this; for (const c of this.children) { const f = c.find(pred); if (f) return f; } return null; }
}
const byId = {};
const getById = (id) => (byId[id] = byId[id] || new El("div"));

const frame = (marker, extra = {}) => ({ marker, donors: [], products: [], recipients: [], run: null, running: false, ...extra });
const sam = { id: "prod_msg", kind: "message", status: "rederived", title: "Message to Sam: deadline", content: "Replaced by correction.",
  sent_content: "Hi Sam, due Nov 19.", recipient: "Sam (Executive Director, client)", sent_at: "2026-10-06T14:00:00+00:00",
  correction_draft: "Hi Sam, the deadline moved to Nov 9.", correction_approved: false };
const json = (body, status = 200) => Promise.resolve({ ok: status < 400, status, statusText: "err", json: () => Promise.resolve(body) });
const calls = [];
const routes = {
  "/api/config": { judge_mode: true, dev_fake: false, live_ready: false, live_runs_left: 10, has_recorded_run: true, has_bench: false },
  "/api/state": frame("live"),
  "/api/recorded-run": { recorded_at: "2026-10-25T00:00:00Z", models: { nano: "n", ultra: "u" },
    frames: [{ state: frame("f1"), hold_ms: 10 }, { state: frame("f2", { recipients: [sam] }), hold_ms: 10 }] },
};
const context = {
  console, setTimeout, clearTimeout,
  window: { STRINGS: null },
  document: { getElementById: getById, createElement: (t) => new El(t), documentElement: new El("html"), body: new El("body"), querySelector: () => new El("x"), querySelectorAll: () => [] },
  matchMedia: () => ({ matches: true, addEventListener() {} }),
  localStorage: { getItem: () => null, setItem() {} },
  fetch: (url, opts) => {
    const p = String(url).split("?")[0];
    calls.push(`${(opts && opts.method) || "GET"} ${p}`);
    if (p.endsWith("/approve")) return json({ detail: "no correction draft" }, 409);  // what the seed answers on the public server
    return json(routes[p] || {});
  },
};
vm.createContext(context);
const load = (f) => vm.runInContext(fs.readFileSync(path.join(web, f), "utf8"), context, { filename: f });
load("strings.js"); load("replay.js"); load("board.js");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await sleep(100);                                          // init() done: live seed loaded
  const note = getById("controlsNote");
  const noteBeforeReplay = { text: note.textContent, hidden: note.hidden };
  await vm.runInContext("replayRecorded()", context);        // plays both frames, stays on the last one
  const approveBtn = getById("recipients").find((n) => n.tag === "button" && n.textContent === "Approve correction");
  const cardOf = () => getById("recipients").children[0];
  const before = { card: cardOf().className, count: getById("countRecipients").textContent };
  approveBtn.handlers.click();
  await sleep(20);
  const approveCalls = calls.filter((c) => c.endsWith("/approve"));
  const card = cardOf();
  const out = {
    noteBeforeReplay, approveCalls,
    before, afterCard: card.className, afterCount: getById("countRecipients").textContent,
    approvedLine: (card.find((n) => n.className === "approved") || {}).textContent || null,
    stillHasApproveButton: Boolean(getById("recipients").find((n) => n.tag === "button" && n.textContent === "Approve correction")),
  };
  vm.runInContext("replay.stop()", context);                 // Reset or Run takes the board back: the replay approval is forgotten
  out.approvedAfterStop = vm.runInContext('replay.isApproved("prod_msg")', context);
  console.log(JSON.stringify(out));
  process.exit(0);
})();

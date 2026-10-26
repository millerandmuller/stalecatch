// Drives web/board.js in a stubbed DOM: Run clicked mid-replay must stop the replay's frames.
// Prints one JSON line: the state markers painted, and which phase each came from.
const fs = require("fs"), path = require("path"), vm = require("vm");
const web = path.join(__dirname, "..", "web");

const el = () => new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === "then" ? undefined : el()),
  set: () => true,
  apply: () => el(),
});
const frame = (marker) => ({ marker, donors: [], products: [], recipients: [], run: null, running: false });
const json = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });

const routes = {
  "/api/config": { judge_mode: false, dev_fake: true, live_ready: true, has_recorded_run: true, has_bench: false },
  "/api/state": frame("live"),
  "/api/recorded-run": { recorded_at: "2026-10-25T00:00:00Z", models: { nano: "n", ultra: "u" },
    frames: ["f1", "f2", "f3", "f4"].map((m) => ({ state: frame(m), hold_ms: 60 })) },
  "/api/check/run": { run_id: "r1" },
};
const context = {
  console, setTimeout, clearTimeout,
  window: { STRINGS: null, matchMedia: () => ({ matches: false, addEventListener() {} }) },
  document: { getElementById: el, createElement: el, documentElement: el(), body: el(), querySelector: el, querySelectorAll: () => [] },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  localStorage: { getItem: () => null, setItem() {} },
  fetch: (url) => {
    const p = String(url).split("?")[0];
    if (p.startsWith("/api/run/")) return json({ events: [], running: true, run: null });
    return json(routes[p] || {});
  },
};
vm.createContext(context);
const load = (f) => vm.runInContext(fs.readFileSync(path.join(web, f), "utf8"), context, { filename: f });
load("strings.js");
load("replay.js");
load("board.js");

const painted = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await sleep(100);                                   // init() finished, live state loaded
  const realRender = context.render;
  const errors = [];
  context.render = () => { painted.push(vm.runInContext("state.marker", context)); try { realRender(); } catch (e) { errors.push(String(e)); throw e; } };
  vm.runInContext("replayRecorded()", context);
  await sleep(90);                                    // f1 painted, f2 waiting
  const before = painted.length;
  await vm.runInContext("runCheck()", context);       // Run clicked mid-replay
  await sleep(400);                                   // would have been long enough for f2..f4
  const after = painted.slice(before);
  console.log(JSON.stringify({ errors, before: painted.slice(0, before), replayFramesAfterRun: after.filter((m) => /^f\d/.test(m)) }));
  process.exit(0);
})();

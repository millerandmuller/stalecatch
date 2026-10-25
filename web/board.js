// Lookback board: three columns, one status system (icon + label + colour), no navigation.
const S = window.STRINGS;
const $ = (id) => document.getElementById(id);
const ICON = { changed: "▲", quarantined: "◐", rederived: "✓", gone: "○", source_unavailable: "○", ok: "●", released: "●" };

let state = null, config = null, selectedDonor = null, polling = null, lastEventSeq = 0, activeRunId = null;

function h(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const api = async (path, opts) => {
  let r;
  try { r = await fetch(path, opts); } catch (e) { throw new Error(S.offline); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

function stateTag(key, label) {
  const s = h("div", "state");
  s.append(h("span", "", ICON[key] || "●"), h("span", "", label));
  return s;
}

function donorCard(d) {
  const key = d.state;
  const c = h("div", `card clickable s-${key}${selectedDonor === d.id ? " selected" : ""}`);
  c.dataset.id = d.id;
  c.append(h("div", "kind", "Donor"), h("div", "title", d.label));
  c.append(h("div", "body", d.claim));
  c.append(h("div", "mono", `${S.fetched} ${fmtTime(d.fetched_at)} · via ${d.fetch_via}`));
  c.append(stateTag(key, S.donorState[key]));
  c.addEventListener("click", () => { selectedDonor = selectedDonor === d.id ? null : d.id; render(); });
  return c;
}

function parentLabel(id) {
  const d = state.donors.find((x) => x.id === id);
  if (d) return `donor: ${d.label.replace(/ \(demo page\)/, "")}`;
  const p = state.products.find((x) => x.id === id);
  return p ? `${p.kind}: ${p.title.replace(/^[A-Za-z]+: /, "")}` : id;
}

function productCard(p) {
  const c = h("div", `card s-${p.status}${p.status === "released" ? " compact" : ""}`);
  c.dataset.id = p.id;
  c.append(h("div", "kind", p.kind + (p.sent ? " · sent" : "")), h("div", "title", p.title), h("div", "body", p.content));
  c.append(h("div", "from", `built from ${p.parents.map(parentLabel).join(" + ")}`));
  c.append(stateTag(p.status, S.productState[p.status]));
  return c;
}

function recipientCard(p, first) {
  const affected = p.status === "rederived" || p.status === "quarantined";
  const c = h("div", `card s-${affected ? "changed" : "released"}`);
  c.dataset.id = p.id;
  if (affected && first) c.append(h("div", "recip-lead", `${p.recipient || "Recipient"} ${S.receivedOld}`));
  c.append(h("div", "kind", `${S.sentLine} ${p.recipient || "recipient"}`), h("div", "title", p.title));
  c.append(h("div", "body mono", p.sent_content || p.content));
  if (affected) c.append(stateTag(p.status === "rederived" ? "changed" : "quarantined", p.status === "rederived" ? S.correctionPrepared : S.productState.quarantined));
  if (p.correction_draft) {
    const dr = h("div", "draft");
    dr.append(h("div", "label", S.correctionDraft), h("p", "", p.correction_draft));
    if (p.correction_approved) dr.append(h("div", "mono", S.approved));
    else {
      const b = h("button", "ghost", S.approve);
      b.addEventListener("click", async () => { await api(`/api/products/${p.id}/approve`, { method: "POST" }); refresh(); });
      dr.append(b);
    }
    c.append(dr);
  }
  return c;
}

// FLIP: animate cards that changed position (the sent message "rises").
function renderList(el, nodes) {
  const before = new Map([...el.children].map((n) => [n.dataset.id, n.getBoundingClientRect().top]));
  el.replaceChildren(...nodes);
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  for (const n of el.children) {
    const was = before.get(n.dataset.id);
    if (was === undefined) continue;
    const dy = was - n.getBoundingClientRect().top;
    if (Math.abs(dy) < 2) continue;
    n.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 650, easing: "cubic-bezier(.2,.8,.2,1)" });
  }
}

function renderRecord() {
  const box = $("record");
  const d = state.donors.find((x) => x.id === selectedDonor);
  if (!d) { box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren(h("h3", "", `Donor record: ${d.label}`), h("div", "mono", d.url));
  const diff = h("div", "diff");
  const mk = (cls, cap, txt, ts) => { const x = h("div", cls); x.append(h("div", "cap", cap), h("div", "txt", txt), h("div", "ts", ts)); return x; };
  if (d.prev_excerpt) {
    diff.append(mk("old", S.diffOld, d.prev_excerpt, `${S.fetched} ${fmtTime(d.prev_fetched_at)}`), mk("new", S.diffNew, d.excerpt, `${S.fetched} ${fmtTime(d.fetched_at)}`));
  } else {
    diff.append(mk("new", "Recorded excerpt (verbatim)", d.excerpt, `${S.fetched} ${fmtTime(d.fetched_at)} · via ${d.fetch_via}`));
  }
  box.append(diff);
}

function renderReceipt() {
  const r = state.receipt, el = $("receipt");
  el.replaceChildren();
  if (!r || (!r.nano.calls && !r.ultra.calls)) { el.append(h("span", "", "No check has run yet.")); return; }
  const run = state.run && state.run.summary;
  const usd = (v) => `$${v.toFixed(4)}`;
  if (run && run.donors !== undefined) {
    const sp = h("span");
    sp.append(`Tonight's check: ${run.donors} donors, ${run.changed || 0} changed, `, h("b", "", usd(r.total_usd)), run.errors && run.errors.length ? " (incomplete)" : "");
    el.append(sp);
  }
  const part = (label, t) => {
    const s = h("span");
    s.append(`${label} `, h("b", "", `${t.calls} calls`), ` · ${t.prompt_tokens + t.completion_tokens} tokens · ${usd(t.usd)} · ${(t.latency_ms / 1000).toFixed(1)}s`);
    return s;
  };
  el.append(part("Nano (watches)", r.nano), part("Ultra (thinks)", r.ultra));
  const e = h("span");
  e.append("If Ultra had checked everything: ", h("b", "", usd(r.ultra_everything_usd)));
  el.append(e);
}

function render() {
  if (!state) return;
  renderList($("donors"), state.donors.length ? state.donors.map(donorCard) : [h("div", "empty", S.emptyDonors)]);
  renderList($("products"), state.products.map(productCard));
  const rec = state.recipients;
  renderList($("recipients"), rec.length ? rec.map((p, i) => recipientCard(p, i === 0)) : [h("div", "empty", S.emptyRecipients)]);
  renderRecord();
  renderReceipt();
  $("runBtn").disabled = state.running || !config.live_ready;
  $("runBtn").title = config.live_ready ? "" : S.notConfigured;
  $("demoNote").textContent = S.demoNote + (config.live_ready ? "" : " " + S.notConfigured);
}

function setTicker(text) { $("ticker").textContent = text; }

async function refresh() {
  state = await api("/api/state");
  render();
}

async function poll() {
  clearTimeout(polling);
  try {
    if (activeRunId) {
      const ev = await api(`/api/run/${activeRunId}/events?after=${lastEventSeq}`);
      for (const e of ev.events) { lastEventSeq = e.seq; setTicker(`${e.step}: ${e.detail}`); }
      await refresh();
      if (!ev.running && ev.run && ev.run.finished_at) { activeRunId = null; polling = setTimeout(poll, 4000); return; }
      polling = setTimeout(poll, 450);
      return;
    }
  } catch (err) { setTicker(String(err.message || err)); }
  polling = setTimeout(poll, 4000);
}

async function runCheck() {
  try {
    const { run_id } = await api("/api/check/run", { method: "POST" });
    activeRunId = run_id; lastEventSeq = 0; setTicker(S.running); poll();
  } catch (err) { setTicker(err.message); }
}

async function editPage() {
  const deadline = $("newDeadline").value.trim();
  try {
    await api("/api/demo/edit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ deadline }) });
    setTicker(`Demo funder page now says: ${deadline}. Open /demo/funder to see it. Run tonight's check next.`);
  } catch (err) { setTicker(err.message); }
}

async function resetDemo() {
  try { await api("/api/demo/reset", { method: "POST" }); selectedDonor = null; setTicker("Demo reset to the seed."); await refresh(); }
  catch (err) { setTicker(err.message); }
}

async function showBench() {
  const body = $("benchBody");
  body.replaceChildren(h("h3", "", S.benchTitle));
  try {
    const r = await api("/api/bench");
    const sum = r.summary;
    body.append(h("p", "", `Nano alone: ${sum.nano_recall}/${sum.changed_pairs} changes found · Nano + Ultra: ${sum.confirmed_recall}/${sum.changed_pairs} (same cycle changed: ${sum.verdict_changed}, newer cycle: ${sum.verdict_superseded}) · false alarms on unchanged pages: ${sum.false_alarms}/${sum.unchanged_pairs} · cost $${sum.total_usd.toFixed(4)}`));
    const t = h("table"), head = h("tr");
    for (const c of ["Page", "What changed", "Nano", "Ultra: changed", "Ultra: newer cycle", "Ultra: unchanged", "Snapshots"]) head.append(h("th", "", c));
    t.append(head);
    for (const row of r.rows) {
      const tr = h("tr");
      tr.append(h("td", "", row.label), h("td", "", row.what_changed || (row.changed ? `changed: ${row.field}` : "unchanged")), h("td", "", row.nano_verdict),
        ...["changed", "superseded", "unchanged"].map((v) => h("td", "", row.final_verdict === v ? "●" : (row.final_verdict === "error" && v === "changed" ? "error" : ""))));
      const td = h("td");
      [["before", row.before_url], ["after", row.after_url]].forEach(([name, url], i) => {
        if (i) td.append(" → ");
        const a = h("a", "", name); a.href = url; a.target = "_blank"; a.rel = "noopener"; td.append(a);
      });
      tr.append(td); t.append(tr);
    }
    body.append(t);
  } catch (err) { body.append(h("p", "", err.message)); }
  $("benchDialog").showModal();
}

async function replayRecorded() {
  try {
    const rec = await api("/api/recorded-run");
    setTicker(`Replaying a recorded run from ${rec.recorded_at.slice(0, 10)} (${rec.models.nano} + ${rec.models.ultra}). Read-only.`);
    for (const frame of rec.frames) {
      state = frame.state; render(); setTicker(`Recorded run. ${frame.ticker || ""}`);
      await new Promise((r) => setTimeout(r, frame.hold_ms || 900));
    }
    await refresh();
  } catch (err) { setTicker(err.message); }
}

async function init() {
  $("tagline").textContent = S.tagline;
  for (const [id, key] of [["colDonors", "donors"], ["colProducts", "products"], ["colRecipients", "recipients"]]) {
    $(id).querySelector("h2").textContent = S.columns[key];
    $(id).querySelector(".hint").textContent = S.columnHints[key];
  }
  $("runBtn").textContent = S.runButton; $("resetBtn").textContent = S.resetButton; $("editBtn").textContent = S.editButton;
  $("replayBtn").textContent = S.replayButton; $("proofBtn").textContent = S.proofButton;
  config = await api("/api/config");
  $("modeBadge").hidden = !(config.judge_mode || config.dev_fake);
  $("modeBadge").textContent = config.dev_fake ? "DEV: simulated models, not Nemotron" : "Judge mode";
  $("replayBtn").hidden = !config.has_recorded_run; $("proofBtn").hidden = !config.has_bench;
  $("runBtn").addEventListener("click", runCheck);
  $("editBtn").addEventListener("click", editPage);
  $("resetBtn").addEventListener("click", resetDemo);
  $("replayBtn").addEventListener("click", replayRecorded);
  $("proofBtn").addEventListener("click", showBench);
  await refresh();
  if (state.running && state.run) activeRunId = state.run.id;
  poll();
}
init().catch((e) => setTicker(String(e.message || e)));

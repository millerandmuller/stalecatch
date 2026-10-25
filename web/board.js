// StaleCatch board: three glass columns, one status system (dot + label + colour), no navigation.
const S = window.STRINGS;
const $ = (id) => document.getElementById(id);

let replaying = false, state = null, config = null, selectedDonor = null, polling = null, lastEventSeq = 0, activeRunId = null;

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

// ---- theme: light / dark, remembered when storage allows it ----
function currentTheme() {
  const set = document.documentElement.dataset.theme;
  return set || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function paintThemeButton() {
  const dark = currentTheme() === "dark";
  $("themeGlyph").textContent = dark ? "☾" : "☀"; $("themeLabel").textContent = dark ? "Dark" : "Light";
}
function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("stalecatch-theme", next); } catch (e) { /* private window or blocked storage: the choice just is not remembered */ }
  paintThemeButton();
}

// ---- small helpers for dates inside excerpts ----
const MONTHS = "(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\\.?";
const DATE_RE = new RegExp(`${MONTHS}\\s+\\d{1,2}(?:st|nd|rd|th)?(?!\\d)(?:,?\\s+(?:19|20)\\d{2})?`);
const firstDate = (text) => { const m = DATE_RE.exec(text || ""); return m ? m[0] : ""; };
const shortDate = (text) => firstDate(text).replace(/^(\w{3})\w*\.?\s+(\d+)\D*.*$/, "$1 $2");

// Split an excerpt around its first date so the date can be marked.
function splitAtDate(text) {
  const m = DATE_RE.exec(text || "");
  return m ? [text.slice(0, m.index), m[0], text.slice(m.index + m[0].length)] : [text || "", "", ""];
}

function chip(cls, label, dotKind) {
  const c = h("span", "chip");
  c.append(h("span", `dot ${dotKind || ""}`), h("span", "", label));
  if (cls) c.classList.add(cls);
  return c;
}

const DOT = { changed: "full", quarantined: "half", rederived: "full", gone: "", source_unavailable: "", ok: "", released: "" };

function diffBlock(d) {
  const diff = h("div", "diff");
  const side = (cls, cap, text, ts) => {
    const x = h("div", cls === "old" ? "old" : "new");
    const [pre, hit, post] = splitAtDate(text);
    const txt = h("div", "txt");
    txt.append(pre);
    if (hit) txt.append(h("mark", cls, hit));
    txt.append(post);
    x.append(h("div", "cap", `${cap} · ${ts}`), txt);
    return x;
  };
  diff.append(side("old", S.diffOld, d.prev_excerpt, fmtTime(d.prev_fetched_at)), side("new", S.diffNew, d.excerpt, fmtTime(d.fetched_at)));
  return diff;
}

function donorCard(d) {
  const key = d.state;
  const c = h("div", `card clickable s-${key}${selectedDonor === d.id ? " selected" : ""}${key === "changed" ? "" : ""}`);
  c.dataset.id = d.id;
  const label = key === "changed" && d.prev_excerpt && shortDate(d.prev_excerpt) && shortDate(d.excerpt)
    ? `${S.donorState.changed} · ${shortDate(d.prev_excerpt)} → ${shortDate(d.excerpt)}` : S.donorState[key];
  const top = h("div", "card-top");
  top.append(h("div", "kind", S.sourceLabel), chip("", label, DOT[key]));
  c.append(top, h("div", "title", d.label.replace(/ \(demo page\)/, "")), h("div", "body", d.claim));
  c.append(h("div", "mono", `${S.fetched} ${fmtTime(d.fetched_at)} · via ${d.fetch_via}${/demo/.test(d.url) ? " · our demo page" : ""}`));
  if (key === "changed" && d.prev_excerpt) c.append(diffBlock(d));
  if (state.donors.some((x) => x.state === "changed") && key !== "changed") c.classList.add("dim");
  c.addEventListener("click", () => { selectedDonor = selectedDonor === d.id ? null : d.id; render(); });
  return c;
}

function parentLabel(id) {
  const d = state.donors.find((x) => x.id === id);
  if (d) return d.label.replace(/ \(demo page\)/, "") + " page";
  const p = state.products.find((x) => x.id === id);
  return p ? `${p.title.replace(/^[A-Za-z]+: /, "")} (${p.kind})` : id;
}

function productCard(p) {
  const affected = p.status === "quarantined" || p.status === "rederived";
  const c = h("div", `card s-${p.status}${affected ? " has-rule" : ""}`);
  c.dataset.id = p.id;
  c.append(h("span", "rule"));
  const top = h("div", "card-top");
  top.append(h("div", "kind", p.kind + (p.sent ? " · sent" : "")), chip("", S.productState[p.status], DOT[p.status]));
  c.append(top, h("div", "title", p.title));
  const replaced = p.sent && p.status === "rederived";
  c.append(h("div", "body", replaced ? S.replacedSeeCorrection : p.content));
  if (p.status === "rederived" && p.prev_content && !replaced) c.append(h("div", "before", p.prev_content));
  if (p.status === "rederived" && p.kind === "plan" && /^Moved earlier/i.test(p.content)) c.append(h("div", "because", S.planNoLongerFits));
  c.append(h("div", "from", `from: ${p.parents.map(parentLabel).join(" + ")}`));
  if (state.donors.some((x) => x.state === "changed") && !affected && p.status === "released") {
    const parentIsChanged = p.parents.some((id) => state.donors.some((x) => x.id === id && x.state === "changed"));
    if (!parentIsChanged) c.classList.add("dim");
  }
  return c;
}

function recipientName(p) { return (p.recipient || "Recipient").replace(/\s*\(.*$/, ""); }

function recipientCard(p, first) {
  const affected = p.status === "rederived" || p.status === "quarantined";
  const tone = !affected ? "s-released" : p.status === "quarantined" ? "s-quarantined has-rule" : p.correction_approved ? "s-rederived has-rule" : "s-changed has-rule";
  const c = h("div", `card ${tone}`);  // red while the message went out with the old deadline, amber while on hold, green once the correction is approved
  c.dataset.id = p.id;
  c.append(h("span", "rule"));
  if (affected && first) c.append(h("div", "recip-lead", `${recipientName(p)} ${S.receivedOld} ${S.correctionPrepared}`));
  const top = h("div", "card-top");
  const stateLabel = p.status === "source_unavailable" ? S.productState.source_unavailable
    : !affected ? S.productState.released : (p.correction_approved ? S.correctionApproved : (p.status === "quarantined" ? S.productState.quarantined : S.sentWithOld));
  top.append(h("div", "kind", p.recipient || "recipient"), chip(affected && p.status === "rederived" && !p.correction_approved ? "" : "", stateLabel, affected ? (p.status === "quarantined" ? "half" : "full") : ""));
  c.append(top, h("div", "title", p.title));
  c.append(h("div", "body mono", p.sent_content || p.content));
  if (p.correction_draft) {
    const dr = h("div", "draft");
    dr.append(h("div", "label", S.correctionDraft), h("p", "", p.correction_draft));
    if (p.correction_approved) dr.append(h("div", "approved", `✓ ${S.approved}`));
    else {
      const b = h("button", "", S.approve);
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
  if (!d || (d.state === "changed" && d.prev_excerpt)) { box.hidden = true; return; }  // a changed source already shows its diff on the card
  box.hidden = false;
  const x = h("div", "diff"), cell = h("div", "new");
  cell.append(h("div", "cap", `Recorded excerpt (verbatim) · ${S.fetched} ${fmtTime(d.fetched_at)} · via ${d.fetch_via}`), h("div", "txt", d.excerpt));
  x.append(cell);
  box.replaceChildren(h("h3", "", `Source record: ${d.label}`), h("div", "mono", d.url), x);
}

function setCount(id, text, tone) {
  const el = $(id);
  el.className = `count ${tone || ""}`;
  el.replaceChildren(h("span", "cdot"), h("span", "", text));
}

function renderCounts() {
  const donors = state.donors, products = state.products, recips = state.recipients;
  const changed = donors.filter((d) => d.state === "changed").length;
  setCount("countDonors", changed ? `${changed} changed` : `${donors.length} current`, changed ? "red" : "");
  const held = products.filter((p) => p.status === "quarantined").length, updated = products.filter((p) => p.status === "rederived").length;
  setCount("countProducts", held ? `${held} on hold` : updated ? `${updated} updated` : `${products.length} current`, held ? "amber" : updated ? "green" : "");
  const rHeld = recips.filter((p) => p.status === "quarantined").length;
  const rNeeds = recips.filter((p) => p.status === "rederived" && !p.correction_approved).length;
  const rGone = recips.filter((p) => p.status === "source_unavailable").length;
  setCount("countRecipients", rHeld ? `${rHeld} on hold` : rNeeds ? `${rNeeds} needs correction` : rGone ? `${rGone} source unavailable` : `${recips.length} current`, rHeld ? "amber" : rNeeds ? "red" : "");
}

function rcCard(label, num, sub, dashed) {
  const c = h("div", `rc${dashed ? " dashed" : ""}`);
  c.append(h("div", "lbl", label), h("div", "num", num), h("div", "sub", sub || ""));
  return c;
}

// Measured numbers only: every figure comes from the receipt the server builds from logged usage fields.
function renderReceipt() {
  const r = state.receipt, el = $("receipt");
  el.replaceChildren();
  const failed = state.run && state.run.finished_at && !state.run.complete;
  const none = !r || (!r.nano.calls && !r.ultra.calls);
  const usd = (v) => `$${v.toFixed(4)}`;
  const run = state.headline_run && state.headline_run.summary;
  let total = rcCard(S.cost.total, none ? "—" : usd(r.total_usd), none ? S.cost.none : "");
  if (failed) {
    const why = (state.run.summary && state.run.summary.errors && state.run.summary.errors[0] && state.run.summary.errors[0].error) || "unknown error";
    const sub = total.querySelector(".sub"); sub.className = "sub warn"; sub.textContent = friendly(`The last check did not finish: ${why}`);
  } else if (!none && run && run.donors !== undefined) {
    total.querySelector(".sub").textContent = `${run.donors} sources · ${run.changed || 0} changed · ${r.nano.calls + r.ultra.calls} calls${run.errors && run.errors.length ? " (incomplete)" : ""}`;
  }
  const line = (t) => `${t.calls} calls · ${(t.prompt_tokens + t.completion_tokens).toLocaleString()} tokens · ${(t.latency_ms / 1000).toFixed(1)} s`;
  const pct = !none && r.total_usd > 0 ? `${r.ultra_everything_usd >= r.total_usd ? "+" : ""}${Math.round((r.ultra_everything_usd / r.total_usd - 1) * 100)}% for the same result` : "";
  el.append(total,
    rcCard(S.cost.nano, none ? "—" : usd(r.nano.usd), none ? "" : line(r.nano)),
    rcCard(S.cost.ultra, none ? "—" : usd(r.ultra.usd), none ? "" : line(r.ultra)),
    rcCard(S.cost.counter, none ? "—" : usd(r.ultra_everything_usd), pct, true));
}

function render() {
  if (!state) return;
  renderList($("donors"), state.donors.length ? state.donors.map(donorCard) : [h("div", "empty", S.emptyDonors)]);
  renderList($("products"), state.products.map(productCard));
  const rec = state.recipients;
  renderList($("recipients"), rec.length ? rec.map((p, i) => recipientCard(p, i === 0)) : [h("div", "empty", S.emptyRecipients)]);
  renderCounts();
  renderRecord();
  renderReceipt();
  $("runBtn").disabled = state.running || !config.live_ready;
  $("runBtn").title = config.live_ready ? "" : S.notConfigured;
  $("demoNote").textContent = S.demoNote + (config.live_ready ? "" : " " + S.notConfigured);
}

// Friendly one-line ticker for each run event: [text, dot colour, phase label].
// Old internal words never reach the screen: sources, items, on hold.
const vocab = (t) => t.replace(/donors?/g, (m) => (m.endsWith("s") ? "sources" : "source")).replace(/products?/g, (m) => (m.endsWith("s") ? "items" : "item")).replace(/quarantined/gi, "on hold").replace(/\b1 sources\b/, "1 source");

function tickerFor(step, detail) {
  const sides = detail.split("' -> '");  // donor_red details read "Source changed: 'old' -> 'new'"
  const shift = sides.length === 2 ? [shortDate(sides[0]), shortDate(sides[1])] : [];
  switch (step) {
    case "start": return [vocab(detail.replace("Tonight's check: ", "Checking ")) + "…", "amber", "quick scan · nano"];
    case "fetch": return ["Re-fetching your sources…", "amber", "quick scan · nano"];
    case "nano_alarm": return ["Quick scan flagged a change. Deep review is confirming…", "amber", "deep review · ultra"];
    case "unchanged": return ["Quick scan: no change on this source.", "", "quick scan · nano"];
    case "alarm_rejected": return ["Deep review checked twice and found no change.", "", "deep review · ultra"];
    case "donor_red": {
      const arrow = shift[0] && shift[1] ? `: ${shift[0]} → ${shift[1]}` : "";
      return [detail.startsWith("Next cycle posted") ? `${S.nextCycle}${arrow}.` : `Deep review confirmed a change${arrow}.`, "red", "deep review · ultra"];
    }
    case "quarantine": return [detail.replace(/\.$/, "") + ". Other sources untouched.", "amber", "on hold"];
    case "rederived": return ["Deep review updated the held items from the new source.", "green", "deep review · ultra"];
    case "sent_rises": return ["The message already sent is now first in Who's been told. Correction prepared, nothing sent.", "red", "check complete"];
    case "source_unavailable": return [vocab(detail), "", "source unavailable"];
    case "resume": return [vocab(detail), "amber", "resuming"];
    case "done": return [vocab(detail), "", "done"];
    case "error": return [vocab(detail), "red", "error"];
    default: return [vocab(detail || step), "", step];
  }
}

function heroLine() {
  const r = state && state.recipients.find((p) => p.status === "rederived");
  return r ? `${recipientName(r)} ${S.receivedOld} ${S.correctionPrepared} Nothing is sent.` : "";
}
const friendly = (t) => String(t).replace(/nano call failed \((\w+)\)/i, "Quick scan could not reach the model service ($1)").replace(/ultra call failed \((\w+)\)/i, "Deep review could not reach the model service ($1)");

function setTicker(text, dot, phase) {
  $("tickerText").textContent = text;
  $("tickerDot").className = `tdot ${dot || ""}`;
  $("tickerPhase").textContent = phase || "";
}
let heroShown = false;
function tickEvent(e) {
  if (e.step === "start") heroShown = false;
  if (e.step === "done" && heroShown) return;  // the closing line stays on the hero moment, not on the cost summary
  const [t, dot, ph] = tickerFor(e.step, e.detail || "");
  if (e.step === "sent_rises") { heroShown = true; setTicker(heroLine() || t, dot, ph); return; }
  setTicker(friendly(t), dot, ph);
}

async function refresh() {
  state = await api("/api/state");
  render();
}

async function poll() {
  clearTimeout(polling);
  try {
    if (replaying) { polling = setTimeout(poll, 4000); return; }  // never overwrite a replayed frame with live state
    if (activeRunId) {
      const ev = await api(`/api/run/${activeRunId}/events?after=${lastEventSeq}`);
      for (const e of ev.events) { lastEventSeq = e.seq; tickEvent(e); }
      await refresh();
      if (!ev.running && ev.run && ev.run.finished_at) {
        activeRunId = null;
        const errs = ev.run.summary && ev.run.summary.errors;
        if (!ev.run.complete && errs && errs.length) setTicker(friendly(`The check did not finish: ${errs[0].error}`), "red", "error");  // keep the failure on screen
        polling = setTimeout(poll, 4000); return;
      }
      polling = setTimeout(poll, 450);
      return;
    }
  } catch (err) { setTicker(String(err.message || err), "red", "offline"); }
  polling = setTimeout(poll, 4000);
}

async function runCheck() {
  try {
    const { run_id } = await api("/api/check/run", { method: "POST" });
    activeRunId = run_id; lastEventSeq = 0; setTicker(S.running, "amber", "quick scan · nano"); poll();
  } catch (err) { setTicker(err.message, "red", "error"); }
}

async function editPage() {
  const deadline = $("newDeadline").value.trim();
  try {
    await api("/api/demo/edit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ deadline }) });
    setTicker(`Demo funder page now says ${deadline}. Open /demo/funder to see it. Run tonight's check next.`, "amber", "page edited");
  } catch (err) { setTicker(err.message, "red", "error"); }
}

async function resetDemo() {
  try { await api("/api/demo/reset", { method: "POST" }); selectedDonor = null; setTicker("Demo reset to the seed.", "", "idle"); await refresh(); }
  catch (err) { setTicker(err.message, "red", "error"); }
}

const ARCHIVE_PREFIX = "https://web.archive.org/web/";
const ALERT = (v) => v === "changed" || v === "superseded";

// Rows that explain the result come first: misses, false alarms, alarms Ultra cleared, next-cycle verdicts, then the rest.
function benchRank(row) {
  if (row.changed && row.verified !== false && !ALERT(row.final_verdict)) return 0;
  if (!row.changed && ALERT(row.final_verdict)) return 1;
  if (!row.changed && row.nano_verdict === "changed") return 2;
  if (row.final_verdict === "superseded") return 3;
  if (row.verified === false) return 4;
  return 5;
}

function benchNote(row) {
  const wrap = h("span");
  if (row.final_verdict === "error") wrap.append(h("span", "miss", "ERROR"));
  else if (row.no_donor) wrap.append(h("span", "miss", row.changed ? "MISSED: no usable excerpt" : "NOT COMPARED: no usable excerpt"));
  else if (row.changed && row.verified !== false && !ALERT(row.final_verdict)) wrap.append(h("span", "miss", "MISSED"));
  else if (row.changed && row.verified === false && !ALERT(row.final_verdict)) wrap.append(h("span", "miss", "MISSED (unverified, not in the headline)"));
  else if (!row.changed && ALERT(row.final_verdict)) wrap.append(h("span", "miss", "FALSE ALARM"));
  else if (!row.changed && row.nano_verdict === "changed") wrap.append("Quick scan alarm, deep review cleared it");
  else if (row.final_verdict === "superseded") wrap.append(h("span", "tag next", S.nextCycle));
  return wrap;
}

async function showBench() {
  const body = $("benchBody");
  body.replaceChildren(h("h3", "", S.benchTitle));
  try {
    const r = await api("/api/bench");
    const sum = r.summary, meta = r.meta || {};
    if (meta.subtitle) body.append(h("p", "sub", meta.subtitle));
    body.append(h("p", "sub", `Real changes found: quick scan alone ${sum.nano_recall}/${sum.changed_pairs}, with deep review ${sum.confirmed_recall}/${sum.changed_pairs} (same cycle changed: ${sum.verdict_changed}, ${S.nextCycle.toLowerCase()}: ${sum.verdict_superseded}). Controls compared: ${sum.controls_compared}/${sum.unchanged_pairs}; false alarms on them: quick scan alone ${sum.nano_false_alarms}, after deep review ${sum.false_alarms}. Cost $${sum.total_usd.toFixed(4)}. Misses are listed first.`));
    const wrap = h("div", "table-wrap"), t = h("table"), head = h("tr");
    for (const c of ["Page", "What changed", "Result", "Quick scan", "Changed", S.nextCycle, "Unchanged", "Snapshots"]) head.append(h("th", "", c));
    t.append(head);
    const rows = r.rows.map((row, i) => [row, i]).sort((a, b) => benchRank(a[0]) - benchRank(b[0]) || a[1] - b[1]).map((x) => x[0]);
    for (const row of rows) {
      const tr = h("tr");
      const ultraRan = row.nano_verdict === "changed";  // deep review only looks at quick-scan alarms
      const page = h("td", "", row.label);
      if (row.verified === false) page.append(h("span", "tag", "unverified"));
      if (row.scope) page.append(h("span", "tag", row.scope));
      if (row.listing_page) page.append(h("span", "tag", "listing-page control"));
      const what = h("td", "", row.what_changed || (row.changed ? `changed: ${row.field}` : "unchanged"));
      if (row.pair_note && row.pair_note !== row.what_changed) what.append(h("div", "fine", row.pair_note));
      tr.append(page, what, h("td"), h("td", "", row.nano_verdict),
        ...["changed", "superseded", "unchanged"].map((v) => h("td", "", row.final_verdict === "error" ? "error" : (!ultraRan ? "–" : (row.final_verdict === v ? "●" : "")))));
      tr.children[2].append(benchNote(row));
      const td = h("td");
      [["before", row.before_url], ["after", row.after_url]].forEach(([name, url], i) => {
        if (i) td.append(" → ");
        if (typeof url === "string" && url.startsWith(ARCHIVE_PREFIX)) {
          const a = h("a", "", name); a.href = url; a.target = "_blank"; a.rel = "noopener"; td.append(a);
        } else td.append(name);
      });
      tr.append(td); t.append(tr);
    }
    wrap.append(t); body.append(wrap);
    const note = h("div", "verdict-note");
    note.append(h("b", "", `${S.nextCycle}: `), `${S.nextCycleLine}`);
    if (meta.superseded_rule) note.append(h("div", "fine", `Rule: ${meta.superseded_rule}.`));
    body.append(note);
    if (meta.unverified_note && (sum.unverified || []).length) body.append(h("p", "fine", meta.unverified_note));
    if (meta.scope_note) body.append(h("p", "fine", meta.scope_note));
  } catch (err) { body.append(h("p", "", err.message)); }
  $("benchDialog").showModal();
}

async function replayRecorded() {
  replaying = true;
  try {
    const rec = await api("/api/recorded-run");
    setTicker(`Replaying a recorded run from ${rec.recorded_at.slice(0, 10)} (${rec.models.nano} + ${rec.models.ultra}). Read-only.`, "amber", "replay");
    for (const frame of rec.frames) {
      state = frame.state;
      const red = state.donors.find((d) => d.state === "changed");
      selectedDonor = red ? red.id : selectedDonor;
      render();
      if (frame.event) tickEvent(frame.event);
      await new Promise((r) => setTimeout(r, frame.hold_ms || 900));
    }
    setTicker(heroLine() || "Recorded run finished.", "red", "recorded run");  // stays on screen; reload returns to the live board
  } catch (err) { replaying = false; setTicker(err.message, "red", "error"); }
}

async function init() {
  $("tagline").textContent = S.tagline;
  for (const [id, key] of [["colDonors", "donors"], ["colProducts", "products"], ["colRecipients", "recipients"]]) {
    $(id).querySelector("h2").textContent = S.columns[key];
    $(id).querySelector(".hint").textContent = S.columnHints[key];
  }
  $("runBtn").textContent = S.runButton; $("resetBtn").textContent = S.resetButton; $("editBtn").textContent = S.editButton;
  $("replayBtn").textContent = S.replayButton; $("proofBtn").textContent = S.proofButton;
  $("themeBtn").addEventListener("click", toggleTheme); paintThemeButton();
  setTicker("", "", "idle");
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
  if (state.run && state.run.finished_at && !activeRunId) setTicker(heroLine() || (state.run.complete ? `Last check finished: ${state.donors.length} sources, ${(state.headline_run && state.headline_run.summary && state.headline_run.summary.changed) || 0} changed.` : friendly("The last check did not finish.")), heroLine() ? "red" : "", "last check");
  if (!state.run) setTicker(`Seed loaded. ${state.donors.length} sources, ${state.products.length} items built from them, ${state.recipients.length} people already told.`, "", "idle");
  if (state.running && state.run) activeRunId = state.run.id;
  poll();
}
init().catch((e) => setTicker(String(e.message || e), "red", "error"));

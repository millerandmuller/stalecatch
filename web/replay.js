// Replay ownership: a recorded replay drives the board only until Reset or Run takes it back.
// Each start() hands out a token; stop() invalidates every token handed out so far.
// A replay is read-only: "Approve correction" pressed during it is remembered here, never sent to the server,
// and forgotten when the replay starts again or the board is taken back.
function createReplay() {
  let token = 0, active = false, approved = new Set();
  return {
    start() { active = true; approved = new Set(); return ++token; },
    stop() { token += 1; active = false; approved = new Set(); },
    owns(t) { return t === token; },
    isActive() { return active; },
    approve(productId) { if (active) approved.add(productId); },
    isApproved(productId) { return active && approved.has(productId); },
  };
}

// Shows each frame while the token is still current. Returns true when every frame was shown.
async function playFrames(frames, replay, mine, show, wait) {
  for (const frame of frames) {
    if (!replay.owns(mine)) return false;
    show(frame);
    await wait(frame.hold_ms || 900);
  }
  return replay.owns(mine);
}

if (typeof module !== "undefined") module.exports = { createReplay, playFrames };

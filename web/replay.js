// Replay ownership: a recorded replay drives the board only until Reset or Run takes it back.
// Each start() hands out a token; stop() invalidates every token handed out so far.
function createReplay() {
  let token = 0, active = false;
  return {
    start() { active = true; return ++token; },
    stop() { token += 1; active = false; },
    owns(t) { return t === token; },
    isActive() { return active; },
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

// The voice journeys bench's player (docs/bench/voice-journeys.md), served beside the studio at
// /voice-test/harness.js on a second stack only. It replaces the microphone with the steps' recorded
// words, presses Talk once, plays each step (switching footage on the bridge first when a step says
// so), waits for the conversation to go quiet, and keeps when each step's words started and ended.
// The scorer reads everything else from the stack's own logs; this file decides nothing.
(() => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const state = { marks: [], log: [], done: false, error: null };
  let ctx = null, dest = null;

  function installMic() {
    if (ctx) return;
    ctx = new AudioContext({ sampleRate: 48000 });
    const hum = ctx.createBufferSource(), b = ctx.createBuffer(1, 48000, 48000), c = b.getChannelData(0);
    for (let i = 0; i < c.length; i++) c[i] = (Math.random() * 2 - 1) * 0.0005; // a quiet room, never digital silence
    hum.buffer = b; hum.loop = true; hum.start();
    // Each Talk press gets a fresh "microphone": stopping Talk ends the tracks it was given.
    navigator.mediaDevices.getUserMedia = async () => {
      dest = ctx.createMediaStreamDestination();
      hum.connect(dest);
      return dest.stream;
    };
  }

  async function play(url) {
    await ctx.resume();
    const speech = await ctx.decodeAudioData(await (await fetch(url, { cache: 'no-store' })).arrayBuffer());
    const s = ctx.createBufferSource(); s.buffer = speech; s.connect(dest);
    const ended = new Promise((r) => { s.onended = r; });
    const start = Date.now(); s.start(); await ended;
    return { start, end: Date.now() };
  }

  // The chat changing is the conversation still going; quiet for `quietMs` (or `maxMs` in all) ends a step.
  let lastChange = Date.now();
  new MutationObserver(() => { lastChange = Date.now(); })
    .observe(document.getElementById('log'), { childList: true, subtree: true, characterData: true });
  // At least `minMs` after the words, then until the chat has been quiet for `quietMs` (or `maxMs` in all).
  async function quiet(quietMs, maxMs, minMs = 9000) {
    const t0 = Date.now();
    const busy = () => Date.now() - t0 < minMs || Date.now() - Math.max(lastChange, t0) < quietMs;
    while (Date.now() - t0 < maxMs && busy()) await sleep(250);
  }

  async function bridge(base, path) {
    const r = await fetch(base + path, { method: 'POST' });
    return r.json().catch(() => ({}));
  }

  async function talk(on) {
    const button = document.getElementById('talk');
    const live = () => button.dataset.state === 'live';
    if (live() === on) return;
    button.click();
    for (let i = 0; i < 80 && live() !== on; i++) await sleep(250);
    if (live() !== on) throw new Error(`Talk did not turn ${on ? 'on' : 'off'}`);
  }

  async function typed(text) {
    const input = document.getElementById('question');
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('ask').requestSubmit();
    return Date.now();
  }

  window.__bench = {
    state,
    async run(plan, { bridge: base, quietMs = 6000, maxMs = 25000 } = {}) {
      Object.assign(state, { marks: [], log: [], done: false, error: null });
      try {
        installMic();
        await talk(true);
        await sleep(4000); // the session's first notes
        for (const step of plan.steps) {
          if (step.footage) {
            await bridge(base, `/feed?source=footage&footage=${encodeURIComponent(step.footage)}`);
            await sleep(2500);
            if (step.seek != null) await bridge(base, `/feed/seek?seconds=${step.seek}`);
            await bridge(base, `/feed/pause?paused=${step.pause ? 1 : 0}`);
            await sleep((step.settle ?? 6) * 1000); // the slide settles and the eyes look, as when someone moves to a field
          }
          if (step.kind === 'typed') {
            await talk(false);
            await sleep(2000);
            const at = await typed(step.say);
            state.marks.push({ id: step.id, kind: step.kind, say: step.say, start: at, end: at });
            await quiet(quietMs, maxMs);
            continue;
          }
          const { start, end } = await play(`/voice-test/${step.id}.wav`);
          state.marks.push({ id: step.id, kind: step.kind, say: step.say, start, end });
          await quiet(quietMs, (step.wait ?? maxMs / 1000) * 1000);
        }
        await talk(false);
      } catch (e) {
        state.error = String(e && e.message || e);
      }
      state.done = true;
      return state;
    },
  };
})();

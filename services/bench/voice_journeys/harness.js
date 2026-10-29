// The voice journeys bench's player (docs/bench/voice-journeys.md), served beside the studio at
// /voice-test/harness.js on a second stack only. drive.mjs loads it before the studio's own code (an init script),
// so it sees every socket from the first: it replaces the microphone with the steps' recorded words (or, acoustic,
// plays them through the speakers into the real microphone), holds the talk key in hold mode (Playwright's trusted
// keys when the driver lends them, else the Talk button held), plays each step, waits for the conversation to go
// quiet, and keeps what the page saw, each with its time (epoch ms):
//   audio   the voice audible or silent as heard here: the remote track's level and the element not muted
//   ws-in / ws-out  /ws/voice messages (types, ids, marks; no audio, no pictures)
//   frames  the live view on /ws each second: frames per second and their delay p50/p90 (MMF1 headers)
//   mark    each mark the studio drew (svg#voice-marks .voice-mark), its rect and its target's, and when it cleared
//   line    the voice's lines as they grow on /ws (when each part of a line was said, not only when it began)
//   researchers  the researchers board's rows when they change; key, fault, check, problem
// The scorer (standard.py) reads these with the stack's own logs; this file decides nothing.
(() => {
  if (window.__bench || window.top !== window) return; // the studio's own page, once (not its frames)
  const options = window.__benchOptions || {};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const state = { steps: [], events: [], done: false, error: null, paused: null, plan: null, voice: null, talk: null };
  const ev = (e) => { state.events.push({ t: Date.now(), ...e }); };

  // --- the voice socket, both ways ----------------------------------------------------------------------------------
  const KEEP_IN = new Set(['model', 'ready', 'interrupted', 'turn_complete', 'error', 'closed', 'tool_call', 'tool_result', 'answer']);
  const KEEP_TYPES = new Set(['talk', 'snapshot', 'snapshot-request', 'marks', 'marks-shown', 'marks-clear', 'bench', 'text', 'mute']);
  function brief(message) {
    const m = {};
    for (const key of ['type', 'kind', 'id', 'n', 'down', 'cancel', 'name', 'callId', 'how', 'until', 'fault', 'w', 'h', 'at'])
      if (message[key] !== undefined) m[key] = message[key];
    if (message.marks) m.marks = message.marks;
    if (message.resolved) m.resolved = message.resolved;
    if (message.data && typeof message.data === 'object') {
      for (const key of ['phase', 'talk', 'model', 'message', 'stage']) if (message.data[key] !== undefined) m[key] = message.data[key];
    }
    return m;
  }
  function wsIn(data) {
    let message; try { message = JSON.parse(data); } catch { return; }
    if (KEEP_TYPES.has(message.type) || KEEP_IN.has(message.kind)) ev({ e: 'ws-in', m: brief(message) });
  }
  function wsOut(data) {
    let message; try { message = JSON.parse(data); } catch { return; }
    if (KEEP_TYPES.has(message.type)) ev({ e: 'ws-out', m: brief(message) });
  }

  // --- the live view's frames on /ws (hub.py: "MMF1", a 4-byte length, the JSON header, the JPEG) ------------------
  let frameBin = { start: Date.now(), n: 0, delays: [] };
  function frame(data) {
    if (!(data instanceof ArrayBuffer) || data.byteLength < 8) return;
    const head = new Uint8Array(data, 0, 4);
    if (String.fromCharCode(...head) !== 'MMF1') return;
    const length = new DataView(data).getUint32(4);
    try {
      const header = JSON.parse(new TextDecoder().decode(new Uint8Array(data, 8, Math.min(length, data.byteLength - 8))));
      frameBin.n += 1;
      const taken = header.ba || header.t;
      if (typeof taken === 'number') frameBin.delays.push(Date.now() - taken * 1000);
    } catch { /* not a frame */ }
  }
  const quantile = (values, q) => { if (!values.length) return null; const v = [...values].sort((a, b) => a - b); return Math.round(v[Math.min(v.length - 1, Math.round(q * (v.length - 1)))]); };
  setInterval(() => {
    const now = Date.now(), seconds = (now - frameBin.start) / 1000;
    if (frameBin.n || seconds > 5) {
      ev({ e: 'frames', fps: Math.round((frameBin.n / seconds) * 10) / 10, delayP50: quantile(frameBin.delays, 0.5), delayP90: quantile(frameBin.delays, 0.9), n: frameBin.n });
      frameBin = { start: now, n: 0, delays: [] };
    }
  }, 1000);

  // The voice's lines as they grow on /ws (a line's journal record keeps only when it started): when each word came.
  const lineText = new Map();
  function chat(data) {
    if (data.length > 20000 || !data.includes('"lineId"')) return;
    let line; try { line = JSON.parse(data); } catch { return; }
    if (line.type !== 'chat' || line.role !== 'assistant' || !line.voice || !line.lineId) return;
    const text = String(line.text || '');
    if (lineText.get(line.lineId) === text) return;
    lineText.set(line.lineId, text);
    ev({ e: 'line', id: line.lineId, text });
  }

  const NativeWebSocket = window.WebSocket;
  class BenchWebSocket extends NativeWebSocket {
    constructor(url, protocols) {
      super(url, protocols);
      let path = '';
      try { path = new URL(url, location.href).pathname; } catch { /* keep '' */ }
      if (path === '/ws/voice') {
        voice.socket = this;
        this.addEventListener('message', (m) => { if (typeof m.data === 'string') wsIn(m.data); });
        const send = this.send.bind(this);
        this.send = (data) => { if (typeof data === 'string') wsOut(data); return send(data); };
      } else if (path === '/ws') {
        this.addEventListener('message', (m) => { if (typeof m.data === 'string') chat(m.data); else frame(m.data); });
      }
    }
  }
  window.WebSocket = BenchWebSocket;
  const voice = { socket: null };

  // --- what was heard: the remote track's level, and the element it plays in not muted -------------------------------
  const NativeAudio = window.Audio;
  const elements = [];
  function BenchAudio(...args) { const el = new NativeAudio(...args); elements.push(el); return el; }
  BenchAudio.prototype = NativeAudio.prototype;
  window.Audio = BenchAudio;
  let meter = null;
  const LEVEL = 0.012, HANGOVER_MS = 250;
  function tapRemote(track) {
    if (track.kind !== 'audio') return;
    meter = meter || new AudioContext();
    const analyser = meter.createAnalyser(); analyser.fftSize = 512;
    meter.createMediaStreamSource(new MediaStream([track])).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    let audible = false, lastLoud = 0;
    const element = () => elements.find((el) => el.srcObject && el.srcObject.getAudioTracks && el.srcObject.getAudioTracks().some((t) => t.id === track.id));
    const timer = setInterval(() => {
      if (track.readyState === 'ended') { if (audible) ev({ e: 'audio', on: false, why: 'ended' }); audible = false; clearInterval(timer); return; }
      analyser.getFloatTimeDomainData(samples);
      let peak = 0; for (const s of samples) peak = Math.max(peak, Math.abs(s));
      const el = element();
      const muted = !!el && (el.muted || el.volume === 0 || !el.srcObject);
      const now = Date.now();
      if (peak >= LEVEL && !muted) lastLoud = now;
      if (!audible && peak >= LEVEL && !muted) { audible = true; ev({ e: 'audio', on: true, level: Math.round(peak * 1000) / 1000 }); }
      else if (audible && muted) { audible = false; ev({ e: 'audio', on: false, why: 'muted' }); }
      else if (audible && now - lastLoud > HANGOVER_MS) { audible = false; state.events.push({ t: lastLoud, e: 'audio', on: false, why: 'quiet' }); }
    }, 10);
  }
  const NativePeer = window.RTCPeerConnection;
  if (NativePeer) {
    window.RTCPeerConnection = class extends NativePeer {
      constructor(...args) { super(...args); this.addEventListener('track', (event) => tapRemote(event.track)); }
    };
  }
  const audibleNow = () => {
    const last = [...state.events].reverse().find((e) => e.e === 'audio');
    return !!(last && last.on);
  };

  // --- the microphone: the recorded words injected, or the real one (acoustic) --------------------------------------
  let ctx = null, dest = null;
  function installMic() {
    if (options.acoustic || ctx) return;
    ctx = new AudioContext({ sampleRate: 48000 });
    const hum = ctx.createBufferSource(), b = ctx.createBuffer(1, 48000, 48000), c = b.getChannelData(0);
    for (let i = 0; i < c.length; i++) c[i] = (Math.random() * 2 - 1) * 0.0005; // a quiet room, never digital silence
    hum.buffer = b; hum.loop = true; hum.start();
    // Each Talk press gets a fresh "microphone": stopping Talk ends the tracks it was given.
    navigator.mediaDevices.getUserMedia = async () => { dest = ctx.createMediaStreamDestination(); hum.connect(dest); return dest.stream; };
  }
  installMic();

  async function play(url) {
    if (options.acoustic) { // through this Mac's speakers, into its microphone (needs both, and the studio in front)
      const a = new NativeAudio(url);
      const ended = new Promise((r) => { a.onended = r; a.onerror = r; });
      await a.play();
      const start = Date.now(); await ended;
      return { start, end: Date.now() };
    }
    await ctx.resume();
    const speech = await ctx.decodeAudioData(await (await fetch(url, { cache: 'no-store' })).arrayBuffer());
    const s = ctx.createBufferSource(); s.buffer = speech; s.connect(dest);
    const ended = new Promise((r) => { s.onended = r; });
    const start = Date.now(); s.start(); await ended;
    return { start, end: Date.now() };
  }

  // --- marks and the researchers board, sampled -------------------------------------------------------------------
  function targetRect(target) {
    const [kind, ...rest] = String(target).split(':'); const id = rest.join(':');
    let el = null;
    if (kind === 'tab') el = document.getElementById(`work-tab-${id}`) || [...document.querySelectorAll('[id^="work-tab-"]')].find((b) => (b.textContent || '').trim() === id);
    else if (kind === 'researcher') el = document.querySelector(`.researchers-board-row[data-rid="${CSS.escape(id)}"]`) || [...document.querySelectorAll('.researchers-board-row')].find((r) => new RegExp(`\\b(?:Researcher|Analyst) ${id}\\b`).test(r.textContent || ''));
    else if (kind === 'window') el = document.querySelector(`section.workspace-window[data-presentation-id="${CSS.escape(id)}"]`);
    else if (kind === 'document') el = document.querySelector(`#study-artifacts > [data-placement-id="${CSS.escape(id)}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
  }
  const shown = new Map();
  setInterval(() => {
    const now = Date.now(), seen = new Set();
    for (const g of document.querySelectorAll('svg#voice-marks .voice-mark')) {
      const key = `${g.dataset.markId}|${g.dataset.target}`; seen.add(key);
      const last = shown.get(key);
      if (last && now - last < 500) continue; // the first sighting, then a sample every half second
      shown.set(key, now);
      const rect = (g.dataset.rect || '').split(',').map(Number);
      ev({ e: 'mark', on: true, id: g.dataset.markId, target: g.dataset.target, style: g.dataset.style,
           rect: rect.length === 4 && rect.every(Number.isFinite) ? rect : null, targetRect: targetRect(g.dataset.target) });
    }
    for (const key of [...shown.keys()]) {
      if (seen.has(key)) continue;
      shown.delete(key);
      const [id, target] = key.split('|');
      ev({ e: 'mark', on: false, id, target });
    }
  }, 40);
  let board = '';
  function researchers() {
    return [...document.querySelectorAll('.researchers-board-row')].map((r) => {
      const m = /\b(?:Researcher|Analyst) (\d+)\b/.exec(r.textContent || '');
      return { rid: r.dataset.rid, group: r.dataset.group, state: r.dataset.state, number: m ? Number(m[1]) : null };
    });
  }
  setInterval(() => {
    const rows = researchers(), key = JSON.stringify(rows);
    if (key !== board) { board = key; ev({ e: 'researchers', rows }); }
  }, 1000);

  // --- the conversation going quiet -----------------------------------------------------------------------------------
  let lastChange = Date.now(), observing = false;
  function observeChat() {
    const log = document.getElementById('log');
    if (observing || !log) return;
    observing = true;
    new MutationObserver(() => { lastChange = Date.now(); }).observe(log, { childList: true, subtree: true, characterData: true });
  }
  const lastAudio = () => { const a = [...state.events].reverse().find((e) => e.e === 'audio'); return a ? (a.on ? Date.now() : a.t) : 0; };
  // At least `minMs` after the words, then until the chat and the voice have been quiet for `quietMs` (or `maxMs` in all).
  async function quiet(quietMs, maxMs, minMs) {
    const t0 = Date.now();
    const busy = () => Date.now() - t0 < minMs || Date.now() - Math.max(lastChange, lastAudio(), t0) < quietMs;
    while (Date.now() - t0 < maxMs && busy()) await sleep(200);
  }
  // Until the voice has been audible for `afterMs` (an interruption comes in the middle of an answer), at most `maxMs`.
  async function speaking(afterMs, maxMs) {
    const t0 = Date.now();
    while (Date.now() - t0 < maxMs) {
      const on = [...state.events].reverse().find((e) => e.e === 'audio');
      if (on && on.on && Date.now() - on.t >= afterMs) return true;
      await sleep(20);
    }
    return false;
  }

  async function bridge(base, path) {
    const r = await fetch(base + path, { method: 'POST' });
    return r.json().catch(() => ({}));
  }

  const talkButton = () => document.getElementById('talk');
  async function connect(on) {
    const button = talkButton();
    const live = () => button.dataset.state === 'live';
    if (live() === on) return;
    if (on) button.click();
    else (document.querySelector('.voice-session-controls .voice-session-end') || button).click();
    for (let i = 0; i < 120 && live() !== on; i++) await sleep(250);
    if (live() !== on) throw new Error(`Talk did not turn ${on ? 'on' : 'off'}`);
  }

  // The talk key: Playwright's trusted Control+Option when the driver lends it, else the Talk button held.
  async function hold(down) {
    if (typeof window.__benchKey === 'function') {
      await window.__benchKey(down);
      ev({ e: 'key', down, via: 'keys' });
      return Date.now();
    }
    const button = talkButton(), type = down ? 'pointerdown' : 'pointerup';
    button.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, isPrimary: true, button: 0, buttons: down ? 1 : 0 }));
    ev({ e: 'key', down, via: 'button' });
    return Date.now();
  }

  async function typed(text) {
    const input = document.getElementById('question');
    input.focus();
    if ('value' in input) input.value = text; else input.textContent = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('ask').requestSubmit();
    return Date.now();
  }

  // What a step waits for besides quiet: a researcher done ("done": any finished; "latest-done": the newest one).
  function satisfied(waitFor, before) {
    if (!waitFor) return false;
    const rows = researchers();
    if (waitFor.researcher === 'done') return rows.some((r) => r.group === 'done' && !before.has(r.rid));
    if (waitFor.researcher === 'latest-done') {
      const newest = rows.filter((r) => r.number != null).sort((a, b) => b.number - a.number)[0];
      return !!newest && newest.group === 'done';
    }
    return false;
  }

  // After the restart: what must still be there (the Project, its Tab, the researcher's output).
  async function checks(step) {
    for (const check of step.checks || []) {
      const [kind, ...rest] = check.split(':'); const value = rest.join(':');
      let ok = false, detail = '';
      for (let i = 0; i < 40 && !ok; i++) {
        if (kind === 'project') {
          const listing = await (await fetch('/api/sessions', { cache: 'no-store' })).json().catch(() => ({}));
          const rows = listing.sessions || [];
          const current = rows.find((s) => s.id === (listing.current?.id ?? listing.current)) || listing.current || {};
          detail = `open: ${current.name ?? current.title ?? JSON.stringify(current).slice(0, 80)}`;
          ok = String(current.name ?? current.title ?? '').includes(value);
        } else if (kind === 'tab') {
          const tabs = [...document.querySelectorAll('[id^="work-tab-"]')].map((b) => (b.textContent || '').trim());
          detail = `tabs: ${tabs.join(', ')}`;
          ok = tabs.some((t) => t.includes(value));
        } else if (kind === 'researcher') {
          const rows = researchers(), latest = state.lastResearcher;
          detail = `rows: ${rows.map((r) => r.rid).join(', ')}; expected ${latest}`;
          ok = latest ? rows.some((r) => r.rid === latest) : rows.length > 0;
        }
        if (!ok) await sleep(500);
      }
      ev({ e: 'check', step: step.id, name: check, ok, detail: detail.slice(0, 300) });
    }
  }

  window.__bench = {
    state,
    async run(plan, { bridge: base = '', quietMs = plan.quietMs ?? 6000, maxMs = 25000, minMs = plan.minMs ?? 9000, from = 0, carry = null,
      mode = 'open', setup = false, baselineMs = 10000 } = {}) {
      if (carry) Object.assign(state, { steps: carry.steps || [], events: [...(carry.events || []), ...state.events], lastResearcher: carry.lastResearcher });
      else if (from === 0) Object.assign(state, { steps: [], events: state.events.filter((e) => e.e === 'frames') });
      Object.assign(state, { done: false, error: null, paused: null, plan: plan.id, talk: mode, acoustic: !!options.acoustic });
      state.started = state.started || Date.now();
      const steps = setup ? plan.setup || [] : plan.steps;
      try {
        observeChat();
        document.querySelector('.example-skip')?.click(); // a fresh home's walkthrough steps aside
        if (from === 0 && !setup) await sleep(baselineMs); // the live view before Talk: the baseline
        if (from > 0 && steps[from - 1]?.kind === 'restart') await checks(steps[from - 1]);
        const spokenAhead = steps.slice(from).some((s) => !['typed', 'idle', 'restart'].includes(s.kind));
        if (spokenAhead) { await connect(true); await sleep(3000); }
        for (let i = from; i < steps.length; i++) {
          const step = steps[i], next = steps[i + 1];
          if (step.footage) {
            await bridge(base, `/feed?source=footage&footage=${encodeURIComponent(step.footage)}`);
            await sleep(2500);
            if (step.seek != null) await bridge(base, `/feed/seek?seconds=${step.seek}`);
            await bridge(base, `/feed/pause?paused=${step.pause ? 1 : 0}`);
            await sleep((step.settle ?? 6) * 1000);
          }
          if (step.fault) { // only a second stack started with faults on answers it
            voice.socket?.send(JSON.stringify({ type: 'bench', fault: step.fault }));
            ev({ e: 'fault', fault: step.fault, step: step.id });
            await sleep(4000);
          }
          if (step.kind === 'restart') {
            const at = Date.now();
            state.steps.push({ id: step.id, kind: step.kind, say: '', start: at, end: at, restart: true });
            state.lastResearcher = (researchers().filter((r) => r.number != null).sort((a, b) => b.number - a.number)[0] || {}).rid;
            state.paused = { restart: true, next: i + 1 };
            return JSON.parse(JSON.stringify(state));
          }
          if (step.kind === 'idle') {
            const at = Date.now(), before = new Set(researchers().map((r) => r.rid));
            const limit = (step.wait ?? 120) * 1000;
            while (Date.now() - at < limit && !satisfied(step.waitFor, before)) await sleep(500);
            await quiet(quietMs, 30000, 8000); // the event's one line, if it is told
            state.steps.push({ id: step.id, kind: step.kind, say: '', start: at, end: Date.now(), idle: true });
            continue;
          }
          if (step.kind === 'typed') {
            // Asked in the chat after the talking (the demo's memory question); setup is typed before Talk.
            if (!setup && talkButton()?.dataset.state === 'live') { await connect(false); await sleep(2000); }
            const before = new Set(researchers().map((r) => r.rid));
            const at = await typed(step.say);
            state.steps.push({ id: step.id, kind: step.kind, say: step.say, start: at, end: at, setup });
            const limit = (step.wait ?? maxMs / 1000) * 1000;
            if (step.waitFor) { while (Date.now() - at < limit && !satisfied(step.waitFor, before)) await sleep(1000); }
            await quiet(quietMs, step.waitFor ? 30000 : limit, Math.min(minMs, limit));
            continue;
          }
          const record = { id: step.id, kind: step.kind, say: step.say };
          if (step.interrupt) record.interrupt = true;
          if (mode === 'hold') {
            record.down = await hold(true);
            await sleep(150);
          }
          const { start, end } = await play(`/voice-test/${plan.id}/${step.id}.wav`);
          Object.assign(record, { start, end });
          if (mode === 'hold') { await sleep(200); record.up = await hold(false); }
          state.steps.push(record);
          if (step.kind === 'restart') continue;
          if (next && next.interrupt) { // the next words come in the middle of this answer
            await speaking(next.interrupt.afterMs ?? 2000, 20000);
            continue;
          }
          await quiet(quietMs, (step.wait ?? maxMs / 1000) * 1000, minMs);
        }
        if (!setup && spokenAhead) await connect(false).catch(() => {});
      } catch (e) {
        state.error = String(e && e.message || e);
      }
      state.ended = Date.now();
      state.done = true;
      return JSON.parse(JSON.stringify(state));
    },
  };
})();

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
//   takeover  the person's mouse or Escape while the partner acted (a plan step's "takeover")
//   staged  the bench put the specimen back on an empty stage (Use live camera, then the bridge plays the still)
//   researchers  the Project's researchers from the orchestrator (/api/research; the board only when that fails),
//           when they change; key, fault, check, recovered (the bench put the journey back where it belongs)
//   frames also say whether the stage had a source ("source") or showed the empty stage ("empty": nothing to view)
// The scorer (standard.py) reads these with the stack's own logs; this file decides nothing.
(() => {
  if (window.__bench || window.top !== window) return; // the studio's own page, once (not its frames)
  const options = window.__benchOptions || {};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const state = { steps: [], events: [], done: false, error: null, paused: null, plan: null, voice: null, talk: null };
  const ev = (e) => { state.events.push({ t: Date.now(), ...e }); };

  // --- the voice socket, both ways ----------------------------------------------------------------------------------
  const KEEP_IN = new Set(['model', 'ready', 'interrupted', 'turn_complete', 'error', 'closed', 'tool_call', 'tool_result', 'answer']);
  const KEEP_TYPES = new Set(['talk', 'snapshot', 'snapshot-request', 'marks', 'marks-shown', 'marks-clear', 'bench', 'text', 'mute',
    'voice-phase', 'offer', 'reoffer', 'control', 'drive', 'drive-shown', 'drive-end']);
  function brief(message) {
    const m = {};
    for (const key of ['type', 'kind', 'id', 'n', 'down', 'cancel', 'name', 'callId', 'how', 'until', 'fault', 'w', 'h', 'at', 'phase',
      'version', 'owner', 'ok', 'why', 'outcome', 'intent'])
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
  // The empty stage ("What will you look at?", session-home.ts) shows when the Project has nothing on its stage: no
  // frames then is no live view to measure, not a slow one.
  const stage = () => { const home = document.querySelector('.session-home'); return home && !home.hidden && home.getClientRects().length ? 'empty' : 'source'; };
  setInterval(() => {
    const now = Date.now(), seconds = (now - frameBin.start) / 1000;
    ev({ e: 'frames', fps: Math.round((frameBin.n / seconds) * 10) / 10, delayP50: quantile(frameBin.delays, 0.5),
         delayP90: quantile(frameBin.delays, 0.9), n: frameBin.n, stage: document.body ? stage() : 'source' });
    frameBin = { start: now, n: 0, delays: [] };
  }, 1000);

  // The voice's lines as they grow on /ws (a line's journal record keeps only when it started): when each word came.
  const lineText = new Map();
  // The app's settings as the studios are sent them: a change of setting (set_default, use) is the app's own record of it.
  let settingsSeen = null;
  function settings(data) {
    let message; try { message = JSON.parse(data); } catch { return; }
    const now = message && typeof message.settings === 'object' && message.settings ? message.settings : null;
    if (!now) return;
    const changed = Object.fromEntries(Object.entries(now).filter(([k, v]) => !settingsSeen || JSON.stringify(settingsSeen[k]) !== JSON.stringify(v)));
    if (settingsSeen && !Object.keys(changed).length) return;
    ev({ e: 'settings', first: !settingsSeen, changed: Object.keys(changed) });
    settingsSeen = now;
  }

  function chat(data) {
    if (data.length < 200000 && data.includes('"settings"')) settings(data);
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
        main.socket = this;
        this.addEventListener('message', (m) => { if (typeof m.data === 'string') chat(m.data); else frame(m.data); });
      }
    }
  }
  window.WebSocket = BenchWebSocket;
  const voice = { socket: null }, main = { socket: null };

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
  // A mark's target on the page, found the way the studio finds it (marks.ts tabPlace, researcherPlace).
  function targetRect(target) {
    const [kind, ...rest] = String(target).split(':'); const id = rest.join(':');
    let el = null;
    if (kind === 'tab') {
      el = document.querySelector(`[id="work-tab-${CSS.escape(id)}"]`);
      if (!el) {
        const tabs = [...document.querySelectorAll('[data-workspace-view-tab]')].map((t) => ({ t, title: (t.querySelector('.workspace-tab-label')?.textContent ?? t.textContent ?? '').trim() }));
        let found = tabs.filter((x) => x.title === id.trim());
        if (!found.length) found = tabs.filter((x) => x.title.toLowerCase() === id.trim().toLowerCase());
        el = found.length === 1 ? found[0].t : null;
      }
    } else if (kind === 'researcher') {
      const rids = /^\d+$/.test(id) ? apiRows.filter((r) => r.number === Number(id)).map((r) => r.rid) : [id];
      for (const rid of rids) {
        for (const css of ['.researchers-board-row', '.researcher-card', '.chat-rcard']) {
          const found = document.querySelector(`${css}[data-rid="${CSS.escape(rid)}"]`);
          if (found && found.getClientRects().length) { el = found; break; }
        }
        if (el) break;
      }
    } else if (kind === 'window') el = document.querySelector(`section.workspace-window[data-presentation-id="${CSS.escape(id)}"]`);
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
  // The Project's researchers, from the orchestrator (/api/research: the open Project's, and those still working
  // elsewhere); the board's rows only when that does not answer (the board may not be drawn).
  const ACTIVE = new Set(['queued', 'starting', 'running', 'revising']);
  let apiRows = [], apiAnswered = false, board = '';
  function boardRows() {
    return [...document.querySelectorAll('.researchers-board-row')].map((r) => {
      const m = /\b(?:Researcher|Analyst) (\d+)\b/.exec(r.textContent || '');
      return { rid: r.dataset.rid, group: r.dataset.group, state: r.dataset.state, number: m ? Number(m[1]) : null, source: 'board' };
    });
  }
  const researchers = () => (apiAnswered ? apiRows : boardRows());
  async function pollResearchers() {
    try {
      const listing = await (await fetch('/api/research', { cache: 'no-store' })).json();
      apiRows = (listing.sessions || []).filter((s) => s.purpose === 'research' || s.n).map((s) => ({
        rid: s.rid, day: s.day, number: s.n || null, status: s.status, group: ACTIVE.has(s.status) ? 'working' : 'done',
        sitting: s.sitting, created: s.created, started: s.started, finished: s.finished, notes: s.notes, source: 'api' }));
      apiAnswered = true;
    } catch { apiAnswered = false; }
    const rows = researchers(), key = JSON.stringify(rows);
    if (key !== board) { board = key; ev({ e: 'researchers', rows }); }
  }
  setInterval(() => { void pollResearchers(); }, 1500);
  const newest = () => researchers().filter((r) => r.number != null).sort((a, b) => b.number - a.number)[0];

  // The open Project, from the orchestrator (/api/sessions).
  async function openProject() {
    const listing = await (await fetch('/api/sessions', { cache: 'no-store' })).json().catch(() => ({}));
    const id = listing.current?.id ?? listing.current;
    const row = (listing.sessions || []).find((s) => s.id === id) || {};
    return { id, name: String(row.name ?? row.label ?? ''), rows: listing.sessions || [] };
  }

  // --- the conversation going quiet -----------------------------------------------------------------------------------
  let lastChange = Date.now(), observing = false;
  function observeChat() {
    const log = document.getElementById('log');
    if (observing || !log) return;
    observing = true;
    new MutationObserver(() => { lastChange = Date.now(); }).observe(log, { childList: true, subtree: true, characterData: true });
  }
  const lastAudio = () => { const a = [...state.events].reverse().find((e) => e.e === 'audio'); return a ? (a.on ? Date.now() : a.t) : 0; };
  // At least `minMs` after the words, then until the chat and the voice have been quiet for `quietMs` (or `maxMs` in all),
  // and no tool the voice called is still running (Talk's working dot): a voice that waits quietly on a long tool is
  // not done (the smoke run of 29 Oct ended a step 4 s into Cellpose, and the stack's teardown stopped its worker).
  // A hand-off's row saying "Hooke is working" is work under way too, as the user sees it: they wait for its outcome
  // before the next words (will-workspace, 29 Oct: Cellpose's outcome landed in the next step), for at most HANDOFF_MS.
  const HANDOFF_MS = 120000;
  async function quiet(quietMs, maxMs, minMs) {
    const t0 = Date.now();
    const handing = () => !!document.querySelector('[data-role="handoff"][data-state="working"]');
    const working = () => document.getElementById('talk')?.dataset.working !== undefined || handing();
    const busy = () => Date.now() - t0 < minMs || working() || Date.now() - Math.max(lastChange, lastAudio(), t0) < quietMs;
    while (Date.now() - t0 < (handing() ? Math.max(maxMs, HANDOFF_MS) : maxMs) && busy()) await sleep(200);
  }
  // Until the voice has been audible for `afterMs` (an interruption comes in the middle of an answer), at most `maxMs`.
  // Until the answer has been under way for `afterMs` from its first audio after `since` (the request's release; a
  // preamble counts as its start) and is audible now: an interruption comes into the answer itself, not its first
  // word (think-broad-2: 2 s in cut the answer before it began). At most `maxMs`.
  async function speaking(afterMs, maxMs, since = 0) {
    const t0 = Date.now();
    while (Date.now() - t0 < maxMs) {
      const first = state.events.find((e) => e.e === 'audio' && e.on && e.t >= since);
      const last = [...state.events].reverse().find((e) => e.e === 'audio');
      if (first && last && last.on && Date.now() - first.t >= afterMs) return true;
      await sleep(20);
    }
    return false;
  }

  async function bridge(base, path) {
    const r = await fetch(base + path, { method: 'POST' });
    return r.json().catch(() => ({}));
  }

  // The specimen on the stage (the plan's camera: the still played as test footage, stack.py noisy_loop): a Project
  // opens on the empty stage (a new one, or one reopened: "the live view starts fresh"), where a person clicks Use live
  // camera; the bridge then plays the specimen. Done in the setup, and again whenever a step leaves the journey on the
  // empty stage, each time recorded ("staged"), never silently.
  async function stageSpecimen(plan, base, stepId, always = false) {
    const footage = plan.camera?.footage;
    if (!footage || !base || (!always && stage() !== 'empty')) return null;
    const live = document.querySelector('button.home-go-live');
    if (live && live.getClientRects().length) { live.click(); await sleep(2500); }
    await bridge(base, `/feed?source=footage&footage=${encodeURIComponent(footage)}`).catch(() => ({}));
    const at = Date.now();
    let ok = false;
    while (Date.now() - at < 12000 && !ok) {
      await sleep(500);
      const last = [...state.events].reverse().find((e) => e.e === 'frames');
      ok = !!last && last.n > 0 && last.stage !== 'empty';
    }
    ev({ e: 'staged', step: stepId, ok, footage });
    return ok;
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
    if (waitFor.researcher === 'latest-done') { const last = newest(); return !!last && last.group === 'done'; }
    if (waitFor.frames) { const last = [...state.events].reverse().find((e) => e.e === 'frames'); return !!last && last.n > 0 && last.stage !== 'empty'; }
    return false;
  }

  // The journey put back where its plan says it is ("project:Nuclei") when a step left it elsewhere: the step that
  // failed is scored as it was, and the steps after it are not scored again for that one failure.
  async function ensure(step) {
    const [kind, ...rest] = String(step.ensure).split(':'); const name = rest.join(':');
    if (kind !== 'project') return;
    const open = await openProject();
    if (open.name === name) return;
    const want = open.rows.filter((s) => String(s.name ?? s.label ?? '') === name).sort((a, b) => (b.lastUsed ?? b.started ?? 0) - (a.lastUsed ?? a.started ?? 0))[0];
    if (!want || !main.socket || main.socket.readyState !== WebSocket.OPEN) {
      ev({ e: 'recovered', step: step.id, what: step.ensure, ok: false, from: open.name, detail: want ? 'no studio connection' : 'no such Project' });
      return;
    }
    main.socket.send(JSON.stringify({ type: 'session', action: 'switch', id: want.id })); // the sessions rail's own message
    let now = open;
    for (let i = 0; i < 40 && now.id !== want.id; i++) { await sleep(500); now = await openProject(); }
    ev({ e: 'recovered', step: step.id, what: step.ensure, ok: now.id === want.id, from: open.name });
    await sleep(4000);
  }

  // Before the restart: what is there, so the checks after it ask only that it is still there (a Tab or a researcher
  // an earlier step failed to make is that step's failure, never the restart's).
  async function beforeRestart() {
    const open = await openProject();
    const tabs = [...document.querySelectorAll('[data-workspace-view-tab], [id^="work-tab-"]')].map((b) => (b.querySelector?.('.workspace-tab-label')?.textContent ?? b.textContent ?? '').trim());
    const last = newest();
    return { project: { id: open.id, name: open.name }, tabs: [...new Set(tabs)], researcher: last ? { rid: last.rid, day: last.day, number: last.number } : null };
  }

  // After the restart: what was there before is still there (the Project open, its Tab, the researcher's work).
  // ok null: it was not there before the restart, so there is nothing to keep.
  async function checks(step) {
    const before = state.beforeRestart || {};
    for (const check of step.checks || []) {
      const [kind, ...rest] = check.split(':'); const value = rest.join(':');
      let ok = false, detail = '';
      for (let i = 0; i < 40 && !ok; i++) {
        if (kind === 'project') {
          const open = await openProject();
          detail = `open: ${open.name}; before the restart: ${before.project?.name ?? '?'}`;
          ok = before.project?.id ? open.id === before.project.id : open.name.includes(value);
        } else if (kind === 'tab') {
          if (before.tabs && !before.tabs.some((t) => t.includes(value))) { ok = null; detail = `no Tab ${value} before the restart`; break; }
          const tabs = [...document.querySelectorAll('[data-workspace-view-tab], [id^="work-tab-"]')].map((b) => (b.querySelector?.('.workspace-tab-label')?.textContent ?? b.textContent ?? '').trim());
          detail = `tabs: ${[...new Set(tabs)].join(', ')}`;
          ok = tabs.some((t) => t.includes(value));
        } else if (kind === 'researcher') {
          const last = before.researcher;
          if (!last) { ok = null; detail = 'no researcher before the restart'; break; }
          const r = await fetch(`/api/research/${encodeURIComponent(last.rid)}?day=${encodeURIComponent(last.day || '')}`, { cache: 'no-store' }).catch(() => null);
          detail = `Researcher ${last.number} (${last.rid}): ${r ? r.status : 'no answer'}`;
          ok = !!r && r.ok;
        }
        if (ok === false) await sleep(500);
      }
      ev({ e: 'check', step: step.id, name: check, ok, detail: detail.slice(0, 300) });
    }
  }

  window.__bench = {
    state,
    async run(plan, { bridge: base = '', quietMs = plan.quietMs ?? 6000, maxMs = 25000, minMs = plan.minMs ?? 9000, from = 0, carry = null,
      mode = 'open', setup = false, baselineMs = 10000 } = {}) {
      if (carry) Object.assign(state, { steps: carry.steps || [], events: [...(carry.events || []), ...state.events], beforeRestart: carry.beforeRestart });
      else if (from === 0) Object.assign(state, { steps: [], events: state.events.filter((e) => e.e === 'frames') });
      Object.assign(state, { done: false, error: null, paused: null, plan: plan.id, talk: mode, acoustic: !!options.acoustic });
      state.started = state.started || Date.now();
      const steps = setup ? plan.setup || [] : plan.steps;
      try {
        observeChat();
        document.querySelector('.example-skip')?.click(); // a fresh home's walkthrough steps aside
        if (!setup) await stageSpecimen(plan, base, 'start'); // the journey starts with the specimen on the stage
        if (from === 0 && !setup) await sleep(baselineMs); // the live view before Talk: the baseline
        if (from > 0 && steps[from - 1]?.kind === 'restart') await checks(steps[from - 1]);
        const spokenAhead = steps.slice(from).some((s) => !['typed', 'idle', 'restart', 'stage'].includes(s.kind));
        // Connected, it may talk first (its opening line, one-brain voice): the first step waits until it is quiet,
        // so its first press never cuts that line and the line is never taken for the step's answer.
        if (spokenAhead) { await connect(true); await sleep(3000); await quiet(2500, 20000, 0); }
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
            state.beforeRestart = await beforeRestart();
            state.paused = { restart: true, next: i + 1 };
            return JSON.parse(JSON.stringify(state));
          }
          if (step.kind === 'idle') {
            const at = Date.now(), before = new Set(researchers().map((r) => r.rid));
            const limit = (step.wait ?? 120) * 1000;
            while (Date.now() - at < limit && !satisfied(step.waitFor, before)) await sleep(500);
            await quiet(quietMs, 30000, 8000); // the event's one line, if it is told
            state.steps.push({ id: step.id, kind: step.kind, say: '', start: at, end: Date.now(), idle: true });
            if (step.ensure) await ensure(step);
            await stageSpecimen(plan, base, step.id);
            continue;
          }
          if (step.kind === 'stage') { // the specimen on a new Project's stage, as a person opens it (setup)
            const at = Date.now();
            const ok = await stageSpecimen(plan, base, step.id, true);
            state.steps.push({ id: step.id, kind: step.kind, say: '', start: at, end: Date.now(), setup, staged: ok });
            if (step.ensure) await ensure(step);
            await stageSpecimen(plan, base, step.id);
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
            if (step.ensure) await ensure(step);
            await stageSpecimen(plan, base, step.id);
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
          if (step.takeover) { // the person takes over while the partner acts: at its first cue (a drive) after release
            const from = Date.now();
            let cue = null;
            while (Date.now() - from < (step.takeover.waitMs ?? 20000) && !cue) {
              cue = state.events.find((e) => e.e === 'ws-in' && (e.m || {}).type === 'drive' && e.t >= (record.up ?? record.end));
              if (!cue) await sleep(20);
            }
            if (!cue) ev({ e: 'takeover', step: step.id, how: step.takeover.how, skipped: 'no cue to take over from' });
            else {
              await sleep(step.takeover.afterMs ?? 100);
              const how = step.takeover.how || 'mouse';
              let at;
              if (typeof window.__benchTakeover === 'function') at = await window.__benchTakeover(how);
              else {
                if (how === 'escape') window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                else window.dispatchEvent(new MouseEvent('mousemove', { clientX: 760, clientY: 490, bubbles: true }));
                at = Date.now();
              }
              ev({ e: 'takeover', step: step.id, how, at, cue: (cue.m || {}).id });
            }
          }
          if (next && next.interrupt) { // the next words come in the middle of this answer
            await speaking(next.interrupt.afterMs ?? 4500, 30000, record.up ?? record.end);
            continue;
          }
          await quiet(quietMs, (step.wait ?? maxMs / 1000) * 1000, minMs);
          if (step.ensure) await ensure(step);
          await stageSpecimen(plan, base, step.id);
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

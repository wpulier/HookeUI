// The camera page (served by the camera bridge at /camera/): get the picture live and keep it so, with nothing else
// of HookeUI running. The picture leads; beside it, what is true now and the one thing to do; under it, the
// phone's own controls. The same checklist and controls are the studio's Phone panel (checklist.ts, phone-controls.ts).

import './camera.css';
import { BridgeApi, type CameraDevice, type Doctor, type Health, type PhoneCheckState, type PhoneState, type Trial } from './bridge-api';
import { Checklist } from './checklist';
import { el } from './dom';
import { PhoneControls } from './phone-controls';
import { SOURCES, SOURCE_NOTES, dotState, headline, setupSteps, smoothness, tab } from './words';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const api = new BridgeApi(() => ''); // the bridge's own page: same origin

let health: Health | null = null;
let doctor: Doctor | null = null;
let phone: PhoneState | null = null;
let switchingTo: string | null = null;
let liveShown = false;
let cadence: { ago: number[]; windowS: number } = { ago: [], windowS: 5 };
let trials: Trial[] = [];
// Other camera: the picker is open when it was chosen with no camera yet, or the user asked to change it.
let pickingOther = location.hash === '#other';
let cameras: CameraDevice[] | null = null;
let camerasShown = ''; // the list as last drawn: redrawn only when it changes, so a click never lands on a replaced button

const say = (text: string, problem = false) => {
  const line = $('checkState');
  line.textContent = text;
  line.dataset.problem = String(problem);
};

const checklist = new Checklist(api, {
  openPhoneCheck: () => openSheet('phoneCheck'),
  onChanged: () => void refresh(),
  onError: (text) => say(text, true),
  onNote: (text) => say(text),
  // What the page explains itself: the setup card the Blackmagic steps, the picker the other camera's choice.
  covered: (d) => (setupSteps(d).show ? ['app', 'settings', 'quality'] : tab(d.source) === 'other' ? ['other-chosen'] : []),
  guiding: (d) => setupSteps(d).show,
  waitingLine: false, // the preview says it, with the same pulse
  events: true,
});
$('checklist').append(checklist.root);

const controls = new PhoneControls(api, {
  frameRate: true,
  onPhone: (p) => { phone = p; },
  onError: (text) => say(text, true),
  openPhoneCheck: () => openSheet('phoneCheck'),
});
$('camera').append(controls.root);

// --- where the picture comes from ---------------------------------------------------------------------------------
const kind = () => switchingTo ?? tab(health?.sourceKind);
const shown = () => (pickingOther ? 'other' : kind()); // the tab pressed: Other while its picker is open
const otherChoice = () => health?.sourceChoices?.find((c) => c.kind === 'other');
const sourceButtons = SOURCES.map(([k, label]) => {
  const button = el('button', { type: 'button', textContent: label, 'data-kind': k });
  button.addEventListener('click', () => void choose(k));
  return button;
});
$('sources').append(...sourceButtons);

async function choose(next: string, choice: { device?: string; url?: string } = {}): Promise<void> {
  if (next === 'other' && !choice.device && !choice.url && !otherChoice()?.device && !otherChoice()?.stream) {
    pickingOther = true; // nothing chosen yet: choose first, then switch
    void loadCameras();
    render();
    return;
  }
  if (next !== 'other') pickingOther = false;
  if (switchingTo || (next === kind() && !choice.device && !choice.url)) return;
  switchingTo = next;
  render();
  try {
    health = (await api.source(next, choice)).bridge ?? health;
    if (next === 'other') pickingOther = false;
  } catch (error) {
    say(`Couldn't switch: ${(error as Error).message}`, true);
  } finally {
    switchingTo = null;
    liveShown = false;
    void refresh();
  }
}

// --- the other camera: one this Mac sees, or a stream address ---------------------------------------------------------
async function loadCameras(): Promise<void> {
  try { cameras = await api.cameras(); } catch { cameras = []; }
  renderOther();
}

/** Which camera: every camera this Mac sees, always listed, the one in use marked; one click switches. A network
 * camera's address is folded under it. */
function renderOther(): void {
  const on = shown() === 'other';
  $('other').hidden = !on;
  if (!on) return;
  if (cameras === null) { void loadCameras(); return; }
  const current = otherChoice();
  const inUse = kind() === 'other' ? current?.device ?? null : null;
  const streaming = kind() === 'other' && Boolean(current?.stream) && !current?.device;
  $('otherNetNote').textContent = streaming ? 'In use now: a camera on the network.'
    : "Its address starts with rtsp://, srt:// or rtmp://, and is in the camera's manual or app.";
  if (streaming) ($('otherNet') as HTMLDetailsElement).open = true;
  const list = $('otherList');
  const key = JSON.stringify([cameras, inUse, switchingTo, doctor?.live]);
  if (key === camerasShown) return;
  camerasShown = key;
  const names = cameras.map((c) => c.name);
  const rows = [...cameras, ...(inUse && !names.includes(inUse) ? [{ name: inUse, iphone: false, chosen: true, missing: true }] : [])];
  if (!rows.length) {
    list.replaceChildren(el('p', { className: 'other-hint', textContent: "This Mac doesn't see a camera yet. Plug one in: it appears here." }));
    return;
  }
  list.replaceChildren(...rows.map((camera) => {
    const checked = camera.name === inUse;
    const state = camera.iphone ? 'the iPhone: use its own tab'
      : (camera as { missing?: boolean }).missing ? 'not connected now'
      : checked ? (switchingTo === 'other' || !doctor?.live ? 'starting…' : 'in use') : '';
    const row = el('button', { type: 'button', role: 'radio', disabled: camera.iphone, className: 'other-row' },
      el('span', { className: 'other-dot' }), el('span', { className: 'other-name', textContent: camera.name }),
      el('span', { className: 'other-state', textContent: state }));
    row.setAttribute('aria-checked', String(checked));
    row.addEventListener('click', () => { if (!checked) void choose('other', { device: camera.name }); });
    return row;
  }));
}
$('otherRefresh').addEventListener('click', () => { cameras = null; camerasShown = ''; void loadCameras(); });
// A camera plugged in while the list is open appears by itself (a scan every 8 s, only while the list shows and no
// picture comes; never while a camera is live, so a running capture is never disturbed).
setInterval(() => { if (shown() === 'other' && !doctor?.live && !document.hidden) void loadCameras(); }, 8000);
$('otherStream').addEventListener('submit', (event) => {
  event.preventDefault();
  const url = $<HTMLInputElement>('otherUrl').value.trim();
  if (url) void choose('other', { url });
});

// --- the picture --------------------------------------------------------------------------------------------------
const live = $<HTMLImageElement>('live');
// A stream that fails (the bridge restarting, the picture gone) is asked for again after a second, never at once: an
// immediate retry looped thousands of times a second.
let liveRetryAt = 0;
live.addEventListener('error', () => {
  liveShown = false;
  live.removeAttribute('src');
  live.hidden = true;
  liveRetryAt = Date.now() + 1000;
});

function showLive(on: boolean): void {
  if (on && !liveShown && Date.now() >= liveRetryAt) { live.src = api.live; live.hidden = false; liveShown = true; }
  if (!on && liveShown) { live.removeAttribute('src'); live.hidden = true; liveShown = false; }
  // With no picture the preview says what to do, not just black.
  const first = doctor?.items.find((i) => i.id === doctor?.first);
  $('noPicture').hidden = liveShown;
  $('noPicture').dataset.waiting = String(doctor?.mode === 'connect' || (!first && !switchingTo && doctor?.mode !== 'off'));
  $('noPicture').textContent = !doctor ? "The camera bridge isn't answering"
    : doctor.mode === 'off' ? 'No camera: pictures and videos only. Choose a camera above when you want one.'
    : setupSteps(doctor, haveApp()).show ? "Its picture shows here once the Blackmagic Camera app is set up."
    : doctor.mode === 'connect' ? `${doctor.waiting ?? 'Waiting for the camera'}…`
    : first?.fix || 'Starting the camera…';
}

// --- the rhythm: one tick per frame over the last five seconds ------------------------------------------------------
const canvas = $<HTMLCanvasElement>('trace');
const ctx = canvas.getContext('2d');
function drawTrace(): void {
  if (!ctx) return;
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * ratio)) { canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio); }
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const span = (cadence.windowS || 5) * 1000;
  const x = (ago: number) => w - (ago / span) * w;
  const style = getComputedStyle(document.documentElement);
  ctx.fillStyle = style.getPropertyValue('--cam-hair').trim();
  ctx.fillRect(0, Math.round(h / 2), w, 1);
  const ticks = [...cadence.ago].sort((a, b) => b - a); // oldest first
  for (let i = 1; i < ticks.length; i++) {
    if (ticks[i - 1] - ticks[i] > 100) { // a frame visibly late: the gap shows where the app waited
      ctx.fillStyle = 'rgba(255, 107, 94, 0.24)';
      ctx.fillRect(x(ticks[i - 1]), 4, x(ticks[i]) - x(ticks[i - 1]), h - 8);
    }
  }
  ctx.fillStyle = style.getPropertyValue('--cam-ink').trim();
  for (const ago of ticks) ctx.fillRect(Math.round(x(ago)), 8, 1.5, h - 16);
}

function renderSteady(): void {
  const c = health?.cadence;
  const on = Boolean(health?.ok);
  const said = on ? smoothness(c?.fps, c?.p95Ms, c?.even) : { word: 'no frames', line: '' };
  $('fps').textContent = on && c ? `${Math.round(c.fps)} fps` : '–';
  $('steady').dataset.even = String(!on || said.word !== 'stutters');
  $('evenness').textContent = said.word;
  $('gaps').textContent = said.line;
}

// --- what is true now -----------------------------------------------------------------------------------------------
// "I have the app": the one setup step the Mac can't see (an installed app says nothing until it is opened and its
// HTTP Server is on). Kept in this browser only.
const HAVE_APP = 'mm-camera-have-blackmagic-app';
const haveApp = (): boolean => { try { return localStorage.getItem(HAVE_APP) === '1'; } catch { return false; } };
$('haveApp').addEventListener('click', () => {
  try { localStorage.setItem(HAVE_APP, '1'); } catch { /* this visit only */ }
  renderSetup();
});
$('noApp').addEventListener('click', () => {
  try { localStorage.removeItem(HAVE_APP); } catch { /* nothing kept */ }
  renderSetup();
});
let setupWasShown = false;
let setupDoneAt = 0;

/** One step at a time: the step to do now is open, the done ones folded with a tick, the rest waiting. */
function renderSetup(): void {
  const steps = setupSteps(doctor, haveApp());
  $('setup').hidden = !steps.show;
  if (setupWasShown && !steps.show && doctor?.live) setupDoneAt = Date.now(); // it just came live: say so, once
  setupWasShown = steps.show;
  $('setupDone').hidden = !(setupDoneAt && Date.now() - setupDoneAt < 30000);
  if (!steps.show) return;
  steps.done.forEach((done, i) => {
    const li = $(`setup${i + 1}`);
    li.dataset.done = String(done);
    li.dataset.now = String(i === steps.now);
  });
  for (const [id, url] of [['appQr', api.appStoreQr], ['setupQr', api.settingsQr]] as const) {
    const qr = $<HTMLImageElement>(id);
    if (!qr.getAttribute('src')) qr.src = url;
  }
}

function render(): void {
  for (const button of sourceButtons) button.setAttribute('aria-pressed', String(button.dataset.kind === shown()));
  const state = switchingTo ? 'work' : dotState(doctor);
  const top = $('topDot');
  top.dataset.state = state;
  top.setAttribute('aria-label', headline(doctor, switchingTo));
  const setup = setupSteps(doctor, haveApp()).show;
  // The first time, nothing is wrong yet: it isn't set up. The steps lead, above the picture.
  $('headline').textContent = setup && !switchingTo ? 'Set up the Blackmagic Camera app'
    : pickingOther && !switchingTo ? 'Choose the other camera' : headline(doctor, switchingTo);
  $('body').dataset.setup = String(setup || pickingOther); // a walkthrough or a choice leads, above the picture
  $('sourceNote').textContent = SOURCE_NOTES[shown()] ?? '';
  renderOther();
  showLive(Boolean(health?.ok) && !switchingTo);
  renderSetup();
  checklist.update(doctor);
  renderSteady();
  const p = phone ?? health?.phone;
  const show = kind() === 'blackmagic' && Boolean(p?.reachable);
  $('camera').hidden = !show;
  if (show && p) controls.update(p);
  renderTrials();
  if ($('details').dataset.open === 'true') renderFacts();
}

// --- comparing sources ----------------------------------------------------------------------------------------------
$('check').addEventListener('click', async () => {
  try { await api.startTrial(60); } catch (error) { say((error as Error).message, true); }
  void refresh();
});

function renderTrials(): void {
  const running = health?.trial;
  $<HTMLButtonElement>('check').disabled = Boolean(running) || !health?.ok;
  if (running) say(`Watching ${running.title} for a minute`);
  if (!running && trials.length !== (health?.trialCount ?? trials.length)) void refreshTrials();
  if (!trials.length) return;
  const steady = trials.filter((t) => t.fps >= 27 && t.p95Ms != null);
  const best = steady.length ? Math.min(...steady.map((t) => t.p95Ms as number)) : null;
  $('trialRows').replaceChildren(...[...trials].reverse().map((t) => {
    const even = t.fps >= 27 && t.p95Ms != null && t.p95Ms <= 50;
    const row = el('tr');
    [t.title, String(Math.round(t.fps)), smoothness(t.fps, t.p95Ms, even).word, String(t.gaps), (t.startedAt ?? '').slice(11, 16)]
      .forEach((value, i) => row.append(el('td', { textContent: value, className: i === 2 && best != null && t.p95Ms === best && t.fps >= 27 ? 'best' : '' })));
    return row;
  }));
}

async function refreshTrials(): Promise<void> {
  try { trials = await api.trials(); } catch { return; }
  renderTrials();
}

// --- details, for whoever is helping --------------------------------------------------------------------------------
function renderFacts(): void {
  if (!health) return;
  const p = health.phone ?? {};
  const c = health.cadence;
  const rows: [string, string | undefined][] = [
    ['Source', health.targetDevice],
    ['State', health.stateLabel],
    ['Frames', `${health.frameCount ?? 0}, the last ${health.lastFrameAgeSeconds ?? '–'} s ago`],
    ['Steadiness', c?.fps ? `${c.fps} fps; gaps p50 ${c.p50Ms} ms, p95 ${c.p95Ms} ms, longest ${c.maxGapMs} ms` : 'no frames'],
    ['Requests', `${health.requestCount ?? 0}, restarts ${health.restartCount ?? 0}`],
    ['Last problem', health.lastError || 'none'],
    ['Last Continuity reset', health.lastHeal?.at || 'none'],
    ['Phone', p.reachable ? `${p.model || 'Blackmagic Camera'} over ${p.via === 'usb' ? 'the cable' : 'Wi-Fi'}, ${p.streamStatus || '?'} to ${p.server || '?'}` : 'not reachable'],
    ['Mac on Wi-Fi', p.addresses?.wifi || '–'],
    ['Mac on the cable', p.addresses?.usb || 'not plugged in'],
  ];
  $('facts').replaceChildren(...rows.flatMap(([k, v]) => [el('dt', { textContent: k }), el('dd', { textContent: v ?? '–' })]));
}

// --- sheets ---------------------------------------------------------------------------------------------------------
function openSheet(id: string | null): void {
  for (const sheet of document.querySelectorAll<HTMLElement>('.sheet')) sheet.dataset.open = String(sheet.id === id);
  if (id === 'details') renderFacts();
  if (id === 'compare') void refreshTrials();
  if (id === 'phoneCheck') void pollPhoneCheck();
}
$('openCompare').addEventListener('click', () => openSheet('compare'));
$('openPhoneCheck').addEventListener('click', () => openSheet('phoneCheck'));
$('openDetails').addEventListener('click', () => openSheet('details'));
document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => openSheet(null)));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') openSheet(null); });
for (const id of ['settingsLink', 'detailsSettings']) $<HTMLAnchorElement>(id).href = api.settingsFile;

// --- the Phone check: what the phone's app lets this Mac change, learned once (the bridge's phone_lab.py) ------------
function renderPhoneCheck(state: PhoneCheckState | null): void {
  const running = Boolean(state?.running);
  const start = $<HTMLButtonElement>('pcStart');
  start.hidden = running;
  start.textContent = state?.finished ? 'Run it again' : 'Start the Phone check';
  $('pcNeeds').hidden = running || Boolean(state?.finished);
  $('pcRun').hidden = !running && !state?.finished;
  $('pcBar').style.width = `${Math.round((100 * (state?.done ?? 0)) / (state?.total || 1))}%`;
  $('pcStep').textContent = state?.finished ? 'Done.' : state?.step ?? '';
  $('pcNeedsYou').hidden = !state?.needsYou;
  $('pcNeedsYou').textContent = state?.needsYou ?? '';
  $('pcSummary').hidden = !(state?.summary ?? []).length;
  $('pcSummary').replaceChildren(...(state?.summary ?? []).map((line) => el('li', { textContent: line })));
  $('pcError').hidden = !state?.error;
  $('pcError').textContent = state?.error ?? '';
}

let pcTimer: number | undefined;
async function pollPhoneCheck(): Promise<void> {
  let state: PhoneCheckState | null = null;
  try { state = await api.phoneCheck(); } catch { /* the sheet says nothing new */ }
  renderPhoneCheck(state);
  clearTimeout(pcTimer);
  if ($('phoneCheck').dataset.open === 'true' || state?.running) pcTimer = window.setTimeout(pollPhoneCheck, 1000);
}
$('pcStart').addEventListener('click', async () => {
  const start = $<HTMLButtonElement>('pcStart');
  start.disabled = true;
  try { renderPhoneCheck(await api.startPhoneCheck()); } catch (error) { $('pcError').hidden = false; $('pcError').textContent = (error as Error).message; }
  start.disabled = false;
  void pollPhoneCheck();
});

// --- the delay check (?clock=1): point the phone at this screen; the picture shows the clock as it was ---------------
if (new URLSearchParams(location.search).has('clock')) {
  const clock = el('div', { className: 'clock' });
  document.body.append(clock);
  (function tick() {
    const now = new Date();
    clock.textContent = `${String(now.getSeconds()).padStart(2, '0')}.${String(now.getMilliseconds()).padStart(3, '0')}`;
    requestAnimationFrame(tick);
  })();
}

// --- polling: health and the checklist every second, the rhythm four times a second, the phone every two -------------
async function refresh(): Promise<void> {
  try {
    [health, doctor] = await Promise.all([api.health(), api.doctor()]);
  } catch {
    health = null;
    doctor = null;
  }
  render();
}
async function refreshCadence(): Promise<void> {
  if (document.hidden) return;
  try { cadence = await api.cadence(); } catch { cadence = { ago: [], windowS: 5 }; }
  drawTrace();
}
async function refreshPhone(): Promise<void> {
  if (kind() !== 'blackmagic' || document.hidden) return;
  try { phone = await api.phone(); render(); } catch { /* the checklist says why */ }
}

void refresh();
void refreshTrials();
if (location.hash === '#phone-check') openSheet('phoneCheck'); // from the studio's Phone panel
setInterval(() => void refresh(), 1000);
setInterval(() => void refreshCadence(), 250);
setInterval(() => void refreshPhone(), 2000);
window.addEventListener('resize', drawTrace);

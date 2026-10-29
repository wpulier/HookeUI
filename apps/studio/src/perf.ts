// The performance meter: the dock's status line is one dot and a word, green, amber or red by how
// far the live view and the computer are from their ideal; on hover (or focus, or a tap) its panel
// shows the view now (delay, slide, outlines: the numbers the status line once listed beside it),
// each part now against its ideal, plus the programs using the most CPU. It reads numbers the studio already has (frame
// times, the 1 s health poll); the program list is asked for every 5 s only while it is open
// (orchestrator/machine.py). The app's own workers come with every poll (`machine.ours`: role, CPU, the model
// loading there), so when the cause is our own load the verdict says so ("micro-sam is loading: the live view is
// slowed"), not another program's. The ideals are the contract's (contract/overlay.json "performance"):
// - frame rate: the camera's 30 fps;
// - camera to screen and frame analysis: from 6,230 ten-second windows of real sessions (p90
//   camera to screen typically 36 ms, 72 ms at worst for 9 in 10; analysis must fit one frame);
// - clicks: under 300 ms at p95 (AGENTS.md, the dissect-clicks scenario);
// - the computer: no more than one runnable thread per core, memory free, swap with room left,
//   disk with room for swap and recordings (2026-10-06: load 200-300 on 14 cores, swap 96% used
//   and the disk 95% full gave 16 fps and 910 ms clicks in the sim).
// The rating functions are pure, so they are unit tested (perf.test.ts).

import contract from '../../../contract/overlay.json';
import type { Panel } from './panels';
import { positionMeter } from './meter-popover';
import type { Health, MachineState, ModelsHealth, OurWorker, TopProgram } from './protocol';

export type Level = 'good' | 'fair' | 'poor' | 'unknown';

const P = contract.performance;
const TOP_EVERY_MS = 5000;

export interface Reading {
  key: string;
  name: string;
  now: string; // the value now, in words
  ideal: string;
  level: Level;
}

/** What the meter is given: the studio's own frame numbers and the server's health. */
export interface PerfInput {
  live: boolean; // a camera (or test footage) is feeding the view
  fps: number;
  screenMs: number | null; // camera to screen, p90 of the last few seconds
  analyseMs: number | null; // frame analysis on the server, p90
  gpu: Health['gpu'] | null;
  machine: MachineState | null;
  view?: ViewNow; // the view's own numbers, shown in the panel's first section
  models?: ModelsHealth | null; // which defaults ran and how providers' calls ended (the panel's Models section)
}

const PROBLEMS: Record<string, string> = {
  'cut-off': 'cut off by the app\'s deadline', 'rate-limit': 'rate limited or out of quota', auth: 'key or billing refused',
  server: 'provider errors', connection: 'connection failures', failed: 'failed',
};

/** The panel's Models lines: the defaults ran, or each exception with how often and why; then each provider whose calls
 * did not all return in the last ten minutes, by what happened (live 2026-10-11: "am I even on the right model?"). */
export function modelLines(models: ModelsHealth | null | undefined): string[] {
  if (!models) return [];
  const lines = models.fallbacks.length
    ? models.fallbacks.map((f) => `${capitalised(f.role)}: ${f.used} instead of ${f.default}, ${f.times}× (${f.why})`)
    : ['The default models ran.'];
  for (const [provider, counts] of Object.entries(models.providers)) {
    const problems = Object.entries(counts).filter(([k, n]) => k !== 'ok' && (n ?? 0) > 0);
    if (!problems.length) continue;
    const total = Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0);
    lines.push(`${provider}: ${problems.map(([k, n]) => `${n} ${PROBLEMS[k] ?? k}`).join(', ')} of ${total} calls`);
  }
  return lines;
}

const capitalised = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** The panel's "Doing each job now" rows: the job, then its model, where it runs and its calls' typical time lately
 * (orchestrator/models_in_use.py); the row's title says where to change it. */
export function inUseRows(models: ModelsHealth | null | undefined): { job: string; doing: string; title: string }[] {
  return (models?.inUse ?? []).map((row) => {
    const ms = row.speed?.p50Ms;
    const speed = ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
    return { job: row.title, doing: [row.model, row.where, speed].filter(Boolean).join(' · '),
      title: [row.how, row.note, row.speedWords, `Change: ${row.change}`].filter(Boolean).join('. ') };
  });
}

/** The view right now, as the studio measures it (main.ts, every 500 ms). */
export interface ViewNow {
  delayMs: number; // the newest frame's age when it arrived
  slide: 'searching' | 'still' | 'moving' | ''; // '' before the first frame
  outlines: number; // outlines on the view, cells not counted
}

/** The panel's "The view now" rows. The frame rate is the table's first row, not said twice. */
export function viewRows(v: ViewNow): [string, string][] {
  return [
    ['Delay', `${Math.max(0, Math.round(v.delayMs))} ms`],
    ['Slide', v.slide || '–'],
    ['Outlines', String(v.outlines)],
  ];
}

/** Higher is better (frame rate, memory free, disk free). */
export function atLeast(value: number | null | undefined, t: { good: number; fair: number }): Level {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'unknown';
  return value >= t.good ? 'good' : value >= t.fair ? 'fair' : 'poor';
}

/** Lower is better (times, load, swapping). */
export function atMost(value: number | null | undefined, t: { good: number; fair: number }): Level {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'unknown';
  return value <= t.good ? 'good' : value <= t.fair ? 'fair' : 'poor';
}

const RANK: Record<Level, number> = { unknown: -1, good: 0, fair: 1, poor: 2 };

/** Lower is better, and a worse level (`was`) is left only once the value is past the line it crossed by the contract's
 * `hysteresis` (a share of the line), so a reading that hovers at a line holds; a worse value is said at once (live
 * 2026-10-11 16:32-16:35: swapping flipped between good, fair and poor every 5 s). machine.py `levels` rates the same. */
export function atMostHeld(value: number | null | undefined, t: { good: number; fair: number; hysteresis?: number },
  was: Level | undefined): Level {
  const now = atMost(value, t);
  if (!t.hysteresis || !was || was === 'unknown' || now === 'unknown' || RANK[now] >= RANK[was]) return now;
  const back = atMost((value as number) / t.hysteresis, t);
  return RANK[back] < RANK[was] ? back : was;
}

/** The worst of the known readings (unknown when none is known). */
export function overall(readings: Reading[]): Level {
  return readings.reduce<Level>((worst, r) => (RANK[r.level] > RANK[worst] ? r.level : worst), 'unknown');
}

const gb = (mb: number) => (mb / 1024).toFixed(1);

/** Each part now against its ideal. `was`: the readings before (the meter's last), for the levels that hold. */
export function readings(input: PerfInput, was: Reading[] = []): Reading[] {
  const m = input.machine ?? {};
  const out: Reading[] = [];
  out.push({
    key: 'fps', name: 'Frame rate',
    now: input.live ? `${Math.round(input.fps)} fps` : 'no camera',
    ideal: `${P.fps.ideal} fps`,
    level: input.live ? atLeast(input.fps, P.fps) : 'unknown',
  });
  out.push({
    key: 'screen', name: 'Camera to screen',
    now: input.live && input.screenMs !== null ? `${input.screenMs} ms` : '–',
    ideal: `under ${P.screenMs.good} ms`,
    level: input.live ? atMost(input.screenMs, P.screenMs) : 'unknown',
  });
  out.push({
    key: 'analyse', name: 'Frame analysis',
    now: input.live && input.analyseMs !== null ? `${input.analyseMs} ms` : '–',
    ideal: `under ${P.analyseMs.good} ms`, // one frame's time at 30 fps
    level: input.live ? atMost(input.analyseMs, P.analyseMs) : 'unknown',
  });
  out.push({
    key: 'clicks', name: 'Click to outline',
    now: m.clickP95Ms !== undefined ? `${m.clickP95Ms} ms` : 'no clicks yet',
    ideal: `under ${P.clickMs.good} ms`,
    level: atMost(m.clickP95Ms, P.clickMs),
  });
  out.push({
    key: 'gpu', name: 'Outline model',
    now: !input.gpu ? '–' : input.gpu.ready ? `ready${input.gpu.device ? ` (${input.gpu.device})` : ''}` : input.gpu.error ? 'failed' : 'starting',
    ideal: 'ready',
    level: !input.gpu ? 'unknown' : input.gpu.ready ? 'good' : input.gpu.error ? 'poor' : 'fair',
  });
  const perCore = m.load1 !== undefined && m.cores ? m.load1 / m.cores : null;
  out.push({
    key: 'cpu', name: 'CPU',
    now: perCore === null ? '–' : `load ${m.load1} on ${m.cores} cores`,
    ideal: m.cores ? `${m.cores} or fewer` : 'one per core',
    level: atMost(perCore, P.loadPerCore),
  });
  out.push({
    key: 'memory', name: 'Memory free',
    now: m.memFreePct !== undefined ? `${m.memFreePct}%` : '–',
    ideal: `over ${P.memFreePct.good}%`,
    level: atLeast(m.memFreePct, P.memFreePct),
  });
  // Swapping now (pages swapped back in a second over the contract's window: programs waiting on disk), with how much is
  // swapped out for context; how full swap is said nothing (macOS keeps it near full long after memory ran short: red
  // all of 2026-10-11).
  const ins = m.swapInsPerS ?? null;
  const held = m.swapTotalMb ? ` · ${gb(m.swapUsedMb ?? 0)} GB swapped out` : '';
  out.push({
    key: 'swap', name: 'Swapping',
    now: ins === null ? '–' : `${Math.round(ins)} pages/s back in over ${P.swapInsPerS.windowS} s${held}`,
    ideal: `under ${P.swapInsPerS.good} pages/s`,
    level: atMostHeld(ins, P.swapInsPerS, was.find((r) => r.key === 'swap')?.level),
  });
  out.push({
    key: 'disk', name: 'Disk free',
    now: m.diskFreeGb !== undefined ? `${m.diskFreeGb} GB` : '–',
    ideal: `over ${P.diskFreeGb.good} GB`,
    level: atLeast(m.diskFreeGb, P.diskFreeGb),
  });
  return out;
}

/** The models loading in this app's own workers now ("micro-sam"), each once. */
export function loadingNow(ours: OurWorker[] = []): string[] {
  return [...new Set(ours.map((w) => w.loading).filter((m): m is string => !!m))];
}

const and = (names: string[]) => (names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names[0]);

/** In a sentence: what is holding the view back most, or that it runs as it should. Our own load is named when it
 * is the cause (live 2026-10-11: micro-sam loading at load 142 was blamed on WindowServer): a model loading in one
 * of our workers, or our workers using more of the CPU than the other programs. */
export function verdict(rs: Reading[], top: TopProgram[] = [], ours: OurWorker[] = []): string {
  const level = overall(rs);
  if (level === 'unknown') return 'Waiting for numbers.';
  if (level === 'good') return 'Running as it should.';
  const bad = (key: string) => rs.find((r) => r.key === key && r.level === level);
  const worst = rs.filter((r) => r.level === level);
  const loading = loadingNow(ours);
  if (loading.length && !(worst.length === 1 && bad('gpu'))) {
    return `${and(loading)} ${loading.length > 1 ? 'are' : 'is'} loading: the live view is slowed.`;
  }
  // Every outside program counts, and programs of one name together ("Python ×14"): many small ones can take more
  // than any one (2026-10-11 gate: 14 at ~45% each were counted as none, and the app's own work was blamed).
  const grouped = new Map<string, { cpu: number; n: number }>();
  for (const p of top.filter((p) => !p.ours)) {
    const g = grouped.get(p.name) ?? { cpu: 0, n: 0 };
    g.cpu += p.cpu; g.n += 1; grouped.set(p.name, g);
  }
  const outside = [...grouped].sort((a, b) => b[1].cpu - a[1].cpu);
  const others = outside.filter(([, g]) => g.cpu >= 50).slice(0, 2).map(([name, g]) => g.n > 1 ? `${name} ×${g.n}` : name);
  const outsideCpu = outside.reduce((sum, [, g]) => sum + g.cpu, 0);
  const ourCpu = ours.reduce((sum, w) => sum + w.cpu, 0);
  if (bad('cpu') && ourCpu >= 100 && ourCpu >= outsideCpu) {
    const busiest = [...ours].sort((a, b) => b.cpu - a.cpu).slice(0, 2).map((w) => w.role);
    return `This app's own work wants more CPU than the computer has: ${and(busiest)}.`;
  }
  if (bad('cpu')) return `Other programs want more CPU than the computer has${others.length ? `: ${others.join(', ')}` : ''}.`;
  if (bad('memory')) return 'Memory is short: programs wait while the disk gives back what was swapped out.';
  if (bad('swap')) return 'Memory is short: programs are waiting for what was moved to disk.';
  if (bad('disk')) return 'The disk is nearly full: swap and recordings have little room.';
  if (bad('gpu')) return 'The outline model is not ready: clicks cannot be outlined yet.';
  if (bad('clicks')) return 'Clicks are slow to outline.';
  return 'The live view is slower than the camera.';
}

const WORDS: Record<Level, string> = { good: 'Running well', fair: 'Slowed', poor: 'Struggling', unknown: 'Performance' };

/** The dot on the dock's status line and its panel. A Panel so Escape closes it (it covers nothing). */
export class PerfMeter implements Panel {
  readonly escapePriority = 100;
  readonly id = 'performance';
  readonly root: HTMLElement;
  private readonly button: HTMLButtonElement;
  private readonly panel: HTMLElement;
  private readonly viewHead: HTMLElement;
  private readonly view: HTMLDListElement; // #readout: kept by id, now inside the panel
  private readonly fetchTop: () => Promise<TopProgram[]>;
  private open = false;
  private pinned = false; // opened by a click or tap: stays until clicked again or Escape
  private top: TopProgram[] = [];
  private ours: OurWorker[] = []; // our own workers, from every health poll (machine.py samples them each tick)
  private models: ModelsHealth | null = null; // which defaults ran (the Models section), from every health poll
  private topTimer = 0;
  private last: Reading[] = [];

  constructor(fetchTop: () => Promise<TopProgram[]>) {
    this.fetchTop = fetchTop;
    this.root = document.createElement('div');
    this.root.className = 'perf';
    this.button = Object.assign(document.createElement('button'), { type: 'button', className: 'perf-dot' });
    this.button.setAttribute('aria-expanded', 'false');
    this.button.setAttribute('aria-label', 'Performance');
    this.button.setAttribute('aria-controls', 'perf-panel');
    this.panel = Object.assign(document.createElement('div'), { id: 'perf-panel', className: 'perf-panel', hidden: true });
    this.panel.setAttribute('role', 'dialog');
    this.panel.setAttribute('aria-label', 'The view and performance now');
    this.viewHead = Object.assign(document.createElement('p'), { id: 'readout-title', className: 'perf-sub', textContent: 'The view now' });
    this.view = Object.assign(document.createElement('dl'), { id: 'readout', className: 'readout' });
    this.view.setAttribute('aria-labelledby', 'readout-title');
    this.panel.append(this.viewHead, this.view); // in the page from the start, so #readout is always there
    this.root.append(this.button, this.panel);
    this.root.addEventListener('pointerenter', (e) => e.pointerType === 'mouse' && this.show(true));
    this.root.addEventListener('pointerleave', (e) => e.pointerType === 'mouse' && !this.pinned && this.show(false));
    this.root.addEventListener('focusin', () => this.show(true));
    this.root.addEventListener('focusout', (e) => !this.root.contains(e.relatedTarget as Node) && !this.pinned && this.show(false));
    this.button.addEventListener('click', () => {
      this.pinned = !this.pinned;
      this.show(this.pinned);
    });
    const position = () => { if (this.open) positionMeter(this.panel, this.button); };
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position);
    window.visualViewport?.addEventListener('scroll', position);
  }

  isOpen(): boolean {
    return this.open;
  }

  /** Placed again by its button while open: the workspace's layout changed (a side column opened or closed). */
  place(): void {
    if (this.open) positionMeter(this.panel, this.button);
  }

  close(): void {
    this.button.focus();
    this.pinned = false;
    this.show(false);
  }

  footprint(): null {
    return null; // it floats over the dock for a moment; nothing moves for it
  }

  private show(open: boolean): void {
    if (open === this.open) return;
    this.open = open;
    this.panel.hidden = !open;
    this.button.setAttribute('aria-expanded', String(open));
    window.clearInterval(this.topTimer);
    if (open) {
      positionMeter(this.panel, this.button);
      const load = () => void this.fetchTop().then((top) => {
        this.top = top;
        this.renderPanel();
      }).catch(() => undefined);
      load();
      this.topTimer = window.setInterval(load, TOP_EVERY_MS);
      this.renderPanel();
    }
  }

  /** Every readout tick (500 ms): the dot always, the panel while open. */
  update(input: PerfInput): void {
    this.last = readings(input, this.last);
    this.ours = input.machine?.ours ?? [];
    this.models = input.models ?? null;
    const level = overall(this.last);
    this.root.dataset.level = level;
    this.button.textContent = WORDS[level];
    this.button.setAttribute('aria-label', `Performance: ${WORDS[level]}`);
    this.button.title = verdict(this.last, this.top, this.ours);
    if (input.view) this.renderView(viewRows(input.view));
    if (this.open) this.renderPanel();
  }

  /** The view's rows, kept current even while closed so the panel opens on today's numbers. */
  private renderView(rows: [string, string][]): void {
    this.view.replaceChildren(...rows.map(([term, value]) => {
      const row = document.createElement('div');
      row.append(Object.assign(document.createElement('dt'), { textContent: term }), Object.assign(document.createElement('dd'), { textContent: value }));
      return row;
    }));
  }

  private renderPanel(): void {
    const level = overall(this.last);
    const head = Object.assign(document.createElement('p'), { className: 'perf-verdict', textContent: verdict(this.last, this.top, this.ours) });
    head.dataset.level = level;
    const table = document.createElement('table');
    table.setAttribute('aria-label', 'Each part now against its ideal');
    table.innerHTML = '<thead><tr><th scope="col"></th><th scope="col">Now</th><th scope="col">Ideal</th></tr></thead>';
    const body = document.createElement('tbody');
    for (const r of this.last) {
      const row = document.createElement('tr');
      row.dataset.level = r.level;
      const name = document.createElement('th');
      name.scope = 'row';
      name.textContent = r.name;
      row.append(name, Object.assign(document.createElement('td'), { textContent: r.now }), Object.assign(document.createElement('td'), { textContent: r.ideal }));
      body.append(row);
    }
    table.append(body);
    const children: HTMLElement[] = [head, this.viewHead, this.view, table];
    if (this.top.length) {
      children.push(Object.assign(document.createElement('p'), { className: 'perf-sub', textContent: 'Using the most CPU now' }));
      const list = Object.assign(document.createElement('ol'), { className: 'perf-top' });
      for (const p of this.top) {
        const item = document.createElement('li');
        if (p.ours) item.dataset.ours = '';
        const share = p.cpu >= 100 ? `${(p.cpu / 100).toFixed(1)} cores` : `${p.cpu}% of a core`;
        item.append(Object.assign(document.createElement('span'), { textContent: p.name + (p.ours ? ' (this app)' : '') }),
          Object.assign(document.createElement('span'), { textContent: `${share} · ${p.memMb >= 1024 ? `${gb(p.memMb)} GB` : `${p.memMb} MB`}` }));
        list.append(item);
      }
      children.push(list);
    }
    const doing = inUseRows(this.models);
    if (doing.length) {
      children.push(Object.assign(document.createElement('p'), { className: 'perf-sub', textContent: 'Doing each job now' }));
      const list = Object.assign(document.createElement('ul'), { className: 'perf-top perf-in-use' });
      for (const row of doing) {
        const item = Object.assign(document.createElement('li'), { title: row.title });
        item.append(Object.assign(document.createElement('span'), { textContent: row.job }),
          Object.assign(document.createElement('span'), { textContent: row.doing }));
        list.append(item);
      }
      children.push(list);
    }
    const models = modelLines(this.models);
    if (models.length) {
      children.push(Object.assign(document.createElement('p'), { className: 'perf-sub', textContent: 'Models, the last 10 minutes' }));
      const list = Object.assign(document.createElement('ul'), { className: 'perf-models' });
      for (const line of models) list.append(Object.assign(document.createElement('li'), { textContent: line }));
      children.push(list);
    }
    this.panel.replaceChildren(...children);
  }
}

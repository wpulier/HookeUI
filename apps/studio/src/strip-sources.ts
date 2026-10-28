// What the Working strip (working-strip.ts) shows besides researchers (shelf.ts): a run the person started by hand, an
// agent step still running after its answer, and a model loading. Each says only what the app knows: a run's own status
// (run-chip-words.ts), a step's recorded state, a load's Activity line. None has a measured fraction on the wire, so none
// shows a percent; work that ends within a second never flashes a pill.

import { actionName, capital, WORKING } from './activity';
import type { ActivityEvent, ToolRun } from './protocol';
import { runChipWords } from './run-chip-words';
import type { StudyRun } from './workspace-study-model';
import type { StripPill, Urgency } from './working-strip';
import type { LitCellState } from './lit-cell';
import { runnableWords } from './run-spec';

/** Work under a second shows no pill. */
export const QUICK_MS = 1000;

/** A hand-started run as the workspace follows it: its own status and message, when it began and (once over) ended. */
export interface RunFacts extends Pick<StudyRun, 'id' | 'status' | 'message' | 'interrupted'> {
  started: number;
  ended?: number;
}

const RUN_CELL: Record<StudyRun['status'], LitCellState> = {
  running: 'working', queued: 'queued', stopping: 'still', completed: 'ready', failed: 'attention', stopped: 'stopped', unknown: 'idle',
};
const live = (status: StudyRun['status']) => status === 'running' || status === 'queued' || status === 'stopping';

/** The pill's form of a model's name: the part before " · " ("Bright objects" for "Bright objects · Basic local
 * method", the mock's short name); a name without one is kept whole. The pill's tooltip keeps the whole name. */
export function pillName(name: string): string {
  const cut = name.indexOf(' · ');
  return cut > 0 ? name.slice(0, cut) : name;
}

/** A run's pill: its model's name (short, whole in its tooltip), its time while it runs, then what happened (green when
 * done, until opened or hidden). Null for a run that ended within a second. */
export function runPill(run: RunFacts, name: string, open: () => void, dismiss: () => void): StripPill | null {
  const going = live(run.status);
  if (!going && run.ended !== undefined && run.ended - run.started < QUICK_MS) return null;
  const chip = runChipWords(run);
  const counting = run.status === 'running';
  const urgency: Urgency = run.status === 'failed' ? 0 : going ? 1 : 2;
  const next = run.status === 'completed' ? 'Open its results in Data' : 'Open its run page';
  const short = pillName(name);
  return {
    id: `run:${run.id}`, name: short, cell: RUN_CELL[run.status], urgency, since: run.started, counting,
    ...(counting ? {} : { words: chip.label }), ...(going ? { after: run.started + QUICK_MS } : {}),
    title: [`${name} · ${chip.label}`, chip.title, next].filter(Boolean).join('\n'),
    hide: `Hide ${short} from the strip`, open, dismiss,
  };
}

/** At most this many runs started by hand keep their pills; past it the oldest that has ended leaves first. */
export const KEPT_RUNS = 8;

/** A run as the workspace follows it (workspace-study.ts StudyRun): kept by reference, so its pill says its status now. */
export type HandRun = Pick<StudyRun, 'id' | 'status' | 'message' | 'interrupted'> & { input: { session: string } };

/** The runs started by hand that have a pill (`runPill`), one per run: a run keeps its pill while it works, then until
 * its results are opened (its own click when done, or Data opened from a run page) or it is hidden (its ×). A new start
 * adds its own pill beside the others; none is taken off by another starting. Its click opens Data once done, else its
 * run page. */
export class RunPills<R extends HandRun = HandRun> {
  private readonly kept = new Map<string, { run: R; started: number; ended?: number; capability: string; name: string }>();
  private signature = '';
  private readonly openResults: () => void;
  private readonly openRunPage: (capability: string) => void;
  private readonly sink: (pills: StripPill[]) => void;
  private readonly now: () => number;

  constructor(openResults: () => void, openRunPage: (capability: string) => void, sink: (pills: StripPill[]) => void, now: () => number = () => Date.now()) {
    this.openResults = openResults;
    this.openRunPage = openRunPage;
    this.sink = sink;
    this.now = now;
  }

  /** A run started: its pill joins the others (shown on the next `render`). */
  start(run: R, capability: string, name: string): void {
    this.kept.delete(run.id);
    // Run again from its page: an earlier run of the same task that ended without results (failed, stopped) gives its
    // place to this one (its page now shows this run; what went wrong stays in Activity).
    for (const [id, kept] of this.kept) if (kept.capability === capability && !live(kept.run.status) && kept.run.status !== 'completed') this.kept.delete(id);
    this.kept.set(run.id, { run, started: this.now(), capability, name });
    while (this.kept.size > KEPT_RUNS) {
      const older = [...this.kept.keys()].filter((id) => id !== run.id); // oldest first: the order they started
      const drop = older.find((id) => !live(this.kept.get(id)!.run.status)) ?? older[0];
      if (drop === undefined) break;
      this.kept.delete(drop);
    }
  }

  /** A kept run by its id, to hear what became of it. */
  get(id: string): R | undefined { return this.kept.get(id)?.run; }

  /** Every kept run, oldest first. */
  runs(): R[] { return [...this.kept.values()].map((kept) => kept.run); }

  /** Its results were opened, or it was hidden: its pill leaves the strip. */
  leave(id: string): void {
    if (this.kept.delete(id)) this.render();
  }

  /** Data opened from a run page (its Open in Data): the one run done in that Project leaves. With more than one, which
   * run's page it was is not known here, so each stays until opened or hidden. */
  resultsOpened(session: string): void {
    const done = [...this.kept.values()].filter((kept) => kept.run.status === 'completed' && kept.run.input.session === session);
    if (done.length === 1) this.leave(done[0].run.id);
  }

  render(): void {
    const pills: StripPill[] = [];
    for (const [id, kept] of [...this.kept]) {
      const run = kept.run;
      if (kept.ended === undefined && !live(run.status)) kept.ended = this.now();
      const done = run.status === 'completed';
      const pill = runPill({ id, status: run.status, message: run.message, interrupted: run.interrupted, started: kept.started, ...(kept.ended !== undefined ? { ended: kept.ended } : {}) }, kept.name,
        () => { if (done) { this.kept.delete(id); this.openResults(); this.render(); } else this.openRunPage(kept.capability); },
        () => this.leave(id));
      if (pill) pills.push(pill);
      else if (!live(run.status)) this.kept.delete(id); // ended within a second: it never shows, so it keeps no place
    }
    const signature = this.kept.size ? JSON.stringify([...this.kept].map(([id, k]) => [id, k.run.status, k.run.message, k.run.interrupted, k.ended, k.name])) : '';
    if (signature === this.signature) return;
    this.signature = signature;
    this.sink(pills);
  }
}

/** A step of an answer, as the chat holds it (chatlog.ts): `ok` null while it still runs after its answer was shown. */
export interface StepFact { key: string; tool: string; ok: ToolRun['ok']; at?: number; progress?: string; summary: string }

/** Agent steps still running after their answer (an Assistant-started run gets its pill when its answer arrives), then
 * what became of them: done stays green until opened or hidden, a failure says so. Opening one opens Activity. */
export class StepPills {
  private readonly seen = new Map<string, number>(); // a step seen running here: since when (ms)
  private readonly hidden = new Set<string>();
  private signature = '';
  private steps: StepFact[] = [];
  private readonly openActivity: () => void;
  private readonly sink: (pills: StripPill[]) => void;
  private readonly now: () => number;

  constructor(openActivity: () => void, sink: (pills: StripPill[]) => void, now: () => number = () => Date.now()) {
    this.openActivity = openActivity;
    this.sink = sink;
    this.now = now;
  }

  update(steps: StepFact[]): void {
    this.steps = steps;
    for (const step of steps) if (step.ok === null && !this.seen.has(step.key)) this.seen.set(step.key, step.at ? step.at * 1000 : this.now());
    const present = new Set(steps.map((s) => s.key));
    for (const key of [...this.seen.keys()]) if (!present.has(key)) this.seen.delete(key); // another conversation, or cleared
    const shown = steps.filter((s) => this.seen.has(s.key) && !this.hidden.has(s.key));
    const signature = JSON.stringify(shown.map((s) => [s.key, s.ok, s.progress, s.summary]));
    if (signature === this.signature) return;
    this.signature = signature;
    this.sink(shown.map((s) => this.pill(s)));
  }

  private pill(step: StepFact): StripPill {
    const name = actionName(step.tool);
    const since = this.seen.get(step.key) ?? this.now();
    const hide = () => { this.hidden.add(step.key); this.signature = ''; this.update(this.steps); };
    const base = { id: `step:${step.key}`, name, since, hide: `Hide ${name} from the strip. It stays in Activity.`, dismiss: hide };
    if (step.ok === null) {
      const doing = step.progress ? `${capital(step.progress)}…` : WORKING[step.tool] ?? `${name} in progress…`;
      return { ...base, cell: 'working', urgency: 1, counting: true, title: `${doing}\nOpen Activity`, open: () => this.openActivity() };
    }
    const opened = () => { hide(); this.openActivity(); };
    return step.ok
      ? { ...base, cell: 'ready', urgency: 2, words: 'Done', title: `${capital(step.summary || name)}\nOpen Activity`, open: opened }
      : { ...base, cell: 'attention', urgency: 0, words: 'Failed', title: `${capital(step.summary || `${name} did not work`)}\nOpen Activity`, open: opened };
  }
}

/** Models loading (Activity's `model` lines: "Loading micro-sam (usually about 2 min)"): a pill while one loads past a
 * second; a load that ends leaves the strip (its run says the rest), one that fails stays until opened or hidden. One
 * pill per action: a load a hand-started run claims (its model, since it started: `claimed`, actions-panel.ts
 * claimsLoad) makes no pill of its own, the run's pill and footer carry it; one still loading after its run ended keeps
 * its own pill, saying Loading. A pill names a catalog model by its display name (`models`, the wire's rows), with where
 * it runs beside it, never by its key. */
export interface LoadOptions {
  claimed?: (event: ActivityEvent) => boolean;
  models?: () => readonly { id: string; name: string; runs?: string }[];
}
export class LoadPills {
  private readonly loads = new Map<string, { name: string; state: ActivityEvent['state']; at: number; text: string; event: ActivityEvent; withRun: boolean }>();
  private readonly hidden = new Set<string>();
  private readonly openActivity: () => void;
  private readonly sink: (pills: StripPill[]) => void;
  private readonly options: LoadOptions;

  constructor(openActivity: () => void, sink: (pills: StripPill[]) => void, options: LoadOptions = {}) {
    this.openActivity = openActivity;
    this.sink = sink;
    this.options = options;
  }

  apply(event: ActivityEvent): void {
    if (event.kind !== 'model') return;
    const name = event.ref?.model;
    if (name && (event.state === 'running' || event.state === 'failed')) {
      const withRun = this.loads.get(event.id)?.withRun ?? false;
      this.loads.set(event.id, { name, state: event.state, at: event.at, text: event.text, event, withRun });
    } else if (!this.loads.delete(event.id)) return;
    this.render();
  }

  /** A run started or ended: which loads its pill carries changes. */
  refresh(): void { if (this.loads.size) this.render(); }

  /** The connection was lost: what loaded meanwhile is not known here, so no load stays on the strip. */
  clear(): void {
    if (!this.loads.size) return;
    this.loads.clear();
    this.render();
  }

  /** A catalog model's display name for a load named by its key ("swin2sr-enlarge" → "Swin2SR enlarge (on this Mac)"),
   * keeping where it runs when the line says it; any other name as the line says it. */
  private named(name: string): string {
    const where = /\s*\((?:on this Mac|in the cloud)\)$/.exec(name)?.[0] ?? '';
    const bare = where ? name.slice(0, -where.length) : name;
    const entry = this.options.models?.().find((m) => m.id === bare);
    if (!entry) return name;
    return `${entry.name}${where || (entry.runs === 'cloud' ? ' (in the cloud)' : ' (on this Mac)')}`;
  }

  private render(): void {
    const pills: StripPill[] = [];
    for (const [id, load] of this.loads) {
      if (this.hidden.has(id)) continue;
      if (load.state === 'running' && this.options.claimed?.(load.event)) { load.withRun = true; continue; } // its run's pill says it
      const hide = () => { this.hidden.add(id); this.render(); };
      const since = load.at * 1000, name = this.named(load.name);
      const base = { id: `load:${id}`, name, since, dismiss: hide };
      pills.push(load.state === 'running'
        ? { ...base, cell: 'working' as const, urgency: 1 as const, title: `${load.text}\nOpen Activity`,
          // After its run ended it says what it is still doing, not a bare clock beside a stopped run.
          ...(load.withRun ? { counting: false, words: 'Loading' } : { counting: true, after: since + QUICK_MS }),
          hide: `Hide ${name} from the strip. It keeps loading.`, open: () => this.openActivity() }
        : { ...base, cell: 'attention' as const, urgency: 0 as const, words: 'Did not load', title: `${load.text}\nOpen Activity`,
          hide: `Hide ${name} from the strip. It stays in Activity.`, open: () => { hide(); this.openActivity(); } });
    }
    this.sink(pills);
  }
}

/** A model's files downloading (the capabilities wire's `setup.downloads`, from any page: a run page, Models, Tools): a
 * pill whose lit cell fills from the measured bytes only (no total, no fill), with how much has come; once its files are
 * in it turns green, Ready, only when the model's own row says ready (`models`, the wire's rows); with more to set up (an
 * install after the download) it says Downloaded on a quiet cell, and its run page names the next step. Either stays
 * until opened or hidden (only a download seen under way here: an old finished one never appears); a failed one stays
 * until opened or hidden. Opening one shows the run page that asked for it, else its card in Models. */
export class DownloadPills {
  private readonly hidden = new Set<string>();
  private readonly started = new Map<string, number>();
  private signature = '';
  private last: readonly DownloadFact[] = [];
  private models: readonly ModelFact[] = [];
  private readonly open: (model: string) => void;
  private readonly sink: (pills: StripPill[]) => void;
  private readonly now: () => number;

  constructor(open: (model: string) => void, sink: (pills: StripPill[]) => void, now: () => number = () => Date.now()) {
    this.open = open;
    this.sink = sink;
    this.now = now;
  }

  update(downloads: readonly DownloadFact[], models: readonly ModelFact[] = this.models): void {
    this.last = downloads;
    this.models = models;
    const going = (d: DownloadFact) => ['queued', 'downloading', 'checking'].includes(d.state);
    for (const d of downloads) if (going(d) && !this.started.has(d.model)) this.started.set(d.model, this.now());
    const live = downloads.filter((d) => !this.hidden.has(d.model) && (going(d) || ((d.state === 'failed' || d.state === 'done') && this.started.has(d.model))));
    const entry = (d: DownloadFact) => models.find((m) => m.id === d.model);
    const ready = (d: DownloadFact) => entry(d)?.status === 'ready';
    // Ready as Models says it: only once Hooke has checked it here, else "Downloaded · not checked" (ticket 67).
    const said = (d: DownloadFact) => ready(d) ? runnableWords(entry(d)) : '';
    const signature = JSON.stringify(live.map((d) => [d.model, d.state, d.got, d.total, said(d)]));
    if (signature === this.signature) return;
    this.signature = signature;
    this.sink(live.map((d) => {
      const short = pillName(d.name);
      const failed = d.state === 'failed', done = d.state === 'done', usable = done && ready(d), checked = usable && said(d) === 'Ready';
      const since = this.started.get(d.model) ?? this.now();
      const fraction = d.total ? Math.max(0, Math.min(1, d.got / d.total)) : null;
      const words = failed ? 'Did not download' : usable ? said(d) : done ? 'Downloaded' : d.state === 'checking' ? 'Checking' : d.state === 'queued' ? 'Waiting'
        : fraction !== null ? `${Math.round(100 * fraction)}%` : 'Downloading';
      const dismiss = () => { this.hidden.add(d.model); this.signature = ''; this.update(this.last); };
      return {
        id: `download:${d.model}`, name: short, cell: failed ? 'attention' : checked ? 'ready' : done ? 'idle' : 'setup', urgency: failed ? 0 : done ? 2 : 1, since,
        words, fraction: failed || done ? null : fraction, ...(failed || done ? {} : { after: since + QUICK_MS }),
        title: `${d.name} · ${usable ? 'its files are in; it is ready' : done ? 'its files are in; its run page names the next step' : d.says || words}\nOpen where it is being set up`,
        hide: done ? `Hide ${short} from the strip` : `Hide ${short} from the strip. It keeps downloading.`,
        open: () => { if (failed || done) dismiss(); this.open(d.model); }, dismiss,
      } satisfies StripPill;
    }));
  }
}

/** One download as the wire says it (protocol.ts DownloadRow). */
export interface DownloadFact { model: string; name: string; state: string; got: number; total: number; says: string }
/** A model's row as the wire says it (protocol.ts ModelRow): whether it can run now. */
/** What a download's pill reads of its model (the wire's row): its readiness, and its card's face for one meaning of Ready. */
export interface ModelFact { id: string; status: string; group?: string; runs?: string; size?: number; face?: { state: string } }

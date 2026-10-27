// The Data light and the Datasets tab (#datasets): what Record collected (docs/datasets.md).
//
// A dataset is for programs and agents to analyse later, not a viewer: this tab says where each one
// is and what it holds (its window, its runs and gaps, its video parts, how many records, frame
// facts and pictures, the microscope facts with where each came from), and lets the user name one,
// copy its path or show it in the Finder. Record is the switch (ADR 0009); the Data light beside it
// only looks: blue once the server has seen every file of the dataset growing, amber with the reason
// when one is not, and after Stop, "Saved" once it has checked the dataset was saved whole.
//
// "Save this session" (datasets/snapshot.py; the UX document §5): a review of what a dataset of this
// conversation, or of today so far, would hold, miss and leave pending, and where it would go; Save
// writes it without stopping anything. Only this press makes one (ADR 0008).

import type { DatasetCheck, DatasetEndCheck, DatasetListing, DatasetRow, DatasetState, RigState } from './protocol';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "1:29:23 am": always with seconds (an edge read as 1:29 could be anywhere in a minute). */
export function clockText(epoch: number): string {
  const d = new Date(epoch * 1000);
  const h = d.getHours();
  return `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}

/** "Oct 6". */
export function dayText(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

/** A length of time in words: "0.4 s", "12 s", "34 min 34 s", "9 h 0 min". */
export function durationText(seconds: number): string {
  if (seconds < 9.95) return `${Math.max(0, Math.round(seconds * 10) / 10)} s`; // a short gap is not "0 s"
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** Record's running time: "0:07", "12:34", "1:02:03". */
export function elapsedText(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`;
}

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** "Oct 6, 1:29:23 am to 2:03:57 am (34 min 34 s)"; "... to now" while it collects. */
export function spanText(row: Pick<DatasetRow, 'startedAtEpoch' | 'stoppedAtEpoch' | 'status'>, now: number): string {
  if (!row.startedAtEpoch) return 'start unknown';
  const start = `${dayText(row.startedAtEpoch)}, ${clockText(row.startedAtEpoch)}`;
  const end = row.status === 'collecting' || !row.stoppedAtEpoch ? null : row.stoppedAtEpoch;
  const sameDay = end !== null && dayText(end) === dayText(row.startedAtEpoch);
  const to = end === null ? 'now' : sameDay ? clockText(end) : `${dayText(end)}, ${clockText(end)}`;
  return `${start} to ${to} (${durationText((end ?? now) - row.startedAtEpoch)})`;
}

/** What the Data light shows (orchestrator/datasets/checks.py). Recording here while the server has no
 * dataset on (it could not be started) is a problem too. */
export type Light = DatasetCheck['state'];

export function lightState(state: DatasetState | null, recording: boolean): Light {
  if (recording && !state?.on) return 'problem';
  return state?.check?.state ?? 'off';
}

export function lightLabel(light: Light): string {
  return light === 'checking' ? 'Checking' : light === 'saving' ? 'Saving' : light === 'saved' ? 'Saved' : 'Data';
}

const SAVED_PARTS = 'the camera video, its frame times, the frame facts and the records';

export function lightTitle(state: DatasetState | null, recording: boolean, startError = ''): string {
  if (recording && !state?.on) {
    return `Recording, but no dataset is being collected${startError ? `: ${startError}` : ''}. The studio recording goes on.`;
  }
  const check = state?.check;
  const light = lightState(state, recording);
  const extras: string[] = [];
  if (state?.on) {
    if (state.resumedAfter) extras.push(`It carried on after the app was off for ${durationText(state.resumedAfter)} (the gap is written down).`);
    if (state.dropped) extras.push(`${state.dropped} frame facts were dropped while the disk fell behind (no records).`);
  }
  const more = extras.length ? ` ${extras.join(' ')}` : '';
  switch (light) {
    case 'checking':
      return `Checking that the dataset is being saved: ${SAVED_PARTS}.${more}`;
    case 'ok':
      return `Saving the dataset: ${SAVED_PARTS} are all growing${state?.bytes ? ` (${sizeText(state.bytes)} so far)` : ''}.${more} Click to see it in the Datasets tab.`;
    case 'problem':
      if (state?.on) return `Recording, but not everything is being saved. ${(check?.problems ?? []).join(' ')}${more}`;
      return `The last dataset was not saved whole. ${(check?.problems ?? []).join(' ')} It is in the Datasets tab.`;
    case 'saving':
      return 'Finishing the dataset: waiting for the camera video to be finished, then checking every file is in place.';
    case 'saved':
      return check?.last ? `${savedText(check.last)} Click to see it in the Datasets tab.` : 'Saved.';
    default:
      return 'Datasets keep available camera video, frame times and app records locally (~/MicroscopeMind/datasets). This light turns blue once it has checked those records. Screen, tab audio and microphone depend on the recording channels you enable and successfully capture; this light does not certify them.';
  }
}

/** What the end check found, said once Record has stopped (the hint, and the light's title). */
export function savedText(end: DatasetEndCheck): string {
  if (!end.ok) return `The dataset was not saved whole: ${end.problems.join(' ')}`;
  const counts = end.counts ?? {};
  const parts = [
    `${end.videoParts} camera video${end.videoParts === 1 ? '' : ' parts'}`,
    `${(counts.frames ?? 0).toLocaleString('en-US')} frame facts`,
    `${(counts.events ?? 0).toLocaleString('en-US')} records`,
  ];
  if (counts.pictures) parts.push(`${counts.pictures.toLocaleString('en-US')} pictures`);
  return `Dataset saved whole: ${parts.join(', ')}.`;
}

/** "Oct 6, 1:21:39 am to 2:11:16 am": the window without its length (the row shows that apart). */
export function whenText(row: Pick<DatasetRow, 'startedAtEpoch' | 'stoppedAtEpoch' | 'status'>): string {
  return spanText(row, 0).replace(/ \([^)]*\)$/, '');
}

/** The figures a row shows: what the dataset holds, each a number and what it counts. */
export function figures(row: DatasetRow): { value: string; what: string }[] {
  const out: { value: string; what: string }[] = [];
  const video = row.parts.reduce((n, p) => n + p.bytes, 0);
  out.push(row.parts.length ? { value: sizeText(video), what: row.parts.length > 1 ? `video, ${row.parts.length} parts` : 'video' }
    : { value: 'No', what: 'video' });
  out.push({ value: (row.counts.events ?? 0).toLocaleString(), what: 'records' });
  if (row.counts.frames) out.push({ value: row.counts.frames.toLocaleString(), what: 'frame facts' });
  out.push({ value: row.pictures.toLocaleString(), what: 'pictures' });
  if (row.research) out.push({ value: String(row.research), what: row.research > 1 ? 'researchers' : 'researcher' });
  return out;
}

/** The ribbon: the window from start to stop (or now), the stretches the app collected and the gaps
 * between them, each as a share of the whole. */
export function ribbon(row: Pick<DatasetRow, 'startedAtEpoch' | 'stoppedAtEpoch' | 'status' | 'runSpans'>, now: number):
  { kind: 'run' | 'gap'; left: number; width: number }[] {
  const start = row.startedAtEpoch;
  const stop = row.status === 'collecting' || row.stoppedAtEpoch == null ? now : row.stoppedAtEpoch;
  if (start == null || stop <= start) return [{ kind: 'run', left: 0, width: 1 }];
  const share = (t: number) => Math.min(1, Math.max(0, (t - start) / (stop - start)));
  const runs = (row.runSpans?.length ? row.runSpans : [[start, stop]]).map(([a, b]) => [share(a ?? start), share(b ?? stop)]);
  const out: { kind: 'run' | 'gap'; left: number; width: number }[] = [];
  let at = 0;
  for (const [a, b] of runs) {
    if (a > at) out.push({ kind: 'gap', left: at, width: a - at });
    out.push({ kind: 'run', left: a, width: Math.max(0, b - a) });
    at = Math.max(at, b);
  }
  return out;
}

/** Under the ribbon: what its gaps were, and whether its times are estimates. */
export function ribbonNote(row: DatasetRow): string {
  const notes: string[] = [];
  if (row.gaps.length) {
    notes.push(`the app was off for ${row.gaps.map((g) => durationText(g.seconds)).join(' and ')}`);
  }
  if (row.parts.some((p) => p.hasSidecar === false)) notes.push('frame times are estimates');
  return notes.join('; ');
}

const FACT_NAMES: Record<string, string> = {
  microscope: 'Microscope', eyepiece: 'Eyepiece', fieldNumber: 'Field number', objective: 'Objective',
  objectives: 'Objectives', umPerPx: 'Scale', calibrated: 'Calibration', frameRate: 'Frame rate', frameSize: 'Frame size',
  eyepieceDisc: 'Eyepiece disc', camera: 'Phone or camera', cameraDevice: 'Camera feed', cameraRequested: 'Asked of the camera',
  adapter: 'Mount', notes: 'Notes',
};

/** A fact's value as a person reads it: "25x", "18 mm", "0.69 µm a pixel". */
export function factValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '';
  if (key === 'eyepiece' || key === 'objective') return `${String(value)}x`;
  if (key === 'fieldNumber') return `${String(value)} mm`;
  if (key === 'umPerPx' && typeof value === 'number') return `${value.toPrecision(2)} µm a pixel`;
  if (key === 'frameRate' && typeof value === 'number') return `${value.toFixed(1)} a second`;
  if (key === 'frameSize') return String(value).replace('x', ' × ');
  if (key === 'eyepieceDisc' && typeof value === 'object') return `radius ${Math.round((value as { radius: number }).radius)} px`;
  if (key === 'cameraRequested' && typeof value === 'object') {
    const v = value as { size?: string; fps?: number };
    return `${(v.size ?? '').replace('x', ' × ')} at ${v.fps ?? '?'} a second`;
  }
  if (typeof value === 'object') return Object.keys(value as object).length ? JSON.stringify(value) : '';
  return String(value);
}

/** The microscope facts a dataset started with, known ones first, each with where it came from; the
 * names nobody gave, in one line. */
export function factRows(rig: Record<string, unknown>): { rows: { name: string; value: string; source: string }[]; unknown: string[] } {
  const rows: { name: string; value: string; source: string }[] = [];
  const unknown: string[] = [];
  for (const [key, raw] of Object.entries(rig)) {
    const fact = raw as { value?: unknown; source?: string; at?: number | null } | null;
    if (!fact || typeof fact !== 'object' || !('value' in fact)) continue;
    const value = factValue(key, fact.value);
    const name = FACT_NAMES[key] ?? key;
    if (value) rows.push({ name, value, source: sourceText(fact.source ?? 'unknown', fact.at) });
    else unknown.push(name.toLowerCase());
  }
  return { rows, unknown };
}

/** "you, Oct 6" / "assumed" / "measured": where a fact came from, for a person. */
export function sourceText(source: string, at?: number | null): string {
  const words: Record<string, string> = { user: 'you', measured: 'measured', 'bridge-config': 'asked of the camera',
    default: 'assumed', estimate: 'estimated', unknown: 'nobody said' };
  return `${words[source] ?? source}${source === 'user' && at ? `, ${dayText(at)}` : ''}`;
}

// The microscope form: what only the user can say, in the order they would look for it.
const RIG_FORM: { key: string; label: string; hint: string }[] = [
  { key: 'microscope', label: 'Microscope', hint: 'make and model' },
  { key: 'eyepiece', label: 'Eyepiece (x)', hint: 'e.g. 10' },
  { key: 'fieldNumber', label: 'Field number (mm)', hint: 'e.g. 18, as in WF10x/18' },
  { key: 'objectives', label: 'Objectives', hint: 'e.g. 4, 10, 40, 100' },
  { key: 'camera', label: 'Phone or camera', hint: 'e.g. iPhone 15 Pro' },
  { key: 'adapter', label: 'Mount', hint: 'e.g. clamp adapter' },
  { key: 'notes', label: 'Notes', hint: 'anything else about the setup' },
];

/** One line for the folded form: what was entered, or that nothing was. */
export function rigSummary(facts: RigState['facts']): string {
  const parts: string[] = [];
  if (facts.microscope?.value) parts.push(String(facts.microscope.value));
  if (facts.eyepiece?.value) parts.push(`${String(facts.eyepiece.value)}x eyepiece`);
  if (facts.fieldNumber?.value) parts.push(`field number ${String(facts.fieldNumber.value)} mm`);
  if (facts.camera?.value) parts.push(String(facts.camera.value));
  return parts.length ? parts.join(', ') : 'Not described yet';
}

/** What saving the session would hold, miss and leave pending (GET /api/session-dataset). */
export interface SessionReview {
  scope: string;
  scopeText: string;
  from: number;
  to: number;
  holds: {
    records: number;
    turns: number;
    interactions: Record<string, number>;
    pictures: number;
    snapshots: number;
    evidence?: number; // saved sets of outlines (populations), with their masks and measurements
    evidenceMembers?: number;
    tracks?: number; // motion measurements kept (their tracks' points and pictures)
    tracksCount?: number;
    researchers: { rid: string; name: string | null; status: string | null; kept: boolean }[];
    cameraSeconds: number;
    collected: { id: string; name: string; from: number; to: number }[];
    microscope: string[];
  };
  missing: string[];
  pending: { rid: string; name: string | null; status: string | null }[];
  destination: string;
  staysHere: string;
}

export const SESSION_SCOPES: { key: string; label: string }[] = [
  { key: 'conversation', label: 'This conversation' },
  { key: 'today', label: 'Today so far' },
];

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** The review in plain lines: its window, what it holds, what it misses, what is still being worked on,
 * and where it goes. */
export function sessionReviewLines(review: SessionReview): { window: string; holds: string[]; missing: string[]; pending: string[]; where: string } {
  const h = review.holds;
  const holds: string[] = [];
  holds.push(`${plural(h.turns, 'exchange')} in the conversation, ${plural(h.records, 'record')} in all`);
  const clicks = h.interactions.click ?? 0;
  if (clicks) holds.push(`${plural(clicks, 'click')} with what was outlined and named`);
  if (h.pictures) holds.push(`${plural(h.pictures, 'picture')} the app kept or showed`);
  if (h.snapshots) holds.push(plural(h.snapshots, 'snapshot'));
  if (h.evidence) holds.push(`${plural(h.evidence, 'saved set')} of outlines (${plural(h.evidenceMembers ?? 0, 'outline')}), with their masks and measurements`);
  if (h.tracks) holds.push(`${plural(h.tracks, 'motion measurement')} (${plural(h.tracksCount ?? 0, 'track')}), with their points and pictures`);
  const done = h.researchers.filter((r) => !review.pending.some((p) => p.rid === r.rid)).length;
  if (done) holds.push(`the work of ${plural(done, 'researcher')}`);
  if (h.cameraSeconds) holds.push(`${durationText(h.cameraSeconds)} of camera video, with its frame times`);
  if (h.microscope.length) holds.push(`your microscope: ${h.microscope.join(', ')}`);
  const pending = review.pending.map((p) => `${p.name ?? 'A researcher'} is still working: its work is saved as it is now.`);
  return {
    window: `${review.scopeText.charAt(0).toUpperCase()}${review.scopeText.slice(1)}: ${clockText(review.from)} to ${clockText(review.to)} (${durationText(review.to - review.from)})`,
    holds, missing: review.missing, pending, where: `${home(review.destination)}. ${review.staysHere}`,
  };
}

export class DatasetsView {
  private readonly root: HTMLElement;
  private readonly rename: (id: string, name: string) => Promise<unknown>;
  private readonly reveal: (id: string) => Promise<unknown>;
  private readonly saveFact: (field: string, value: string) => Promise<unknown>;
  private readonly trash: (id: string) => Promise<unknown>;
  private rig: RigState | null = null;
  private factError = '';
  private listing: DatasetListing | null = null;
  private error = '';
  private loading = false;
  private actionError = '';
  private busy = false; // a name being typed or a trash being confirmed: the list is not redrawn under it
  private renderedKey = '';
  private readonly open = new Set<string>(); // datasets whose details are open, kept across redraws
  private rigOpen: boolean | null = null; // the microscope form, as the user left it (null: open when empty)
  private timer: number | undefined;
  private reviewScope: string | null = null; // the session review, while it is open
  private review: SessionReview | null = null;
  private reviewError = '';
  private saving = false;
  private savedId = '';
  private reviewName = ''; // the name being typed, kept across redraws

  constructor(root: HTMLElement, rename: (id: string, name: string) => Promise<unknown>, reveal: (id: string) => Promise<unknown>,
    saveFact: (field: string, value: string) => Promise<unknown>, trash: (id: string) => Promise<unknown>) {
    this.root = root;
    this.rename = rename;
    this.reveal = reveal;
    this.saveFact = saveFact;
    this.trash = trash;
  }

  /** The tab is shown or left: while shown the list is read again every 15 s (sizes grow, another
   * window may have trashed one). */
  shown(visible: boolean): void {
    window.clearInterval(this.timer);
    this.timer = undefined;
    if (!visible) return;
    this.render();
    void this.load();
    this.timer = window.setInterval(() => void this.load(), 15000);
  }

  /** The microscope facts as the server keeps them (rig.json). */
  setRig(rig: RigState): void {
    this.rig = rig;
    this.render();
  }

  async load(): Promise<void> {
    if (this.loading) return;
    this.loading = true;
    try {
      const response = await fetch('/api/datasets', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Dataset service returned ${response.status}`);
      const listing = await response.json() as DatasetListing;
      if (!Array.isArray(listing.datasets)) throw new Error('Dataset service returned an invalid listing');
      this.listing = listing;
      this.error = '';
    } catch (exc) {
      this.error = `The datasets could not be read: ${exc instanceof Error ? exc.message : String(exc)}`;
    } finally {
      this.loading = false;
    }
    this.render();
  }

  render(): void {
    if (this.busy) return;
    const typing = document.activeElement instanceof HTMLInputElement && this.root.contains(document.activeElement);
    if (typing) return; // a microscope fact being typed is saved when the field is left
    const key = JSON.stringify([this.listing, this.error, this.actionError, this.rig, this.factError, this.reviewScope, this.review, this.reviewError,
      this.saving, this.savedId]);
    if (key === this.renderedKey && this.root.childElementCount) return; // nothing new: the page (and focus) stays
    this.renderedKey = key;
    const now = Date.now() / 1000;
    const children: HTMLElement[] = [this.header()];
    if (this.reviewScope !== null) children.push(this.sessionReview());
    children.push(this.microscope());
    if (this.actionError) {
      const problem = el('p', 'ds-rig-error', this.actionError); problem.setAttribute('role', 'alert'); children.push(problem);
    }
    if (this.error) children.push(el('p', 'ds-empty', this.error));
    else if (!this.listing) children.push(el('p', 'ds-empty', 'Reading the datasets…'));
    else if (!this.listing.datasets.length) children.push(this.empty());
    else {
      const list = el('ol', 'ds-list');
      for (const row of this.listing.datasets) list.append(this.item(row, now));
      children.push(list);
    }
    const focused = (document.activeElement as HTMLElement | null)?.dataset?.key;
    const scroll = this.root.scrollTop;
    this.root.replaceChildren(...children);
    this.root.scrollTop = scroll;
    if (focused) this.root.querySelector<HTMLElement>(`[data-key="${CSS.escape(focused)}"]`)?.focus();
  }

  private header(): HTMLElement {
    const head = el('header', 'ds-head');
    head.append(el('h2', '', 'Datasets'));
    const rows = this.listing?.datasets ?? [];
    if (rows.length) {
      const total = rows.reduce((n, r) => n + r.bytes, 0);
      head.append(el('p', 'ds-total', `${rows.length} ${rows.length === 1 ? 'dataset' : 'datasets'}, ${sizeText(total)}`));
    }
    head.append(el('p', 'ds-lede',
      `Assets packaged with their associated Project metadata, captured by Record or Save this project. Each is a folder in ${home(this.listing?.root ?? '~/MicroscopeMind/datasets')}. Browse individual recordings and images in the Library.`));
    if (this.reviewScope === null) {
      const save = button('Save this project as a dataset', () => void this.openReview('conversation'), 'session:open');
      save.classList.add('ds-save-session');
      head.append(save);
    }
    if (this.savedId) head.append(el('p', 'ds-saved-note', `Saved as ${this.savedId}. The project carries on.`));
    return head;
  }

  private empty(): HTMLElement {
    const box = el('div', 'ds-empty-state');
    box.append(el('span', 'ds-disc ds-disc-empty'));
    box.append(el('p', '', 'Nothing collected yet. Press Record in the header to start a dataset, and Stop when you are done.'));
    return box;
  }

  /** Your microscope: folded to one line once described. */
  private microscope(): HTMLElement {
    const facts = this.rig?.facts ?? {};
    const box = el('details', 'ds-rig');
    box.open = this.rigOpen ?? Object.keys(facts).length === 0;
    const summary = el('summary', 'ds-rig-line');
    // Only the user's own open or close is remembered (setting `open` fires toggle too).
    summary.addEventListener('click', () => {
      this.rigOpen = !box.open;
    });
    summary.append(el('span', 'ds-rig-title', 'Your microscope'), el('span', 'ds-rig-summary', rigSummary(facts)));
    box.append(summary);
    const form = el('div', 'ds-rig-form');
    form.append(el('p', 'ds-rig-note', 'Every dataset starts with these, marked as yours. The field number you give is the one sizes use.'));
    const grid = el('div', 'ds-rig-grid');
    for (const field of RIG_FORM) {
      const label = el('label', 'ds-field');
      const fact = facts[field.key];
      const input = Object.assign(document.createElement('input'), { type: 'text', value: fact?.value == null ? '' : String(fact.value),
        placeholder: field.hint });
      input.dataset.key = `rig:${field.key}`;
      const saved = el('span', 'ds-field-saved', '');
      saved.setAttribute('aria-live', 'polite');
      input.addEventListener('change', () => {
        void this.saveFact(field.key, input.value).then(() => {
          this.factError = '';
          saved.textContent = input.value.trim() ? 'Saved' : 'Cleared';
          window.setTimeout(() => (saved.textContent = ''), 1800);
        }).catch((exc: Error) => {
          this.factError = `${field.label}: ${exc.message}`;
          this.render();
        });
      });
      const name = el('span', 'ds-field-name', field.label);
      label.append(name, input, saved);
      grid.append(label);
    }
    form.append(grid);
    if (this.factError) form.append(el('p', 'ds-rig-error', this.factError));
    box.append(form);
    return box;
  }

  /** The review before Save: what it would hold, miss and leave pending, and where it goes. */
  async openReview(scope: string): Promise<void> {
    this.reviewScope = scope;
    this.review = null;
    this.reviewError = '';
    this.savedId = '';
    this.render();
    try {
      const response = await fetch(`/api/session-dataset?scope=${encodeURIComponent(scope)}`, { cache: 'no-store' });
      const body = (await response.json()) as SessionReview & { error?: string };
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      if (this.reviewScope === scope) this.review = body;
    } catch (exc) {
      this.reviewError = `The review could not be made: ${exc instanceof Error ? exc.message : String(exc)}`;
    }
    this.render();
  }

  private closeReview(): void {
    this.reviewScope = null;
    this.reviewName = '';
    this.review = null;
    this.reviewError = '';
    this.render();
  }

  private async saveSession(name: string): Promise<void> {
    if (!this.reviewScope || this.saving) return;
    this.saving = true;
    this.render();
    try {
      const response = await fetch('/api/session-dataset', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'save-session' },
        body: JSON.stringify({ scope: this.reviewScope, name }),
      });
      const body = (await response.json()) as { ok: boolean; id?: string; error?: string };
      if (!response.ok || !body.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      this.savedId = body.id ?? '';
      this.reviewName = '';
      this.reviewScope = null;
      this.review = null;
      this.open.add(this.savedId);
    } catch (exc) {
      this.reviewError = `It was not saved: ${exc instanceof Error ? exc.message : String(exc)}. Nothing was changed; try again.`;
    } finally {
      this.saving = false;
    }
    this.renderedKey = '';
    await this.load();
  }

  private sessionReview(): HTMLElement {
    const box = el('section', 'ds-review');
    box.setAttribute('aria-label', 'Save this project as a dataset');
    const scopes = el('div', 'ds-review-scopes');
    scopes.setAttribute('role', 'group');
    for (const s of SESSION_SCOPES) {
      const b = button(s.label, () => void this.openReview(s.key), `session:${s.key}`);
      b.setAttribute('aria-pressed', String(this.reviewScope === s.key));
      scopes.append(b);
    }
    box.append(el('h3', 'ds-review-title', 'Save this project as a dataset'), scopes);
    if (this.reviewError) box.append(el('p', 'ds-rig-error', this.reviewError));
    if (!this.review) {
      if (!this.reviewError) box.append(el('p', 'ds-unknown', 'Looking at what it would hold…'));
    } else {
      const lines = sessionReviewLines(this.review);
      box.append(el('p', 'ds-review-window', lines.window));
      const list = (title: string, items: string[], cls: string) => {
        if (!items.length) return;
        box.append(el('h4', '', title));
        const ul = el('ul', cls);
        for (const item of items) ul.append(el('li', '', item));
        box.append(ul);
      };
      list('It holds', lines.holds, 'ds-review-holds');
      list('Still being worked on', lines.pending, 'ds-review-pending');
      list('What it will not have', lines.missing, 'ds-review-missing');
      box.append(el('p', 'ds-unknown', `Saved to ${lines.where}`));
    }
    const name = Object.assign(document.createElement('input'), { type: 'text', maxLength: 120, className: 'ds-name-input',
      placeholder: 'A name (optional), e.g. sperm sample 2' });
    name.setAttribute('aria-label', 'Name');
    name.dataset.key = 'session:name';
    name.value = this.reviewName;
    name.addEventListener('input', () => (this.reviewName = name.value));
    const bar = el('div', 'ds-actions');
    const save = button(this.saving ? 'Saving…' : 'Save', () => void this.saveSession(name.value.trim()), 'session:save');
    save.disabled = this.saving || !this.review;
    save.classList.add('ds-primary');
    bar.append(save, button('Cancel', () => this.closeReview(), 'session:cancel'));
    box.append(name, bar);
    return box;
  }

  private item(row: DatasetRow, now: number): HTMLElement {
    const item = el('li', 'ds-item');
    item.dataset.status = row.status;
    if (row.reconstructed) item.dataset.rebuilt = 'true';

    const disc = el('span', 'ds-disc');
    if (row.thumb) {
      const img = Object.assign(document.createElement('img'), { alt: '', loading: 'lazy' as const, decoding: 'async' as const,
        src: `/api/datasets/${encodeURIComponent(row.id)}/thumb` });
      img.addEventListener('error', () => {
        img.remove();
        disc.classList.add('ds-disc-empty');
      });
      disc.append(img);
    } else {
      disc.classList.add('ds-disc-empty');
    }
    item.append(disc);

    const body = el('div', 'ds-body');
    const top = el('div', 'ds-top');
    const name = el('h3', 'ds-name', row.name || 'Untitled');
    if (!row.name) name.classList.add('ds-untitled');
    top.append(name);
    if (row.status === 'collecting') top.append(el('span', 'ds-tag ds-live', 'Collecting'));
    else if (row.endedBy === 'snapshot') top.append(el('span', 'ds-tag ds-rebuilt', 'Saved from the project'));
    else if (row.reconstructed) top.append(el('span', 'ds-tag ds-rebuilt', 'Rebuilt from the logs'));
    else if (row.endCheck?.ok) top.append(el('span', 'ds-tag ds-whole', 'Saved whole'));
    else if (row.endCheck) top.append(el('span', 'ds-tag ds-broken', 'Not saved whole'));
    const end = row.status === 'collecting' || !row.stoppedAtEpoch ? now : row.stoppedAtEpoch;
    top.append(el('span', 'ds-length', row.startedAtEpoch ? durationText(end - row.startedAtEpoch) : ''));
    body.append(top, el('p', 'ds-when', whenText(row)));

    const strip = el('div', 'ds-ribbon');
    strip.setAttribute('role', 'img');
    strip.setAttribute('aria-label', `Collected ${whenText(row)}${ribbonNote(row) ? `; ${ribbonNote(row)}` : ''}`);
    for (const part of ribbon(row, now)) {
      const seg = el('span', `ds-seg ds-${part.kind}`);
      seg.style.left = `${(part.left * 100).toFixed(3)}%`;
      seg.style.width = `${(part.width * 100).toFixed(3)}%`;
      strip.append(seg);
    }
    body.append(strip);
    const note = ribbonNote(row);
    if (note) body.append(el('p', 'ds-ribbon-note', note.charAt(0).toUpperCase() + note.slice(1)));
    if (row.endCheck && !row.endCheck.ok) body.append(el('p', 'ds-ribbon-note ds-broken', row.endCheck.problems.join(' ')));

    const figs = el('dl', 'ds-figures');
    for (const f of figures(row)) {
      const pair = el('div', 'ds-figure');
      pair.append(el('dt', '', f.what), el('dd', '', f.value)); // shown value first (column-reverse)
      figs.append(pair);
    }
    body.append(figs);
    const details = this.details(row);
    body.append(this.actions(item, row, name, details), details);
    item.append(body);
    return item;
  }

  private actions(item: HTMLElement, row: DatasetRow, name: HTMLElement, details: HTMLElement): HTMLElement {
    const bar = el('div', 'ds-actions');
    const more = button('Details', () => {
      const open = details.hidden;
      details.hidden = !open;
      more.setAttribute('aria-expanded', String(open));
      if (open) this.open.add(row.id);
      else this.open.delete(row.id);
    }, `${row.id}:details`);
    more.classList.add('ds-more');
    more.setAttribute('aria-expanded', String(this.open.has(row.id)));
    more.setAttribute('aria-controls', `ds-details-${row.id}`);
    bar.append(button('Show in Finder', () => {
      void this.reveal(row.id).then(() => { this.actionError = ''; }, (error: unknown) => {
        this.actionError = `Could not show this dataset: ${error instanceof Error ? error.message : String(error)}`;
      }).finally(() => this.render());
    }, `${row.id}:reveal`));
    bar.append(button('Rename', () => this.startRename(row, name), `${row.id}:rename`));
    if (row.status !== 'collecting') {
      bar.append(button('Move to trash', () => this.confirmTrash(item, row, bar), `${row.id}:trash`));
    }
    bar.append(more);
    return bar;
  }

  private details(row: DatasetRow): HTMLElement {
    const box = el('div', 'ds-details');
    box.id = `ds-details-${row.id}`;
    box.hidden = !this.open.has(row.id);
    const { rows, unknown } = factRows(row.rig);
    const table = (lines: { name: string; value: string; source?: string }[]) => {
      const list = el('dl', 'ds-facts');
      for (const line of lines) {
        const pair = el('div', 'ds-fact');
        const value = el('dd', '', line.value);
        if (line.source) value.append(' ', el('span', 'ds-source', line.source));
        pair.append(el('dt', '', line.name), value);
        list.append(pair);
      }
      return list;
    };
    if (rows.length || unknown.length) box.append(el('h4', '', 'At the start'));
    if (rows.length) box.append(table(rows));
    if (unknown.length) box.append(el('p', 'ds-unknown', `Not known then: ${unknown.join(', ')}.`));
    const later = [
      ...(row.enteredAfter ?? []).map((f) => ({ name: FACT_NAMES[f.field] ?? f.field, value: factValue(f.field, f.value), source: 'you' })),
      ...Object.entries(row.sample ?? {}).map(([k, v]) => ({
        name: k === 'collectedAt' ? 'Sample collected' : k === 'kind' ? 'Sample' : k, value: String(v), source: 'you' })),
    ];
    if (later.length) box.append(el('h4', '', 'Added afterwards'), table(later));
    const kept: string[] = [];
    if (row.screens) kept.push("The studio's screen recording is kept with it.");
    if (row.parts.some((p) => p.joined)) kept.push('Its video joined a recording already running, so it starts earlier.');
    if (row.counts.droppedFrames) kept.push(`${row.counts.droppedFrames.toLocaleString()} frame facts were dropped while the disk fell behind.`);
    for (const line of kept) box.append(el('p', 'ds-unknown', line));
    const citations = el('div', 'ds-citations');
    const cite = button('Models and citations', async () => {
      cite.disabled = true; citations.textContent = 'Reading recorded model provenance…';
      try {
        const response = await fetch(`/api/datasets/${encodeURIComponent(row.id)}/citations`, {cache:'no-store'});
        if (!response.ok) throw new Error('Dataset records could not be read.');
        const facts = await response.json() as {models:{model:string;events:number;sources:{title:string;url:string;licence:string}[]}[];complete:boolean;qualification:string};
        citations.replaceChildren(el('p', '', `${facts.models.length} recorded model identifiers${facts.complete ? '' : ' · incomplete coverage'}. ${facts.qualification}`));
        const lines = facts.models.map(model => `${model.model} (${model.events} recorded events; exact version not declared)\n${model.sources.map(source => `${source.title}: ${source.url} — ${source.licence}`).join('\n')}`);
        if (lines.length) {
          const text = document.createElement('textarea'); text.readOnly = true; text.setAttribute('aria-label', 'Recorded model source references'); text.value = lines.join('\n\n');
          const copy = button('Copy source references', async () => { try { await navigator.clipboard.writeText(text.value); copy.textContent = 'Copied'; } catch { text.focus(); text.select(); copy.textContent = 'Select and copy the sources'; } }, `${row.id}:cite-copy`);
          citations.append(text, copy);
        }
      } catch { citations.textContent = 'Model provenance could not be loaded. Try again; recorded data is unchanged.'; }
      finally { cite.disabled = false; }
    }, `${row.id}:cite`);
    box.append(cite, citations);
    const where = el('div', 'ds-where');
    where.append(el('span', '', home(row.path)), button('Copy path', () => void navigator.clipboard?.writeText(row.path), `${row.id}:copy`));
    box.append(where);
    return box;
  }

  /** The name becomes a field where it stands: Enter or leaving it saves, Escape keeps the old one. */
  private startRename(row: DatasetRow, name: HTMLElement): void {
    this.busy = true;
    const input = Object.assign(document.createElement('input'), { type: 'text', value: row.name, maxLength: 120,
      placeholder: 'A name, e.g. sperm sample 1', className: 'ds-name-input' });
    input.setAttribute('aria-label', 'Name');
    let done = false;
    const finish = (save: boolean) => {
      if (done) return;
      done = true;
      this.busy = false;
      const next = input.value.trim();
      if (save && next !== row.name) {
        this.busy = true; input.disabled = true;
        void this.rename(row.id, next).then(() => { this.actionError = ''; }, (error: unknown) => {
          this.actionError = `Name was not saved: ${error instanceof Error ? error.message : String(error)}`;
        }).finally(() => {
          this.busy = false; input.blur(); this.renderedKey = ''; void this.load();
        });
      }
      else {
        input.blur(); this.renderedKey = '';
        this.render();
      }
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') finish(true);
      if (event.key === 'Escape') {
        event.stopPropagation(); // ends the rename, not the tab
        finish(false);
      }
    });
    input.addEventListener('blur', () => finish(true));
    name.replaceWith(input);
    input.focus();
    input.select();
  }

  /** Asked where the button was: the dataset is moved whole to datasets/.trash/ (kept until you empty it). */
  private confirmTrash(item: HTMLElement, row: DatasetRow, bar: HTMLElement): void {
    this.busy = true;
    const ask = el('div', 'ds-actions ds-confirm');
    ask.append(el('span', '', 'Move to the trash? It stays in datasets/.trash until you empty it.'));
    const end = () => {
      this.busy = false;
      this.renderedKey = '';
    };
    const yes = button('Move to trash', () => {
      if (yes.disabled) return;
      yes.disabled = true;
      keep.disabled = true;
      void this.trash(row.id).then(() => {
        this.actionError = ''; item.classList.add('ds-leaving');
      }, (error: unknown) => {
        this.actionError = `Dataset was not moved to trash: ${error instanceof Error ? error.message : String(error)}`;
      }).finally(() => {
        end();
        void this.load();
      });
    }, `${row.id}:trash-yes`);
    yes.classList.add('ds-danger');
    const cancel = () => {
      if (yes.disabled) return; // an admitted move must settle before the row changes
      end();
      this.render();
      this.root.querySelector<HTMLButtonElement>(`[data-key="${CSS.escape(`${row.id}:trash`)}"]`)?.focus({preventScroll:true});
    };
    const keep = button('Keep it', cancel, `${row.id}:trash-no`);
    ask.append(yes, keep);
    ask.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopPropagation(); cancel();
    });
    bar.replaceWith(ask);
    yes.focus();
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text = ''): HTMLElementTagNameMap[K] {
  return Object.assign(document.createElement(tag), { className, textContent: text });
}

function button(label: string, action: () => void, key = ''): HTMLButtonElement {
  const b = Object.assign(document.createElement('button'), { type: 'button', className: 'ds-action', textContent: label });
  if (key) b.dataset.key = key;
  b.addEventListener('click', action);
  return b;
}

function home(path: string): string {
  return path.replace(/^\/Users\/[^/]+/, '~');
}

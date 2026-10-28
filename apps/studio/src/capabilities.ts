import { ACTION_GROUPS, AGENT_ONLY } from './actions-panel';
import { fijiWords, rowReady, toolboxFrame, type FijiCounts } from './toolbox';
import { toolboxBand, type AddWay } from './keystrip';
/** Capabilities (#capabilities; ADR 0007): a searchable family list with actor and verdict
 * filters. Each capability opens its current implementation, local verdict, declared evaluation
 * evidence and source references. Model details share the same responsive sheet host.
 * Everything comes from the server's capability wire; missing scientific/CI facts stay unknown.
 * The agent reads the same catalogue through its capabilities tool. */
import './capabilities.css';
import { StudioSheet } from './sheet';
import { capabilityReport, modelReport, downloadCapabilityReport, reportPicture, type ReportPicture } from './capability-report';
import type { Actor, CapabilitiesWire, Capability, DownloadRow, KeyFacts, ModelCheck, Part, Readiness, Role, RoleOption, VerdictState } from './protocol';
import { keyActions, openAddKey } from './addkey';
import { laterSteps, readinessWords } from './readiness';

export const ACTOR_ORDER: Actor[] = ['you', 'agent', 'auto', 'researcher'];
/** Who does it, on a family tile's chip when yours and the agent's share a title: "by you", "by asking". */
const CHIP_WHO: Record<Actor, string> = { you: 'by you', agent: 'by asking', auto: 'on its own', researcher: 'by a researcher' };

const CHECK_WORDS: Record<ModelCheck['state'], string> = {
  works: 'works', broken: 'broken', 'not-installed': 'not installed', 'needs-key': 'needs a key',
  'not-in-edition': 'not in this edition', 'not-checked': 'not checked',
};

/** "02:41" from a check's time (its own clock's), the date too when not today. */
export function checkTime(at: string, now: Date = new Date()): string {
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return at;
  const hm = `${String(when.getHours()).padStart(2, '0')}:${String(when.getMinutes()).padStart(2, '0')}`;
  return when.toDateString() === now.toDateString() ? hm : `${when.toISOString().slice(0, 10)} ${hm}`;
}

/** The app's own last check of a model, in words (model_checks.py): "Last check: works · 02:41", "Broken: <why> ·
 * 02:41". Shown beside the user's verdicts, never in their place. */
export function checkWords(check: ModelCheck | undefined, now: Date = new Date()): string {
  if (!check) return '';
  const when = checkTime(check.at, now) + (check.earlierBuild ? ', an earlier build' : '');
  if (check.state === 'broken') return `Broken: ${check.why || 'it failed when checked'} · ${when}`;
  if (check.state === 'works') return `Last check: works${check.loadMs != null ? ` (loaded in ${(check.loadMs / 1000).toFixed(1)} s)` : ''} · ${when}`;
  return `Last check: ${CHECK_WORDS[check.state]}${check.why ? ` (${check.why})` : ''} · ${when}`;
}

/** The app's own last check of each model a capability runs on now (its roles' choices, a fixed role's model in use):
 * shown on its card beside the user's verdict, never instead of it. */
export function capabilityChecks(wire: CapabilitiesWire, c: Capability): { role: string; model: string; check: ModelCheck }[] {
  const roles = new Map(wire.roles.map(r => [r.id, r]));
  const out: { role: string; model: string; check: ModelCheck }[] = [];
  for (const id of c.roles ?? []) {
    const role = roles.get(id);
    if (!role) continue;
    const option = role.fixed ? null : role.options.find(o => o.id === role.current);
    const check = role.fixed ? role.check : option?.check;
    if (check) out.push({ role: role.title, model: role.fixed ? role.label ?? role.current ?? id : option?.said ?? option?.label ?? id, check });
  }
  return out;
}

/** The check under way, in words ("Checking 3 of 21: micro-sam…"), or "". */
export function checkingWords(wire: CapabilitiesWire): string {
  const c = wire.checking;
  return c ? `Checking ${Math.min(c.done + 1, c.of)} of ${c.of}${c.now ? `: ${c.now}` : ''}…` : '';
}
export type Status = 'all' | 'works' | 'broken' | 'not-yet' | 'unavailable' | 'stale';

export interface Filter {
  text: string;
  actors: Set<Actor>;
  status: Status;
}

export function emptyFilter(): Filter {
  return { text: '', actors: new Set(ACTOR_ORDER), status: 'all' };
}

export function verdictOf(c: Capability): VerdictState {
  return c.verdict?.state ?? 'not-yet';
}

/** A verdict given on an older build than the one running now. */
export function stale(c: Capability, build: string): boolean {
  const given = c.verdict?.build;
  return Boolean(given && build && given !== build && verdictOf(c) !== 'not-yet');
}

/** How to reach it, in a few words: what to say, the gesture or key, or that it runs by itself. */
export function howText(c: Capability): string {
  const how = c.how;
  if (how.say) return `Say “${how.say}”`;
  if (how.gesture) return how.gesture[0].toUpperCase() + how.gesture.slice(1);
  if (how.key) return `Key ${how.key}`;
  if (c.actor === 'auto') return 'Runs by itself';
  if (how.control) return 'A button in the studio';
  return '';
}

export function matches(c: Capability, f: Filter, roles: Map<string, Role>, familyTitle = '', build = ''): boolean {
  if (!f.actors.has(c.actor)) return false;
  if (f.status === 'unavailable' && c.available) return false;
  if (f.status === 'stale' && !stale(c, build)) return false;
  if (!['all', 'unavailable', 'stale'].includes(f.status) && verdictOf(c) !== f.status) return false;
  const words = f.text.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [c.title, c.what, c.how.say ?? '', familyTitle, ...(c.roles ?? []).map((r) => roles.get(r)?.title ?? r), ...(c.parts ?? [])]
    .join(' ')
    .toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** Unserved roles still belong in the catalogue, but not in actor/verdict-filtered results. */
export function visibleModelRoles(wire: CapabilitiesWire, filter: Filter): Role[] {
  const roles = new Map(wire.roles.map(role => [role.id, role]));
  const filtered = wire.capabilities.filter(capability => matches(capability, filter, roles,
    wire.families.find(family => family.id === capability.family)?.title, wire.build));
  const unrestricted = filter.status === 'all' && ACTOR_ORDER.every(actor => filter.actors.has(actor));
  const words = filter.text.toLowerCase().split(/\s+/).filter(Boolean);
  return wire.roles.filter(role => {
    const serves = (capability: Capability) => role.powers.includes(capability.id) || role.fallbackFor?.includes(capability.id);
    if (filtered.some(serves)) return true;
    return unrestricted && !wire.capabilities.some(serves) && words.every(word =>
      [role.title, role.purpose, role.id, ...role.options.map(option => option.label)].join(' ').toLowerCase().includes(word));
  });
}

/** Availability is declared capability, never evidence of scientific quality or a CI pass. */
export function roleAvailability(role: Role): string | null {
  if (role.fixed || role.kind !== 'model') return null;
  const available = role.options.filter(option => option.available && option.id !== 'off').length;
  return available === 0 ? 'No model can run here.' : available === 1 ? 'Only one model can run here.' : null;
}

export interface Counts {
  total: number;
  works: number;
  broken: number;
  notYet: number;
  unavailable: number;
}

export function count(caps: Capability[]): Counts {
  const out = { total: caps.length, works: 0, broken: 0, notYet: 0, unavailable: 0 };
  for (const c of caps) {
    const v = verdictOf(c);
    if (v === 'works') out.works += 1;
    else if (v === 'broken') out.broken += 1;
    else out.notYet += 1;
    if (!c.available) out.unavailable += 1;
  }
  return out;
}

export interface FamilyRow {
  id: string;
  title: string;
  what: string;
  counts: Counts;
  /** Lines of four cells (you, agent, on its own, researcher): paired things share a line. */
  lines: (Capability | null)[][];
}

/** The atlas: families in order, each with the matching capabilities laid out in lines, one ability
 * a line: things that are one ability done by different actors (a `pair`) share a line, the rest
 * have one each, so the line's "What powers it" is about that ability. Families with nothing
 * matching are left out. */
export function layout(wire: CapabilitiesWire, f: Filter): FamilyRow[] {
  const roles = new Map(wire.roles.map((r) => [r.id, r]));
  const rows: FamilyRow[] = [];
  for (const fam of wire.families) {
    const caps = wire.capabilities.filter((c) => c.family === fam.id && matches(c, f, roles, fam.title));
    if (!caps.length) continue;
    const lines: (Capability | null)[][] = [];
    const pairs = new Map<string, Capability[]>();
    for (const c of caps) {
      if (c.pair) pairs.set(c.pair, [...(pairs.get(c.pair) ?? []), c]);
    }
    for (const group of pairs.values()) {
      if (group.length < 2) continue;
      let line: (Capability | null)[] = [null, null, null, null];
      for (const c of group) {
        const i = ACTOR_ORDER.indexOf(c.actor);
        if (line[i]) {
          lines.push(line);
          line = [null, null, null, null];
        }
        line[i] = c;
      }
      lines.push(line);
    }
    // Everything else on its own line, so the line's "What powers it" is about one ability.
    for (const c of caps) {
      if (!c.pair || (pairs.get(c.pair)?.length ?? 0) < 2) {
        const line: (Capability | null)[] = [null, null, null, null];
        line[ACTOR_ORDER.indexOf(c.actor)] = c;
        lines.push(line);
      }
    }
    rows.push({ id: fam.id, title: fam.title, what: fam.what ?? '', counts: count(caps), lines });
  }
  return rows;
}

/** What a power is, for the key: a model in the cloud or on this Mac, an approach you can switch,
 * an algorithm on the picture (no model), or the app's own code (no model). */
export type Mark = 'cloud' | 'mac' | 'way' | 'algorithm' | 'app';

export const MARKS: { mark: Mark; title: string; what: string }[] = [
  { mark: 'cloud', title: 'Cloud model', what: 'An AI model on a provider’s servers (Google, OpenAI, fal); switched in the Models menu' },
  { mark: 'mac', title: 'Model on this Mac', what: 'An AI model running on this computer; switched in the Models menu' },
  { mark: 'way', title: 'Behaviour setting', what: 'A choice in the Behaviour menu: how the app uses its models, such as pointing with a box or with numbered marks. Changing it never changes the model.' },
  { mark: 'algorithm', title: 'Algorithm', what: 'Measures the picture with plain maths, no AI model' },
  { mark: 'app', title: 'App code', what: 'Keeps, draws or connects things, no AI model' },
];

/** One model a model role can use: where it runs, and whether it is the one in use now. */
export interface PowerOption { id: string; label: string; mark: 'cloud' | 'mac'; active: boolean; available: boolean }

export interface Power {
  kind: 'model' | 'method' | 'policy' | 'code' | 'asked';
  mark: Mark;
  id: string;
  title: string;
  detail: string; // the current choice (a role), or what the code does (a part)
  off?: boolean; // a role switched off: listed, but not running now
  options?: PowerOption[]; // a model role: every model it can use, the one in use marked
  fixed?: boolean; // fixed in the code: no menu chooses it
}

/** The catalog card behind an atlas power. Code and policy keep their existing explanatory panel. */
export function powerTool(wire: CapabilitiesWire, power: Pick<Power, 'id' | 'kind'>): string | null {
  if (power.kind !== 'model') return null;
  return wire.models?.find(m => m.usedFor.some(u => u.role === power.id && u.current))?.id ?? null;
}

/** Where a power is chosen: fixed in the code, or which header menu switches it ("" for code). */
export function chosenIn(p: Power): string {
  if (p.kind === 'code') return '';
  if (p.fixed) return 'fixed in the code';
  return p.kind === 'model' || p.kind === 'asked' ? 'set in Models' : 'set in Behaviour';
}

/** Every model a model role can use (its options that run a model; "off" is not one), each with
 * where it runs and whether it is in use now. Empty for methods and policies. */
export function modelOptions(r: Role | undefined): PowerOption[] {
  if (!r || r.kind !== 'model') return [];
  if (r.fixed) {
    // Fixed in the code: the model it names, and the fallback the code takes when that cannot run.
    const mark = (runs?: string): 'cloud' | 'mac' => (runs === 'mac' ? 'mac' : 'cloud');
    const first = r.primary ?? { label: r.label ?? r.current ?? '', value: r.current ?? '', runs: r.runs ?? '', available: true };
    return [
      { id: 'primary', label: first.label, mark: mark(first.runs), active: !r.fallback?.active, available: first.available },
      ...(r.fallback ? [{ id: 'fallback', label: r.fallback.label, mark: mark(r.fallback.runs), active: !!r.fallback.active, available: r.fallback.available }] : []),
    ];
  }
  return r.options.filter((o) => o.runs).map((o) => ({
    id: o.id, label: o.label, mark: o.runs === 'mac' ? 'mac' : 'cloud', active: o.id === r.current, available: o.available,
  }));
}

/** A role's mark: a model by where its current choice runs (a model switched off: where its
 * options run); a method or policy is an approach. */
export function roleMark(r: Role | undefined): Mark {
  if (!r || r.kind !== 'model') return r ? 'way' : 'cloud';
  const runs = r.fixed ? r.runs : r.options.find((o) => o.id === r.current)?.runs || r.options.find((o) => o.runs)?.runs;
  return runs === 'mac' ? 'mac' : 'cloud';
}


/** Everything behind a capability, in one list: for the agent's things, which models decide to
 * do it when you ask; then its model roles (with what they are set to) and the code it runs on. */
export function powers(wire: CapabilitiesWire, c: Capability): Power[] {
  const roles = new Map(wire.roles.map((r) => [r.id, r]));
  const parts = new Map((wire.parts ?? []).map((p) => [p.id, p]));
  const choice = (r: Role | undefined): string =>
    !r ? '' : r.fixed ? r.label || r.current || '' : r.options.find((o) => o.id === r.current)?.label ?? String(r.current ?? '');
  const out: Power[] = [];
  if (c.actor === 'agent') {
    for (const id of ['fast', 'vision']) {
      const r = roles.get(id);
      if (r) out.push({ kind: 'asked', mark: roleMark(r), id, title: r.title, detail: choice(r), fixed: r.fixed });
    }
  }
  for (const id of c.roles ?? []) {
    if (out.some((p) => p.id === id)) continue; // already listed as deciding
    const r = roles.get(id);
    out.push({ kind: r?.kind ?? 'model', mark: roleMark(r), id, title: r?.title ?? id, detail: choice(r), off: r?.current === 'off',
      options: modelOptions(r), fixed: r?.fixed });
  }
  for (const id of c.parts ?? []) {
    const p: Part | undefined = parts.get(id);
    out.push({ kind: 'code', mark: p?.kind === 'algorithm' ? 'algorithm' : 'app', id, title: p?.title ?? id, detail: p?.purpose ?? '' });
  }
  return out;
}

/** What a capability falls back to, as the code does it: when, what happens, and the roles that
 * then do it (each with its choice now). */
export function fallbackPowers(wire: CapabilitiesWire, c: Capability): { when: string; does: string; powers: Power[] }[] {
  const roles = new Map(wire.roles.map((r) => [r.id, r]));
  return (c.fallbacks ?? []).map((fb) => ({
    when: fb.when, does: fb.does,
    powers: fb.roles.map((id) => {
      const r = roles.get(id);
      const label = !r ? '' : r.fixed ? r.label || r.current || '' : r.options.find((o) => o.id === r.current)?.label ?? String(r.current ?? '');
      return { kind: r?.kind ?? 'model', mark: roleMark(r), id, title: r?.title ?? id, detail: label, off: r?.current === 'off', options: modelOptions(r), fixed: r?.fixed };
    }),
  }));
}

/** What powers a line of the atlas: its capabilities' roles and code, each once. */
export function linePowers(wire: CapabilitiesWire, line: (Capability | null)[]): Power[] {
  const out: Power[] = [];
  for (const c of line) {
    if (!c) continue;
    for (const p of powers(wire, c)) {
      if (p.kind !== 'asked' && !out.some((q) => q.kind === p.kind && q.id === p.id)) out.push(p);
    }
  }
  return out;
}

/** A family's powers: what every one of its lines uses, said once, and what each line adds. */
export function familyPowers(wire: CapabilitiesWire, lines: (Capability | null)[][]): { shared: Power[]; lines: Power[][] } {
  const each = lines.map((line) => linePowers(wire, line));
  const key = (p: Power) => `${p.kind === 'code' ? 'part' : 'role'}:${p.id}`;
  const shared = each.length > 1 ? each[0].filter((p) => each.every((ps) => ps.some((q) => key(q) === key(p)))) : [];
  const sharedKeys = new Set(shared.map(key));
  return { shared, lines: each.map((ps) => ps.filter((p) => !sharedKeys.has(key(p)))) };
}

// --- the page ---------------------------------------------------------------------------------
const SVG = 'http://www.w3.org/2000/svg';
const GLYPHS: Record<Actor, string> = {
  // a pointer, a spark, a loop, a flask: drawn, not lettered
  you: 'M5 2 L5 15 L8.5 11.5 L11 17 L13 16 L10.5 10.5 L15 10.5 Z',
  agent: 'M10 1.5 L12 8 L18.5 10 L12 12 L10 18.5 L8 12 L1.5 10 L8 8 Z',
  auto: 'M10 3 A7 7 0 1 1 3.4 7.6 M3 2.5 L3.4 7.6 L8.4 7',
  researcher: 'M7.5 2 L12.5 2 M8.5 2 L8.5 8 L3.5 16.5 Q3 18 4.5 18 L15.5 18 Q17 18 16.5 16.5 L11.5 8 L11.5 2 M5.5 13 L14.5 13',
};

const MARK_GLYPHS: Record<Mark, string> = {
  // a cloud, a chip, two sliders, a measured line, braces
  cloud: 'M6 15.5 H14.5 A3.5 3.5 0 0 0 14.5 8.5 A5 5 0 0 0 5 8 A3.75 3.75 0 0 0 6 15.5 Z',
  mac: 'M6 6 H14 V14 H6 Z M8.5 3 V6 M11.5 3 V6 M8.5 14 V17 M11.5 14 V17 M3 8.5 H6 M3 11.5 H6 M14 8.5 H17 M14 11.5 H17',
  way: 'M3 6.5 H17 M3 13.5 H17 M8 4 V9 M12.5 11 V16',
  algorithm: 'M2.5 14.5 L7 9 L10.5 12 L17.5 4.5 M2.5 17.5 H17.5',
  app: 'M7.5 3.5 Q4.5 3.5 4.5 6.5 V8 Q4.5 10 2.5 10 Q4.5 10 4.5 12 V13.5 Q4.5 16.5 7.5 16.5 M12.5 3.5 Q15.5 3.5 15.5 6.5 V8 Q15.5 10 17.5 10 Q15.5 10 15.5 12 V13.5 Q15.5 16.5 12.5 16.5',
};

function drawn(d: string, cls: string): SVGSVGElement {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('cap-glyph', cls);
  const path = document.createElementNS(SVG, 'path');
  path.setAttribute('d', d);
  svg.append(path);
  return svg;
}

export function glyph(actor: Actor): SVGSVGElement {
  return drawn(GLYPHS[actor], `cap-glyph-${actor}`);
}

/** The key's mark for a power: the same drawing on its chip, in its card and in the key. */
export function markGlyph(mark: Mark): SVGSVGElement {
  return drawn(MARK_GLYPHS[mark], `cap-mark-${mark}`);
}

const MARK_TITLES = new Map(MARKS.map((m) => [m.mark, m.title]));

type Attrs = Record<string, string | boolean | undefined>;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: (Node | string | null)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === false) continue;
    if (key === 'class') node.className = String(value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children) if (child !== null) node.append(child);
  return node;
}

const STATUS_WORDS: Record<VerdictState, string> = { works: 'Works', broken: 'Broken', 'not-yet': 'Not checked yet' };
const EVIDENCE_WORDS = (ref: string): string =>
  ref.startsWith('sim:') ? `Simulated scenario “${ref.slice(4)}”` : ref.split('/').pop()?.replace(/\.(json|md|py)$/, '') ?? ref;

/** A preference's value in words: on or off, a number, or "Not set". */
export const prefValue = (v: unknown): string =>
  v === null || v === undefined || v === '' ? 'Not set' : v === true ? 'On' : v === false ? 'Off' : String(v);

/** Declared current choices only: defaults, unused alternatives and inferred citations do not qualify. */
export function capabilityEvidence(wire: CapabilitiesWire, capability: Capability) {
  const roles = [...new Set(capability.roles ?? [])].map(id => {
    const role = wire.roles.find(candidate => candidate.id === id);
    const option = role?.options.find(candidate => candidate.id === role.current);
    return {id, title:role?.title ?? id, choice:option?.label ?? role?.current ?? null,
      note:option?.note ?? null,
      sources:(option?.sources ?? []).filter(source => /^https?:\/\//.test(source.url))
        .map(source => ({title:source.title,url:source.url,licence:source.licence}))};
  });
  return {roles, checks:[...(capability.evidence ?? [])]};
}

export function discoveryAction(c: Capability, wire: CapabilitiesWire): { label: string; model?: string; capability?: string; href?: string } | null {
  if(c.how.href?.startsWith('#workflows?'))return {label:'Open scientific workflow',href:c.how.href};
  if (c.catalog) {
    const row = wire.models?.find(m => m.id === c.catalog);
    const setup = row && !row.execution && ['needs-setup', 'broken'].includes(row.status) && (row.next || row.setups?.some(s => s.plan));
    return { label: c.available ? 'Try' : setup ? 'Turn on' : 'View details', model: c.catalog };
  }
  if (!c.available) {
    const model = wire.models?.find(m => (m.status === 'needs-setup' || m.status === 'broken') && m.next
      && m.usedFor.some(u => c.roles?.includes(u.role) && u.current));
    if (model) return { label: 'Turn on', model: model.id };
  }
  return c.actor === 'agent' && c.how.tool && ACTION_GROUPS.some(g => g.family === c.family) && !AGENT_ONLY.has(c.id)
    ? { label: 'Configure and run', capability: c.id } : null;
}

export interface ToolboxLinks { action?: (id: string) => void; fiji?: () => Promise<{ summary: FijiCounts }>; add?: (way: AddWay) => void }

export class CapabilityPage {
  private wire: CapabilitiesWire | null = null;
  private filter = emptyFilter();
  private shown: string | null = null; // what the card shows: a capability id, or "role:<id>" / "part:<id>"
  private from: string | null = null; // the capability a role or part card was opened from (its "back")
  private pinned = false; // the card stays (clicked), not just hovered
  private noting = false; // a report/citation sheet retains its original card context
  private hoverTimer = 0;
  private view: 'family' | 'model' = 'family';
  private band: HTMLElement;
  private readonly toolbox: ToolboxLinks;
  private fiji: FijiCounts | null = null; // Fiji's counts (inventories.py), read once the view is shown: its tile's lines
  private fijiAsked = false;
  private fijiFailed = false; // the read failed: said on the tile with Try again, never asked again on a wire message
  private statusSelect!: HTMLSelectElement;
  private readonly body: HTMLElement;
  private readonly card: HTMLElement;
  private readonly summary: HTMLElement;
  private readonly agentText: HTMLElement;
  private readonly modelOpen = new Set<string>();

  private readonly root: HTMLElement;
  private readonly validate: (id: string, state: VerdictState, note: string) => Promise<unknown> | void;
  private readonly choose: (role: string, option: string) => void;
  private readonly checkNow: (() => Promise<unknown> | void) | null;
  private readonly download: Downloader | null;
  private readonly checkButton: HTMLButtonElement;
  private readonly checkStatus: HTMLElement;

  constructor(root: HTMLElement, validate: (id: string, state: VerdictState, note: string) => Promise<unknown> | void,
    choose: (role: string, option: string) => void, checkNow: (() => Promise<unknown> | void) | null = null,
    download: Downloader | null = null, toolbox: ToolboxLinks = {}) {
    this.root = root;
    this.toolbox = toolbox;
    this.validate = validate;
    this.choose = choose;
    this.checkNow = checkNow;
    this.download = download;
    // Check now (model_checks.py): every model on this Mac runs once on a real frame, inside the app, one at a time.
    this.checkButton = el('button', { type: 'button', class: 'mm-control cap-check-now', hidden: !checkNow,
      title: 'Run every model on this Mac once on a real frame and show which work (the cloud models: make capability-check-cloud)' },
      'Check now');
    this.checkStatus = el('span', { class: 'cap-check-status', role: 'status', 'aria-live': 'polite' });
    this.checkButton.addEventListener('click', () => {
      if (!this.checkNow) return;
      this.checkButton.disabled = true;
      this.checkStatus.textContent = 'Starting the check…';
      void Promise.resolve().then(() => this.checkNow!()).catch(() => {
        this.checkStatus.textContent = 'The check could not start. Try again.';
        this.checkButton.disabled = false;
      });
    });
    this.body = el('div', { class: 'cap-body' });
    this.card = el('aside', { tabindex: '-1', class: 'cap-card', hidden: true, role: 'dialog', 'aria-label': 'Capability' });
    this.summary = el('p', { class: 'cap-summary toolbox-head-sub', 'aria-live': 'polite' });
    this.agentText = el('pre', { class: 'cap-agent-text' });
    try {
      this.view = localStorage.getItem('micromind.capabilities.view') === 'model' ? 'model' : 'family';
    } catch { /* Preferences are optional. */ }
    // The frame both Toolbox views share (toolbox.ts): tabs, the band (your keys, Add a capability), then this header.
    this.band = toolboxBand(undefined, undefined, { add: (way) => this.toolbox.add?.(way) }, 'all');
    root.replaceChildren(toolboxFrame('all', this.band, this.header(), this.body), this.card);
    root.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.cardOpen()) {
        e.stopPropagation();
        this.closeCard();
      }
    });
    // A card shown by hovering goes when the pointer leaves it (a kept one stays).
    this.card.addEventListener('mouseleave', () => {
      if (!this.pinned) this.hoverTimer = window.setTimeout(() => this.hideHover(), 220);
    });
    this.card.addEventListener('mouseenter', () => window.clearTimeout(this.hoverTimer));
    this.card.addEventListener('pointerdown', () => { this.pinned = true; }); // using it keeps it
    // A click anywhere but the card or a chip puts the card away.
    document.addEventListener('pointerdown', (e) => {
      const target = e.target as Node | null;
      if (!this.cardOpen() || !target || target instanceof Element && target.closest('.studio-sheet')) return;
      if (this.card.contains(target) || (target instanceof Element && target.closest('.cap-chip, .cap-pw'))) return;
      this.closeCard(false);
    });
  }

  update(wire: CapabilitiesWire): void {
    const cardFocused = this.card.contains(document.activeElement);
    const optionFocus = this.root.contains(document.activeElement) && document.activeElement instanceof HTMLElement
      ? document.activeElement.dataset.optionFocus : undefined;
    this.wire = wire;
    this.agentText.textContent = wire.agentText;
    const band = toolboxBand(wire.setup?.keys, wire.setup?.counts, { add: (way) => this.toolbox.add?.(way) }, 'all');
    if (!this.band.contains(document.activeElement)) { this.band.parentElement?.insertBefore(band, this.band); this.band.remove(); this.band = band; } // never under a press
    if (!this.root.hidden) this.readFiji(); // only while this view is shown: never on every studio's capabilities message
    const scroll = this.root.scrollTop;
    this.render();
    this.root.scrollTop = scroll; // a verdict or a setting redraws the page: it stays where it was
    if (this.shown && !this.noting) {
      this.fillCard(this.shown);
      if (cardFocused) (this.optionControl(optionFocus) ?? this.card).focus({ preventScroll: true });
      const anchor = this.anchor(this.shown);
      if (anchor && !this.isPower(this.shown)) this.place(anchor); // a role or part card stays where it was opened
    }
    if (!cardFocused) this.optionControl(optionFocus)?.focus({ preventScroll: true });
  }

  cardOpen(): boolean {
    return !this.card.hidden;
  }

  closeCard(refocus = true): void {
    this.card.hidden = true;
    this.pinned = false;
    this.noting = false;
    window.clearTimeout(this.hoverTimer);
    const chip = refocus && this.shown ? this.anchor(this.shown) : null;
    this.shown = null;
    this.from = null;
    if (chip) {
      chip.focus();
    }
  }

  // --- header: search, who, status ---------------------------------------------------------
  private header(): HTMLElement {
    const search = el('input', { type: 'search', class: 'cap-search', placeholder: 'What do you want to do?', 'aria-label': 'Search capabilities' });
    search.addEventListener('input', () => {
      this.filter.text = search.value;
      this.render();
    });
    const who = el('select', { class: 'cap-who capc-select', 'aria-label': 'Who does it' },
      el('option', { value: 'all' }, 'Anyone'),
      ...ACTOR_ORDER.map(a => el('option', { value: a }, this.actorWord(a))));
    who.addEventListener('change', () => {
      this.filter.actors = new Set(who.value === 'all' ? ACTOR_ORDER : [who.value as Actor]);
      this.render();
    });
    const status = this.statusSelect = el('select', { class: 'cap-status capc-select', 'aria-label': 'Show capabilities' });
    for (const [value, label] of [['all', 'Everything'], ['broken', 'Broken'], ['not-yet', 'Not checked yet'], ['works', 'Works'], ['unavailable', 'Not available here'], ['stale', 'To check again']]) {
      status.append(el('option', { value, 'data-label': label }, label));
    }
    status.addEventListener('change', () => { this.filter.status = status.value as Status; this.render(); });
    const views = el('div', { class: 'cap-views', role: 'group', 'aria-label': 'Capability view' });
    for (const [value, label] of [['family', 'By family'], ['model', 'By model']] as const) {
      const button = el('button', { type: 'button', 'aria-pressed': String(this.view === value) }, label);
      button.addEventListener('click', () => {
        this.view = value;
        try { localStorage.setItem('micromind.capabilities.view', value); } catch { /* optional */ }
        views.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
        this.closeCard(false); this.render();
      });
      views.append(button);
    }
    return el('header', { class: 'toolbox-head capc-head' },
      el('div', { class: 'toolbox-head-title' }, el('h2', { id: 'capabilities-heading' }, 'Everything it can do'), this.summary),
      el('div', { class: 'toolbox-head-acts' }, views, el('span', { class: 'cap-check' }, this.checkButton, this.checkStatus)),
      el('div', { class: 'capc-filters' }, search,
        el('label', { class: 'capc-field' }, 'Who', who), el('label', { class: 'capc-field' }, 'Show', status)));
  }

  /** This view was shown (#toolbox/all): Fiji's counts are read now if they haven't been, a failed read tried again. */
  opened(): void {
    this.fijiFailed = false;
    this.readFiji();
  }

  /** Fiji's counts, once: the same list Toolbox reads (GET /api/catalog/inventories/fiji). Asked only while the view is
   * shown; a failure is said on its tile and asked again only by its Try again or the view's next showing. */
  private readFiji(): void {
    if (this.fiji || this.fijiAsked || this.fijiFailed || !this.toolbox.fiji) return;
    this.fijiAsked = true;
    void this.toolbox.fiji().then((got) => { this.fiji = got.summary; }, () => { this.fijiFailed = true; })
      .finally(() => { this.fijiAsked = false; if (this.wire) this.render(); });
  }

  private actorWord(actor: Actor): string {
    return this.wire?.actors[actor] ?? { you: 'You', agent: 'I', auto: 'I, in the background', researcher: 'My researchers' }[actor];
  }

  // --- body ---------------------------------------------------------------------------------
  private render(): void {
    for (const fold of this.body.querySelectorAll<HTMLDetailsElement>('details[data-model-fold]')) {
      if (fold.open) this.modelOpen.add(fold.dataset.modelFold!);
      else this.modelOpen.delete(fold.dataset.modelFold!);
    }
    const wire = this.wire;
    if (!wire) {
      this.body.replaceChildren(el('p', { class: 'cap-empty' }, 'Waiting for the app…'));
      return;
    }
    const all = count(wire.capabilities);
    const broken = el('button', { type: 'button', class: 'capc-summary-link' }, `${all.broken} broken`);
    broken.addEventListener('click', () => {
      this.filter.status = this.filter.status === 'broken' ? 'all' : 'broken';
      this.statusSelect.value = this.filter.status; this.render();
    });
    // What can run here leads; your verdicts (works, broken, not checked yet) are the Show menu's, broken said when any.
    this.summary.replaceChildren(`${all.total} capabilities · ${all.total - all.unavailable} run here`, ...(all.broken ? [' · ', broken] : []));
    const checking = checkingWords(wire);
    this.checkButton.disabled = !!checking;
    if (checking || this.checkStatus.textContent?.startsWith('Checking') || this.checkStatus.textContent === 'Starting the check…')
      this.checkStatus.textContent = checking || 'Checked: each model’s state is beside it below.';
    const roles = new Map(wire.roles.map(r => [r.id, r]));
    for (const option of this.statusSelect.options) {
      const n = wire.capabilities.filter(c => matches(c, { ...emptyFilter(), status: option.value as Status }, roles, '', wire.build)).length;
      option.textContent = `${option.dataset.label} (${n})`;
      option.disabled = n === 0 && option.value !== this.filter.status;
    }
    const agent = el('details', { class: 'cap-agent' }, el('summary', {}, 'What the agent reads'), this.agentText);
    this.body.replaceChildren(this.view === 'family' ? this.familyTiles(wire) : this.modelsView(wire),
      wire.pending ? el('p', { class: 'cap-local' }, `${wire.pending} verdicts · Saved locally · not committed`) : el('span'),
      el('p', {class:'cap-local cap-usage-coverage'}, 'Usage history is incomplete. ', wire.usageCoverage?.reason ?? 'Recorded use is unavailable.', ' Recorded tool activity appears in capability details; missing records do not mean unused.'),
      this.preferences(wire), agent);
  }

  /** Every family at once, as tiles (Will, 2026-10-15: one row per family, folded, was too spread out to take in): what it
   * covers, how many of its capabilities run here, and each capability as a chip whose
   * card opens on a click. Nothing folds; the filters hide what doesn't match. */
  private familyTiles(wire: CapabilitiesWire): HTMLElement {
    const roles = new Map(wire.roles.map(r => [r.id, r]));
    const grid = el('div', { class: 'capt-grid' });
    for (const family of wire.families) {
      const all = wire.capabilities.filter(c => c.family === family.id);
      const caps = all.filter(c => matches(c, this.filter, roles, family.title, wire.build));
      if (!caps.length) continue;
      // What each key would add is the Keys band's, from the server's own count (setup.counts.unlock), never guessed here.
      const tally = count(all);
      const line = el('p', { class: 'capt-tally' }, `${tally.total - tally.unavailable} of ${tally.total} run here`);
      if (tally.broken) line.append(' · ', el('span', { class: 'capt-broken' }, `${tally.broken} broken`));
      // Stable pair grouping keeps equivalent user/agent gestures together.
      caps.sort((a, b) => (a.pair ?? a.id).localeCompare(b.pair ?? b.id));
      const chips = el('div', { class: 'capt-chips' });
      // The same thing done by you and by the agent is two capabilities with one title: each chip then says who.
      const twice = new Set(caps.map(c => c.title).filter((t, i, all) => all.indexOf(t) !== i));
      for (const c of caps) {
        const chip = el('button', { type: 'button', class: 'capt-chip cap-chip', 'data-cap': c.id,
          'data-verdict': verdictOf(c), 'data-unavailable': !c.available, 'data-stale': stale(c, wire.build),
          title: c.available ? c.what : `Not available here: ${c.why || 'no available implementation'}`,
          'aria-label': `${c.title}: ${STATUS_WORDS[verdictOf(c)]}${c.available ? '' : ', not available here'}` },
          el('span', { class: 'cap-dot', 'aria-hidden': 'true' }), el('span', { class: 'capt-chip-name' }, c.title),
          twice.has(c.title) ? el('span', { class: 'capt-chip-who' }, CHIP_WHO[c.actor]) : null);
        chip.addEventListener('click', () => this.showCard(c.id, chip, true)); chips.append(chip);
      }
      grid.append(el('section', { class: 'capt-tile', 'data-family': family.id, 'aria-labelledby': `capt-${family.id}` },
        el('header', { class: 'capt-head' }, el('h3', { id: `capt-${family.id}` }, family.title),
          family.what ? el('p', { class: 'capt-what' }, family.what) : null),
        line, chips));
    }
    const fiji = this.fiji, text = this.filter.text.trim().toLowerCase();
    const fijiShown = this.filter.status === 'all' && this.filter.actors.size === ACTOR_ORDER.length && (!text || 'fiji imagej'.includes(text));
    if (!fiji && this.fijiFailed && fijiShown) {
      const again = el('button', { type: 'button', class: 'capc-summary-link capt-fiji-again' }, 'Try again');
      again.addEventListener('click', () => { this.fijiFailed = false; this.readFiji(); });
      grid.append(el('section', { class: 'capt-tile capt-fiji', 'data-family': 'fiji', 'aria-labelledby': 'capt-fiji' },
        el('header', { class: 'capt-head' }, el('h3', { id: 'capt-fiji' }, 'Fiji')),
        el('p', { class: 'capt-tally' }, 'Couldn’t read Fiji’s list. ', again)));
    }
    if (fiji && this.filter.status === 'all' && this.filter.actors.size === ACTOR_ORDER.length && (!text || 'fiji imagej'.includes(text))) {
      const words = fijiWords(fiji, wire.models);
      grid.append(el('section', { class: 'capt-tile capt-fiji', 'data-family': 'fiji', 'aria-labelledby': 'capt-fiji' },
        el('header', { class: 'capt-head' }, el('h3', { id: 'capt-fiji' }, 'Fiji'),
          el('p', { class: 'capt-what' }, 'ImageJ’s Fiji, kept by the app. Installed in Fiji is not usable here: the app runs a function only through a reviewed connection.')),
        el('p', { class: 'capt-tally' }, words.runs), el('p', { class: 'capt-tally capt-quiet' }, words.listed),
        // A connected function is drawn as not available here unless its own row is ready (readiness.py, ADR 0019).
        el('div', { class: 'capt-chips' }, ...(fiji.bound ?? []).map(b => {
          const ready = rowReady(wire.models, b.id), row = wire.models?.find(r => r.id === b.id);
          return el('a', { class: 'capt-chip', href: `#toolbox/${b.id}`, 'data-unavailable': !ready,
            title: ready ? 'Ready here' : `Not ready on this Mac: ${row?.word ?? 'its state is not known yet'}`,
            'aria-label': `${b.name}${ready ? '' : ', not ready on this Mac'}` },
            el('span', { class: 'cap-dot', 'aria-hidden': 'true' }), el('span', { class: 'capt-chip-name' }, b.name));
        }), el('a', { class: 'capt-chip capt-more', href: '#toolbox/fiji-extensions' }, 'Every extension, on Toolbox'))));
    }
    if (!grid.childElementCount) grid.append(el('p', { class: 'cap-empty' }, 'No capabilities match these filters.'));
    return grid;
  }

  private modelsView(wire: CapabilitiesWire): HTMLElement {
    const out = el('div', { class: 'cap-models' });
    const visible = visibleModelRoles(wire, this.filter);
    for (const [group, title] of [['models', 'Models'], ['behaviour', 'Behaviour'], ['fixed', 'Fixed in the code']]) {
      const cards = visible.filter(r => (r.fixed ? 'fixed' : r.kind === 'model' ? 'models' : 'behaviour') === group);
      if (!cards.length) continue;
      const section = el('section', { class: 'cap-model-group' }, el('h3', {}, title));
      for (const role of cards) {
        const key = `role:${role.id}`;
        const button = el('button', { type: 'button', class: 'cap-pw cap-model-title', 'data-power': key }, role.title);
        button.addEventListener('click', () => this.showPower(key, button, null));
        const current = role.options.find(o => o.id === role.current);
        const alternatives = role.fixed ? 0 : role.options.filter(o => o.available && o.id !== role.current && o.id !== 'off').length;
        const facts = role.fixed ? `${role.label ?? role.current ?? 'Not declared'} · Fixed${role.fallback ? ' · 1 fixed fallback' : ''}`
          : `${current?.label ?? role.current ?? 'Not selected'} · ${alternatives} available alternatives`;
        const check = role.fixed ? role.check : current?.check;
        section.append(el('article', { class: 'cap-model-card' }, button, el('p', {}, role.purpose), el('p', { class: 'cap-model-facts' }, facts),
          check ? el('p', { class: 'cap-model-check', 'data-check': check.state }, checkWords(check)) : null,
          !role.fixed ? el('p', {class:'cap-model-facts'}, defaultWords(role)) : null,
          !wire.capabilities.some(c => role.powers.includes(c.id) || role.fallbackFor?.includes(c.id))
            ? el('p', {class:'cap-model-facts'}, 'Serves nothing yet in this capability catalogue.') : null,
          role.operation ? el('details', {class:'cap-role-contract'},
            el('summary', {}, `Produces ${role.operation.returns} · contract ${role.operation.id}`),
            el('p', {}, 'Declared inputs'), el('pre', {}, JSON.stringify(role.operation.accepts, null, 2)))
            : el('p', {class:'cap-model-facts'}, 'Input/output contract not declared.'),
          el('p', {class:'cap-model-facts'}, 'CI results for this build are not declared.'),
          roleAvailability(role) ? el('p', {class:'cap-unavailable'}, roleAvailability(role)!) : null,
          el('details', {'data-model-fold':role.id,open:this.modelOpen.has(role.id)}, el('summary', {}, 'Options and declared facts'), ...role.options.map(option => this.optionRow(role, option)))));
      }
      out.append(section);
    }
    if (!out.childElementCount) out.append(el('p', { class: 'cap-empty' }, 'No model roles match these filters.'));
    return out;
  }

  /** What you or the agent can set that is not a choice of model: below the family list, with its value now. */
  private preferences(wire: CapabilitiesWire): HTMLElement {
    return el('section', { class: 'cap-prefs', 'aria-label': 'Preferences' },
      el('h3', {}, 'Preferences'),
      el('p', { class: 'cap-prefs-what' }, 'Settings that are not a model: you set them in the studio, and the agent can set the marked ones when you ask.'),
      el('div', { class: 'cap-prefs-list' },
        ...wire.preferences.map((p) => el('article', { class: 'cap-pref' },
          el('div', { class: 'cap-pref-head' }, el('h4', {}, p.title),
            el('span', { class: 'cap-pref-value' }, prefValue(p.value))),
          el('p', { class: 'cap-purpose' }, p.purpose),
          p.agentCanChange.length ? el('span', { class: 'cap-badge', title: p.agentCanChange.join(', ') }, 'The agent can change this') : null))));
  }

  private hideHover(): void {
    if (!this.pinned && !this.card.matches(':hover')) {
      this.card.hidden = true;
      this.shown = null;
    }
  }

  private showCard(id: string, chip: HTMLElement, pin: boolean): void {
    this.shown = id;
    this.from = null;
    this.pinned = pin || this.pinned;
    this.noting = false;
    this.fillCard(id);
    this.card.hidden = false;
    this.place(chip);
    if (pin) this.card.focus({ preventScroll: true });
  }

  /** The card beside its chip, inside the page. */
  private place(chip: HTMLElement): void {
    const page = this.root.getBoundingClientRect();
    const box = chip.getBoundingClientRect();
    const width = Math.min(360, page.width - 24);
    // In the page's own coordinates, which scroll both ways: beside the chip, inside what is visible.
    const x0 = this.root.scrollLeft + 12;
    const x1 = this.root.scrollLeft + this.root.clientWidth - 12;
    let left = box.right - page.left + this.root.scrollLeft + 10;
    if (left + width > x1) left = box.left - page.left + this.root.scrollLeft - width - 10;
    left = Math.max(x0, Math.min(left, x1 - width));
    this.card.style.width = `${width}px`;
    // Beside the chip, but kept inside what is visible: a chip near the bottom gets its card above.
    const visibleBottom = this.root.scrollTop + this.root.clientHeight - 8;
    let top = box.top - page.top + this.root.scrollTop - 12;
    top = Math.min(top, visibleBottom - this.card.offsetHeight);
    top = Math.max(this.root.scrollTop + 8, top);
    this.card.style.left = `${left}px`;
    this.card.style.top = `${top}px`;
  }

  /** The chip or powers button a card belongs to, if it is on the page. */
  private anchor(shown: string): HTMLElement | null {
    const attr = this.isPower(shown) ? 'data-power' : 'data-cap';
    return this.root.querySelector<HTMLElement>(`[${attr}="${CSS.escape(shown)}"]`);
  }

  private isPower(shown: string): boolean {
    return shown.startsWith('role:') || shown.startsWith('part:');
  }

  /** A model, method, policy or part of the code, in the same card: what it is, its choice, what it powers. */
  private showPower(key: string, anchor: HTMLElement, from: string | null, pin = true): void {
    window.clearTimeout(this.hoverTimer);
    this.shown = key;
    this.from = from;
    this.pinned = pin || this.pinned;
    this.noting = false;
    this.fillCard(key);
    this.card.hidden = false;
    if (!from) this.place(anchor); // from a capability’s card: it stays in place
    if (pin) this.card.focus({ preventScroll: true });
  }

  private async showUsage(node: HTMLElement, tool: string): Promise<void> {
    try {
      const response = await fetch('/api/capabilities/usage', {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json() as {tools: Record<string,{count:number;failed:number;last:number}>;from:number|null;through:number|null;reason:string};
      if (!node.isConnected) return;
      const use = data.tools[tool];
      node.textContent = use ? `${tool}: ${use.count} recorded executions (${use.failed} failed/cancelled). Last ${new Date(use.last * 1000).toLocaleString()}. ${data.reason}`
        : `No recorded executions of ${tool} in this retained sample. Earlier or unrecorded use is unknown.`;
      node.title = data.from === null ? 'Collection period unavailable' : `Retained sample: ${new Date(data.from * 1000).toISOString()} through ${new Date((data.through ?? data.from) * 1000).toISOString()}`;
    } catch { /* The existing unknown-history explanation stays visible. */ }
  }

  private fillCard(id: string): void {
    if (this.isPower(id)) {
      this.fillPower(id);
      return;
    }
    const wire = this.wire;
    const c = wire?.capabilities.find((x) => x.id === id);
    if (!wire || !c) return;
    const v = verdictOf(c);
    const media = c.media
      ? (c.media.endsWith('.mp4')
        ? el('video', { src: `/api/capabilities/media/${c.media}`, autoplay: true, loop: true, muted: true, playsinline: true })
        : el('img', { src: `/api/capabilities/media/${c.media}`, alt: `What “${c.title}” looks like` }))
      : el('div', { class: 'cap-media-empty' }, glyph(c.actor), el('span', {}, 'No preview yet'));
    const powerRow = (p: Power) => {
      const where = chosenIn(p);
      const detail = p.off ? `Off, ${where}` : [p.detail, where].filter(Boolean).join(', ');
      const tool = powerTool(wire, p);
      const row = el(tool ? 'a' : 'button', { ...(tool ? { href: `#toolbox/${tool}` } : { type: 'button' }), class: `cap-power-row cap-mark-${p.mark}`, 'data-off': p.off },
        el('span', { class: 'cap-power-kind' }, markGlyph(p.mark), p.kind === 'asked' ? 'Decides' : MARK_TITLES.get(p.mark) ?? ''),
        el('span', { class: 'cap-power-title' }, p.title),
        detail ? el('span', { class: 'cap-power-detail' }, detail) : null);
      if (!tool) row.addEventListener('click', () => this.showPower(p.kind === 'code' ? `part:${p.id}` : `role:${p.id}`, row, c.id));
      return row;
    };
    const powerRows = powers(wire, c).map(powerRow);
    // What the code does when its own model cannot run (verified in the code, not a wish list).
    const fallbackBlocks = fallbackPowers(wire, c).map((fb) => el('div', { class: 'cap-fallback' },
      el('p', { class: 'cap-powered-head' }, `When ${fb.when}`),
      el('p', { class: 'cap-fallback-does' }, `${fb.does[0].toUpperCase()}${fb.does.slice(1)}${c.viaFallback ? ' (that is what runs here now)' : ''}.`),
      ...fb.powers.map(powerRow)));
    const verdict = c.verdict;
    const when = verdict && v !== 'not-yet'
      ? el('p', { class: 'cap-verdict-when' }, `${STATUS_WORDS[v]}, ${verdict.at.replace('T', ' ').slice(0, 16)}` +
        (stale(c, wire.build) ? '; on an older build, check it again' : '') + (verdict.note ? `: “${verdict.note}”` : ''))
      : null;
    const buttons = el('div', { class: 'cap-verdict', role: 'group', 'aria-label': 'Your verdict' },
      ...(['works', 'broken', 'not-yet'] as VerdictState[]).map((state) => {
        const b = el('button', { type: 'button', class: `cap-v cap-v-${state}`, 'aria-pressed': String(v === state) }, STATUS_WORDS[state]);
        b.addEventListener('click', () => {
          this.pinned = true;
          if (state === 'broken') this.askNote(c);
          else void Promise.resolve().then(() => this.validate(c.id, state, '')).catch(() => {
            const problem = el('p', {role:'alert'}, 'The verdict could not be saved. Try again; the previous verdict is kept.');
            this.card.querySelector('.cap-card-body')?.append(problem);
          });
        });
        return b;
      }));
    const cite = el('button', {type:'button',class:'mm-control cap-cite'}, 'Cite');
    cite.addEventListener('click', () => this.citeCapability(wire, c, cite));
    const report = el('button', {type:'button',class:'cap-report-local mm-control'}, 'Report a problem');
    report.addEventListener('click', () => { if (!this.noting) this.askNote(c); });
    const close = el('button', { type: 'button', class: 'cap-card-close', 'aria-label': 'Close' }, '×');
    close.addEventListener('click', () => this.closeCard());
    const next = discoveryAction(c, wire);
    const primary = next ? el(next.model||next.href ? 'a' : 'button', {
      class: 'mm-control mm-primary cap-next', ...(next.href?{href:next.href}:next.model ? { href: `#toolbox/${next.model}` } : { type: 'button' }),
    }, next.label) : null;
    if (primary && next?.capability) primary.addEventListener('click', () => {
      this.closeCard(); this.toolbox.action?.(next.capability!);
    });
    const checked = capabilityChecks(wire, c);
    const checks = checked.length ? el('div', { class: 'cap-model-checks' },
      el('p', { class: 'cap-powered-head' }, 'The app’s own check of its models'),
      ...checked.map(k => el('p', { class: 'cap-model-check', 'data-check': k.check.state }, `${k.role}, ${k.model}: ${checkWords(k.check)}`)))
      : null;
    this.card.replaceChildren(
      close,
      el('div', { class: 'cap-media' }, media),
      el('div', { class: 'cap-card-body' },
        el('p', { class: 'cap-card-who' }, glyph(c.actor), this.actorWord(c.actor)),
        el('h4', {}, c.title),
        el('p', { class: 'cap-what' }, c.what), primary,
        howText(c) ? el('p', { class: 'cap-how' }, howText(c)) : null,
        c.available ? null : el('p', { class: 'cap-why' }, c.how.href?.startsWith('#workflows?')?c.why:`Not available here: ${c.why}`),
        el('div', { class: 'cap-powered' }, el('p', { class: 'cap-powered-head' }, 'What powers it'), ...powerRows),
        ...fallbackBlocks,
        this.evaluation(wire, c),
        el('p', { class: 'cap-usage-note' }, wire.usageCoverage?.reason ?? 'Usage history is not available. Unrecorded use is unknown.'),
        checks, when, buttons, el('div', {class:'cap-report-actions'}, cite, report)),
    );
    const usage = this.card.querySelector<HTMLElement>('.cap-usage-note');
    if (usage && c.how?.tool) void this.showUsage(usage, c.how.tool);
  }

  private evaluation(wire: CapabilitiesWire, capability: Capability): HTMLElement {
    const facts = capabilityEvidence(wire, capability), verdict = capability.verdict;
    const checks = facts.checks.length ? el('ul', {}, ...facts.checks.map(ref =>
      el('li', {}, EVIDENCE_WORDS(ref), el('code', {}, ` · ${ref}`))))
      : el('p', {}, 'No measured checks are declared for this capability.');
    return el('details', {class:'cap-evaluation'}, el('summary', {}, 'How it was evaluated'),
      el('p', {}, 'Declared checks and selected-option notes; these references do not establish a pass on this build.'), checks,
      ...facts.roles.filter(role => role.note).map(role => el('p', {}, `${role.title} · ${role.choice}: ${role.note}`)),
      verdict ? el('p', {}, `Your local verdict: ${STATUS_WORDS[verdict.state]} · ${verdict.at} · build ${verdict.build || 'unknown'}${stale(capability, wire.build) ? ' (older build)' : ''}.`)
        : el('p', {}, 'No local verdict has been recorded.'),
      verdict?.setup ? el('details', {}, el('summary', {}, `Verdict setup · ${verdict.setup.edition} edition`),
        el('pre', {}, JSON.stringify(verdict.setup.options, null, 2)))
        : el('p', {}, 'The local verdict’s model setup is not recorded.'),
      el('p', {}, 'Current CI status, benchmark fixture/model settings and scientific quality certification are not declared in this record.'));
  }

  private citeCapability(wire: CapabilitiesWire, capability: Capability, opener: HTMLElement): void {
    this.pinned = true; this.noting = true;
    const facts = capabilityEvidence(wire, capability), host = el('div', {class:'cap-option-sheet'});
    const sheet = new StudioSheet(`Cite · ${capability.title}`);
    host.append(el('p', {}, 'No formal citation is declared. These are implementation source references for this capability’s currently selected role options, not proof of which model ran on a particular result.'),
      ...facts.roles.map(role => el('section', {}, el('h3', {}, `${role.title} · ${role.choice ?? 'Selection unknown'}`),
        ...role.sources.map(source => el('p', {}, el('a', {href:source.url,target:'_blank',rel:'noopener noreferrer'}, source.title), ` · ${source.licence || 'Licence not declared'}`)),
        role.sources.length ? null : el('p', {}, 'No source reference is declared for this selected option.'))));
    if (!facts.roles.length) host.append(el('p', {}, 'No model role or source reference is declared for this capability.'));
    const lines = facts.roles.flatMap(role => role.sources.map(source => `${role.title} / ${role.choice}: ${source.title} — ${source.url} (${source.licence || 'licence not declared'})`));
    const text = el('textarea', {readonly:true,'aria-label':'Declared capability source references'}, lines.join('\n') || 'No source reference declared.');
    const copy = el('button', {type:'button',class:'mm-control',disabled:!lines.length}, 'Copy source references');
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(text.value); copy.textContent = 'Copied'; }
      catch { text.focus(); text.select(); copy.textContent = 'Select and copy the sources'; }
    });
    host.append(el('p', {}, 'For a cloud API, record its provider/model identifier and the version actually used. An unrecorded version remains unknown.'), text, copy);
    document.body.append(host); sheet.open(host, opener); sheet.onDismiss = () => { this.noting = false; host.remove(); };
  }

  private askNote(c: Capability | null, model?: {role: Role; option: RoleOption}, opener?: HTMLElement): void {
    if (!this.wire || (!c && !model)) return;
    this.noting = true;
    const wire = this.wire;
    const host = el('div', {class:'cap-report-sheet'});
    const sheet = new StudioSheet(`Report a problem · ${c?.title ?? model!.option.label}`);
    const note = el('textarea', {class:'cap-note', rows:'3', maxlength:'2000', placeholder:'What went wrong?', 'aria-label':'What went wrong'});
    const happened = el('fieldset', {class:'cap-happened'}, el('legend', {}, 'What happened?'));
    for (const [i, label] of ['Failed or stopped', 'Ran but wrong', 'Too slow', 'Something else'].entries()) {
      const radio = el('input', {type:'radio', name:'cap-happened', value:label}); radio.checked = i === 0;
      happened.append(el('label', {}, radio, label));
    }
    const include = el('input', {type:'checkbox'});
    const pictureChoice = el('label', {class:'cap-report-picture'}, include, 'Include the current rendered microscope view');
    const image = el('img', {alt:'Optional current rendered microscope canvas', hidden:true});
    const status = el('p', {role:'status', 'aria-live':'polite'});
    const preview = el('pre', {class:'cap-report-preview'});
    const details = el('details', {}, el('summary', {}, 'See exactly what is in the local report'), preview);
    let picture: ReportPicture | null = null;
    const outcome = () => happened.querySelector<HTMLInputElement>('input:checked')?.value ?? 'Something else';
    const report = () => c ? capabilityReport(wire, c, outcome(), note.value, picture)
      : modelReport(wire, model!.role, model!.option, outcome(), note.value, picture);
    const update = () => { preview.textContent = JSON.stringify(report(), null, 2); };
    note.addEventListener('input', update); happened.addEventListener('change', update);
    include.addEventListener('change', () => {
      picture = null; image.hidden = true; image.removeAttribute('src'); status.textContent = '';
      if (include.checked) {
        try { picture = reportPicture(document.getElementById('view') as HTMLCanvasElement | null); image.src = picture.dataUrl; image.hidden = false; }
        catch (error) { include.checked = false; status.textContent = error instanceof Error ? error.message : 'Picture unavailable.'; }
      }
      update();
    });
    const save = el('button', {type:'button',class:'mm-control'}, 'Keep as my verdict');
    save.addEventListener('click', () => {
      if (!c || save.disabled) return;
      save.disabled = true; status.textContent = 'Saving local verdict…';
      const selected = capabilityReport(wire, c, outcome(), note.value, picture);
      void Promise.resolve().then(() => this.validate(c.id, 'broken', selected.verdictNote)).then(() => {
        status.textContent = 'Verdict saved locally. Use Download local report to keep the reviewed context and optional picture.';
      }, () => { status.textContent = 'The verdict could not be saved. Try again; your report is still here.'; }).finally(() => { save.disabled = false; });
    });
    const download = el('button', {type:'button',class:'mm-control'}, 'Download local report');
    download.addEventListener('click', () => {
      try { downloadCapabilityReport(report()); status.textContent = 'Local report download requested. Nothing was uploaded.'; }
      catch { status.textContent = 'The local report could not be downloaded. Its exact contents are available below.'; details.open = true; }
    });
    host.append(el('p', {}, c ? 'Private local report. No recording starts and nothing is sent publicly. The verdict keeps up to 300 characters of your note and server setup; the full note and pictures are only included in the local file you download.'
      : 'Private local model report. No recording starts and nothing is uploaded. Download keeps the selected option, current setup, your note and any picture you include. No capability verdict is inferred.'),
      happened, note, pictureChoice, el('p', {class:'cap-local'}, 'This optional canvas is what the microscope last rendered now, not a historical request recording. No request or earlier steps are selected.'), image,
      el('div', {class:'cap-report-actions'}, c ? save : null, download), status, details);
    update(); document.body.append(host); sheet.open(host, opener ?? this.card.querySelector('.cap-report-local'));
    sheet.onDismiss = () => { this.noting = false; host.remove(); };
  }

  private fillPower(key: string): void {
    const wire = this.wire;
    if (!wire) return;
    const caps = new Map(wire.capabilities.map((c) => [c.id, c]));
    const isPart = key.startsWith('part:');
    const id = key.slice(5); // after "role:" or "part:"
    const role = isPart ? undefined : wire.roles.find((r) => r.id === id);
    const part = isPart ? (wire.parts ?? []).find((x) => x.id === id) : undefined;
    if (!role && !part) return;
    const powered = (role?.powers ?? part?.powers ?? []).map((cid) => {
      const c = caps.get(cid);
      const b = el('button', { type: 'button', class: 'cap-power', 'data-verdict': c ? verdictOf(c) : 'not-yet' },
        el('span', { class: 'cap-dot', 'aria-hidden': 'true' }), c?.title ?? cid,
        c ? el('span', { class: 'cap-chip-actor', title: this.actorWord(c.actor) }, glyph(c.actor)) : null);
      b.addEventListener('click', () => this.openCapability(cid));
      return b;
    });
    let choice: HTMLElement | null = null;
    if (role) {
      choice = role.fixed
        ? el('div', { class: 'cap-fixed' },
          // No menu chooses it: the model the code names, and its fallback, the one in use dotted.
          ...[role.primary, role.fallback].filter((m): m is NonNullable<typeof m> => !!m).map((m, i) => el('p', { class: 'cap-fixed-model' },
            (i === 0 ? !role.fallback?.active : !!m.active) ? el('span', { class: 'cap-in-use', title: 'In use now' }) : null,
            markGlyph(m.runs === 'mac' ? 'mac' : 'cloud'), el('span', { class: 'cap-fixed-label' }, m.label),
            el('span', { class: 'cap-fixed-note' }, i === 0 ? `${m.value}, fixed in the code${m.available ? '' : `: ${m.why}`}` : `when ${m.when}`),
            m.check ? el('span', { class: 'cap-model-check', 'data-check': m.check.state }, checkWords(m.check)) : null)))
        : el('div', { class: 'cap-options', role: 'radiogroup', 'aria-label': role.title },
          ...role.options.map(o => this.optionRow(role, o)));
    }
    const fromCap = this.from ? caps.get(this.from) : undefined;
    const back = fromCap ? el('button', { type: 'button', class: 'cap-card-back' }, `Back to “${fromCap.title}”`) : null;
    back?.addEventListener('click', () => { if (fromCap) this.openCapability(fromCap.id); });
    const close = el('button', { type: 'button', class: 'cap-card-close', 'aria-label': 'Close' }, '×');
    close.addEventListener('click', () => this.closeCard());
    const mark: Mark = part ? (part.kind === 'algorithm' ? 'algorithm' : 'app') : roleMark(role);
    const kind = el('span', { class: `cap-mark-${mark}` }, markGlyph(mark),
      `${MARK_TITLES.get(mark)}${role?.fixed ? ', fixed in the code' : role ? `, switched here or in the ${role.kind === 'model' ? 'Models' : 'Behaviour'} menu` : ''}`);
    this.card.replaceChildren(close,
      el('div', { class: 'cap-card-body cap-card-power' },
        back,
        el('p', { class: 'cap-card-who' }, kind),
        el('h4', {}, role?.title ?? part!.title),
        el('p', { class: 'cap-what' }, role?.purpose ?? part!.purpose),
        part ? el('p', { class: 'cap-source', title: part.source }, part.source) : null,
        role?.agentCanChange.length ? el('p', { class: 'cap-badge', title: role.agentCanChange.join(', ') }, 'The agent can change this when you ask') : null,
        choice,
        role?.options.some(o => o.sources?.length) ? el('details', {}, el('summary', {}, 'Declared sources and licences'),
          ...role.options.flatMap(o => (o.sources ?? []).filter(source => /^https?:\/\//.test(source.url)).map(source =>
            el('p', {}, el('a', {href:source.url,target:'_blank',rel:'noopener noreferrer'}, `${o.label}: ${source.title}`), ` · ${source.licence}`)))) : null,
        role?.operation ? el('details', {}, el('summary', {}, 'Input and output contract'), el('p', {}, `Returns: ${role.operation.returns}`), el('pre', {}, JSON.stringify(role.operation.accepts, null, 2))) : null,
        role && !role.fixed ? el('p', { class: 'cap-choice-note' }, defaultWords(role)) : null,
        powered.length ? el('div', { class: 'cap-powers' }, el('span', { class: 'cap-powers-label' }, `Powers ${powered.length}`), ...powered) : null,
        role?.fallbackFor?.length ? el('div', { class: 'cap-powers' }, el('span', { class: 'cap-powers-label' }, 'Takes over when their own model can’t run'),
          ...role.fallbackFor.map((cid) => {
            const c = caps.get(cid);
            const b = el('button', { type: 'button', class: 'cap-power', 'data-verdict': c ? verdictOf(c) : 'not-yet' },
              el('span', { class: 'cap-dot', 'aria-hidden': 'true' }), c?.title ?? cid);
            b.addEventListener('click', () => this.openCapability(cid));
            return b;
          })) : null));
  }

  private optionRow(role: Role, option: RoleOption): HTMLElement {
    const focusKey = `${role.id}:${option.id}`;
    const tags = [option.id === role.current ? 'In use' : '', option.id === role.default ? 'Default' : '', option.runs === 'mac' ? 'On this Mac' : option.runs === 'cloud' ? 'Cloud' : option.runs === 'both' ? 'Mac and cloud' : '', option.tier ?? 'Tier not declared'];
    const use = el('button', {type:'button',role:'radio',class:'cap-option','data-option-focus':`${focusKey}:use`,'aria-checked':String(option.id === role.current),disabled:!option.available},
      el('span', {}, option.label), el('small', {}, tags.filter(Boolean).join(' · ')),
      option.note ? el('small', {}, option.note) : null,
      readinessNote(option.available, option.readiness, option.why),
      option.check ? el('small', { class: 'cap-model-check', 'data-check': option.check.state }, checkWords(option.check)) : null);
    use.addEventListener('click', () => this.choose(role.id, option.id));
    const detailsKey = `${focusKey}:details`;
    const details = el('button', {type:'button',class:'mm-control cap-option-details','data-option-focus':detailsKey,'aria-label':`${option.label}: model details`}, 'Details');
    details.addEventListener('click', () => {
      const host = el('div', {class:'cap-option-sheet'});
      const sheet = new StudioSheet(option.label);
      const sources = (option.sources ?? []).filter(source => /^https?:\/\//.test(source.url));
      host.append(el('p', {}, `${role.title} · ${tags.filter(Boolean).join(' · ')}`),
        el('h3', {}, 'Declared implementation'), el('p', {}, `Licence: ${option.licence || 'Not declared'}`),
        ...sources.map(source => el('p', {}, el('a', {href:source.url,target:'_blank',rel:'noopener noreferrer'}, source.title), ` · ${source.licence}`)),
        el('p', {}, 'Maintainer, citation format, model version and permissions: not declared in the option record.'),
        el('h3', {}, 'How it was evaluated'), el('p', {}, option.note || 'No measured note declared.'),
        el('p', {}, 'Per-model CI result and benchmark setup are not declared in this record. This is not a quality certification.'),
        el('h3', {}, 'Input and output'), el('p', {}, role.operation ? `Accepts: ${(option.accepts ?? role.operation.accepts[option.id] ?? []).join(', ') || 'Not declared'}. Returns: ${role.operation.returns}.` : 'Not declared for this role.'),
        el('h3', {}, 'Cite'), el('p', {}, 'No formal citation is declared. These are implementation source references:'));
      const text = el('textarea', {readonly:true,'aria-label':'Declared implementation sources'}, sources.map(source => `${source.title} — ${source.url} (${source.licence})`).join('\n') || 'No source reference declared.');
      host.append(text);
      const copy = el('button', {type:'button',class:'mm-control',disabled:!sources.length}, 'Copy source references');
      copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(text.value); copy.textContent = 'Copied'; } catch { text.focus(); text.select(); copy.textContent = 'Select and copy the sources'; } }); host.append(copy);
      const select = el('button', {type:'button',class:'mm-control',disabled:!option.available}, option.id === role.current ? 'In use' : 'Use this option');
      select.addEventListener('click', () => { this.choose(role.id,option.id); sheet.close(); }); host.append(select);
      const report = el('button', {type:'button',class:'mm-control'}, 'Report a problem');
      report.addEventListener('click', () => { sheet.close(); this.askNote(null, {role, option}, details); }); host.append(report);
      // Stateless details can move into the shared modal. Dismissal restores focus to Details.
      document.body.append(host); sheet.open(host, details); sheet.onDismiss = () => {
        host.remove();
        // A settings echo can replace the invoking row while this sheet is open.
        if (!details.isConnected) this.optionControl(detailsKey)?.focus({ preventScroll: true });
      };
    });
    return el('div', {class:'cap-option-row'}, use,
      stepControl(option.readiness, this.wire?.setup?.keys, this.wire?.setup?.downloads, this.download), details);
  }

  private optionControl(key?: string): HTMLElement | null {
    if (!key) return null;
    const selector = `[data-option-focus="${CSS.escape(key)}"]`;
    return (!this.card.hidden ? this.card.querySelector<HTMLElement>(selector) : null)
      ?? this.body.querySelector<HTMLElement>(selector);
  }

  /** A capability's card, beside its chip when the chip is on the page, else where the card is. */
  private openCapability(id: string): void {
    const chip = this.root.querySelector<HTMLElement>(`[data-cap="${CSS.escape(id)}"]`);
    chip?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    this.shown = id;
    this.from = null;
    this.pinned = true;
    this.noting = false;
    this.fillCard(id);
    this.card.hidden = false;
    if (chip) this.place(chip);
    this.card.focus({ preventScroll: true });
  }
}

/** Default is a server fact; current choice and benchmark notes cannot stand in for it. */
export function defaultWords(role: Role): string {
  if (!role.default) return 'Default and selection rationale not declared.';
  const label = role.options.find(option => option.id === role.default)?.label ?? role.default;
  return `Default: ${label}${role.default !== role.current ? ' (not in use)' : ''}. ${role.defaultReason || 'Selection rationale not declared.'}`;
}

/** What an option needs here, from its readiness (orchestrator/readiness.py): the next step with how, a link where to
 * do it, and the rest of the checklist; the server's `why` when it sent no readiness. Nothing for a ready option. */
function readinessNote(available: boolean, r: Readiness | undefined, why?: string): HTMLElement | null {
  if (!r) return available ? null : el('small', {}, why || 'Not available here');
  if (r.status === 'ready') return null;
  const later = laterSteps(r);
  return el('small', { class: 'cap-readiness', 'data-status': r.status }, readinessWords(r),
    later.length ? ` · then ${later.join(', then ')}` : null);
}

/** The next step's own control beside an option (never inside the option's button): Add key for its service's key,
 * else a link to the page the step names (a licence to accept). */
/** Download (cancel: true stops it): the app's `model-download` commands (downloads.py). */
export type Downloader = (model: string, cancel?: boolean) => Promise<unknown>;

function stepControl(r: Readiness | undefined, keys?: Record<string, KeyFacts>, downloads?: DownloadRow[],
  download?: Downloader | null): HTMLElement | null {
  const next = r?.status === 'ready' ? null : r?.next;
  if (!next) return null;
  const key = next.kind === 'key' && next.service ? Object.values(keys ?? {}).find((k) => k.service === next.service) : undefined;
  if (key && keyActions(key).save) {
    const add = el('button', { type: 'button', class: 'mm-control cap-add-key' }, 'Add key');
    add.addEventListener('click', () => openAddKey(key, add));
    return add;
  }
  if (next.kind === 'download' && next.model && download) return downloadControl(next.model, next.says, downloads, download);
  return next.url ? el('a', { class: 'cap-step-link', href: next.url, target: '_blank', rel: 'noopener noreferrer' }, 'Open its page') : null;
}

/** What a model's download control shows (pure, for downloadControl): while it downloads its words and Cancel;
 * otherwise the button's words and, after a failure or a cancel, why. */
export function downloadView(row: DownloadRow | undefined, says: string):
  { busy: true; words: string } | { busy: false; button: string; why: string } {
  if (row && (row.state === 'queued' || row.state === 'downloading' || row.state === 'checking')) {
    const pct = row.total ? Math.floor((100 * row.got) / row.total) : 0;
    return { busy: true, words: row.state === 'queued' ? 'Waiting to download' : row.state === 'checking'
      ? 'Checking the files' : `Downloading ${pct}%` };
  }
  const why = row && (row.state === 'failed' || row.state === 'cancelled') ? row.says : '';
  return { busy: false, button: row?.state === 'failed' ? 'Try again' : says, why };
}

/** A model's Download button, or while it downloads its progress and Cancel; a failure is said beside it. */
export function downloadControl(model: string, says: string, downloads: DownloadRow[] | undefined,
  download: Downloader): HTMLElement {
  const view = downloadView(downloads?.find((d) => d.model === model), says);
  if (view.busy) {
    const box = el('span', { class: 'cap-download', role: 'status' }, el('span', { class: 'cap-download-state' }, view.words));
    const cancel = el('button', { type: 'button', class: 'mm-control cap-download-cancel' }, 'Cancel');
    cancel.addEventListener('click', () => { cancel.disabled = true; void download(model, true).catch(() => { cancel.disabled = false; }); });
    box.append(cancel);
    return box;
  }
  const button = el('button', { type: 'button', class: 'mm-control cap-download-start', title: view.why || says }, view.button);
  const why = el('span', { class: 'cap-download-why', hidden: !view.why }, view.why);
  button.addEventListener('click', () => {
    button.disabled = true;
    button.textContent = 'Starting…';
    void download(model).catch((error: unknown) => {
      button.disabled = false;
      button.textContent = view.button;
      why.textContent = error instanceof Error ? error.message : String(error);
      why.hidden = false;
    });
  });
  return el('span', { class: 'cap-download' }, button, why);
}


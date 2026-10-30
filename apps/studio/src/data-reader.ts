import {materialSummary,inspectionFacts} from './material-overview';
// The Data reader (ticket 66, D1; the mock's open-yours, open-research, open-how, open-tab, open-narrow and open-light
// frames): one place where anything in Data opens, on the right of the Tab you are on, looking the same for every kind.
// Its header says what it is (the kind's mark, or the lit cell while it is being written), its title and count, who made
// it and when; its two views are the thing itself and How it was made; its actions are the only ways out: Show on
// picture (outlines), Talk to Researcher N (a researcher's work, the one action that goes to its Tab), Open in new Tab
// (the same reader, full width, in a Tab of its own). ⋯ keeps every route a Data row had (Add to Tab…, Go to <Tab>, the
// kind's full tool by name, Move to trash), so nothing is removed.
//
// Each renderer draws into the body it is given, with the code the windows use (workspace-study-content.ts masks and
// text, measurements.ts tables, research.ts pages). Every field of the wire contract (role, maker, run, forms, values,
// untrusted, the detail's `how`) is read as optional: an older server sends none, and the reader then says only what
// the entry itself records, never a guess and never raw JSON. Opening draws the header at once and the body as its
// content arrives; nothing heavy runs on the open.
//
// The same reader is the right of a researcher's Tab (mode 'research'; ticket 66 D3, the mock's research,
// research-docs, research-sources and research-narrow frames): its title switches between everything that researcher
// made (Data's entries whose maker it is) and the sources it cited; its own write-ups are drawn by its page
// (research-workspace.ts, `deps.page()`: the desk that follows its writing, with Fit, download, its versions and its ⋯),
// everything else by the renderers here. Nothing on that Tab leaves it but Show on Main ↗ and Open in new Tab.
import { litCell, type LitCellState } from './lit-cell';
import { moreButton, popMenu, type MenuItem } from './drawer-menu';
import { tabMark, type TabMark } from './tab-marks';
import { artifactPresentation, drawRetainedMasks, readRetainedText, undrawnWords } from './workspace-study-content';
import { formatValue, header, readMeasurementTable, type MeasurementTable, type TableColumn } from './measurements';
import { clean, defaultResearchOutput, researchChartNode, researchFindingPage, researchHero, researchTableNode } from './research';
import { daysAgo, monthDay } from './sessions';
import { clock } from './working-strip';
import type { AssetMeta, ResearchOutput, ResearchSession } from './protocol';
import type { StudyResource } from './workspace-study-model';
import { showScientificSource, showScientificFolder } from './scientific-file-import';
import { showMatrixResult } from './scientific-matrix-result';
import { readMaterialRevision } from './project-material-reader';
import { materialKind } from './project-inputs';
import { showRetainedMedia } from './retained-media-preview';
import type { ProjectMaterial } from './data-roles';
import './data-reader.css';

/** Who made something, as the server says it (wire contract S2); null when its record cannot tell. */
export interface ReaderMaker { kind: 'you' | 'assistant' | 'researcher' | 'agent'; id?: string; name: string }
/** One form of a write-up, newest first (S4): its findings revisions and its presentation. */
export interface ReaderForm { label: string; version?: number; outputId?: string; at?: number; state?: 'writing' | 'ready' }
/** Another thing in Data that this one links to (S5). */
export interface ReaderRef { id: string; kind: string; role: string; title: string; maker?: ReaderMaker }
/** How it was made (S5): each part present only when recorded. */
export interface ReaderHow {
  maker?: ReaderMaker; at?: number; asked?: string; method?: { name: string; implementation?: string; where?: string };
  settings?: { name: string; value: unknown }[]; from?: ReaderRef[]; usedIn?: ReaderRef[]; citedIn?: ReaderRef[];
  sources?: ReaderRef[]; versions?: ReaderForm[];
}
/** A cited source's facts (S4). Web data: untrusted, shown as words and links only. */
export interface ReaderSourceValues { url: string; site: string; title: string; authors?: string; licence?: string; credit?: string; picture?: string }
/** A Data entry as the reader reads it: the index entry with the contract's optional fields, or an input of the
 * Project (`asset`, from the sources model). */
export type ReaderEntry = StudyResource & {
  role?: string | null; maker?: ReaderMaker | null; run?: string | null; forms?: ReaderForm[]; values?: ReaderSourceValues;
  untrusted?: boolean; asset?: AssetMeta; material?: ProjectMaterial;
};
/** The detail route's reply (`GET /api/resources/<id>`), with the contract's `how`. */
interface Detail { entry?: ReaderEntry; detail?: { text?: string }; how?: ReaderHow; error?: string }

/** What the thing is, for its renderer. `sources` is a researcher's Tab's list of the sources it cited (no index entry
 * of its own: `<rid>:sources`, made by `sourcesItem`). */
export type ReaderShape = 'masks' | 'table' | 'writeup' | 'finding-table' | 'finding-chart' | 'source' | 'sources' | 'note' | 'recording'
  | 'picture' | 'answer' | 'other';
const WRITEUP_KINDS = ['research', 'lesson', 'video', 'figure', 'presentation'];
export function readerShape(entry: Pick<ReaderEntry, 'kind' | 'table' | 'asset'>): ReaderShape {
  if (entry.kind === 'population') return 'masks';
  if (entry.kind === 'source') return 'source';
  if (entry.kind === 'sources') return 'sources';
  if (entry.kind === 'finding-table') return 'finding-table';
  if (entry.kind === 'finding-chart') return 'finding-chart';
  if (WRITEUP_KINDS.includes(entry.kind)) return 'writeup';
  if (entry.kind === 'annotation') return 'note';
  if (entry.kind === 'recording' || entry.kind === 'asset' && entry.asset?.kind === 'recording') return 'recording';
  if (entry.kind === 'asset' || entry.kind === 'snapshot') return 'picture';
  if (entry.kind === 'tracks' || artifactPresentation(entry) === 'table') return 'table';
  if (entry.kind === 'result') return 'answer';
  return 'other';
}

/** The researcher a thing belongs to: its maker when the server says so, a researcher's own report, a researcher's
 * other work (`<rid>:<part>`, and the studio's older `<rid>#<output>`), else the researcher it was made for, found by
 * name among this Project's researchers. Null when the record does not say. */
export function researcherOf(entry: Pick<ReaderEntry, 'id' | 'kind' | 'maker' | 'madeBy'>, researchers: { rid: string; name: string }[]): { rid: string; name: string } | null {
  const named = (rid: string) => { const row = researchers.find((r) => r.rid === rid); return row ? { rid, name: row.name } : null; };
  if (entry.maker?.kind === 'researcher' && entry.maker.id) return named(entry.maker.id) ?? { rid: entry.maker.id, name: entry.maker.name };
  if (entry.kind === 'research') return named(entry.id);
  if (entry.id.includes('#')) return named(entry.id.split('#')[0]);
  if (entry.id.includes(':') && ['lesson', 'video', 'figure', 'finding-table', 'finding-chart', 'source'].includes(entry.kind)) return named(entry.id.split(':')[0]);
  const forName = entry.madeBy?.for;
  if (forName) { const row = researchers.find((r) => r.name === forName); return row ? { rid: row.rid, name: row.name } : null; }
  return null;
}

/** Who made it, in words: the server's maker, else only what the record says (a researcher's report, work made for a
 * researcher, a note or table the person made); null, never "You" by default (ADR 0006). An older run's `requestedBy:
 * 'user'` is not a maker: it was set whenever a run had a request, which the Assistant also makes. */
export function makerOf(entry: Pick<ReaderEntry, 'id' | 'kind' | 'maker' | 'madeBy'>, researchers: { rid: string; name: string }[]): ReaderMaker | null {
  if (entry.maker) return entry.maker;
  const researcher = researcherOf(entry, researchers);
  if (researcher) return { kind: 'researcher', id: researcher.rid, name: researcher.name };
  if (entry.madeBy?.for) return { kind: 'researcher', name: entry.madeBy.for };
  if (entry.madeBy?.by === 'user') return { kind: 'you', name: 'You' };
  return null;
}
export const makerWords = (maker: ReaderMaker): string => maker.kind === 'you' ? 'You' : maker.kind === 'assistant' ? 'The Assistant' : maker.name;

/** The kind in a word, as the header and the first view say it. */
export function kindWord(entry: Pick<ReaderEntry, 'kind' | 'table' | 'asset' | 'values' | 'forms' | 'pictures' | 'material'>): string {
  if (entry.kind === 'project' && entry.material) return materialKind(entry.material.kind);
  const shape = readerShape(entry);
  switch (shape) {
    case 'masks': return 'Outlines';
    case 'table': case 'finding-table': return 'Table';
    case 'finding-chart': return 'Chart';
    case 'answer': return 'Answer';
    case 'note': return 'Note';
    case 'recording': return 'Recording';
    case 'picture': return 'Picture';
    case 'source': return entry.values?.picture || entry.pictures?.length ? 'Web picture' : 'Source';
    case 'sources': return 'Sources';
    case 'writeup': {
      const words: Record<string, string> = { lesson: 'Lesson', video: 'Video', figure: 'Figure', presentation: 'Presentation' };
      if (words[entry.kind]) return words[entry.kind];
      return entry.forms?.[0]?.label?.startsWith('Presentation') ? 'Presentation' : 'Report';
    }
    default: { const plain = entry.kind.replace(/-/g, ' '); return plain.charAt(0).toUpperCase() + plain.slice(1); }
  }
}
/** The first view's name: Page for a write-up, Source for a source, else the kind's word. */
export const thingView = (entry: Pick<ReaderEntry, 'kind' | 'table' | 'asset' | 'values' | 'forms' | 'pictures' | 'material'>): string =>
  readerShape(entry) === 'writeup' ? 'Page' : readerShape(entry) === 'source' ? 'Source' : kindWord(entry);

/** Everything a researcher made, as its Tab's reader switches between them (the mock's research-docs frame): Data's
 * entries whose maker is that researcher (S2; an older server's by their ids), its write-ups first (its report, then
 * its lessons, videos and pictures), then its analyses (its findings' tables and charts, the outlines its hands made),
 * in Data's order; the sources it cited apart (the switcher's "Sources · N"). Nothing Data does not list. */
export function workOf<T extends Pick<ReaderEntry, 'id' | 'kind' | 'maker' | 'role'>>(entries: T[], rid: string): { items: T[]; sources: T[] } {
  const mine = entries.filter((entry) => entry.role !== null && (entry.maker ? entry.maker.kind === 'researcher' && entry.maker.id === rid
    : entry.kind === 'research' ? entry.id === rid : entry.id.startsWith(`${rid}:`) || entry.id.startsWith(`${rid}#`)));
  const rank = (entry: T) => entry.kind === 'research' ? 0 : entry.role === 'writeup' || WRITEUP_KINDS.includes(entry.kind) ? 1 : 2;
  const items = mine.filter((entry) => entry.kind !== 'source').map((entry, at) => ({ entry, at }))
    .sort((a, b) => rank(a.entry) - rank(b.entry) || a.at - b.at).map(({ entry }) => entry);
  return { items, sources: mine.filter((entry) => entry.kind === 'source') };
}
/** How many a thing holds, in its record's own words: "4 rows" for a table, "271 bright regions" for outlines whose
 * record says so ("271 outlines" from its plain line otherwise); nothing when the record does not say. */
export function countWords(entry: Pick<ReaderEntry, 'summary' | 'plain'>): string {
  const summary = String(entry.summary ?? '').trim(), plain = String(entry.plain ?? '').trim();
  const rows = /^(\d[\d,]*) rows?\b/.exec(summary);
  if (rows) return `${rows[1]} ${rows[1] === '1' ? 'row' : 'rows'}`;
  const found = /^(\d[\d,]*) ((?:[a-z]+ ){0,2}[a-z]+) (?:detected|found|outlined|segmented)\b/i.exec(summary);
  if (found) return `${found[1]} ${found[2]}`;
  const outlined = /^(\d[\d,]*) outlined\b/.exec(plain);
  return outlined ? `${outlined[1]} outline${outlined[1] === '1' ? '' : 's'}` : '';
}
/** A thing as the switcher lists it: "Bright regions · Outlines, 271 bright regions", "Short report on the Stage ·
 * Presentation". */
export function switchLabel(entry: ReaderEntry, title = readerTitle(entry.title, entry.kind), kind = kindWord(entry), count = countWords(entry)): string {
  // A long title gives way, so its kind stays in view.
  const short = title.length > 48 ? `${title.slice(0, 47).trimEnd()}…` : title;
  return `${short} · ${kind}${count ? `, ${count}` : ''}`;
}
/** A researcher's Tab's list of the sources it cited: one item of the switcher, drawn as their cards. */
export function sourcesItem(rid: string, day: string): ReaderEntry {
  return { id: `${rid}:sources`, kind: 'sources', title: 'Sources', summary: '', state: '', actions: [], day } as ReaderEntry;
}

/** A researcher's own page on its Tab (research-workspace.ts), as the reader in mode 'research' shows it: its
 * write-ups (its report, and its lessons, videos and pictures) are drawn by the page, which follows its writing; the
 * reader draws its header (the switcher, its kind and state, its tools and ⋯) and How it was made. */
export interface ReaderPage {
  rid: string;
  /** "Researcher 3". */
  name: string;
  /** The Project its work belongs to (its entries' `day`). */
  day: string;
  /** The entry its page shows now: its report (`<rid>`), or a lesson, video or picture (`<rid>:<output id>`). */
  entry: ReaderEntry;
  /** True for an entry its page draws (its report and its lessons, videos and pictures). */
  owns(id: string): boolean;
  /** Its page shows that entry (a deliverable chosen in the switcher); the page then tells the reader. */
  show(id: string): void;
  /** The desk with its Sections bar, kept by the page (never rebuilt by the reader). */
  host: HTMLElement;
  /** Its kind in a word ("Presentation", "Report", "Lesson") and its page's title. */
  kind: string;
  title: string;
  /** Its state, as its status pill said it: the lit cell, the server's words, the clock's start while it works. */
  cell: LitCellState;
  words: string;
  since: number | null;
  /** Fit and download, on the header's right. */
  tools: HTMLElement[];
  /** Its ⋯ (Show beside the live view, Ask about this page, make or stop its presentation, …). */
  more: MenuItem[];
  /** Its versions, the one shown ticked; choosing one shows it. */
  versions: { label: string; checked: boolean; choose: () => void }[];
  /** How it was made, its own part: what it worked from, its basis, what it noted, where it read, its effort. */
  how(): HTMLElement | null;
}

/** When, in a word: the time today, else the day. */
export function whenWords(at: number | undefined, now = Date.now() / 1000): string {
  if (typeof at !== 'number' || !Number.isFinite(at)) return '';
  return daysAgo(at, now) === 0 ? new Date(at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : monthDay(at, now);
}

/** A title's first part: the server titles a result "<what> · <made by> · HH:MM", and the header says the maker and
 * the time itself. A title without that shape stays whole. */
export function readerTitle(title: string, kind = ''): string {
  const parts = title.split(' · ').map((part) => part.trim()).filter(Boolean);
  if (kind === 'asset' && parts.length > 1) return parts[0]; // an input's name; its credit is said on the quiet line
  return parts.length > 1 && /^\d{1,2}:\d{2}(\s?[ap]m)?$/i.test(parts[parts.length - 1]) ? parts[0].replace(/, for Researcher \d+$/, '') : title.trim();
}

/** The header's quiet line: its kind, who made it, when ("Outlines · You · 10:41"). */
export function byLine(entry: ReaderEntry, maker: ReaderMaker | null, kind = kindWord(entry), now = Date.now() / 1000): string {
  const credit = entry.kind === 'asset' ? entry.title.split(' · ').slice(1).join(' · ') : '';
  return [kind, credit, maker ? makerWords(maker) : '', whenWords(entry.at, now)].filter(Boolean).join(' · ');
}

/** One row of How it was made: its label, then words, links to other things in Data, versions, or a researcher. */
export interface HowRow { label: string; words?: string[]; quiet?: string; refs?: ReaderRef[]; versions?: ReaderForm[]; talk?: { rid: string; name: string }; link?: { text: string; url: string } }
export interface HowFacts {
  /** The model or tool that made it, by its one display name ('' when not known here). */
  method?: string;
  /** The input it was made from, by name, when its record names one. */
  input?: { id: string; title: string } | null;
  /** The Tabs it is open on. */
  places?: { title: string }[];
  /** A researcher's session, for a write-up's versions and sources. */
  session?: ResearchSession | null;
  now?: number;
}
/** How it was made, from the detail's `how` when the server sends it, otherwise only from what the entry records. */
export function howRows(entry: ReaderEntry, how: ReaderHow | null | undefined, facts: HowFacts, researchers: { rid: string; name: string }[]): HowRow[] {
  const rows: HowRow[] = [];
  const now = facts.now ?? Date.now() / 1000;
  const writeup = readerShape(entry) === 'writeup';
  const maker = how?.maker ?? makerOf(entry, researchers);
  const at = how?.at ?? entry.at;
  const researcher = researcherOf(entry, researchers) ?? (maker?.kind === 'researcher' && maker.id ? { rid: maker.id, name: maker.name } : null);
  const when = whenWords(at, now);
  const who = maker ? makerWords(maker) : '';
  const whoWords = who && maker?.kind === 'assistant' && how?.asked ? `The Assistant, when you asked “${how.asked}”` : who;
  const made = [whoWords, when ? (daysAgo(at!, now) === 0 ? `today at ${when}` : when) : ''].filter(Boolean).join(', ');
  const talk = researcher && readerShape(entry) !== 'source' ? researcher : undefined;
  if (writeup) {
    const versions = how?.versions ?? entry.forms ?? sessionVersions(facts.session);
    if (versions?.length) rows.push({ label: 'Versions', versions });
    if (who) rows.push({ label: 'Written by', words: [made], talk });
    else if (when) rows.push({ label: 'Written', words: [daysAgo(at!, now) === 0 ? `Today at ${when}` : when] });
    if (how?.from?.length) rows.push({ label: 'Made from', refs: how.from });
    if (how?.sources) rows.push(how.sources.length ? { label: 'Sources', refs: how.sources } : { label: 'Sources', quiet: 'None cited' });
    else {
      const read = (facts.session?.result?.sources ?? []).length;
      if (read) rows.push({ label: 'Sources', words: [`${read} read on the web`], quiet: 'Background, not proof about this slide' });
    }
  } else {
    if (how?.from?.length) rows.push({ label: 'From', refs: how.from });
    else if (facts.input) rows.push({ label: 'From', refs: [{ id: facts.input.id, kind: 'asset', role: 'acquired', title: facts.input.title }] });
    const method = how?.method?.name ?? facts.method ?? '';
    const settings = how?.settings ?? settingsOf(entry.madeBy);
    const settingWords = settings.map((s) => `${s.name} ${typeof s.value === 'number' || typeof s.value === 'string' || typeof s.value === 'boolean' ? String(s.value) : ''}`.trim()).filter(Boolean).join(' · ');
    if (method) rows.push({ label: 'Made with', words: [[method, how?.method?.where].filter(Boolean).join(' · ')], quiet: settingWords || undefined });
    if (who) rows.push({ label: readerShape(entry) === 'source' ? 'Found by' : 'Made by', words: [made], talk });
    else if (when) rows.push({ label: 'Made', words: [daysAgo(at!, now) === 0 ? `Today at ${when}` : when] });
    if (how?.usedIn?.length) rows.push({ label: 'Used in', refs: how.usedIn });
    if (how?.citedIn?.length) rows.push({ label: 'Cited in', refs: how.citedIn });
    if (how?.sources?.length) rows.push({ label: 'Sources', refs: how.sources });
    if (facts.places) rows.push(facts.places.length ? { label: 'Open on', words: [facts.places.map((p) => p.title).join(', ')] } : { label: 'Open on', quiet: 'Not open on a Tab' });
  }
  if (typeof entry.version === 'number' && entry.version > 0 && !writeup) rows.push({ label: 'Version', words: [`Revision ${entry.version}`] });
  if (entry.untrusted && readerShape(entry) === 'source') rows.push({ label: 'Note', quiet: 'From the web: background, not proof about this slide' });
  return rows;
}
/** A run's settings when its record keeps them as name and value. */
function settingsOf(madeBy: ReaderEntry['madeBy'] | undefined): { name: string; value: unknown }[] {
  const settings = (madeBy as { settings?: unknown } | undefined)?.settings;
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return [];
  return Object.entries(settings as Record<string, unknown>).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 8).map(([name, value]) => ({ name: name.replace(/[_-]+/g, ' '), value }));
}
/** A researcher's versions from its session: its presentation (when one is current), then its findings revisions,
 * newest first. */
function sessionVersions(s: ResearchSession | null | undefined): ReaderForm[] {
  if (!s) return [];
  const forms: ReaderForm[] = [];
  const presentation = s.outputs.findLast((o) => o.kind === 'presentation' && o.current !== false);
  if (presentation) forms.push({ label: 'Presentation', outputId: presentation.id, version: presentation.version });
  for (const revision of [...(s.findingRevisions ?? [])].sort((a, b) => b.version - a.version)) forms.push({ label: `Findings v${revision.version}`, version: revision.version, at: revision.at, state: 'ready' });
  return forms;
}

/** Which rows of a table to draw for a scroll position (the rest are spacers): what is in view and a few either side. */
export function windowRows(total: number, rowHeight: number, scrollTop: number, viewport: number, overscan = 8): { start: number; end: number } {
  if (total <= 0) return { start: 0, end: 0 };
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const count = Math.ceil(Math.max(viewport, rowHeight) / rowHeight) + overscan * 2;
  return { start: Math.min(first, Math.max(0, total - 1)), end: Math.min(total, first + count) };
}

/** The reader's width beside the Stage: the mock's 600 px, narrower when the Stage would keep under 400 px; below
 * 1200 px (where it takes the Data drawer's place) 540 px, keeping the Stage at least 320 px. */
export function readerWidth(viewport: number, rail: number, left: number): number {
  if (viewport < 1200) return Math.max(320, Math.min(540, viewport - rail - 320));
  return Math.max(440, Math.min(600, viewport - rail - left - 400));
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); node.className = className; if (text) node.textContent = text; return node;
};
const button = (text: string, click: () => void, className = ''): HTMLButtonElement => {
  const node = el('button', className, text); node.type = 'button'; node.addEventListener('click', click); return node;
};
const MARKS: Partial<Record<ReaderShape, TabMark>> = { masks: 'outline', table: 'table', 'finding-table': 'table', 'finding-chart': 'count',
  writeup: 'output', note: 'name', recording: 'recording', picture: 'picture', source: 'link', answer: 'name' };
/** A thing's mark for a Tab (its kind), shared with the reader Tab in the row. */
export const readerMark = (entry: Pick<ReaderEntry, 'kind' | 'table' | 'asset'>): TabMark => MARKS[readerShape(entry)] ?? 'output';

/** What the reader needs from the workspace (workspace-study.ts gives it). */
export interface ReaderDeps {
  /** Metadata already present in this Project's index; never substitutes for an exact report read. */
  materialAction?(entry:ReaderEntry,action:'inspect'|'experiment'|'ask'):void;
  openStudy?(id:string):void;
  retainedEntry?(ref: {id: string; day: string; version: number}): ReaderEntry | null;
  researchers(): { rid: string; name: string; day?: string; status?: string }[];
  working(rid: string): boolean;
  method(entry: ReaderEntry): string;
  input(id: string): { id: string; title: string } | null;
  places(entry: ReaderEntry): { id: string; title: string; open: boolean }[];
  /** This Tab's id, so Go to lists the other Tabs. */
  tab(): string;
  /** × (side): close; ‹ Data (below 1200 px): close and give Data back. */
  close(): void;
  back(): void;
  talk(rid: string, entry: ReaderEntry): void;
  openInNewTab(entry: ReaderEntry): void;
  showOnPicture(entry: ReaderEntry, show: boolean): Promise<void>;
  onPicture(entry: ReaderEntry): boolean;
  addToTab(entry: ReaderEntry, anchor: HTMLElement): void;
  goTo(tab: string): void;
  /** The kind's full tools, by name (every route its Data row had). */
  routes(entry: ReaderEntry): MenuItem[];
  trash(entry: ReaderEntry): MenuItem;
  editNote(entry: ReaderEntry): void;
  /** Another thing in Data, opened in this reader (Made from, Cited in…). */
  open(ref: { id: string; day?: string }): void;
  /** The item or its view changed (the screen report reads it). */
  changed?(): void;
  /** Mode 'research': the researcher's own page on this Tab. */
  page?(): ReaderPage | null;
  /** Mode 'research': everything a researcher made, as Data lists it (sources included). */
  made?(rid: string): ReaderEntry[];
  /** Mode 'research': Data, filtered to what a researcher made ("All its work in Data"). */
  allInData?(rid: string): void;
  /** Mode 'research': leaves this Tab for the work Tab and opens it there, where Data opens it ("Show on Main ↗"). */
  leave?(entry: ReaderEntry): void;
  /** Mode 'research': the work Tab this Tab returns to ("Main"). */
  home?(): string;
}

/** One reader: the right edge's (`side`), a reader Tab's (`tab`, full width, its title on the Stage bar), or the right
 * of a researcher's Tab (`research`, beside its conversation). */
export class DataReader {
  readonly root: HTMLElement;
  private readonly head = el('header', 'rd-head');
  private readonly body = el('div', 'rd-body-host');
  private entry: ReaderEntry | null = null;
  private view: 'thing' | 'how' = 'thing';
  private controller: AbortController | null = null;
  private session: ResearchSession | null = null;
  private detail: Detail | null = null;
  private narrow = false;
  private picturePending = false;
  private pictureError = '';
  private ticking = false;
  private clockTimer: ReturnType<typeof setInterval> | null = null;
  /** What the header says once the thing has loaded (a researcher's title, a table's count). */
  private refreshMaterial: ((entry: ReaderEntry) => void) | null = null;
  private loaded: { title?: string; count?: string; kind?: string; live?: boolean } = {};
  readonly mode: 'side' | 'tab' | 'research';
  private readonly deps: ReaderDeps;
  constructor(deps: ReaderDeps, mode: 'side' | 'tab' | 'research') {
    this.deps = deps; this.mode = mode;
    this.root = el(mode === 'side' ? 'aside' : 'section', `data-reader data-reader-${mode}`);
    // The screen report names a surface by its root's id (the server refuses an empty one).
    this.root.id = mode === 'side' ? 'data-reader' : mode === 'tab' ? 'data-reader-tab' : 'data-reader-research';
    this.root.hidden = true;
    this.root.append(this.head, this.body);
  }
  /** Research mode: the researcher's page when it draws this entry (its report, its lessons, videos and pictures). */
  private pageOf(entry: Pick<ReaderEntry, 'id'> | null): ReaderPage | null {
    if (this.mode !== 'research' || !entry) return null;
    const page = this.deps.page?.() ?? null;
    return page && page.owns(entry.id) ? page : null;
  }
  /** Research mode: the researcher's page changed (its state, versions, the deliverable it shows). The reader follows
   * it while it shows the page (or nothing yet); on anything else only its header follows (the state words). */
  pageChanged(): void {
    const page = this.mode === 'research' ? this.deps.page?.() ?? null : null;
    if (!page) return;
    if (!this.entry || page.owns(this.entry.id)) this.show(page.entry, this.view);
    else this.drawHead();
  }
  /** Research mode: show something its researcher made (the switcher, a step's link, a table's Open): its page draws
   * its own write-ups; everything else draws here. */
  choose(entry: ReaderEntry, view: 'thing' | 'how' = 'thing'): void {
    const page = this.pageOf(entry);
    if (page) { page.show(entry.id); const now = this.deps.page?.(); if (now) { this.show(now.entry, view); return; } }
    this.show(entry, view);
  }
  /** Research mode: the state's clock in the header, once a second while its Tab is in view (text only, nothing per
   * frame). */
  setTicking(on: boolean): void { this.ticking = on; this.tick(); }
  private tick(): void {
    const page = this.mode === 'research' && this.entry ? this.deps.page?.() ?? null : null;
    const node = this.head.querySelector<HTMLElement>('.rd-clock');
    const since = node ? page?.since ?? null : null;
    if (node && since !== null) { const words = clock(Math.max(0, Date.now() / 1000 - since) * 1000); if (node.textContent !== words) node.textContent = words; }
    const counting = this.ticking && since !== null;
    if (counting && this.clockTimer === null) this.clockTimer = setInterval(() => this.tick(), 1000);
    else if (!counting && this.clockTimer !== null) { clearInterval(this.clockTimer); this.clockTimer = null; }
  }
  /** The item shown, or null. */
  current(): ReaderEntry | null { return this.entry; }
  currentView(): 'thing' | 'how' { return this.view; }
  /** Opens an item (the same one again keeps its loaded body and only switches the view). */
  show(entry: ReaderEntry, view: 'thing' | 'how' = 'thing', options: { narrow?: boolean } = {}): void {
    const same = !!this.entry && sameItem(this.entry, entry);
    this.narrow = !!options.narrow;
    this.entry = entry; this.root.hidden = false; this.root.dataset.readerOpen = 'true';
    this.root.dataset.readerItem = `${entry.day ?? ''}:${entry.id}:${entry.version ?? ''}`;
    // What the screen report says it shows (workspace-document-surfaces.ts readerSurfaces).
    this.root.dataset.readerResource = JSON.stringify({ day: entry.day ?? '', id: entry.id, version: entry.version ?? null });
    this.root.setAttribute('aria-label', readerTitle(entry.title, entry.kind));
    if (!same) {
      this.controller?.abort(); this.controller = new AbortController();
      this.session = null; this.detail = null; this.loaded = {}; this.picturePending = false; this.pictureError = '';
      this.view = view; this.drawHead(); this.drawBody();
    } else if (view !== this.view) { this.view = view; this.drawHead(); this.drawBody(); }
    else { this.refreshMaterial?.(entry); this.drawHead(); }
    this.deps.changed?.();
  }
  /** Redraws the header from the newest facts (a Tab it is on, the picture's layer), keeping the body. */
  refresh(entry?: ReaderEntry): void {
    if (!this.entry) return;
    const before = this.kindLine();
    if (entry && sameItem(entry, this.entry)) this.entry = { ...entry, asset: entry.asset ?? this.entry.asset };
    this.refreshMaterial?.(this.entry); this.drawHead();
    if (before !== this.kindLine()) this.deps.changed?.();
  }
  clear(): void {
    this.refreshMaterial = null;
    this.controller?.abort(); this.controller = null; this.entry = null; this.session = null; this.detail = null;
    this.root.hidden = true; delete this.root.dataset.readerOpen; delete this.root.dataset.readerItem; delete this.root.dataset.displayState;
    delete this.root.dataset.readerResource; delete this.root.dataset.readerTitle; delete this.root.dataset.readerView;
    this.head.replaceChildren(); this.body.replaceChildren(); this.tick(); this.deps.changed?.();
  }
  /** A reader Tab whose item is gone from Data: its name and what happened, nothing else. */
  showMissing(title: string, words: string): void {
    this.refreshMaterial = null;
    this.controller?.abort(); this.controller = null; this.entry = null; this.root.hidden = false;
    this.root.dataset.readerOpen = 'true'; this.root.dataset.displayState = 'failed'; delete this.root.dataset.readerItem; delete this.root.dataset.readerResource;
    const top = el('div', 'rd-top'), mark = el('span', 'rd-ic'); mark.append(tabMark('output', 'rd-ic-mark'));
    const head = el('div', 'rd-words'); head.append(el('h2', 'rd-title', title)); top.append(mark, head);
    this.head.replaceChildren(top);
    const body = el('div', 'rd-body'); body.append(el('p', 'rd-empty', words)); this.body.replaceChildren(body);
    this.deps.changed?.();
  }
  /** The view's words for the screen report ("Page", "How it was made"). */
  viewWords(): string { return this.entry ? (this.view === 'how' ? 'How it was made' : thingView(this.entry)) : ''; }
  title(): string {
    if (!this.entry) return '';
    const page = this.pageOf(this.entry);
    return (page ? page.title : this.loaded.title) || readerTitle(this.entry.title, this.entry.kind);
  }
  kindLine(): string { if (!this.entry) return ''; const maker = makerOf(this.entry, this.deps.researchers()); return [this.loaded.kind ?? kindWord(this.entry), maker ? makerWords(maker) : ''].filter(Boolean).join(' · '); }
  /** The title the screen report says (workspace-document-surfaces.ts): its own for what you and the app made; for a
   * researcher's work or a web source, its kind and maker ("Presentation · Researcher 3"), so words a researcher or a
   * web page wrote never reach the Assistant through the report (ticket 66, D6). It names the item by its id there. */
  reportTitle(): string {
    if (!this.entry) return '';
    const untrusted = this.entry.untrusted || this.entry.maker?.kind === 'researcher' || !!researcherOf(this.entry, this.deps.researchers());
    return untrusted ? this.kindLine() : this.title();
  }

  private drawHead(): void {
    if (this.mode === 'research') { this.drawResearchHead(); return; }
    const entry = this.entry!; const shape = readerShape(entry);
    const researchers = this.deps.researchers(), maker = makerOf(entry, researchers), researcher = researcherOf(entry, researchers);
    this.root.dataset.readerTitle = this.reportTitle().slice(0, 80); this.root.dataset.readerView = this.viewWords();
    const kind = this.loaded.kind ?? kindWord(entry);
    const views = el('div', 'mm-dtabs rd-views'); views.setAttribute('role', 'tablist'); views.setAttribute('aria-label', 'View');
    for (const [id, label] of [['thing', this.loaded.kind && shape !== 'writeup' && shape !== 'source' ? this.loaded.kind : thingView(entry)], ['how', 'How it was made']] as const) {
      const tab = button(label, () => { if (this.view !== id && this.entry) this.show(this.entry, id, { narrow: this.narrow }); });
      tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(this.view === id)); tab.dataset.view = id; views.append(tab);
    }
    const talk = researcher && shape !== 'source' ? button('', () => this.deps.talk(researcher.rid, entry), 'rd-talk') : null;
    if (talk) { talk.append(`Talk to ${researcher!.name}`, tabMark('right', 'rd-talk-icon')); talk.title = `Goes to ${researcher!.name}’s Tab, where you talk to it`; }
    const more = moreButton(`More for ${readerTitle(entry.title, entry.kind)}`, () => this.menu(more), 'rd-more mm-dhead-more');
    const bar = el('div', `rd-bar${this.mode === 'tab' ? ' rd-bar-tab' : ''}`);
    bar.append(views, el('span', 'rd-flex'));
    if (talk) bar.append(talk);
    if (this.mode === 'tab') { bar.append(more); this.head.replaceChildren(bar); this.root.dataset.view = this.view; return; }
    if (shape === 'masks') bar.append(this.pictureButton(entry));
    if (shape === 'note') { const edit = button('Edit', () => this.deps.editNote(entry), 'rd-button rd-edit'); edit.title = 'Open it in a note window on this Tab'; bar.append(edit); }
    bar.append(button('Open in new Tab', () => this.deps.openInNewTab(entry), 'rd-button rd-new-tab'));
    const top = el('div', 'rd-top');
    const mark = el('span', 'rd-ic');
    const live = this.loaded.live ?? (researcher ? this.deps.working(researcher.rid) && shape === 'writeup' : false) ?? entry.forms?.[0]?.state === 'writing';
    if (live || entry.forms?.[0]?.state === 'writing') mark.append(litCellSafe('Being written'));
    else mark.append(tabMark(readerMark(entry), 'rd-ic-mark'));
    const words = el('div', 'rd-words');
    const title = el('h2', 'rd-title', this.loaded.title ?? readerTitle(entry.title, entry.kind));
    title.title = entry.title;
    if (this.loaded.count) title.append(' ', el('span', 'rd-count mm-num', this.loaded.count));
    words.append(title, el('span', 'rd-by', byLine(entry, maker, kind)));
    const close = button('', () => this.deps.close(), 'rd-close mm-dhead-more');
    close.setAttribute('aria-label', `Close ${readerTitle(entry.title, entry.kind)}`); close.title = 'Close; nothing is removed'; close.append(tabMark('x', 'rd-close-icon'));
    top.append(mark, words, more, close);
    const parts: HTMLElement[] = [];
    if (this.narrow) { const back = button('', () => this.deps.back(), 'rd-back'); back.append(tabMark('left', 'rd-back-icon'), 'Data'); back.title = 'Back to Data'; parts.push(back); }
    parts.push(top, bar);
    if (this.pictureError) { const problem = el('p', 'rd-problem', this.pictureError); problem.setAttribute('role', 'alert'); parts.push(problem); }
    this.head.replaceChildren(...parts);
    this.root.dataset.view = this.view;
  }
  /** Show on picture: the outlines drawn on this Tab's specimen through the existing present verb; "On the picture ✓"
   * once the studio has shown them (the reply comes after the screen report confirmed it), and pressed again, hidden. */
  private pictureButton(entry: ReaderEntry): HTMLButtonElement {
    const on = this.deps.onPicture(entry);
    const b = button('', () => {
      if (this.picturePending) return;
      this.picturePending = true; this.pictureError = ''; this.drawHead();
      void this.deps.showOnPicture(entry, !on).then(() => { this.pictureError = ''; }, (error: unknown) => {
        this.pictureError = `${on ? 'Not hidden' : 'Not shown'}: ${error instanceof Error ? error.message : String(error)}`;
      }).finally(() => { this.picturePending = false; if (this.entry && sameItem(this.entry, entry)) { this.drawHead(); this.deps.changed?.(); } });
    }, `rd-button rd-picture${on ? ' is-on' : ''}`);
    b.setAttribute('aria-pressed', String(on));
    if (this.picturePending) { b.disabled = true; b.textContent = on ? 'Hiding…' : 'Showing…'; return b; }
    if (on) { b.append(tabMark('tick', 'rd-tick'), 'On the picture'); b.title = 'Drawn on this Tab’s picture; press to hide them'; }
    else { b.textContent = 'Show on picture'; b.title = 'Draw these outlines on this Tab’s picture'; }
    return b;
  }
  /** A researcher's Tab (the mock's rHead): its mark (the researcher's lit cell on its own work), the switcher as its
   * title (everything it made and its sources), its kind and the researcher's state words with the clock; Fit and
   * download for its page, ⋯; then its two views. No ×: the reader is this Tab's own right. */
  private drawResearchHead(): void {
    const entry = this.entry!, shape = readerShape(entry), page = this.deps.page?.() ?? null, onPage = !!page && page.owns(entry.id);
    const researchers = this.deps.researchers(), researcher = researcherOf(entry, researchers);
    const own = !!page && (onPage || shape === 'sources' || researcher?.rid === page.rid);
    this.root.dataset.readerTitle = this.title().slice(0, 80); this.root.dataset.readerView = this.viewWords();
    this.root.dataset.view = this.view;
    const mark = el('span', 'rd-ic');
    if (own) mark.append(litCellSafe(page!.words, page!.cell)); else mark.append(tabMark(readerMark(entry), 'rd-ic-mark'));
    const words = el('div', 'rd-words'), heading = el('h2', 'rd-title'), text = this.title();
    if (page) {
      const sw = button('', () => this.switchMenu(sw), 'rd-switch');
      sw.setAttribute('aria-haspopup', 'menu'); sw.setAttribute('aria-expanded', 'false');
      sw.title = `Switch between what ${page.name} made`;
      sw.append(el('span', 'rd-switch-t', text), tabMark('down', 'rd-switch-icon'));
      heading.append(sw);
    } else heading.textContent = text;
    const by = el('span', 'rd-by');
    if (own) {
      const sources = shape === 'sources' ? workOf(this.deps.made?.(page!.rid) ?? [], page!.rid).sources.length : 0;
      const kind = shape === 'sources' ? `${sources} source${sources === 1 ? '' : 's'}`
        : onPage ? page!.kind : [this.loaded.kind ?? kindWord(entry), this.loaded.count ?? countWords(entry)].filter(Boolean).join(' · ');
      by.append([kind, page!.words].filter(Boolean).join(' · '));
      if (page!.since !== null) by.append(' ', el('span', 'rd-clock mm-num'));
    } else by.append(byLine(entry, makerOf(entry, researchers), this.loaded.kind ?? kindWord(entry)));
    words.append(heading, by);
    const top = el('div', 'rd-top');
    top.append(mark, words, ...(onPage ? page!.tools : []));
    if (shape !== 'sources') { const more = moreButton(`More for ${text}`, () => this.menu(more), 'rd-more mm-dhead-more'); top.append(more); }
    const parts: HTMLElement[] = [top];
    if (shape !== 'sources') {
      const views = el('div', 'mm-dtabs rd-views'); views.setAttribute('role', 'tablist'); views.setAttribute('aria-label', 'View');
      for (const [id, label] of [['thing', this.loaded.kind && shape !== 'writeup' && shape !== 'source' ? this.loaded.kind : thingView(entry)], ['how', 'How it was made']] as const) {
        const tab = button(label, () => { if (this.view !== id && this.entry) this.show(this.entry, id); });
        tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(this.view === id)); tab.dataset.view = id; views.append(tab);
      }
      const bar = el('div', 'rd-bar'); bar.append(views, el('span', 'rd-flex'));
      // Another researcher's work (a Data row opened here): Talk to it goes to its own Tab, and says so.
      if (researcher && !own && shape !== 'source') {
        const talk = button('', () => this.deps.talk(researcher.rid, entry), 'rd-talk');
        talk.append(`Talk to ${researcher.name}`, tabMark('right', 'rd-talk-icon')); talk.title = `Goes to ${researcher.name}’s Tab, where you talk to it`;
        bar.append(talk);
      }
      parts.push(bar);
    }
    this.head.replaceChildren(...parts);
    this.tick();
  }
  /** The title's switcher: everything this researcher made, as Data lists it (its page first when Data does not list it
   * yet), its sources as one item, then All its work in Data. */
  private switchMenu(anchor: HTMLElement): void {
    const page = this.deps.page?.(); if (!page) return;
    const { items, sources } = workOf(this.deps.made?.(page.rid) ?? [], page.rid);
    const list = items.some((item) => item.id === page.entry.id) ? items : [page.entry, ...items];
    const current = this.entry;
    const menu: MenuItem[] = [{ heading: `Made by ${page.name}` }];
    for (const item of list) {
      const label = item.id === page.entry.id ? switchLabel(item, page.title || readerTitle(item.title, item.kind), page.kind, '') : switchLabel(item);
      menu.push({ label, checked: !!current && current.id === item.id, act: () => this.choose(item) });
    }
    if (sources.length) menu.push({ label: `Sources · ${sources.length}`, checked: current?.kind === 'sources', act: () => this.choose(sourcesItem(page.rid, page.day)) });
    if (this.deps.allInData) menu.push('separator', { label: 'All its work in Data', title: `Data, showing only what ${page.name} made`, act: () => this.deps.allInData!(page.rid) });
    popMenu(anchor, menu, `What ${page.name} made`, { start: true })?.classList.add('rd-switch-menu');
  }
  /** ⋯ on a researcher's Tab: its page's own menu (its versions, Show beside the live view, How it was made, Ask about
   * this page, make or stop its presentation, …) for its page; Show on Main ↗ for anything else; then Open in new Tab,
   * Add to Tab…, the Tabs it is on and Move to trash, as everywhere. */
  private researchMenu(anchor: HTMLElement): MenuItem[] {
    const entry = this.entry; if (!entry) return [];
    const page = this.pageOf(entry), items: MenuItem[] = [];
    if (page) {
      if (page.versions.length > 1) items.push({ heading: 'Versions' }, ...page.versions.map((version): MenuItem => ({ label: version.label, checked: version.checked, act: () => this.pick(version) })), 'separator');
      items.push(...page.more);
    } else if (this.deps.leave) {
      const home = this.deps.home?.() || 'Main';
      items.push({ label: `Show on ${home} ↗`, title: `Leaves this Tab: it opens on ${home}, where Data opens it, with its windows and tools`, act: () => this.deps.leave!(entry) });
    }
    if (items.length) items.push('separator');
    items.push({ label: 'Open in new Tab', title: 'The same reader, full width, in a Tab of its own', act: () => this.deps.openInNewTab(entry) });
    if (entry.kind !== 'asset') items.push({ label: 'Add to Tab…', title: 'Add it to another Tab; you stay here', act: () => this.deps.addToTab(entry, anchor) });
    for (const place of this.deps.places(entry))
      items.push({ label: `Go to ${place.title}`, title: place.open ? `Go to ${place.title}, where it is open` : `Reopen ${place.title}, where it was placed`, act: () => this.deps.goTo(place.id) });
    items.push('separator', this.deps.trash(entry));
    return items;
  }
  /** A version chosen (⋯ or How it was made): its page shows it, and the reader shows the page. */
  private pick(version: ReaderPage['versions'][number]): void {
    version.choose();
    const page = this.deps.page?.();
    if (page) this.show(page.entry, 'thing');
  }
  private menu(anchor: HTMLElement): MenuItem[] {
    const entry = this.entry; if (!entry) return [];
    if (this.mode === 'research') return this.researchMenu(anchor);
    const items: MenuItem[] = [];
    if (this.mode === 'side' && entry.kind !== 'asset') items.push({ label: 'Add to Tab…', title: 'Add it to another Tab; you stay here', act: () => this.deps.addToTab(entry, anchor) });
    for (const place of this.deps.places(entry).filter((p) => p.id !== this.deps.tab()))
      items.push({ label: `Go to ${place.title}`, title: place.open ? `Go to ${place.title}, where it is open` : `Reopen ${place.title}, where it was placed`, act: () => this.deps.goTo(place.id) });
    const routes = this.deps.routes(entry);
    if (routes.length) items.push(...(items.length ? ['separator' as const] : []), ...routes);
    items.push('separator', this.deps.trash(entry));
    return items;
  }

  private drawBody(): void {
    this.refreshMaterial = null;
    const entry = this.entry!, signal = this.controller!.signal;
    // A researcher's own write-up on its Tab: its page (kept by research-workspace.ts, following its writing), as is.
    const page = this.view === 'thing' ? this.pageOf(entry) : null;
    this.root.dataset.readerPage = String(!!page);
    if (page) { this.body.replaceChildren(page.host); this.root.dataset.displayState = 'rendered'; this.deps.changed?.(); return; }
    const body = el('div', 'rd-body');
    this.body.replaceChildren(body);
    this.root.dataset.displayState = 'loading';
    const done = (state: 'rendered' | 'failed') => { if (!signal.aborted) { this.root.dataset.displayState = state; this.deps.changed?.(); } };
    const fail = (error: unknown) => {
      if (signal.aborted) return;
      body.replaceChildren(el('p', 'rd-problem', error instanceof Error ? error.message : String(error)),
        button('Try again', () => { if (this.entry) { const again = this.entry; this.entry = null; this.show(again, this.view, { narrow: this.narrow }); } }, 'rd-button'));
      done('failed');
    };
    if (this.view === 'how') { void this.drawHow(body, signal).then(() => done('rendered'), fail); return; }
    const shape = readerShape(entry);
    const work = shape === 'masks' ? this.drawMasks(body, signal)
      : shape === 'table' ? this.drawTable(body, signal)
      : shape === 'writeup' || shape === 'finding-table' || shape === 'finding-chart' ? this.drawWriteup(body, signal)
      : shape === 'source' ? this.drawSource(body, signal)
      : shape === 'sources' ? this.drawSources(body)
      : shape === 'note' ? this.drawNote(body, signal)
      : shape === 'recording' ? this.drawRecording(body, signal)
      : shape === 'picture' ? this.drawPicture(body)
      : this.drawAnswer(body, signal);
    void work.then(() => done('rendered'), fail);
  }
  private loading(body: HTMLElement, words: string): void { body.replaceChildren(el('p', 'rd-loading', words)); }

  /** Outlines: the picture with its outlines (the masks window's drawing), then its table. */
  private async drawMasks(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!, ref = refOf(entry);
    const figure = el('div', 'rd-fig rd-fig-loading'); figure.append(el('span', 'rd-loading', 'Drawing its outlines…'));
    const holds = el('section', 'rd-holds'); holds.append(el('h3', 'rd-holds-head', 'Its table'));
    body.replaceChildren(figure, holds);
    const masks = drawRetainedMasks(ref, signal).then(({ data, svg, source }) => {
      if (signal.aborted) return;
      // The picture it was made on: its kept capture, else the input its record names (the same file, by its id), else
      // the outlines alone, said so.
      const asset = (entry.source as { sourceImage?: { asset?: string } } | undefined)?.sourceImage?.asset;
      source.addEventListener('error', () => {
        if (asset && !source.getAttribute('href')?.startsWith('/api/library/')) { source.setAttribute('href', `/api/library/${encodeURIComponent(asset)}/file`); return; }
        source.setAttribute('visibility', 'hidden');
        if (!figure.nextElementSibling?.classList.contains('rd-note')) figure.after(el('p', 'rd-note', 'The picture it was made on is not kept; its outlines are drawn alone.'));
      });
      figure.classList.remove('rd-fig-loading'); figure.replaceChildren(svg);
      const note = undrawnWords(data.undrawn);
      if (note) figure.after(el('p', 'rd-note', note.replace(/^ · /, '')));
    }, (error: unknown) => { if (!signal.aborted) figure.replaceChildren(el('p', 'rd-problem', error instanceof Error ? error.message : String(error))); });
    const table = readMeasurementTable(ref.id, { session: ref.day, revision: ref.version ?? 0 }, signal).then((value) => {
      if (signal.aborted) return;
      const head = holds.querySelector('.rd-holds-head')!; head.append(' ', el('span', 'rd-count mm-num', `${value.count} row${value.count === 1 ? '' : 's'}`));
      this.loaded.count = `${value.count} ${plural(value.memberLabel, value.count)}`; this.drawHead();
      holds.append(drawRows(body, value.columns, value.rows, 'rd-table rd-mini'));
    }, () => { if (!signal.aborted) holds.append(el('p', 'rd-note', 'Its table is not kept with these outlines.')); });
    await Promise.all([masks, table]);
  }
  /** A table or measurements: the table, its rows drawn as they scroll. */
  private async drawTable(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!, ref = refOf(entry);
    this.loading(body, 'Reading the table…');
    const value: MeasurementTable = await readMeasurementTable(ref.id, { session: ref.day, revision: ref.version ?? 0 }, signal);
    if (signal.aborted) return;
    this.loaded.count = `${value.count} ${value.count === 1 ? 'row' : 'rows'}`; this.drawHead();
    body.replaceChildren(...(value.note ? [el('p', 'rd-note', value.note)] : []), drawRows(body, value.columns, value.rows, 'rd-table'));
    body.classList.add('rd-tablebody');
  }
  /** A write-up, or a researcher's table or chart: from its research session (the wire the researcher's Tab reads), on
   * its page in its own theme. */
  private async drawWriteup(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!, researchers = this.deps.researchers(), researcher = researcherOf(entry, researchers);
    if (!researcher) throw new Error('Its researcher is not in this Project any more. It stays in Data.');
    this.loading(body, 'Opening its page…');
    const row = researchers.find((r) => r.rid === researcher.rid);
    const day = row?.day;
    const response = await fetch(`/api/research/${encodeURIComponent(researcher.rid)}${day ? `?day=${encodeURIComponent(day)}` : ''}`, { signal });
    if (!response.ok) throw new Error('Its researcher’s work could not be read. Try again.');
    const s = await response.json() as ResearchSession;
    if (signal.aborted) return;
    this.session = s;
    const r = s.result ?? s.history?.findLast((item) => !item.mode && item.result)?.result ?? null;
    const shape = readerShape(entry);
    if (shape === 'finding-table' || shape === 'finding-chart') {
      // Its place in its findings: the record's own index, else its id's number (counted from 1, research_entries.py).
      const recorded = (entry.source as { index?: unknown } | undefined)?.index;
      const index = typeof recorded === 'number' ? recorded : Number(entry.id.split(':').pop()?.replace(/^\D+-/, '') ?? 'NaN') - 1;
      const node = r && Number.isInteger(index) ? (shape === 'finding-table' ? researchTableNode(r, index) : researchChartNode(r, index)) : null;
      if (!node) throw new Error('This part of its findings is not in its saved work any more.');
      body.replaceChildren(node); body.classList.add('rd-filebody'); return;
    }
    const outputId = outputOf(entry);
    const output = outputId ? s.outputs.find((o) => o.id === outputId) : defaultResearchOutput(s, r);
    if (outputId && !output) throw new Error('That version of its work is not in its saved work any more.');
    const presentation = output?.kind === 'presentation' ? output : undefined;
    let finding = r;
    const wanted = presentation?.findingRevision?.version;
    if (wanted && wanted !== r?.findingRevision?.version) {
      const kept = await fetch(`/api/research/${encodeURIComponent(s.rid)}/findings/${wanted}?day=${encodeURIComponent(s.day ?? day ?? '')}`, { signal });
      if (!kept.ok) throw new Error('The findings this presentation was made from could not be read. Try again.');
      const record = await kept.json() as { version: number; digest: string; complete: boolean; result: NonNullable<ResearchSession['result']> };
      finding = { ...record.result, findingRevision: { version: record.version, digest: record.digest, complete: record.complete } };
    }
    if (signal.aborted) return;
    const hero = researchHero(presentation ? undefined : output, finding, { picture: (o, caption) => readerPicture(o, caption) });
    const nodes = hero.nodes.length ? hero.nodes : finding ? [researchFindingPage(finding, presentation)] : [];
    const working = this.deps.working(s.rid);
    this.loaded.title = clean(finding?.title || output?.caption || r?.title || '') || undefined;
    this.loaded.kind = output?.kind === 'presentation' ? 'Presentation' : output?.kind === 'html' ? 'Lesson' : output?.kind === 'video' ? 'Video' : output?.kind === 'image' ? 'Picture' : finding ? 'Report' : undefined;
    this.loaded.live = working;
    this.drawHead();
    if (!nodes.length) { body.replaceChildren(el('p', 'rd-empty', working ? 'Nothing written yet. It is still working; its page shows here as it writes.' : 'Nothing written. Its conversation is on its Tab.')); return; }
    const theme = (presentation?.content && 'layout' in presentation.content ? presentation.content.style?.theme : null) ?? finding?.presentation?.plan?.style?.theme ?? 'paper';
    const desk = el('div', 'desk rd-desk'); desk.dataset.fit = 'width';
    const pages = el('div', 'desk-pages'), page = el('article', `page${hero.frame ? ' is-frame' : ''}`), inner = el('div', 'page-in');
    page.dataset.theme = ['paper', 'graphite', 'contrast', 'accent'].includes(theme) ? theme : 'paper';
    inner.append(...nodes); page.append(inner); pages.append(page); desk.append(pages);
    body.replaceChildren(desk); body.classList.add('rd-deskbody');
  }
  /** A source: what it is and where it came from (its `values`), the picture for a web picture, and the write-up that
   * cites it (opening it in this reader). Web data: words and links only. */
  private async drawSource(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!, values = entry.values;
    const card = el('article', 'rd-src');
    const icon = el('span', 'rd-src-ic'); icon.append(tabMark(values?.picture || entry.pictures?.length ? 'picture' : 'link', 'rd-ic-mark'));
    const words = el('div', 'rd-src-w');
    words.append(el('strong', '', values?.title || readerTitle(entry.title, entry.kind)));
    if (values?.authors) words.append(el('span', '', values.authors));
    if (values?.url) { const site = el('a', 'rd-src-site', `${values.site || hostOf(values.url)} ↗`); site.href = values.url; site.target = '_blank'; site.rel = 'noopener noreferrer'; words.append(site); }
    const rights = [values?.licence, values?.credit].filter(Boolean).join(' · ');
    if (rights) words.append(el('span', 'rd-src-rights', rights));
    card.append(icon, words);
    const picture = entry.pictures?.[0] ?? values?.picture;
    const parts: HTMLElement[] = [card];
    if (typeof picture === 'string' && picture) { const fig = el('div', 'rd-fig rd-fig-free'); const img = el('img'); img.src = picture; img.alt = values?.title ?? ''; img.loading = 'lazy'; fig.append(img); parts.push(fig); }
    const cited = el('div', 'rd-cited');
    parts.push(cited);
    body.replaceChildren(...parts);
    const detail = await this.readDetail(signal).catch(() => null);
    if (signal.aborted) return;
    const refs = detail?.how?.citedIn ?? [];
    if (refs.length) { cited.append(el('span', 'rd-cited-label', 'Cited in')); for (const ref of refs) cited.append(this.refRow(ref)); }
  }
  /** A researcher's sources (the mock's research-sources frame): a card for each page or web picture it cited, with
   * where it came from (site ↗) and the write-up that cites it (opening it in this reader). Web data: words and links. */
  private async drawSources(body: HTMLElement): Promise<void> {
    const rid = this.entry!.id.replace(/:sources$/, ''), made = this.deps.made?.(rid) ?? [], { sources } = workOf(made, rid);
    const page = this.deps.page?.() ?? null;
    body.classList.add('rd-sources');
    if (!sources.length) { body.replaceChildren(el('p', 'rd-empty', 'It cited no sources.')); return; }
    const titleOf = (id: string) => page?.owns(id) && page.entry.id === id ? page.title : readerTitle(made.find((item) => item.id === id)?.title ?? '', 'research');
    body.replaceChildren(...sources.map((source) => {
      const values = source.values, card = el('article', 'rd-src'), icon = el('span', 'rd-src-ic');
      const picture = !!(values?.picture || source.pictures?.length);
      icon.append(tabMark(picture ? 'picture' : 'output', 'rd-ic-mark'));
      const words = el('div', 'rd-src-w');
      const open = button(values?.title || readerTitle(source.title, source.kind), () => this.show(source, 'thing'), 'rd-src-open');
      open.title = 'Open this source here';
      words.append(open);
      const about = [values?.authors, picture && !values?.authors ? 'A picture from the web' : '', [values?.licence, values?.credit].filter(Boolean).join(' · ')].filter(Boolean).join(' · ');
      if (about) words.append(el('span', '', about));
      const line = el('span', 'rd-src-line');
      let said = 0;
      if (values?.url) { const site = el('a', 'rd-src-site', `${values.site || hostOf(values.url)} ↗`); site.href = values.url; site.target = '_blank'; site.rel = 'noopener noreferrer'; line.append(site); said++; }
      for (const id of ((source.source as { citedIn?: unknown } | undefined)?.citedIn as unknown[] | undefined ?? []).filter((id): id is string => typeof id === 'string').slice(0, 3)) {
        const title = titleOf(id); if (!title) continue;
        if (said++) line.append(' · ');
        const cited = button(`cited in ${title}`, () => { const item = made.find((m) => m.id === id) ?? (page?.owns(id) ? page.entry : null); if (item) this.choose(item); }, 'rd-src-cited');
        cited.title = `Open ${title} here`;
        line.append(cited);
      }
      if (said) words.append(line);
      card.append(icon, words);
      return card;
    }));
  }
  /** A note: its text, with Edit opening today's note window on this Tab. */
  private async drawNote(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!;
    this.loading(body, 'Opening the note…');
    const text = await readRetainedText(refOf(entry), signal);
    if (signal.aborted) return;
    const desk = el('div', 'desk rd-desk'), pages = el('div', 'desk-pages'), page = el('article', 'page'), inner = el('div', 'page-in');
    page.dataset.theme = 'paper';
    // On its page as a write-up is: its title, when, then its words (the page's own type).
    const words = el('div', 'viewer-summary'), when = whenWords(entry.at);
    words.append(el('h2', '', readerTitle(entry.title, entry.kind)), ...(when ? [el('p', 'byline', `Note, ${when}`)] : []), ...text.split(/\n{2,}/).map((part) => el('p', '', part)));
    inner.append(words); page.append(inner); pages.append(page); desk.append(pages);
    body.replaceChildren(desk); body.classList.add('rd-deskbody');
  }
  /** A recording: the player (today's preview modal goes). */
  private async drawRecording(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!;
    let asset = entry.asset ?? null;
    if (!asset) {
      this.loading(body, 'Opening the recording…');
      const response = await fetch(`/api/recordings/${encodeURIComponent(entry.id)}/asset?day=${encodeURIComponent(entry.day ?? '')}`, { method: 'POST', signal });
      const data = await response.json() as { asset?: AssetMeta; error?: string };
      if (!response.ok || !data.asset) throw new Error(data.error ?? 'The recording could not be opened.');
      asset = data.asset;
    }
    if (signal.aborted) return;
    const video = el('video', 'rd-video'); video.controls = true; video.preload = 'metadata'; video.src = `/api/library/${encodeURIComponent(asset.id)}/file`;
    body.replaceChildren(video, el('p', 'rd-note', 'Playing it here leaves your Stage as it is.'));
  }
  /** A picture: the picture. */
  private async drawPicture(body: HTMLElement): Promise<void> {
    const entry = this.entry!;
    const url = entry.asset ? `/api/library/${encodeURIComponent(entry.asset.id)}/file` : typeof entry.pictures?.[0] === 'string' ? entry.pictures[0] as string
      : `/api/resources/${encodeURIComponent(entry.id)}/picture?${new URLSearchParams({ day: entry.day ?? '' })}`;
    const fig = el('div', 'rd-fig'), img = el('img'); img.src = url; img.alt = readerTitle(entry.title, entry.kind); img.decoding = 'async'; fig.append(img);
    const parts: HTMLElement[] = [fig];
    const credit = entry.asset ? [entry.asset.captured, entry.asset.specimen, entry.asset.notes].filter(Boolean).join(' · ') : entry.plain || entry.summary;
    if (credit) parts.push(el('p', 'rd-note', String(credit)));
    body.replaceChildren(...parts);
  }
  /** An answer or anything else: its picture when it has one, and its words. */
  private async drawAnswer(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!;
    if (entry.kind === 'project' && (!entry.material || ['file', 'folder', 'model', 'analysis', 'matrix', 'image', 'recording', 'recorded-dataset'].includes(entry.material.kind))) {
      this.loading(body, 'Reading saved material…');
      const ref = {id: entry.id, day: entry.day ?? '', version: Number(entry.version)};
      const document = await readMaterialRevision(ref, entry.source?.sha256, signal);
      if (signal.aborted) return;
      const open = async (target: {id: string; version: number; title: string; digest?: string}) => {
        if (signal.aborted) return;
        const targetRef = {...ref, id: target.id, version: target.version};
        const saved = await readMaterialRevision(targetRef, target.digest, signal);
        if (signal.aborted) return;
        this.deps.openInNewTab({id: targetRef.id, day: targetRef.day, version: targetRef.version,
          kind: 'project', title: String(saved.title ?? target.title), summary: '', state: 'saved', actions: [],
          ...(target.digest ? {source: {sha256: target.digest}} : {})});
      };
      this.loaded.title = typeof document.title === 'string' ? document.title : entry.title;
      const current = this.entry!;
      this.loaded.kind = current.material ? kindWord(current) : document.kind === 'source-file' ? 'File' : document.kind === 'source-directory' ? 'Folder' : kindWord(entry);
      this.drawHead();
      body.replaceChildren();
      if (document.kind === 'source-file') {
        showScientificSource(body, document.state, ref, open, document.resources);
        showRetainedMedia(body, document.state, ref, signal);
        let signature: string | undefined;
        this.refreshMaterial = (latest) => {
          this.loaded.kind = latest.material ? kindWord(latest) : 'File';
          const reports = (latest.material?.inspection?.reports ?? []).map(report => {
            const candidate = this.deps.retainedEntry?.({id: report.id, day: report.session, version: report.revision});
            const retained = candidate?.id === report.id && candidate.day === report.session
              && candidate.version === report.revision && candidate.source?.sha256 === report.digest ? candidate : null;
            return {report, caption: retained ? byLine(retained, makerOf(retained, this.deps.researchers()), '') : ''};
          });
          const next = JSON.stringify([latest.material, reports]);
          if (next === signature) return;
          signature = next;
          const previous = body.querySelector<HTMLElement>('.rd-material-inspection');
          previous?.remove();
          const inspection = latest.material?.inspection;
          const details=el('section','rd-material-inspection');
          if(latest.material)details.append(materialSummary(latest.material));
          const actions=el('div','material-actions');
          if(this.deps.materialAction){
            for(const [label,action] of [[inspection?'Inspect again':'Inspect','inspect'],['Use in an experiment','experiment'],['Ask about this material','ask']] as const){
              if(action==='inspect'&&latest.material?.kind!=='matrix')continue;
              const b=button(label,()=>{if(!signal.aborted)this.deps.materialAction?.(latest,action);});actions.append(b);
            }
          }
          details.append(actions);
          const uses=latest.material?.uses?.filter(use=>use.study&&use.revision===ref.version&&use.digest===latest.source?.sha256)??[];
          for(const use of uses){const b=button(use.studyTitle?`Study · ${use.studyTitle}`:'Open Study',()=>{if(!signal.aborted)this.deps.openStudy?.(use.study!);});b.className='material-study-link';b.title='Open the linked Study';details.append(b);}
          if (inspection) {
            details.append(el('h3','','Retained inspections'));
            for (const [index, {report, caption}] of reports.entries()) {
              if (report.session !== ref.day) continue;
              const button = el('button', 'rd-inspection-link');
              button.type = 'button';
              const words = el('span', 'rd-inspection-words');
              words.append(el('strong', '', `Inspection ${index + 1}`),
                el('span', 'rd-inspection-caption', [caption, `Revision ${report.revision}`].filter(Boolean).join(' · ')));
              const arrow = el('span', 'rd-inspection-arrow', '↗'); arrow.setAttribute('aria-hidden', 'true');
              button.append(words, arrow);
              button.title = `Open retained inspection ${report.id}, revision ${report.revision}`;
              button.addEventListener('click', () => { void open({id: report.id, version: report.revision, digest: report.digest, title: 'Matrix inspection'}).catch(error => {
                if (!signal.aborted) details.append(el('p', 'rd-note', error instanceof Error ? error.message : 'The retained inspection could not be opened.'));
              }); });
              const reportCard=el('article','material-inspection-report');reportCard.append(button);
              const facts=inspection.facts?.find(f=>f.report.id===report.id&&f.report.session===report.session&&f.report.revision===report.revision&&f.report.digest===report.digest);
              if(facts)reportCard.append(inspectionFacts(facts));
              details.append(reportCard);
            }
            if (inspection.total > inspection.reports.length) details.append(el('p', 'rd-note', `Showing ${inspection.reports.length} of ${inspection.total} retained inspections. All reported shapes were compared.`));
          }
          body.prepend(details);
        };
        this.refreshMaterial(current);
        this.drawHead();
        return;
      }
      if (document.kind === 'source-directory') { showScientificFolder(body, document.state, ref, open); return; }
      if (document.kind === 'matrix-result') { showMatrixResult(body, document.state, ref, document.resources, open); return; }
    }
    const parts: HTMLElement[] = [];
    const picture = entry.pictures?.find((p) => typeof p === 'string') as string | undefined;
    if (picture) { const fig = el('div', 'rd-fig rd-fig-free'), img = el('img'); img.src = picture; img.alt = readerTitle(entry.title, entry.kind); img.decoding = 'async'; fig.append(img); parts.push(fig); }
    const words = el('div', 'rd-words-body'); words.append(el('p', 'rd-loading', 'Reading it…'));
    parts.push(words); body.replaceChildren(...parts);
    const text = await readRetainedText(refOf(entry), signal).catch(() => entry.plain || entry.summary || '');
    if (signal.aborted) return;
    words.replaceChildren(...(text ? text.split(/\n{2,}/).map((part) => el('p', '', part)) : [el('p', 'rd-empty', 'Nothing written was kept with it.')]));
  }
  private async readDetail(signal: AbortSignal): Promise<Detail | null> {
    if (this.detail) return this.detail;
    const entry = this.entry!;
    if (entry.kind === 'asset') return null;
    const query = new URLSearchParams({ day: entry.day ?? '', ...(typeof entry.version === 'number' ? { revision: String(entry.version) } : {}) });
    const response = await fetch(`/api/resources/${encodeURIComponent(entry.id)}?${query}`, { signal });
    const value = await response.json() as Detail;
    if (!response.ok) throw new Error(value.error || 'How it was made could not be read.');
    this.detail = value; return value;
  }
  /** How it was made: the same rows for every kind. */
  private async drawHow(body: HTMLElement, signal: AbortSignal): Promise<void> {
    const entry = this.entry!, researchers = this.deps.researchers();
    const facts = (): HowFacts => ({ method: this.deps.method(entry), input: inputOf(entry, this.deps), places: this.deps.places(entry), session: this.session });
    // A researcher's own write-up on its Tab: its versions can be chosen here, and its page adds its own part (what it
    // worked from, its basis, what it noted, where it read, its effort and spending).
    const page = this.pageOf(entry);
    const draw = (how: ReaderHow | null | undefined) => {
      const list = el('dl', 'rd-how-dl');
      if (page?.versions.length) list.append(this.versionsRow(page));
      for (const row of howRows(entry, how, facts(), researchers)) if (!(page?.versions.length && row.label === 'Versions')) list.append(this.howRow(row));
      const own = page?.how() ?? null;
      own?.classList.add('rd-how-own');
      const link = el('a', 'rd-record', 'The saved record ↗');
      link.href = `/api/resources/${encodeURIComponent(entry.id)}?${new URLSearchParams({ day: entry.day ?? '', ...(typeof entry.version === 'number' ? { revision: String(entry.version) } : {}) })}`;
      link.target = '_blank'; link.rel = 'noopener'; link.title = 'The record as the app keeps it, with its ids';
      const run = (entry.madeBy as { execution?: string } | undefined)?.execution;
      const runLink = run ? el('a', 'rd-record', 'The run’s record ↗') : null;
      if (runLink) { runLink.href = `/api/execution/${encodeURIComponent(run!)}`; runLink.target = '_blank'; runLink.rel = 'noopener'; runLink.title = 'The run that made it, as the app keeps it'; }
      body.replaceChildren(list, ...(own ? [own] : []), ...(entry.kind === 'asset' || page && !this.detail?.entry ? [] : [link]), ...(runLink ? [runLink] : []));
    };
    body.classList.add('rd-how');
    draw(this.detail?.how);
    const [detail] = await Promise.all([this.readDetail(signal).catch(() => null), readerShape(entry) === 'writeup' && !this.session ? this.readSession(signal) : Promise.resolve()]);
    if (signal.aborted) return;
    draw(detail?.how);
  }
  private async readSession(signal: AbortSignal): Promise<void> {
    const researchers = this.deps.researchers(), researcher = researcherOf(this.entry!, researchers);
    if (!researcher) return;
    const day = researchers.find((r) => r.rid === researcher.rid)?.day;
    const response = await fetch(`/api/research/${encodeURIComponent(researcher.rid)}${day ? `?day=${encodeURIComponent(day)}` : ''}`, { signal }).catch(() => null);
    if (response?.ok) this.session = await response.json() as ResearchSession;
  }
  private howRow(row: HowRow): HTMLElement {
    const wrap = el('div', 'rd-how-row'), dd = el('dd');
    wrap.append(el('dt', '', row.label), dd);
    for (const words of row.words ?? []) dd.append(el('span', 'rd-how-words', words));
    for (const form of row.versions ?? []) {
      const line = el('span', `rd-ver${form === row.versions![0] ? ' is-cur' : ''}`);
      if (form.state === 'writing') line.append(litCellSafe('Being written'));
      line.append(form.label);
      const when = form.state === 'writing' ? 'being written' : whenWords(form.at);
      if (when) line.append(el('span', 'rd-ver-when', when));
      dd.append(line);
    }
    for (const ref of row.refs ?? []) dd.append(this.refRow(ref));
    if (row.quiet) dd.append(el('span', 'rd-how-quiet', row.quiet));
    // On a researcher's own Tab, Talk to it would go nowhere: its conversation is beside the reader.
    const here = this.mode === 'research' && row.talk?.rid === this.deps.page?.()?.rid;
    if (row.talk && !here) { const talk = button('', () => this.deps.talk(row.talk!.rid, this.entry!), 'rd-talk'); talk.append(`Talk to ${row.talk.name}`, tabMark('right', 'rd-talk-icon')); dd.append(talk); }
    return wrap;
  }
  /** Its versions, each one a press that shows it (the one shown ticked). */
  private versionsRow(page: ReaderPage): HTMLElement {
    const wrap = el('div', 'rd-how-row'), dd = el('dd');
    wrap.append(el('dt', '', 'Versions'), dd);
    for (const version of page.versions) {
      const pick = button('', () => this.pick(version), `rd-ver rd-ver-pick${version.checked ? ' is-cur' : ''}`);
      pick.setAttribute('aria-pressed', String(version.checked)); pick.title = version.checked ? 'Shown on the page' : 'Show this version on the page';
      if (version.checked) pick.append(tabMark('tick', 'rd-tick'));
      pick.append(version.label);
      dd.append(pick);
    }
    return wrap;
  }
  private refRow(ref: ReaderRef): HTMLElement {
    const row = button('', () => this.deps.open({ id: ref.id, day: this.entry?.day }), 'rd-ref');
    const icon = el('span', 'rd-ref-ic'); icon.append(tabMark(readerMark({ kind: ref.kind, table: false }), 'rd-ic-mark'));
    const words = el('span', 'rd-ref-w');
    words.append(el('span', 'rd-ref-t', ref.title), el('span', 'rd-ref-s', [ref.maker ? makerWords(ref.maker) : '', kindWord({ kind: ref.kind })].filter(Boolean).join(' · ')));
    row.append(icon, words, tabMark('right', 'rd-ref-go'));
    row.title = `Open ${ref.title} here`;
    return row;
  }
}

/** The same thing (and revision) as another. */
export function sameItem(a: Pick<ReaderEntry, 'id' | 'day' | 'version'>, b: Pick<ReaderEntry, 'id' | 'day' | 'version'>): boolean {
  return a.id === b.id && (a.day ?? '') === (b.day ?? '') && (a.version ?? null) === (b.version ?? null);
}
const refOf = (entry: ReaderEntry) => ({ day: entry.day ?? '', id: entry.id, version: entry.version ?? null, picture: null });
/** The output a researcher's work names: S4's `<rid>:<outputId>` lesson, video or figure, the studio's older
 * `<rid>#<output>`, or the newest form that names one. */
function outputOf(entry: ReaderEntry): string | null {
  if (entry.id.includes('#')) return entry.id.split('#')[1] || null;
  if (['lesson', 'video', 'figure'].includes(entry.kind) && entry.id.includes(':')) return entry.id.split(':').slice(1).join(':') || null;
  return null;
}
/** The input its record names (the picture it was made on). */
function inputOf(entry: ReaderEntry, deps: Pick<ReaderDeps, 'input'>): { id: string; title: string } | null {
  const asset = (entry.source as { sourceImage?: { asset?: string } } | undefined)?.sourceImage?.asset;
  return asset ? deps.input(asset) : null;
}
const plural = (word: string, n: number) => !word ? (n === 1 ? 'row' : 'rows') : n === 1 ? word : word.endsWith('s') ? word : `${word}s`;
const hostOf = (url: string) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; } };
function litCellSafe(label: string, state: LitCellState = 'working'): Element {
  try { return litCell(state, { size: 14, label }); } catch { const dot = el('span', 'rd-live'); dot.title = label; return dot; }
}
/** A picture a researcher made, on its page: the picture and its caption, saying when it is an illustration or when the
 * picture model may have changed the photo. */
function readerPicture(o: ResearchOutput, caption: string): HTMLElement {
  const figure = el('figure'), img = el('img'), cap = el('figcaption', '', caption);
  img.src = o.url; img.alt = caption; img.loading = 'lazy';
  if (o.altered) cap.prepend(el('strong', '', 'The picture model may have changed the photo. '));
  else if (o.generated && !/illustration|not the specimen/i.test(caption)) cap.prepend('Illustration, not the specimen. ');
  figure.append(img, cap);
  return figure;
}

const ROW_HEIGHT = 28;
/** A table whose rows are drawn as they scroll into view: a spacer above and below the rows in view, so a table of
 * thousands opens at once and scrolls without drawing them all. `scroller` is the element that scrolls. */
export function drawRows(scroller: HTMLElement, columns: TableColumn[], rows: Record<string, unknown>[], className: string): HTMLElement {
  const wrap = el('div', className), table = el('table', 'rd-rtable');
  const head = el('thead'), headRow = el('tr');
  for (const column of columns) { const th = el('th', '', header(column)); th.scope = 'col'; th.dataset.type = column.type; headRow.append(th); }
  head.append(headRow);
  const body = el('tbody');
  // Spacers hold the rows not drawn: one cell across the table, sized to them.
  const spacer = () => { const tr = el('tr', 'rd-spacer'), td = el('td'); td.colSpan = Math.max(1, columns.length); tr.append(td); return { tr, td }; };
  const top = spacer(), bottom = spacer();
  table.append(head, body); wrap.append(table);
  if (!rows.length) { wrap.append(el('p', 'rd-note', 'No rows.')); return wrap; }
  let drawn = '', frame = 0;
  const draw = (scrollTop: number, viewport: number) => {
    const { start, end } = windowRows(rows.length, ROW_HEIGHT, scrollTop, viewport);
    const key = `${start}:${end}`; if (key === drawn) return; drawn = key;
    top.td.style.height = `${start * ROW_HEIGHT}px`; bottom.td.style.height = `${(rows.length - end) * ROW_HEIGHT}px`;
    top.tr.hidden = start === 0; bottom.tr.hidden = end >= rows.length;
    const visible: HTMLElement[] = [];
    for (let i = start; i < end; i++) {
      const tr = el('tr');
      for (const column of columns) { const td = el('td', '', formatValue(rows[i][column.key], column)); td.dataset.type = column.type; tr.append(td); }
      visible.push(tr);
    }
    body.replaceChildren(top.tr, ...visible, bottom.tr);
  };
  // The first rows at once (no measuring while it opens); then, as the reader scrolls, what is in view.
  draw(0, 30 * ROW_HEIGHT);
  scroller.addEventListener('scroll', () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (!wrap.isConnected) return;
      const offset = body.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      draw(Math.max(0, scroller.scrollTop - offset), scroller.clientHeight);
    });
  }, { passive: true });
  return wrap;
}

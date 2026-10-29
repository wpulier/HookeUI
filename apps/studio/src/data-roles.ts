// Data by what each thing is (ticket 66, D2 and D4; the mock's dataList(), dataRow() and madeWords() in
// design/data-research). Four sections in the order a lab notebook keeps them, Pictures and recordings, Analyses,
// Write-ups, Sources, then Other folded last; who made each thing is a filter ("Made by everyone ▾"), never a section.
// Everything here is read from the index's own fields (S1-S3: `role`, `maker`, `run`; a report's `forms`; a source's
// `values`) and the Project's sources model: nothing is guessed. A maker the record cannot tell is no maker (the row then
// names none), and a run's entries are one row only when the server says they share it.
import type { AssetMeta } from './protocol';
import type { StudyResource } from './workspace-study-model';
import { kindWords, matchesSearch } from './workspace-data-card';
import { daysAgo, monthDay } from './sessions';

export type DataRole = 'acquired' | 'analysis' | 'writeup' | 'source' | 'other' | 'files' | 'model' | 'study' | 'history';
export interface ProjectMaterial {
  kind: string; origin: 'imported' | 'generated' | 'authored' | 'unknown'; input: boolean;
  section: DataRole; category: string; record: boolean; run?: string;
  uses?: {study: string; revision: number; digest: string; title: string; available: boolean; material?: ProjectMaterial}[];
  format?: string; bytes?: number; shape?: number[]; dependencies: unknown[];
}
/** Who made it (S2): you, the Assistant or a researcher (its id when the record says it). */
export interface DataMaker { kind: 'you' | 'assistant' | 'researcher' | 'agent'; id?: string; name: string }
/** A write-up's form, newest first (S4): its presentation ("Presentation") and each findings version ("Findings v2"). */
export interface DataForm { label: string; version?: number; outputId?: string; at?: number; state?: 'writing' | 'ready' }
/** A source's facts (S4), all of them its researcher's or the web's words. */
export interface SourceValues { url?: string; site?: string; title?: string; authors?: string; licence?: string; credit?: string; picture?: string }
/** An index entry with Data's fields (the wire contract of ticket 66); every one is optional, so an older index reads. */
export interface DataEntry extends StudyResource {
  material?: ProjectMaterial;
  role?: DataRole | null;
  maker?: DataMaker | null;
  run?: string | null;
  forms?: DataForm[];
  values?: unknown;
  /** Its title and words are a researcher's (or the web's): shown as theirs, never as the app's. */
  untrusted?: boolean;
}

/** Data's sections, in order (the words Data shows). */
export const ROLE_SECTIONS: readonly (readonly [DataRole, string])[] = [
  ['acquired', 'Pictures and recordings'], ['files', 'Files and folders'], ['model', 'Models'], ['analysis', 'Analyses'], ['study', 'Studies'], ['writeup', 'Write-ups'], ['source', 'Sources'], ['history', 'Workflow history'], ['other', 'Files'],
];

/** A section's heading as Data shows it ("Write-ups"): Help and every message that points into Data say it this way. */
export function sectionWords(role: DataRole): string {
  return ROLE_SECTIONS.find(([r]) => r === role)?.[1] ?? 'Files';
}

/** The section an entry is listed in: the server's role (S1), or null for a kind Data does not list. An index from before
 * ticket 66 sends no role: its inputs are Pictures and recordings, an outcome is left out (as Data left it out), and the
 * rest is Other, so nothing it lists becomes unreachable. */
export function roleOf(entry: Pick<DataEntry, 'kind' | 'role'>): DataRole | null {
  if (entry.role !== undefined) return entry.role ?? null;
  if (entry.kind === 'outcome') return null;
  return entry.kind === 'asset' || entry.kind === 'volume' ? 'acquired' : 'other';
}

/** One row of Data: an input of this Project, or one thing made (one run's outlines, table and answer are one row). */
export interface DataRow {
  /** Its id in the list: the entry it opens (or the input's id). */
  key: string;
  role: DataRole;
  /** The entry a click opens (a run's outlines before its table, its table before its answer); null for an input the
   * index has not listed yet. */
  entry: DataEntry | null;
  /** Every entry of the row, the one it opens first. */
  parts: DataEntry[];
  /** An input of this Project (the sources model), for Pictures and recordings. */
  asset: AssetMeta | null;
  maker: DataMaker | null;
  /** Its newest part's time, for the order within a section. */
  at: number;
}

/** Which of a run's entries its row opens: its outlines before its table, a table before an answer. */
const OPENS_FIRST = ['population', 'named-outline', 'individual', 'tracks', 'dataset-table', 'analysis', 'graph', 'finding-table', 'finding-chart'];
function opensFirst(entry: DataEntry): number {
  if (entry.material?.kind === 'workflow' && !entry.material.run) return -1;
  const at = OPENS_FIRST.indexOf(entry.kind);
  if (at >= 0) return at;
  return entry.kind === 'result' && entry.table === true ? OPENS_FIRST.length : OPENS_FIRST.length + 1;
}

/** A report its researcher has written nothing of yet (no findings, no presentation): it is the researcher, listed in
 * Researchers, not yet a thing in Data. An index without forms (before ticket 66) lists it as before. */
const unwritten = (entry: DataEntry) => entry.kind === 'research' && Array.isArray(entry.forms) && entry.forms.length === 0;

/** Data's rows: the Project's inputs (the sources model, as it orders them, each joined with its index entry), then
 * the index's other entries, those sharing a run folded into one row, newest first within each section, in the
 * sections' order. The live camera is not an input here: it is not a thing kept. */
export function dataRows(entries: readonly DataEntry[], sources: readonly AssetMeta[] = []): DataRow[] {
  const listed = entries.filter((entry) => roleOf(entry) !== null && !unwritten(entry));
  const assets = new Map(listed.filter((entry) => entry.kind === 'asset').map((entry) => [entry.id, entry]));
  const inputs: DataRow[] = sources.map((asset) => {
    const entry = assets.get(asset.id) ?? null;
    return { key: asset.id, role: 'acquired', entry, parts: entry ? [entry] : [], asset, maker: entry?.maker ?? null, at: entry?.at ?? asset.capturedAt ?? 0 };
  });
  const joined = new Set(sources.map((asset) => asset.id));
  const rows: DataRow[] = [], runs = new Map<string, DataRow>();
  for (const entry of listed) {
    if (entry.kind === 'asset' && joined.has(entry.id)) continue;
    const role = roleOf(entry)!;
    // Folded within its section only: a run's parts are one thing, and a thing is in one section.
    const run = entry.run ? `${role}:${entry.run}` : '';
    const folded = run ? runs.get(run) : undefined;
    if (folded) { folded.parts.push(entry); continue; }
    const row: DataRow = { key: entry.id, role, entry, parts: [entry], asset: null, maker: null, at: 0 };
    if (run) runs.set(run, row);
    rows.push(row);
  }
  for (const row of rows) {
    row.parts.sort((a, b) => opensFirst(a) - opensFirst(b) || (a.at ?? 0) - (b.at ?? 0));
    row.entry = row.parts[0]; row.key = row.entry.id;
    row.maker = row.parts.map((part) => part.maker ?? null).find(Boolean) ?? null;
    row.at = Math.max(...row.parts.map((part) => part.at ?? 0));
  }
  const newest = (a: DataRow, b: DataRow) => b.at - a.at;
  return [...inputs, ...ROLE_SECTIONS.flatMap(([role]) => rows.filter((row) => row.role === role).sort(newest))];
}

/** A server title's parts ("Bright regions · Bright objects · 02:35"): what it is, what made it, and when (as
 * workspace-data-card's resultRowWords splits it). A title without the server's time stays whole. */
export function titleParts(title: string): { head: string; rest: string[]; time: string } {
  const parts = title.split(' · ').map((part) => part.trim()).filter(Boolean);
  const timed = parts.length > 1 && /^\d{1,2}:\d{2}(\s?[ap]m)?$/i.test(parts[parts.length - 1]);
  if (!timed) return { head: title.trim(), rest: [], time: '' };
  const time = parts.pop()!;
  return { head: parts.shift()!, rest: parts, time };
}

/** The row's title. A thing made by the app is named by the first part of its server title, without "for Researcher 3"
 * when the row already says who made it; a researcher's own title (a report, its table, a source) is shown whole, in its
 * words; an input by its title's first part ("Human cells", its credit going to the quiet line). */
export function rowTitle(row: DataRow): string {
  const entry = row.entry;
  if (!entry || entry.kind === 'asset') return (row.asset?.title ?? entry?.title ?? '').split(' · ')[0].trim();
  // Your note by its own title ("Your note · Field A looks denser" is "Field A looks denser"; its line says Note).
  if (entry.kind === 'annotation') return entry.title.replace(/^Your note · /, '') || entry.title;
  if (entry.untrusted || row.role === 'writeup' || row.role === 'source') return entry.title;
  const head = titleParts(entry.title).head, maker = row.maker;
  const named = maker?.kind === 'researcher' ? `, for ${maker.name}` : '';
  return named && head.endsWith(named) ? head.slice(0, -named.length) : head;
}

/** How many it holds, beside its title: a run's outlines by its count and its own word ("271 regions", "341 outlines";
 * from the server's plain words, "271 outlined"), a researcher's table by its rows. '' when the record does not say. */
export function rowCount(row: DataRow): string {
  const outlines = row.parts.find((part) => part.kind === 'population');
  const n = outlines ? outlinedCount(outlines) : null;
  if (outlines && n !== null) {
    const what = titleParts(outlines.title).head.replace(/, for .+$/, '').toLowerCase();
    return `${n} ${/\b([a-z]+s)$/.exec(what)?.[1] ?? 'outlined'}`;
  }
  const table = row.parts.find((part) => part.kind === 'finding-table');
  const rows = table ? /^(\d+) rows?\b/.exec(table.summary ?? '')?.[1] : undefined;
  return rows ? `${rows} ${rows === '1' ? 'row' : 'rows'}` : '';
}
const outlinedCount = (entry: DataEntry): number | null => { const n = /^(\d+) outlined\b/.exec(entry.plain ?? '')?.[1]; return n ? Number(n) : null; };

/** One part of a row in a word: what the row holds ("Outlines", "Table", "Answer"). */
export function partWord(entry: DataEntry): string {
  if (entry.kind === 'population') return 'Outlines';
  if (entry.kind === 'result') return entry.table === true ? 'Table' : 'Answer';
  if (entry.kind === 'finding-table') return 'Table';
  if (entry.kind === 'finding-chart') return 'Chart';
  return kindWords(entry).split(' · ')[0] || entry.kind;
}

/** What a row holds, in words ("Outlines", "Table, 271 rows", "Answer"): a run's outlines keep their table. */
export function rowHolds(row: DataRow): string[] {
  const words: string[] = [];
  for (const part of row.parts) {
    if (part.kind !== 'population') { words.push(partWord(part)); continue; }
    const n = outlinedCount(part);
    words.push('Outlines', n === null ? 'Table' : `Table, ${n} ${n === 1 ? 'row' : 'rows'}`);
  }
  return [...new Set(words)];
}

/** A write-up's current form in words: "Presentation · being written", "Findings v2"; a lesson, video, picture or note
 * by its kind. */
export function writeupWords(entry: DataEntry): string {
  if (entry.kind === 'research') {
    const form = entry.forms?.[0];
    return form ? (form.state === 'writing' ? `${form.label} · being written` : form.label) : 'Report';
  }
  return ({ lesson: 'Lesson', video: 'Video', figure: 'Picture', annotation: 'Note' } as Record<string, string>)[entry.kind] ?? partWord(entry);
}

/** Whether its current form is being written now (its row carries the lit cell). */
export function rowWriting(row: DataRow): boolean {
  return row.entry?.kind === 'research' && row.entry.forms?.[0]?.state === 'writing';
}

export function sourceValues(entry: DataEntry | null | undefined): SourceValues {
  const values = entry?.values;
  return values && typeof values === 'object' ? values as SourceValues : {};
}

/** "0:24", "1:02:05". */
const duration = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds)), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
};

/** When, in the quiet line: its time today ("10:41"), else its day ("Oct 24"). */
function whenWords(entry: DataEntry, time: string, now: number): string {
  if (typeof entry.at === 'number' && entry.at > 0 && daysAgo(entry.at, now) !== 0) return monthDay(entry.at, now);
  return time || (entry.time ? entry.time.slice(0, 5) : '');
}

/** The row's quiet line, after its "On this Tab" mark (the list draws that): who made it, unless it was you or the list
 * is filtered to its maker; then what it is or what made it, and when. An input: what it is, its length, its credit. A
 * write-up: its current form. A source: its authors or credit, and its site. */
export function rowLine(row: DataRow, facts: { filtered?: boolean; now: number; model?: (entry: DataEntry) => string }): string {
  const entry = row.entry;
  if (!entry || entry.kind === 'asset') {
    const asset = row.asset, recording = asset?.kind === 'recording';
    const credit = (asset?.title ?? entry?.title ?? '').split(' · ').slice(1).join(' · ');
    return [recording ? 'Recording' : 'Picture', recording && asset?.seconds ? duration(asset.seconds) : '', credit].filter(Boolean).join(' · ');
  }
  const maker = !facts.filtered && row.maker && row.maker.kind !== 'you' ? row.maker.name : '';
  if (row.role === 'writeup') return [maker, writeupWords(entry)].filter(Boolean).join(' · ');
  if (row.role === 'source' && entry.kind === 'source') {
    // Its site, unless its title is its address already (a page whose title was not kept).
    const values = sourceValues(entry), site = values.site && !entry.title.startsWith(values.site) ? values.site : '';
    return [maker, values.picture ? 'Web picture' : 'Page', values.authors || values.credit || '', site].filter(Boolean).join(' · ');
  }
  // A researcher's own title says nothing of the app's: what it is, in the app's word, then when.
  const parts = entry.untrusted ? { head: entry.title, rest: [], time: '' } : titleParts(entry.title);
  const method = parts.rest.join(' · ') || (entry.untrusted ? partWord(entry) : facts.model?.(entry) ?? '');
  const what = method || (row.role === 'other' || row.role === 'acquired' ? kindWords(entry).split(' · ')[0] : '');
  return [maker, what === parts.head ? '' : what, whenWords(entry, parts.time, facts.now)].filter(Boolean).join(' · ');
}

/** Whether Data's search finds the row: any of its parts as Data's search finds an entry (its title, summary, plain
 * words, id, kind), who made it, a write-up's forms, a source's site and address, an input's title. */
export function rowMatches(row: DataRow, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (row.asset && `${row.asset.title} ${row.asset.id}`.toLowerCase().includes(q)) return true;
  return row.parts.some((part) => matchesSearch(part, q) || [part.maker?.name, ...(part.forms ?? []).map((form) => form.label),
    sourceValues(part).site, sourceValues(part).url, sourceValues(part).authors].filter(Boolean).join(' ').toLowerCase().includes(q));
}

/** Whether the row was made from an input (Inputs' "Results" and a preview's Find results from this source). */
export function rowFrom(row: DataRow, asset: string): boolean {
  return row.asset?.id === asset || row.entry?.id === asset || row.parts.some((part) => part.source?.sourceImage?.asset === asset);
}

// --- Made by ----------------------------------------------------------------------------------------------------

/** A maker as a filter: "you", "assistant", an outside agent by its label ("a:Claude Code"; ADR 0026), or a researcher by
 * its name ("r:Researcher 3"). A Project's researchers are
 * named once each (Researcher N), and a record that knows only the name ("for Researcher 1") says no more than that. */
export type MakerKey = string;
export function makerKey(maker: DataMaker | null | undefined): MakerKey | null {
  if (!maker) return null;
  return maker.kind === 'researcher' ? `r:${maker.name}` : maker.kind === 'agent' ? `a:${maker.name}` : maker.kind;
}
export const researcherKey = (name: string): MakerKey => `r:${name}`;
export function rowMaker(row: DataRow): MakerKey | null { return makerKey(row.maker); }

export interface MakerChoice { key: MakerKey; name: string; /** A researcher's id, when a record says it (its Tab can open). */ rid?: string }

/** The "Made by" menu: You, the Assistant, then each researcher, by its number; each only when a record here says it
 * made something. An older record that cannot tell names no maker, so offering "You" with nothing under it would say you
 * made nothing when the record only does not know (ADR 0006). */
export function makerChoices(rows: readonly DataRow[]): MakerChoice[] {
  const researchers = new Map<MakerKey, MakerChoice>(), agents = new Map<MakerKey, MakerChoice>(), others = new Set<string>();
  for (const row of rows) for (const part of row.parts) {
    const maker = part.maker;
    if (maker?.kind === 'agent') { const key = makerKey(maker)!; if (!agents.has(key)) agents.set(key, { key, name: maker.name }); continue; }
    if (maker && maker.kind !== 'researcher') others.add(maker.kind);
    if (maker?.kind !== 'researcher') continue;
    const key = makerKey(maker)!, known = researchers.get(key);
    if (!known) researchers.set(key, { key, name: maker.name, ...(maker.id ? { rid: maker.id } : {}) });
    else if (!known.rid && maker.id) known.rid = maker.id;
  }
  const byNumber = (a: MakerChoice, b: MakerChoice) => a.name.localeCompare(b.name, undefined, { numeric: true });
  return [...(others.has('you') ? [{ key: 'you', name: 'You' }] : []), ...(others.has('assistant') ? [{ key: 'assistant', name: 'The Assistant' }] : []),
    ...[...agents.values()].sort(byNumber), ...[...researchers.values()].sort(byNumber)];
}

/** The chosen maker's name, for its chip ("Researcher 3", "You", "The Assistant"). */
export function makerName(key: MakerKey): string {
  return key === 'you' ? 'You' : key === 'assistant' ? 'The Assistant' : key.replace(/^[ra]:/, '');
}

// --- New since you last looked ----------------------------------------------------------------------------------

/** What has been seen in Data is kept per entry and version, as before ticket 66, so what was seen stays seen. */
export const seenKey = (entry: { id: string; version?: number | null }) => `${entry.id}@${entry.version ?? 0}`;

/** The keys a row is seen by: each of its parts. An input and a source are never marked New, nor counted on Data's
 * badge (a researcher reads many sources; the inputs are yours). */
export function rowSeenKeys(row: DataRow): string[] {
  if (row.role === 'history' || row.entry?.material?.origin === 'imported' || row.role === 'source' || !row.entry || row.entry.kind === 'asset') return [];
  return row.parts.map(seenKey);
}
export function rowIsNew(row: DataRow, seen: ReadonlySet<string>): boolean {
  return rowSeenKeys(row).some((key) => !seen.has(key));
}
/** Data's badge: rows not seen, not entries (a run's outlines and answer are one thing). */
export function unseenRows(rows: readonly DataRow[], seen: ReadonlySet<string>): number {
  return rows.filter((row) => rowIsNew(row, seen)).length;
}

// --- What a researcher made (D4) --------------------------------------------------------------------------------

/** A noun for each kind of thing, with its order in the line: write-ups, then analyses, then what it recorded, then the
 * rest; sources are counted apart, last. `mass`: said without a count ("outlines"). */
const NOUNS: Record<string, { one: string; many: string; order: number; mass?: boolean }> = {
  presentation: { one: 'a presentation', many: 'presentations', order: 0 },
  report: { one: 'a report', many: 'reports', order: 1 },
  lesson: { one: 'a lesson', many: 'lessons', order: 2 },
  video: { one: 'a video', many: 'videos', order: 3 },
  picture: { one: 'a picture', many: 'pictures', order: 4 },
  note: { one: 'a note', many: 'notes', order: 5 },
  table: { one: 'a table', many: 'tables', order: 10 },
  chart: { one: 'a chart', many: 'charts', order: 11 },
  analysis: { one: 'an analysis', many: 'analyses', order: 12 },
  outlines: { one: 'outlines', many: 'outlines', order: 13, mass: true },
  tracks: { one: 'motion tracks', many: 'motion tracks', order: 14, mass: true },
  answer: { one: 'an answer', many: 'answers', order: 15 },
  recording: { one: 'a recording', many: 'recordings', order: 20 },
  clip: { one: 'a clip', many: 'clips', order: 21 },
  dataset: { one: 'a dataset', many: 'datasets', order: 22 },
  bookmark: { one: 'a bookmark', many: 'bookmarks', order: 30 },
};
function nounOf(entry: DataEntry): string | null {
  switch (entry.kind) {
    // A presentation still being written is not made yet (ADR 0006): until it is, what it made is its report.
    case 'research': {
      const ready = (entry.forms ?? []).filter((form) => form.state !== 'writing');
      return ready.some((form) => form.label === 'Presentation') ? 'presentation' : ready.length || !entry.forms ? 'report' : null;
    }
    case 'lesson': return 'lesson';
    case 'video': return 'video';
    case 'figure': case 'snapshot': return 'picture';
    case 'annotation': return 'note';
    case 'finding-table': case 'dataset-table': return 'table';
    case 'finding-chart': case 'graph': return 'chart';
    case 'analysis': return 'analysis';
    case 'population': case 'named-outline': case 'individual': return 'outlines';
    case 'tracks': return 'tracks';
    case 'result': return entry.table === true ? 'table' : 'answer';
    case 'recording': return 'recording';
    case 'clip': return 'clip';
    case 'dataset': return 'dataset';
    case 'bookmark': return 'bookmark';
    default: return null;
  }
}

/** What a researcher made, from its entries ("Made a presentation, a table and outlines", "Made a report and 3
 * sources"): one thing per Data row, so a run's outlines and answer are its outlines; '' when it has made nothing yet. */
export function madeWords(entries: readonly DataEntry[], researcher: { rid: string; name: string }): string {
  const mine = entries.filter((entry) => entry.maker?.kind === 'researcher'
    && (entry.maker.id ? entry.maker.id === researcher.rid : entry.maker.name === researcher.name));
  const counts = new Map<string, number>();
  let sources = 0;
  for (const row of dataRows(mine)) {
    if (row.role === 'source') { sources++; continue; }
    const noun = row.entry ? nounOf(row.entry) : null;
    if (noun) counts.set(noun, (counts.get(noun) ?? 0) + 1);
  }
  const words = [...counts].sort(([a], [b]) => NOUNS[a].order - NOUNS[b].order)
    .map(([noun, n]) => (n === 1 || NOUNS[noun].mass ? NOUNS[noun].one : `${n} ${NOUNS[noun].many}`));
  if (sources) words.push(`${sources} ${sources === 1 ? 'source' : 'sources'}`);
  if (!words.length) return '';
  return `Made ${words.length === 1 ? words[0] : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`}`;
}

/** A researcher's lesson, video, picture, table, chart or source (S4): its researcher and, for a deliverable, the output
 * its Tab opens on. Clicking one opens that researcher's Tab on it, as Data's own rows for them did before ticket 66. */
const RESEARCH_WORK = new Set(['lesson', 'video', 'figure', 'finding-table', 'finding-chart', 'source']);
export function researcherWork(entry: Pick<DataEntry, 'kind' | 'source'>): { rid: string; output?: string } | null {
  if (!RESEARCH_WORK.has(entry.kind)) return null;
  const source = (entry.source ?? {}) as Record<string, unknown>;
  if (typeof source.researcher !== 'string' || !source.researcher) return null;
  return { rid: source.researcher, ...(typeof source.outputId === 'string' && source.outputId ? { output: source.outputId } : {}) };
}

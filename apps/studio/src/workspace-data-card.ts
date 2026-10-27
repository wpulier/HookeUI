/** Data's kinds in a word, its search, and each row's actions (concept mock, docs/design/2026-10-20-hooke-words.md).
 * Since ticket 66 Data lists by what each thing is (data-roles.ts, data-list.ts) and no longer shows the kind filter
 * below; `kindWords` and `matchesSearch` are still Data's words and its search, `menuActions` each row's ⋯.
 * Before, two filters overlapped (All/Artifacts/Answers/Records beside a type menu of Notes/Masks/Tracks/Research) and
 * a card showed up to seven buttons in a row. Now one row of kinds, each with its count, where every entry belongs to
 * exactly one kind (Other catches the rest, so nothing becomes unreachable); and a card shows at most three actions,
 * the others one step away under Details. No action is removed: `cardActions` places every one the card had. Since the
 * workspace-feel pass (step 4) a result is a row and its actions are its ⋯, in `menuActions` order. */
import { artifactPresentation } from './workspace-study-content';
import { resultGroup, type StudyResource } from './workspace-study-model';
import { madeByName } from './tools-catalog';
import type { CapabilitiesWire } from './protocol';

export type DataKind = 'masks' | 'tables' | 'tracks' | 'answers' | 'notes' | 'research' | 'lessons' | 'other';
export type DataFilter = 'all' | DataKind;
export const DATA_FILTERS: readonly [DataFilter, string][] = [
  ['all', 'All'], ['masks', 'Masks'], ['tables', 'Tables'], ['tracks', 'Tracks'], ['answers', 'Answers'],
  ['notes', 'Notes'], ['research', 'Research'], ['lessons', 'Lessons & presentations'], ['other', 'Other'],
];

type Entry = Pick<StudyResource, 'kind' | 'table' | 'product'>;

export function dataKind(entry: Entry): DataKind {
  if (['population', 'named-outline', 'individual'].includes(entry.kind)) return 'masks';
  if (artifactPresentation(entry) === 'table') return 'tables';
  if (entry.kind === 'tracks') return 'tracks';
  if (entry.kind === 'annotation') return 'notes';
  if (entry.kind === 'research') return 'research';
  if (entry.kind === 'lesson' || entry.kind === 'presentation') return 'lessons';
  if (resultGroup(entry as StudyResource) === 'answers') return 'answers';
  return 'other';
}

/** A card's kind in a word ("Masks", "Table"), with its revision only once there is a later one (revision 0 is just
 * the result as made; "not recorded" stays said, since a missing revision is worth knowing). */
export function kindWords(entry: Entry & { version?: number | null }): string {
  const words: Record<DataKind, string> = { masks: 'Masks', tables: 'Table', tracks: 'Tracks', answers: 'Answer', notes: 'Note', research: 'Research', lessons: '', other: '' };
  const plain = entry.kind.replace(/-/g, ' ');
  const word = words[dataKind(entry)] || plain.charAt(0).toUpperCase() + plain.slice(1);
  // A missing revision is said under Technical details, not in the eyebrow (2026-10-23 audit: internal facts off the card's face).
  const revision = typeof entry.version === 'number' && entry.version > 0 ? ` · revision ${entry.version}` : '';
  return `${word}${revision}`;
}

/** Whether Data's search finds an entry: its title, summary, plain words, id, and the kind word its row and the filter
 * show ("Masks", "Table"), so "mask" finds every Masks result. */
export function matchesSearch(entry: Entry & { title: string; summary?: string; plain?: string; id: string; version?: number | null }, query: string): boolean {
  return `${entry.title} ${entry.summary ?? ''} ${entry.plain ?? ''} ${entry.id} ${kindWords(entry)}`.toLowerCase().includes(query.toLowerCase());
}

/** How many entries each filter shows; All is every entry. */
export function dataCounts(entries: Entry[]): Record<DataFilter, number> {
  const counts = Object.fromEntries(DATA_FILTERS.map(([id]) => [id, 0])) as Record<DataFilter, number>;
  for (const entry of entries) { counts.all++; counts[dataKind(entry)]++; }
  return counts;
}

export function matchesFilter(entry: Entry, filter: DataFilter): boolean {
  return filter === 'all' || dataKind(entry) === filter;
}

/** A filter worth showing: All, the one chosen (even at 0, so it can be cleared), and any kind present. */
export function shownFilters(counts: Record<DataFilter, number>, chosen: DataFilter): [DataFilter, string, number][] {
  return DATA_FILTERS.filter(([id]) => id === 'all' || id === chosen || counts[id] > 0).map(([id, label]) => [id, label, counts[id]]);
}

export type CardAction = 'open-in' | 'reveal' | 'masks' | 'measurements' | 'provenance' | 'capture' | 'use-source';

/** A key a person never reads (a catalog id such as basic-bright-objects, a researcher id such as r602534): one token
 * of lower-case letters with a digit, hyphen or other punctuation. Display names have capitals or spaces. */
const rawKey = (text: string) => /^[a-z0-9][a-z0-9._#:/-]*$/.test(text) && /[\d._#:/-]/.test(text);

/** The model that made a result, by its one display name (contract/AGENTS.md "One name per model"): its role's option
 * (madeByName), else any role's option or catalog entry by that id; '' when the name is not known here, never the key. */
export function makerName(wire: CapabilitiesWire | null, madeBy: { implementation?: string; operation?: string } | null | undefined): string {
  const key = typeof madeBy?.implementation === 'string' ? madeBy.implementation : '';
  if (!key) return '';
  const named = madeBy?.operation ? madeByName(wire, madeBy as Record<string, unknown>) : '';
  if (named && named !== key) return named;
  for (const role of wire?.roles ?? []) { const option = role.options?.find((o) => o.id === key); if (option?.label) return option.label; }
  const row = wire?.models?.find((m) => m.id === key || m.usedFor.some((u) => u.option === key));
  return row?.name ?? '';
}

/** Who made it, for the row's quiet line: the researcher it was made for, by name, else the model or tool by its display
 * name; '' when no name a person reads is known (the row then says when). Never a raw key. */
export function madeByWords(entry: { madeBy?: { implementation?: string; operation?: string; for?: string; tool?: string; requestedBy?: string } | null; kind: string },
  researcher?: string | null, names: { model?: (madeBy: NonNullable<StudyResource['madeBy']>) => string; researcher?: (rid: string) => string | undefined } = {}): string {
  if (entry.kind === 'research') return researcher ? `by ${researcher}` : '';
  const made = entry.madeBy;
  const forWho = made?.for ? names.researcher?.(made.for) ?? made.for : '';
  const who = [forWho, made && names.model ? names.model(made as NonNullable<StudyResource['madeBy']>) : ''].find((name) => !!name && !rawKey(name)) ?? '';
  return who ? `by ${who}` : '';
}

/** A Result row's two lines (workspace-feel plan: "a title line, then one quiet line", never a dot-joined string). The
 * server titles a result "<what> · <made by> · HH:MM" (resources.py), which ellipsized before the part that tells rows
 * apart. Such a title splits: the title line is its first part ("Bright regions"); the quiet line is its kind, its time
 * (or its day when not today), where it is, and who made it (once: "Bright objects (Basic local method)", nothing the
 * title line already says). A title without the server's time (a note's) stays whole. The whole title stays the row's
 * tooltip and accessible name. */
export function resultRowWords(title: string, facts: { kind: string; maker?: string; day?: string; where?: string }): { title: string; sub: string } {
  const named = (list: string[]) => list.length > 1 ? `${list[0]} (${list.slice(1).join(' · ')})` : list[0] ?? '';
  const parts = title.split(' · ').map((part) => part.trim()).filter(Boolean);
  const timed = parts.length > 1 && /^\d{1,2}:\d{2}(\s?[ap]m)?$/i.test(parts[parts.length - 1]);
  const time = timed ? parts.pop()! : '';
  const head = timed ? parts.shift()! : title.trim();
  const rest = timed ? parts : [];
  // The maker by its display name ("by Bright objects · Basic local method"): said once, beside what the title says.
  const maker = (facts.maker ?? '').trim(), makerParts = maker.replace(/^by /, '').split(' · ').filter(Boolean);
  const mentioned = !!makerParts.length && (makerParts[0] === head || rest.includes(makerParts[0]));
  const made = rest.length ? named(rest) : named(makerParts[0] === head ? makerParts.slice(1) : makerParts);
  const extra = rest.length && makerParts.length && !mentioned ? maker : '';
  // What tells two rows apart comes first (a 280 px column ellipsizes the rest): when, and where; then who made it.
  return { title: head, sub: [facts.kind, facts.day || time, facts.where, made, extra].filter(Boolean).join(' · ') };
}

/** Where a card's actions go (2026-10-23 audit): the card itself opens (its picture and title are Open), so in view
 * are the Tabs it is on, the kind's own next look, and Add to Tab…; the rest under How it was made. Every action the
 * card had before is kept. */
export function cardActions(entry: Entry, facts: { placed: boolean; source: boolean }): { shown: CardAction[]; details: CardAction[] } {
  const all: CardAction[] = [];
  if (facts.placed) all.push('reveal');
  if (entry.kind === 'population') all.push('masks');
  if (['population', 'tracks'].includes(entry.kind) || artifactPresentation(entry) === 'table') all.push('measurements');
  all.push('open-in', 'provenance');
  if (entry.kind === 'population') all.push('capture');
  if (facts.source) all.push('use-source');
  const next = entry.kind === 'population' ? 'masks' : all.includes('measurements') ? 'measurements' : null;
  const shown = all.filter((action) => action === 'reveal' || action === next || action === 'open-in');
  return { shown, details: all.filter((action) => !shown.includes(action)) };
}

/** A Data row's ⋯ (workspace-feel step 4): every action the card had, once each — the ones the card showed first (the
 * Tabs it is on, its kind's next look, Add to Tab…), then the rest, How it was made (provenance) last. */
export function menuActions(entry: Entry, facts: { placed: boolean; source: boolean }): CardAction[] {
  const { shown, details } = cardActions(entry, facts);
  const all = [...shown, ...details];
  return [...all.filter((action) => action !== 'provenance'), ...all.filter((action) => action === 'provenance')];
}

// 0.3.0's "Researcher N results" Tabs (ticket 66 D5; the mock's legacy frame). A layout made by an earlier version can
// hold a Tab of kind `output` with `provenance.researcher` (workspace_views.py no longer makes them): instead of the old
// report window it now shows a pointer to the researcher, whether or not the researcher's own Tab is in the row:
// "Researcher 3’s results now open in its own Tab", its result's title and what of it is in Data (from Data's entries,
// said only as far as they exist), Open Researcher 3 and Close this Tab. The Tab's saved windows are kept as they were
// (never restored, never dropped), so nothing is lost; an old "Go to results" notice opens the researcher.
import { tabMark } from './tab-marks';
import { litCell, type LitCellState } from './lit-cell';
import { readerTitle, workOf, type ReaderEntry } from './data-reader';
import './legacy-output-tab.css';

/** The researcher a Tab of 0.3.0 points to, or null for any other Tab. */
export function legacyResearcher(view: { kind?: string; provenance?: unknown } | null | undefined): string | null {
  if (!view || view.kind !== 'output') return null;
  const rid = (view.provenance as { researcher?: unknown } | undefined)?.researcher;
  return typeof rid === 'string' && rid ? rid : null;
}

const WORD: Record<string, [string, string]> = {
  lesson: ['its lesson', 'lessons'], video: ['its video', 'videos'], figure: ['its picture', 'pictures'],
  'finding-table': ['its table', 'tables'], 'finding-chart': ['its chart', 'charts'],
};
/** What of a researcher's work is in Data, in its own words, from Data's entries whose maker it is: "Its presentation,
 * its table and its outlines are also in Data." Nothing it has not made is named; nothing at all says nothing. */
export function inDataWords(entries: ReaderEntry[], rid: string): string {
  const { items, sources } = workOf(entries, rid);
  const parts: { words: string; many: boolean }[] = [];
  const report = items.find((item) => item.kind === 'research');
  if (report) {
    const forms = report.forms ?? [];
    parts.push(forms.some((form) => form.label.startsWith('Presentation')) ? { words: 'its presentation', many: false }
      : forms.length ? { words: 'its report', many: false } : { words: 'its notes', many: true });
  }
  const counts = new Map<string, number>();
  for (const item of items) if (WORD[item.kind]) counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1);
  for (const [kind, n] of counts) parts.push(n === 1 ? { words: WORD[kind][0], many: false } : { words: `its ${n} ${WORD[kind][1]}`, many: true });
  if (items.some((item) => item.kind === 'population')) parts.push({ words: 'its outlines', many: true });
  if (sources.length) parts.push(sources.length === 1 ? { words: 'the source it cited', many: false } : { words: `the ${sources.length} sources it cited`, many: true });
  if (!parts.length) return '';
  const words = parts.map((part) => part.words);
  const list = words.length === 1 ? words[0] : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
  return `${list.charAt(0).toUpperCase()}${list.slice(1)} ${parts.length === 1 && !parts[0].many ? 'is' : 'are'} also in Data.`;
}

export interface LegacyFacts {
  /** The researcher this Project lists under that id ("Researcher 3"), or null when it is not here. */
  name: string | null;
  /** Its result's title (its report's Data entry), else what it was asked, else nothing. */
  title: string;
  /** Data's entries, for what of its work is there. */
  entries: ReaderEntry[];
  rid: string;
}
/** The pointer's words: its title, the line under it, and whether Open <researcher> can be offered. */
export function legacyWords(facts: LegacyFacts): { title: string; line: string; open: string | null } {
  const inData = inDataWords(facts.entries, facts.rid);
  const title = facts.title.trim().replace(/[.\s]+$/, '');
  if (!facts.name) {
    return { title: 'An earlier version made this Tab', line: [title ? `${title}.` : '', inData || 'Its researcher is not in this Project now.'].filter(Boolean).join(' '), open: null };
  }
  return { title: `${facts.name}’s results now open in its own Tab`, line: [title ? `${title}.` : '', inData].filter(Boolean).join(' '), open: `Open ${facts.name}` };
}
/** Its result's title: its report's entry in Data (titled by its findings), else what it was asked. */
export function legacyTitle(entries: ReaderEntry[], rid: string, asked = ''): string {
  const report = entries.find((entry) => entry.kind === 'research' && entry.id === rid);
  return report ? readerTitle(report.title, report.kind) : asked;
}

/** The pointer card, centred on the Stage (the mock's emptystage .legacy): the researcher's lit cell, the words, Open
 * <researcher> (primary) and Close this Tab. */
export function legacyCard(words: ReturnType<typeof legacyWords>, cell: LitCellState | null, actions: { open: () => void; close: () => void }): HTMLElement {
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
    const node = document.createElement(tag); node.className = className; if (text) node.textContent = text; return node;
  };
  const inner = el('div', 'legacy-tab-inner');
  const mark = el('span', 'legacy-tab-ic');
  if (cell) mark.append(litCell(cell, { size: 18, label: words.open ? words.open.replace(/^Open /, '') : 'Researcher' }));
  else mark.append(tabMark('output', 'legacy-tab-mark'));
  const title = el('p', 'legacy-tab-title', words.title);
  inner.append(mark, title);
  if (words.line) inner.append(el('p', 'legacy-tab-line', words.line));
  const row = el('div', 'legacy-tab-actions');
  if (words.open) {
    const open = el('button', 'legacy-tab-open mm-primary', words.open); open.type = 'button'; open.addEventListener('click', actions.open);
    open.title = 'Its own Tab: its conversation, and on the right what it made';
    row.append(open);
  }
  const close = el('button', 'legacy-tab-close', 'Close this Tab'); close.type = 'button'; close.addEventListener('click', actions.close);
  close.title = 'Closes this Tab; what it made stays in Data and in Researchers';
  row.append(close);
  inner.append(row);
  const card = el('section', 'legacy-tab'); card.setAttribute('aria-label', words.title);
  card.append(inner);
  return card;
}

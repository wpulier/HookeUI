// Activity, drawn (activity.ts decides what it says): one entry per request, a line per step that opens to plain
// details. The chat's Activity (chatlog.ts), which is the side bar's Activity, draws its entries with these.
import { briefModels, clock, duration, type ActivityEntry, type ActivityLine } from './activity';
import type { LitCellState } from './lit-cell';
import type { ResearchSession } from './protocol';
import { clockWords } from './sessions';

/** Makes an entry's lit cell. The workspace sets it (workspace-study.ts, with lit-cell.ts), so only its Activity draws
 * cells; elsewhere an entry has none. */
let makeCell: ((state: LitCellState, label: string) => Element) | null = null;
export function drawActivityCells(make: ((state: LitCellState, label: string) => Element) | null): void { makeCell = make; }

export interface ActivityRenderHooks {
  research(rid: string): ResearchSession | undefined; // to offer "Show it" only when there is something
  show(rid: string): void; // open a researcher's work in the result viewer
  /** The lines whose details are open, as `${entry.key}|${line.key}` (each view keeps its own). */
  opened: Set<string>;
  /** A line's words clicked while its details are closed (in the tray's strip it opens the full view at itself). */
  openFull?(): void;
}

/** One entry: its heading (a request's words, typed or spoken, and when; else what kind of thing it is, and when), then
 * its lines, then its lit cell (the workspace's Activity draws the cell first, the lines as its title and the heading as
 * its quiet line: workspace-feel.css). The heading is returned too (where an answer's "What powers it" ⓘ goes). */
export function renderActivityEntry(entry: ActivityEntry, hooks: ActivityRenderHooks, idPrefix = 'activity-'): { item: HTMLLIElement; heading: HTMLElement } {
  const item = document.createElement('li');
  item.className = 'activity-entry';
  item.id = `${idPrefix}${entry.key.replace(/[^A-Za-z0-9_-]/g, '-')}`;
  item.dataset.kind = entry.kind;
  item.dataset.role = entry.kind === 'request' ? 'request' : 'activity';
  if (entry.turnId !== undefined) item.dataset.turn = String(entry.turnId);
  item.dataset.eventTime = String(entry.at ?? 0);
  const state = item.dataset.state = entry.lines.some(line => line.state === 'failed') ? 'failed'
    : entry.lines.some(line => line.state === 'running') ? 'running' : 'done';
  const heading = document.createElement('div');
  heading.className = entry.kind === 'request' ? 'activity-heading activity-request' : 'activity-heading';
  // When, to the minute ("6:28 am"); each line keeps its exact time, shown with its details.
  const when = entry.at === null ? '' : clockWords(entry.at);
  if (entry.kind === 'request') {
    const words = document.createElement('span'); words.className = 'activity-words'; words.textContent = entry.heading;
    const meta = document.createElement('span'); meta.className = 'activity-channel';
    meta.textContent = [entry.channel ?? '', when].filter(Boolean).join(' · ');
    heading.append(words, ' ', meta);
  } else heading.textContent = [entry.heading, when].filter(Boolean).join(' · ');
  const rid = entry.research;
  const shown = rid ? hooks.research(rid) : undefined;
  if (rid && (!shown || shown.outputs.length || shown.result)) { // only when there is something to show
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'open-research'; open.textContent = 'Show it';
    open.addEventListener('click', () => hooks.show(rid));
    heading.append(' ', open);
  }
  item.append(heading);
  const list = document.createElement('ul');
  list.className = 'actions';
  for (const line of entry.lines) list.append(renderActivityLine(entry, line, hooks));
  item.append(list);
  // Its lit cell, the one status sign of all work: working while a step runs, a red outline when one did not work, a
  // grey outline for a notice. Green means one thing, an outcome: a researcher with work to show, a model that finished
  // loading, a step of a tool. A finished command or click ("You put … on stage") is done, said on a quiet cell (the
  // workspace's Activity draws it first: workspace-feel.css).
  if (makeCell) {
    const made = (rid ? !!shown && (shown.outputs.length > 0 || !!shown.result) : false) || entry.kind === 'model' || entry.lines.some((line) => !!line.tool);
    const [cellState, cellWords]: [LitCellState, string] = state === 'failed' ? ['attention', 'Did not work'] : state === 'running' ? ['working', 'Working']
      : entry.kind === 'notice' || entry.kind === 'system' ? ['idle', 'Notice'] : made ? ['ready', 'Done'] : ['idle', 'Done'];
    const cell = document.createElement('span'); cell.className = 'activity-cell'; cell.dataset.state = cellState;
    cell.append(makeCell(cellState, cellWords));
    item.append(cell);
  }
  return { item, heading };
}

/** A line: a dot (its state), what happened, its models briefly and how long, its time, and a chevron that opens
 * its plain details (the ids behind a further "Technical"). */
export function renderActivityLine(entry: ActivityEntry, line: ActivityLine, hooks: ActivityRenderHooks): HTMLLIElement {
  const li = document.createElement('li');
  const id = `${entry.key}|${line.key}`;
  li.dataset.state = line.state;
  if (line.tool) li.dataset.tool = line.tool;
  const text = document.createElement('span'); text.className = 'activity-text'; text.textContent = line.text;
  if (line.count && line.count > 1) {
    const count = document.createElement('span'); count.className = 'activity-count';
    count.textContent = ` ×${line.count}`; count.title = `${line.count} recorded steps with the same outcome`; text.append(count);
  }
  li.append(text);
  const said = line.said ? [] : [briefModels(line.models), line.ms ? duration(line.ms) : ''].filter(Boolean);
  if (said.length) {
    const meta = document.createElement('span'); meta.className = 'activity-meta'; meta.textContent = said.join(' · ');
    li.append(meta);
  }
  const time = document.createElement('time'); time.className = 'activity-time';
  if (line.at !== null) {
    const date = new Date(line.at * 1000); time.dateTime = date.toISOString(); time.title = time.dateTime;
    time.textContent = clock(line.at);
  } else { time.textContent = ''; time.title = 'This historical event has no recorded time.'; }
  li.append(time);
  if (line.details.length || line.technical.length) {
    const details = document.createElement('div'); details.className = 'activity-details';
    details.hidden = !hooks.opened.has(id);
    for (const words of line.details) {
      const p = document.createElement('p'); p.textContent = words; details.append(p);
    }
    if (line.technical.length) {
      const technical = document.createElement('details'); technical.className = 'activity-technical';
      const summary = document.createElement('summary'); summary.textContent = 'Technical';
      const body = document.createElement('p'); body.textContent = line.technical.join('\n');
      technical.append(summary, body); details.append(technical);
    }
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'activity-open';
    open.setAttribute('aria-label', `Details: ${line.text}`); open.title = 'Details';
    open.setAttribute('aria-expanded', String(!details.hidden));
    const toggle = (show = details.hidden) => {
      details.hidden = !show;
      open.setAttribute('aria-expanded', String(show));
      if (show) { hooks.opened.add(id); li.dataset.open = ''; } else { hooks.opened.delete(id); delete li.dataset.open; }
    };
    if (!details.hidden) li.dataset.open = '';
    open.addEventListener('click', (event?: Event) => { event?.stopPropagation?.(); toggle(); });
    // In the strip a line opens the full view at itself; in the full view it opens its details.
    text.addEventListener('click', () => { if (details.hidden) hooks.openFull?.(); toggle(); });
    li.append(open, details);
  }
  return li;
}

// The researcher Tab's words and lists (ticket 66 D3; before it the workspace-feel plan, "The researcher Tab";
// design/workspace-feel/researcher-spec.md). Pure: what its state says (the reader's quiet line), what the composer
// invites, which files it made (what the composer's + asks about), what it used (How it was made's Worked from), its
// steps as compact rows, the page's sections (Sections) and which Data entry its page shows, all from the researcher's
// own record (protocol.ts ResearchSession) and its steps (steps.jsonl). research.ts draws them through
// research-workspace.ts. Nothing here invents a fact: a count, a name or a link is shown only when the record has it.
import type { LitCellState } from './lit-cell';
import type { ResearchOutput, ResearchSession } from './protocol';
import { clean } from './presentation';
import { isWorking } from './research-state';
import { stopRequested } from './research-stop';
import { asking, isReady, researcherCell, researcherWords } from './shelf';
import { isLesson, isReportPresentation } from './research-summary';
import { clock } from './working-strip';

type Result = NonNullable<ResearchSession['result']>;

/** Its state, as the reader's header says it beside its page (once the status pill): the lit cell, the researcher's
 * state or the server's own progress words, the elapsed clock while it works (`since`, epoch seconds), and the version
 * once finished. Never a percent. */
export interface ResearchPillView { cell: LitCellState; words: string; since: number | null; version: string; title: string }

export function researchPill(s: ResearchSession): ResearchPillView {
  const working = isWorking(s), question = asking(s), stopping = stopRequested(s);
  const cell = researcherCell(s);
  const step = clean(s.step ?? '').trim();
  const words = question ? 'Needs you'
    : stopping ? 'Stop requested'
    : working && s.status === 'queued' ? 'Waiting for a free slot'
    : working && s.status === 'revising' ? 'Making a new version'
    : working ? (step ? step.charAt(0).toUpperCase() + step.slice(1) : 'Working')
    : researcherWords(s);
  const counting = working && !question && s.status !== 'queued';
  const findings = s.result?.findingRevision?.version;
  const version = !working && isReady(s) && findings ? `v${findings}` : '';
  const title = stopping ? 'Asked to stop; it is Stopped once the server lists it so.'
    : question ? 'Show the question' : 'Versions and how it was made';
  return { cell, words, since: counting ? (s.started ?? s.created) : null, version, title };
}

/** The Stage bar's state words beside the researcher's title: "Working 1:01:13" (the clock is the caller's), "Waiting
 * for a free slot", "Waiting for you", "Stop requested", "Stopped", "Interrupted", "Took 0:28". `counting` says the
 * clock follows the words. */
export function researchBarWords(s: ResearchSession): { words: string; since: number | null; took: number | null } {
  const working = isWorking(s), question = asking(s), stopping = stopRequested(s);
  if (stopping) return { words: 'Stop requested', since: null, took: null };
  if (question) return { words: 'Waiting for you', since: null, took: null };
  if (working && s.status === 'queued') return { words: 'Waiting for a free slot', since: null, took: null };
  if (working) return { words: 'Working', since: s.started ?? s.created, took: null };
  if (s.status === 'interrupted') return { words: 'Interrupted', since: null, took: null };
  if (s.status === 'cancelled') return { words: 'Stopped', since: null, took: null };
  if (['failed', 'timeout', 'over_budget'].includes(s.status)) return { words: 'Could not finish', since: null, took: null };
  const start = s.started ?? s.created, end = s.finished;
  return end && start && end >= start ? { words: 'Took', since: null, took: end - start } : { words: researcherWords(s), since: null, took: null };
}

/** The Stage bar on a researcher's Tab: what it was asked to do (its label or brief, never the findings' title, which
 * the artifact and its page already show), its state words with the clock or how long it took, and whether Stop shows
 * (while it can be stopped; once pressed it goes, and the words say Stop requested until the server lists it stopped). */
export function researchStageBar(s: ResearchSession): { title: string; words: string; since: number | null; tip: string; stop: boolean } {
  const bar = researchBarWords(s);
  return { title: (s.label || s.brief || s.result?.title || s.name).trim(),
    words: bar.took !== null ? `${bar.words} ${clock(bar.took * 1000)}` : bar.words, since: bar.since,
    tip: bar.words === 'Stop requested' ? 'Asked to stop; it is Stopped once the server lists it so.' : '',
    stop: isWorking(s) && !stopRequested(s) };
}

/** A question it asked, in the server's own words: in the present while it waits for the person (with Keep going / Not
 * now beside it), in the past once answered (with the person's answer as their words, unless a message of theirs in
 * the conversation already says it: an answer given through the Assistant or by voice makes none), or once its run
 * ended without an answer. */
type Question = NonNullable<ResearchSession['requests']>[number];
export function researchQuestion(q: Question, s: ResearchSession, unresolved: boolean): { words: string; answer: string | null; at: number; waiting: boolean } {
  const what = clean(q.what), why = clean(q.why);
  if (q.status === 'asked' && isWorking(s)) {
    const reason = sentence(why);
    return { words: q.keepGoing ? `Keep going? ${reason}${/[.!?]$/.test(reason) ? '' : '.'}` : `It asks you for ${what}, to see ${why}.`, answer: null, at: q.at, waiting: true };
  }
  const asked = q.keepGoing ? `It asked whether to keep going (${why}).` : `It asked you for ${what} (to see ${why}).`;
  if (q.status === 'answered') {
    const answer = clean(q.answer ?? '');
    const said = !!answer && (s.interactionJobs ?? []).some((job) => clean(job.request.text) === answer && job.created >= q.at - 1);
    return { words: answer ? asked : `${asked} You answered it.`, answer: answer && !said ? answer : null, at: q.answeredAt ?? q.at + 0.001, waiting: false };
  }
  return { words: `${asked} ${unresolved ? 'It finished without an answer; its finding says what stayed unresolved.' : 'It finished without an answer.'}`, answer: null, at: q.at, waiting: false };
}

/** Versions, as the pill's ▾ lists them: each real version (findings with their time, each output by its kind), the one
 * shown ticked. With one version, only it; with more, "Latest" first, which follows new versions until one is chosen. */
export function researchVersions(s: ResearchSession, selected: string | undefined): { label: string; value: string; checked: boolean }[] {
  const when = (at: number) => new Date(at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  const real = [
    ...(s.findingRevisions ?? []).map((revision) => ({ label: `v${revision.version} · ${when(revision.at)}`, value: `findings:${revision.version}` })),
    ...s.outputs.filter((output) => output.id).map((output) => ({ label: `${OUTPUT_WORDS[outputKind(output)]} v${output.version ?? 1}${output.current === false ? ' · earlier' : ''}`,
      value: `output:${output.id}` })),
  ];
  if (real.length <= 1) return real.map((version) => ({ label: version.label, value: '', checked: !selected || selected === version.value }));
  return [{ label: 'Latest (follows new versions)', value: '', checked: !selected }, ...real.map((version) => ({ ...version, checked: selected === version.value }))];
}
const OUTPUT_WORDS: Record<ResearchFileKind, string> = { presentation: 'Presentation', report: 'Interactive artifact', lesson: 'Lesson', video: 'Video', picture: 'Picture', table: 'Table', figure: 'Figure', mask: 'Mask' };

/** What the composer invites, by the researcher's state (the mock's composer()): a working researcher reads a note at its
 * next step; one that needs you takes the answer; finished work is asked about. */
export function researchPlaceholder(s: ResearchSession | null, options: { writable: boolean; canSend: boolean }): string {
  if (!s || !options.writable) return 'Earlier work · read only';
  if (asking(s)) return `Answer, or tell ${s.name} more.`;
  if (isWorking(s)) return `Tell ${s.name} something. It reads it at its next step.`;
  if (!options.canSend) return `${s.name} can’t take a message now.`;
  if (s.status === 'cancelled' || s.status === 'interrupted') return `Ask ${s.name} about what it kept.`;
  return `Ask ${s.name} about its findings.`;
}

/** What it made and recorded, current versions, deliverables first: what the composer's + asks about. A deliverable
 * shows on its page (`show`, research.ts's choice); a table or a chart (`tab`) and a result it had the app make (cell
 * outlines, measurements, `record`) open in the reader beside the conversation. */
export type ResearchFileKind = 'presentation' | 'report' | 'lesson' | 'video' | 'picture' | 'table' | 'figure' | 'mask';
export type ResearchFileOpen =
  | { show: string }
  | { tab: 'table' | 'chart'; index: number }
  | { record: { id: string; day: string; version: number | null; title: string } };
export interface ResearchFile { key: string; kind: ResearchFileKind; name: string; meta: string; deliverable: boolean; open: ResearchFileOpen }

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function outputKind(o: ResearchOutput): ResearchFileKind {
  if (isLesson(o)) return 'lesson';
  if (isReportPresentation(o)) return 'presentation';
  if (o.kind === 'video') return 'video';
  if (o.kind === 'image') return 'picture';
  return 'report';
}

export function researchFiles(s: ResearchSession, result: Result | null, day: string): ResearchFile[] {
  const files: ResearchFile[] = [];
  for (const o of s.outputs.filter((row) => row.current !== false)) {
    const kind = outputKind(o);
    const name = clean(o.caption) || (kind === 'presentation' ? clean(result?.title) : '') || `${s.name}’s ${kind}`;
    files.push({ key: `output:${o.id ?? o.url}`, kind, name, meta: (o.version ?? 1) > 1 ? `v${o.version}` : '', deliverable: true,
      open: { show: o.id ? `output:${o.id}` : '' } });
  }
  if (result) {
    const version = result.findingRevision?.version;
    files.push({ key: 'findings', kind: 'report', name: clean(result.title) || 'Its findings', meta: version ? `Findings v${version}` : 'Findings',
      deliverable: true, open: { show: version ? `findings:${version}` : '' } });
    (result.tables ?? []).forEach((table, index) => files.push({ key: `table:${index}`, kind: 'table', name: clean(table.title) || `Table ${index + 1}`,
      meta: plural(table.rows.length, 'row'), deliverable: false, open: { tab: 'table', index } }));
    (result.charts ?? []).forEach((chart, index) => files.push({ key: `chart:${index}`, kind: 'figure', name: clean(chart.title) || `Chart ${index + 1}`,
      meta: plural(chart.rows?.length ?? 0, 'value'), deliverable: false, open: { tab: 'chart', index } }));
  }
  // No findings saved, but notes it recorded as it went (record_finding): they are what it kept, on a page of their own.
  if (!result && s.findings.length) {
    files.push({ key: 'notes', kind: 'report', name: 'Notes so far', meta: plural(s.findingCount ?? s.findings.length, 'note'), deliverable: true, open: { show: 'notes' } });
  }
  const seen = new Set<string>();
  for (const r of s.ran ?? []) {
    if (!r.id || seen.has(r.id)) continue;
    seen.add(r.id);
    const count = typeof r.count === 'number' ? r.count : null;
    files.push({ key: `record:${r.id}`, kind: r.kind === 'population' ? 'mask' : 'figure', name: clean(r.title) || 'A result it had the app make',
      meta: count !== null && r.kind === 'population' ? plural(count, 'outline') : '', deliverable: false,
      open: { record: { id: r.id, day, version: typeof r.version === 'number' ? r.version : null, title: clean(r.title) } } });
  }
  return files;
}

/** The first version of an output's line (versions.py: a revision replaces the current version of its line), whose id
 * names the line's Data entry (`<rid>:<first id>`, research_entries.py). */
export function lineRoot(outputs: ResearchOutput[], id: string): string {
  let at = outputs.find((o) => o.id === id);
  const seen = new Set<string>();
  while (at?.replaces && !seen.has(at.replaces)) {
    seen.add(at.replaces);
    const before = outputs.find((o) => o.id === at!.replaces);
    if (!before) break;
    at = before;
  }
  return at?.id ?? id;
}
/** A lesson, a video or a picture it made: a Data entry of its own. Its presentation is a form of its report. */
const ownEntry = (o: ResearchOutput) => ['html', 'video', 'image'].includes(o.kind) && !isReportPresentation(o);
/** What its page shows, as Data names it (ticket 66 S4): its report (`<rid>`: its findings, notes or presentation) or a
 * lesson, video or picture line (`<rid>:<first output id>`), and that page's kind in a word. */
export function pageItem(s: ResearchSession, shown: ResearchOutput | undefined, selected: string | undefined): { id: string; kind: string } {
  if (shown?.id && ownEntry(shown)) return { id: `${s.rid}:${lineRoot(s.outputs, shown.id)}`, kind: OUTPUT_WORDS[outputKind(shown)] === 'Interactive artifact' ? 'Page' : OUTPUT_WORDS[outputKind(shown)] };
  if (shown && isReportPresentation(shown)) return { id: s.rid, kind: 'Presentation' };
  return { id: s.rid, kind: selected === 'notes' || !s.result && s.findings.length && !shown ? 'Notes' : 'Report' };
}
/** The page's choice (research.ts's `output:<id>`, `findings:<n>`, or '' for its default) that shows a Data entry it
 * made: its report (its default when that is the report, else its newest presentation or findings), or the current
 * version of a lesson, video or picture line. Null for an entry its page does not draw. */
export function itemValue(s: ResearchSession, id: string, defaultOutput: ResearchOutput | undefined): string | null {
  if (id === s.rid) {
    if (!defaultOutput || isReportPresentation(defaultOutput)) return '';
    const presentation = s.outputs.findLast((o) => o.current !== false && isReportPresentation(o) && !!o.id);
    const version = s.result?.findingRevision?.version;
    return presentation ? `output:${presentation.id}` : version ? `findings:${version}` : s.findings.length ? 'notes' : '';
  }
  const part = id.startsWith(`${s.rid}:`) || id.startsWith(`${s.rid}#`) ? id.slice(s.rid.length + 1) : '';
  if (!part) return null;
  const output = s.outputs.find((o) => o.current !== false && !!o.id && lineRoot(s.outputs, o.id) === part)
    ?? s.outputs.find((o) => o.id === part);
  return output?.id ? `output:${output.id}` : null;
}

/** Used: what it worked from. The live view when it was asked (opens Main), the Project's records it read (each opens
 * where it lives), and the tools it had the app run (each opens its run page). */
export type ResearchUsedOpen = { main: true } | { record: { id: string; day: string; version: number | null; title: string } } | { tool: string };
export interface ResearchUsed { key: string; kind: 'specimen' | 'record' | 'tool'; name: string; thumb?: string; open: ResearchUsedOpen }

export function researchUsed(s: ResearchSession, day: string, toolName: (key: string) => string | null = () => null): ResearchUsed[] {
  const used: ResearchUsed[] = [];
  used.push({ key: 'specimen', kind: 'specimen', name: clean(s.specimen) || 'The live view when it was asked', thumb: s.snapshot || undefined, open: { main: true } });
  const read = new Set<string>();
  for (const w of s.working ?? []) {
    if (!w.id || read.has(w.id) || (s.ran ?? []).some((r) => r.id === w.id)) continue;
    // The saved picture of the live view it was asked on is the first row already (one name, one row).
    if (w.kind === 'snapshot' && clean(w.title) && clean(w.title) === clean(s.specimen)) continue;
    read.add(w.id);
    used.push({ key: `record:${w.id}`, kind: 'record', name: clean(w.title) || 'A record of this Project',
      open: { record: { id: w.id, day, version: typeof w.version === 'number' ? w.version : null, title: clean(w.title) } } });
  }
  const tools = new Set<string>();
  for (const r of s.ran ?? []) {
    const key = r.capability;
    if (typeof key !== 'string' || !key || tools.has(key)) continue;
    tools.add(key);
    const name = toolName(key);
    used.push({ key: `tool:${key}`, kind: 'tool', name: name ? name.split(' · ')[0] : key, open: { tool: key } });
  }
  return used;
}

/** One step of its work as a compact row: a tick when done, the lit cell while it is the step at work, the still cell
 * once Stop is asked, the partial cell for an early write-up, a speech bubble for a note it read, a red outline for a
 * step that went wrong. The step's own words, split at their first colon into a title and a quiet second line. A run
 * is one row: "Ran Bright objects", what the app said it made as the second line, and chips for what it made (the
 * worker's "Asking the app to …" and the app's "The app …" reply, both kept whole in the row's `detail`). */
export type ResearchStepMark = 'done' | 'live' | 'still' | 'partial' | 'note' | 'attention' | 'stopped';
export interface ResearchStepRow { key: string; at: number; title: string; sub: string; mark: ResearchStepMark; kind: string; detail?: string; made: { id: string; title: string; kind: string; version: number | null }[] }
interface RawStep { at: number; kind: string; text: string }

const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export function splitStep(text: string): { title: string; sub: string } {
  const words = clean(text);
  const at = words.indexOf(': ');
  if (at > 0 && at <= 64 && words.length > at + 2) return { title: words.slice(0, at), sub: sentence(words.slice(at + 2)) };
  return { title: words, sub: '' };
}

const ASKING = 'Asking the app to ';
export function researchSteps(steps: RawStep[], s: ResearchSession, toolName: (key: string) => string | null = () => null): ResearchStepRow[] {
  const working = isWorking(s), stopping = stopRequested(s);
  // A question it asked is shown once, as the question itself with its answer (research.ts), never also as a step.
  const questions = (s.requests ?? []).length > 0;
  const kept = steps.filter((step) => !(questions && step.kind === 'request'));
  // Red is for what ended the run: the last warning of a researcher that failed. Every other warning (a retry, a read it
  // could not keep, a wait for another researcher's operation) is the dim stopped mark, its words unchanged.
  const ending = s.status === 'failed' ? kept.findLastIndex((step) => step.kind === 'warning') : -1;
  const rows: ResearchStepRow[] = kept.map((step, index) => {
    const { title, sub } = splitStep(step.text);
    const mark: ResearchStepMark = step.kind === 'note' ? 'note' : step.kind === 'stall' ? 'partial'
      : step.kind === 'warning' ? (index === ending ? 'attention' : 'stopped') : 'done';
    return { key: `step:${index}:${step.at}`, at: step.at, title: step.kind === 'note' ? 'Got a note' : title,
      sub: step.kind === 'note' ? clean(step.text).replace(/^Got a note:?\s*/i, '') : sub, mark, kind: step.kind, made: [] };
  });
  // What each run made goes on the app's own reply step (researcher.py writes "The app …" right after the run returns and
  // hands.py records the run before replying), the first such step at or after the record; one with no such step is a
  // row of its own. The worker's "Asking the app to …" just before it joins that row: one run, one row, named by its tool.
  const merged = new Map<ResearchStepRow, string>(), joined = new Set<ResearchStepRow>();
  for (const r of s.ran ?? []) {
    if (!r.id) continue;
    const made = { id: r.id, title: clean(r.title) || 'A result', kind: r.kind ?? '', version: typeof r.version === 'number' ? r.version : null };
    const owner = rows.find((row) => row.at >= r.at - 0.5 && /^The app /.test(merged.get(row) ?? row.title) && row.kind !== 'warning');
    if (!owner) {
      // Named by the model that ran when the app recorded it (hands.py, S12), with how many it made.
      const ranWith = typeof r.capability === 'string' && r.capability ? toolName(r.capability) : null;
      const count = ranWith && typeof r.count === 'number' && r.kind === 'population' ? `${r.count} outline${r.count === 1 ? '' : 's'}` : '';
      rows.push({ key: `ran:${r.id}`, at: r.at, title: ranWith ? `Ran ${ranWith.split(' · ')[0]}` : `Had the app make ${made.title}`, sub: count, mark: 'done', kind: 'ran', made: [made] });
      continue;
    }
    owner.made.push(made);
    if (merged.has(owner)) continue;
    merged.set(owner, owner.title);
    const before = rows[rows.indexOf(owner) - 1];
    const request = before && before.title.startsWith(ASKING) && before.mark === 'done' ? before : null;
    const tool = (typeof r.capability === 'string' && r.capability ? toolName(r.capability) : null)
      ?? (request ? /^run (.+?) on (?:the live view|a retained result)$/.exec(request.title.slice(ASKING.length))?.[1] ?? null : null);
    if (!tool && !request) continue; // nothing names the run: the app's own words stay the row's title
    owner.detail = [request ? `${[request.title, request.sub].filter(Boolean).join(': ')}.` : '', `${[owner.title, owner.sub].filter(Boolean).join(': ')}.`].filter(Boolean).join(' ');
    owner.sub = sentence(owner.title.replace(/^The app /, ''));
    owner.title = tool ? `Ran ${tool.split(' · ')[0]}` : `Had the app ${request!.title.slice(ASKING.length)}`;
    if (request) joined.add(request);
  }
  for (const row of joined) rows.splice(rows.indexOf(row), 1);
  rows.sort((a, b) => a.at - b.at);
  // At work, its latest step is the live one; waiting for the person, its question says so instead (no live cell).
  if (working && s.status !== 'queued' && !asking(s)) {
    const last = rows.findLast((row) => row.mark === 'done' && row.kind !== 'ran');
    const step = clean(s.step ?? '');
    if (last && (!step || splitStep(step).title === (merged.get(last) ?? last.title) || rows.at(-1) === last)) last.mark = stopping ? 'still' : 'live';
    else rows.push({ key: 'live', at: Number.MAX_SAFE_INTEGER, title: step ? splitStep(step).title : 'Starting', sub: step ? splitStep(step).sub : '',
      mark: stopping ? 'still' : 'live', kind: 'live', made: [] });
  }
  return rows;
}

/** The page's sections, as Sections lists them (the outline stays inside the page): each top-level anchored block of
 * the rendered findings or presentation (presentation.ts sets `data-anchor`), titled by its own heading. Unnumbered: a
 * report's sections are not a sequence. */
const SECTION_WORDS: Record<string, string> = {
  summary: 'In short', details: 'Details', look_for: 'Look for', limitations: 'What it can’t settle',
  next_questions: 'Worth asking next', 'evidence-checks': 'Evidence checks', notes: 'Its notes', sources: 'Sources',
};
/** Sections the page draws for reading (its numbered sources, its notes) that are not sections of the saved findings, so
 * a message cannot ask about them (the server checks every selection against the findings' own blocks). */
export const PAGE_ONLY_SECTIONS: ReadonlySet<string> = new Set(['sources', 'notes']);
export function outlineOf(root: ParentNode): { anchor: string; title: string }[] {
  const out: { anchor: string; title: string }[] = [];
  const seen = new Set<string>();
  for (const node of root.querySelectorAll<HTMLElement>('[data-anchor]')) {
    const anchor = node.dataset.anchor ?? '';
    if (!anchor || seen.has(anchor) || /:point:/.test(anchor)) continue;
    if (node.parentElement?.closest('[data-anchor]') && !node.closest('.research-presented-blocks, .viewer-summary')) continue;
    seen.add(anchor);
    const heading = ['h3', 'h2', '.viewer-subhead', 'caption'].map((selector) => node.querySelector(selector)).find(Boolean)?.textContent?.trim();
    const words = SECTION_WORDS[anchor] ?? (anchor.startsWith('report:') ? anchor.slice(7).replace(/^./, (c) => c.toUpperCase()) : '');
    out.push({ anchor, title: (anchor === 'summary' ? words : heading || words || node.getAttribute('aria-label') || anchor).slice(0, 80) });
  }
  return out;
}

/** A section or a point of the findings in words, for the chip of what the next message asks about ("Their areas",
 * "Region areas, region 12"); the anchor itself when the findings do not name it. */
export function anchorWords(anchor: string, result: Result | null): string {
  if (SECTION_WORDS[anchor]) return SECTION_WORDS[anchor];
  const [kind, index = '', , point] = anchor.split(':');
  const n = Number(index);
  if (kind === 'report' && index) return index.replace(/^./, (c) => c.toUpperCase());
  if (kind === 'table') return clean(result?.tables?.[n]?.title) || `Table ${n + 1}`;
  if (kind === 'chart') {
    const chart = result?.charts?.[n], name = clean(chart?.title) || `Chart ${n + 1}`;
    const row = point !== undefined ? chart?.rows?.[Number(point)] : undefined;
    return row ? `${name}, ${clean(row.label)}` : name;
  }
  if (kind === 'capture') return clean(result?.captures?.[n]?.title) || 'A capture';
  if (kind === 'step') return `Lesson step ${anchor.slice(5)}`;
  return anchor;
}

/** What it will make, said once before anything is written (never an empty box). */
export function willMake(s: ResearchSession): string {
  const deliverable = { lesson: 'a lesson', video: 'a short video', image: 'a picture', annotated_image: 'a labelled picture' }[s.deliverable ?? ''];
  if (!isWorking(s)) return 'Nothing written yet';
  return deliverable ? `It will make ${deliverable}, then write up its findings here.` : 'It will write up its findings here.';
}

/** The researcher's engine by its one name (the catalog's "GPT-6.1 Sol"), else as the record has it. */
export function engineWords(engine: string, name: (id: string) => string | null = () => null): string {
  return name(engine) ?? engine ?? '';
}

/** Per researcher, kept in this browser (localStorage, every read and write tried): the divider's widths, wide (1240 px
 * of page and over) and narrow. A convenience only; nothing here is research state. Files, Used and Outline went with
 * their column (ticket 66 D3): what an older studio kept of them is ignored. */
export interface ResearchLayout { conv?: number; conv2?: number }
const LAYOUT_KEY = (rid: string) => `hooke-research-layout:${rid}`;
type LayoutStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
/** The page's own localStorage, or null where the browser blocks site storage (reading `localStorage` itself throws). */
function layoutStore(storage: Partial<LayoutStore> | null | undefined): Partial<LayoutStore> | null {
  try { return storage === undefined ? globalThis.localStorage ?? null : storage; } catch { return null; }
}
export function readLayout(rid: string, storage?: Pick<Storage, 'getItem'> | null): ResearchLayout {
  try {
    const value: unknown = JSON.parse(layoutStore(storage)?.getItem?.(LAYOUT_KEY(rid)) ?? 'null');
    if (!value || typeof value !== 'object') return {};
    const v = value as Record<string, unknown>, out: ResearchLayout = {};
    if (typeof v.conv === 'number' && v.conv >= 360 && v.conv <= 1200) out.conv = Math.round(v.conv);
    if (typeof v.conv2 === 'number' && v.conv2 >= 320 && v.conv2 <= 1200) out.conv2 = Math.round(v.conv2);
    return out;
  } catch { return {}; }
}
export function saveLayout(rid: string, patch: Partial<Record<keyof ResearchLayout, unknown>>, storage?: LayoutStore | null): ResearchLayout {
  const store = layoutStore(storage);
  const next = { ...readLayout(rid, store as Pick<Storage, 'getItem'> | null), ...patch } as Record<string, unknown>;
  for (const key of Object.keys(next)) if (key !== 'conv' && key !== 'conv2') delete next[key];
  for (const key of Object.keys(next)) if (next[key] == null) delete next[key];
  try {
    if (Object.keys(next).length) store?.setItem?.(LAYOUT_KEY(rid), JSON.stringify(next));
    else store?.removeItem?.(LAYOUT_KEY(rid));
  } catch { /* a private window keeps the layout until the page closes */ }
  return next as ResearchLayout;
}

/** The page's width class, as its container queries draw it (workspace-feel.css): the conversation and the reader
 * side by side from 860 px (its wide widths from 1240), one column below. */
export function researchColumns(width: number): 1 | 2 {
  return width >= 860 ? 2 : 1;
}

// Activity: what happened, newest first (CONTEXT.md "Activity"; the user's decision Q2, 2026-10-11). Decided here
// without the DOM (chatlog.ts draws it): one entry per request (its words, typed or spoken, its time) with one line
// per step (what was done, the models that did it, how long, whether it worked), and one-line entries for what is not
// a request (a click, a studio action, a setting, a model load, a researcher, an app notice). Every line is drawn from
// a recorded fact: a request's record and its steps (orchestrator/activity.py), the chat's lines and late receipts.
import type { LogLine } from './chatmodel';
import type { ActivityEvent, AppNotice, StepModel, ToolRun } from './protocol';
import { dayLabel } from './sessions';

const NAMES: Record<string, string> = {
  present: 'Window', zoom: 'Magnification', highlight: 'Highlight', select: 'Selection',
  reference: 'Reference photo', reference_photo: 'Reference photo', find: 'Find', start_researcher: 'Research',
  clear: 'Clear', outline: 'Outline', count: 'Count',
};
export function actionName(tool: string): string {
  return NAMES[tool] ?? tool.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
}

/** What a step that has not finished says it is doing. */
export const WORKING: Record<string, string> = {
  dissect: 'Breaking it into parts…', zoom: 'Opening the magnifier…', outline: 'Outlining…', count: 'Counting…',
  compare: 'Comparing…', annotate: 'Labelling a picture…', start_researcher: 'Starting a researcher…',
  reference_photo: 'Finding and loading your photo…',
};

/** Compact repeated outcomes within one reply only. Every occurrence remains in Details;
 * separate requests, pending steps and different failure reasons never coalesce. */
export function activityRows(actions: ToolRun[]) {
  const rows: { action: ToolRun; count: number; actions: ToolRun[] }[] = [];
  for (const action of actions) {
    const prior = rows.at(-1);
    if (action.ok !== null && prior && prior.action.tool === action.tool
        && prior.action.ok === action.ok && prior.action.summary === action.summary
        && prior.action.requestId === action.requestId) {
      prior.count++; prior.actions.push(action);
    } else rows.push({ action, count: 1, actions: [action] });
  }
  return rows;
}

// What a receipt hands the model as untrusted text (orchestrator/untrusted.py's wrappers): the line stops before it,
// with its lead-in ("What it found, in its own words:"); the whole receipt stays in the line's details.
const MODEL_ONLY = /\s*<(?:researchers|earlier_work|session)\b[^>]*\buntrusted="true"[^>]*>/;

export function actionSummary(action: ToolRun): string {
  if (action.ok && (action.summary.toLowerCase() === 'the studio shows the requested window change'
      || action.summary.startsWith('the requesting studio confirmed '))) {
    return action.tool === 'reference' || action.tool === 'reference_photo' ? 'Reference photo displayed'
      : action.tool === 'zoom' ? 'Magnification updated' : 'Window change displayed';
  }
  const wrapped = action.summary.search(MODEL_ONLY);
  const shown = wrapped > 0
    ? action.summary.slice(0, wrapped).replace(/(?:^|[.;]\s*)[^.;]*\bin (?:its|their) own words\s*:?\s*$/i, '').replace(/[\s:,;]+$/, '')
    : action.summary;
  return (shown || action.summary).replace(/^./, c => c.toUpperCase());
}

export type LineState = 'done' | 'running' | 'failed';

/** One line of Activity: a dot (its state), what happened, its models briefly, how long, its time; it opens to
 * plain details, with the ids behind "Technical". */
export interface ActivityLine {
  key: string;
  text: string;
  state: LineState;
  tool?: string;
  models: StepModel[];
  ms?: number;
  at: number | null;
  details: string[];
  technical: string[];
  count?: number; // repeated identical outcomes of one request, shown once (activityRows)
  said?: boolean; // its words already name its models and time (a click's line): not said again beside it
}

export type EntryKind = ActivityEvent['kind'] | 'notice' | 'system';

export interface ActivityEntry {
  key: string; // "turn:12" for a request, else "event:<id>" or "line:<key>"
  kind: EntryKind;
  heading: string; // the request's words or what kind of thing it is (read aloud; shown for a request)
  words?: string;
  channel?: 'typed' | 'spoken';
  turnId?: number;
  at: number | null; // when it began
  order: number; // where it began in the log, for history with no recorded time
  lines: ActivityLine[];
  lineKeys: number[]; // the chat lines it was drawn from (an answer's ⓘ finds its entry by them)
  failed: string[]; // the steps that did not work and were not tried again successfully, by name
  models: StepModel[]; // every model of the entry, once (the answer's hover)
  answer?: { model: string; thinkMs?: number | null; totalMs?: number | null; published: boolean };
  research?: string; // the researcher a line of it is about (its work can be shown)
}

/** Something in it did not work (a step that failed and was not retried successfully, or a failed line): what
 * Activity's Problems only keeps. */
export function hasProblem(entry: Pick<ActivityEntry, 'failed' | 'lines'>): boolean {
  return entry.failed.length > 0 || entry.lines.some(line => line.state === 'failed');
}

/** "1.2 s", "84 ms", "2 min 5 s". */
export function duration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ''}`;
}

/** A line's models briefly, in order: "Gemini 3.8 Flash → EdgeTAM". */
export function briefModels(models: StepModel[]): string {
  return [...new Set(models.map(m => m.name))].join(' → ');
}

/** A model in plain words, for details: "Drawn by SAM 3.1 (cloud), after EdgeTAM (on this Mac) could not". */
export function modelWords(m: StepModel): string {
  return `${m.role.replace(/^./, c => c.toUpperCase())} ${m.said}`
    + (m.instead ? `, after ${m.instead} could not` : '') + (m.failed ? ' (it failed)' : '');
}

export function capital(text: string): string {
  return text.replace(/^./, c => c.toUpperCase());
}

function occurrence(run: ToolRun, fallback: string): string {
  return run.requestId && run.actionId ? `${run.requestId}:${run.actionId}` : fallback;
}

function stepLine(run: ToolRun, key: string, at: number | null, later: ToolRun[]): ActivityLine {
  const state: LineState = run.ok === null ? 'running' : run.ok ? 'done' : 'failed';
  // A step still running says what it waits on when the server said (a model loading: loads.py), else what it does.
  const text = run.ok === null ? (run.progress ? `${capital(run.progress)}…` : WORKING[run.tool] ?? `${actionName(run.tool)}…`)
    : actionSummary(run);
  const models = run.models ?? [];
  // The whole receipt when the line shortened it (a long one is cut at the line's end), then each model.
  const whole = capital(run.summary || actionName(run.tool));
  const details = [...(whole !== text || whole.length > 70 ? [whole] : []), ...models.map(modelWords)];
  if (run.ok !== null && run.ms) details.push(`Took ${duration(run.ms)}`);
  if (run.ok === false) {
    const again = later.find(other => other.tool === run.tool && other.ok !== null);
    if (again) details.push(again.ok ? 'Tried again: it worked' : 'Tried again: it did not work either');
    if (run.next) details.push(`What to do: ${run.next}`);
  }
  const technical = [`Tool: ${run.tool}`, ...(run.requestId ? [`Request: ${run.requestId}`] : []),
    ...(run.actionId ? [`Step: ${run.actionId}`] : [])];
  return { key, text, state, tool: run.tool, models, ms: run.ok === null ? undefined : run.ms, at, details, technical };
}

const RESEARCH_ENDED: Record<string, string> = {
  done: 'finished', partial: 'finished part of its work', timed_out: 'ran out of time', timeout: 'ran out of time',
  failed: 'stopped: it failed', cancelled: 'was stopped', interrupted: 'was interrupted',
};

/** An app notice in plain words, from its recorded facts (a researcher's lifecycle: announcer.py), never ids. */
export function noticeText(notice: AppNotice): string {
  const facts = notice.facts as Record<string, unknown>;
  const name = typeof facts.name === 'string' ? facts.name : '';
  const cut = (value: unknown, n = 140) => {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > n ? `${text.slice(0, n - 1)}…` : text;
  };
  let text = notice.summary;
  if (notice.producer.kind === 'research' && name) {
    const status = typeof facts.status === 'string' ? facts.status : '';
    const result = (facts.result ?? {}) as Record<string, unknown>;
    const request = (facts.request ?? {}) as Record<string, unknown>;
    const presentation = (facts.presentation ?? {}) as Record<string, unknown>;
    const plan = (facts.plan ?? {}) as Record<string, unknown>;
    if (notice.event === 'completed') {
      const said = result.one_liner ?? result.summary;
      text = `${name} ${RESEARCH_ENDED[status] ?? status.replaceAll('_', ' ')}` + (said && status === 'done' ? `: ${cut(said)}` : '');
    } else if (notice.event === 'request') {
      text = request.keepGoing ? `${name} asks whether to keep going: ${cut(request.why ?? request.what ?? '')}`
        : `${name} asks you: ${cut(request.what ?? 'for evidence it lacks')}`;
    } else if (notice.event === 'plan') {
      text = `${name} made a plan` + (Array.isArray(plan.searches) ? `: ${plan.searches.length} search${plan.searches.length === 1 ? '' : 'es'}` : '');
    } else if (notice.event === 'presentation') {
      text = presentation.contentVerified ? `${name}'s work opened beside the view` : `${name}'s work is in Researchers`;
    } else if (notice.event === 'presentation-finished') {
      const kind = cut(presentation.contentKind ?? 'work', 40);
      text = presentation.state === 'ready'
        ? `${name} restyled its ${kind}` + (typeof presentation.version === 'number' ? ` (version ${presentation.version})` : '')
        : `${name} could not restyle its ${kind}` + (presentation.error ? `: ${cut(presentation.error, 120)}` : '');
    }
  }
  if (notice.fromSession?.name) text += ` · ${fromSessionWords(notice.fromSession)}`;
  else if (notice.origin.day && notice.origin.day !== notice.day) text += ` · from ${notice.origin.day}`;
  return text;
}

/** Work that finished in another session, said by that session's name: "from 'Blood smear'". */
export function fromSessionWords(from: { name: string } | undefined): string {
  const name = (from?.name ?? '').trim();
  return name ? `from '${name}'` : '';
}

const HEADINGS: Record<string, string> = {
  click: 'Your click', command: 'Your action in the studio', setting: 'A setting changed', model: 'A model',
  research: 'A researcher', notice: 'App activity', system: 'Notice',
};

interface Building extends ActivityEntry {
  steps: Map<string, { run: ToolRun; at: number | null; from: number }>;
  record?: ActivityEvent;
  lineModel?: { model: string; thinkMs?: number; at: number | null };
  answerStatus?: { model?: string; calls?: number; at: number | null };
}

/** Activity as entries, newest first: requests by their turn (its record, its answer line, its late receipts, each
 * step once by its occurrence), and one entry for each other line. Pure: the same lines give the same entries. */
export function buildActivity(lines: LogLine[]): ActivityEntry[] {
  const entries = new Map<string, Building>();
  const begin = (key: string, kind: EntryKind, order: number, at: number | null): Building => {
    let entry = entries.get(key);
    if (!entry) {
      entry = { key, kind, heading: HEADINGS[kind] ?? 'Activity', at, order, lines: [], lineKeys: [], failed: [],
        models: [], steps: new Map() };
      entries.set(key, entry);
    }
    if (entry.at === null && at !== null) entry.at = at;
    return entry;
  };
  lines.forEach((line, order) => {
    const at = typeof line.at === 'number' && Number.isFinite(line.at) ? line.at : null;
    const event = line.event;
    if (event?.kind === 'request' && typeof event.ref?.turnId === 'number') {
      const entry = begin(`turn:${event.ref.turnId}`, 'request', order, event.at);
      entry.at = event.at; // a request began when it was asked
      entry.record = event;
      entry.turnId = event.ref.turnId;
      entry.lineKeys.push(line.key);
      const doneAt = event.details?.answer?.at ?? event.at;
      for (const [i, run] of (event.steps ?? []).entries()) {
        const key = occurrence(run, `record:${i}`);
        if (!entry.steps.has(key)) entry.steps.set(key, { run, at: doneAt ?? null, from: order });
      }
      return;
    }
    if (event) {
      const entry = begin(`event:${event.id}`, event.kind, order, event.at);
      entry.at = event.at;
      entry.lineKeys.push(line.key);
      const d = event.details ?? {};
      const models = d.models ?? [];
      const from = fromSessionWords(event.fromSession);
      entry.lines = [{ key: event.id, text: from ? `${event.text} · ${from}` : event.text, state: event.state, models, ms: d.ms, at: event.at,
        said: models.every(m => event.text.includes(m.name)) && (d.ms === undefined || event.text.includes(duration(d.ms))),
        details: [...models.map(modelWords), ...(typeof d.ms === 'number' ? [`Took ${duration(d.ms)}`] : []), ...(d.lines ?? [])],
        technical: Object.entries(d.ids ?? {}).map(([k, v]) => `${k}: ${v}`) }];
      return;
    }
    if (line.notice) {
      const entry = begin(`line:${line.key}`, 'notice', order, at);
      entry.lineKeys.push(line.key);
      const n = line.notice;
      entry.lines = [{ key: `notice:${line.key}`, text: noticeText(n), state: n.tone === 'error' ? 'failed' : 'done', models: [],
        at, details: n.summary && n.summary !== noticeText(n) ? [n.summary] : [],
        technical: [`From: ${n.producer.kind}${n.producer.id ? ` ${n.producer.id}` : ''}`, `Event: ${n.event}`] }];
      return;
    }
    if (line.role === 'system') {
      const entry = begin(`line:${line.key}`, 'system', order, at);
      entry.lineKeys.push(line.key);
      entry.lines = [{ key: `system:${line.key}`, text: line.text, state: 'done', models: [], at, details: [], technical: [] }];
      return;
    }
    const steps = line.actions;
    if (line.turnId !== undefined && (steps.length || line.answerStatus || (line.role === 'assistant' && !line.update))) {
      const entry = begin(`turn:${line.turnId}`, 'request', order, at);
      entry.turnId = line.turnId;
      entry.lineKeys.push(line.key);
      if (line.research) entry.research = line.research;
      if (line.answerStatus) entry.answerStatus = { model: line.model, calls: line.calls, at };
      if (line.role === 'assistant' && line.model && !line.update) entry.lineModel = { model: line.model, thinkMs: line.thinkMs, at };
      steps.forEach((run, i) => {
        const key = occurrence(run, `line:${line.key}:${i}`);
        const prior = entry.steps.get(key);
        // A later line has the newer state of the same occurrence; it keeps the place it first had. Its time is its own
        // receipt's when a late one updated it in place (ChatModel.action keeps the receipt's `at`), else its line's.
        const when = typeof run.at === 'number' ? run.at : at;
        entry.steps.set(key, { run, at: when ?? prior?.at ?? null, from: prior?.from ?? order });
      });
      return;
    }
    if (line.role === 'activity' && steps.length) {
      const entry = begin(`line:${line.key}`, 'request', order, at);
      entry.lineKeys.push(line.key);
      steps.forEach((run, i) => entry.steps.set(occurrence(run, `line:${line.key}:${i}`),
        { run, at: typeof run.at === 'number' ? run.at : at, from: order }));
    }
  });
  // A request with nothing to say yet (an answer kept from before models were recorded) stays as the anchor of its
  // "What powers it" ⓘ; the panel keeps it out of sight until its answer's ⓘ asks for it (chatlog.ts).
  const built = [...entries.values()].map(finish).filter(entry => entry.lines.length || entry.kind === 'request');
  return built.sort((a, b) => (a.at !== null && b.at !== null && a.at !== b.at ? b.at - a.at : b.order - a.order));
}

function finish(entry: Building): ActivityEntry {
  const record = entry.record;
  if (entry.kind !== 'request') {
    entry.models = entry.lines.flatMap(line => line.models);
    entry.failed = entry.lines.filter(line => line.state === 'failed').map(line => line.text);
    return strip(entry);
  }
  if (record) {
    entry.words = record.text;
    entry.channel = record.ref?.channel === 'voice' ? 'spoken' : 'typed';
  }
  entry.heading = entry.words ? `“${entry.words}”` : 'A request (its words were not recorded with it)';
  const lines: ActivityLine[] = [];
  const answer = record?.details?.answer;
  const runs = [...entry.steps.values()].map(s => s.run);
  if (record?.state === 'running' && !runs.length && !entry.lineModel) {
    lines.push({ key: 'working', text: 'Working on it…', state: 'running', models: [], at: record.at, details: [], technical: [] });
  }
  if (record?.state === 'failed') {
    lines.push({ key: 'failed', text: record.details?.lines?.[0] ?? 'The request could not finish', state: 'failed', models: [],
      at: record.at, details: record.details?.lines?.slice(1) ?? [], technical: [`Turn: ${entry.turnId}`] });
  }
  const model = answer?.model || entry.lineModel?.model;
  const think = answer?.thinkMs ?? entry.lineModel?.thinkMs;
  if (model && (answer ? answer.published : true) && !entry.answerStatus) {
    const at = answer?.at ?? entry.lineModel?.at ?? record?.at ?? null;
    lines.push({ key: 'answer', text: 'Answered', state: 'done', models: [{ role: 'answered by', name: model, said: model }],
      ms: think ?? undefined, at, details: [`Answered by ${model}`, ...(think ? [`Took ${duration(think)} to answer`] : []),
        ...(answer?.totalMs ? [`The whole request took ${duration(answer.totalMs)}`] : []),
        ...(answer?.calls ? [`${answer.calls} model call${answer.calls === 1 ? '' : 's'}`] : [])],
      technical: entry.turnId !== undefined ? [`Turn: ${entry.turnId}`] : [] });
    entry.answer = { model, thinkMs: think, totalMs: answer?.totalMs, published: true };
  }
  if ((answer && !answer.published) || entry.answerStatus) {
    const said = answer?.model || entry.answerStatus?.model || '';
    const calls = answer?.calls ?? entry.answerStatus?.calls;
    lines.push({ key: 'no-answer', text: 'No answer was given', state: 'failed', models: [], at: entry.answerStatus?.at ?? record?.at ?? null,
      details: [...(said ? [`Asked of ${said}`] : []), ...(calls ? [`${calls} model call${calls === 1 ? '' : 's'}`] : [])], technical: [] });
    entry.answer = { model: said, published: false };
  }
  const look = record?.details?.look;
  if (look && look.outlined > 0) {
    lines.push({ key: 'look', text: `Outlined ${look.outlined} thing${look.outlined === 1 ? '' : 's'} it pointed at`, state: 'done',
      models: look.models, at: answer?.at ?? record?.at ?? null,
      details: look.models.length ? look.models.map(modelWords) : ['Which models did this is not said (a blind comparison, or not recorded)'],
      technical: [] });
  }
  const ordered = [...entry.steps.entries()].sort((a, b) => a[1].from - b[1].from);
  const runsInOrder = ordered.map(([, s]) => s.run);
  for (const row of activityRows(runsInOrder)) {
    const index = runsInOrder.indexOf(row.action);
    const [key, step] = ordered[index];
    const line = stepLine(row.action, key, step.at, runsInOrder.slice(index + 1));
    if (row.count > 1) line.count = row.count;
    lines.push(line);
  }
  entry.lines = lines;
  entry.failed = [...new Set(unresolvedFailures(runsInOrder).map(run => actionName(run.tool)))];
  if (record?.state === 'failed' || entry.answerStatus) entry.failed.unshift('the request');
  const seen = new Set<string>();
  entry.models = lines.flatMap(line => line.key === 'answer' ? [] : line.models)
    .filter(m => !seen.has(`${m.role}|${m.name}`) && !!seen.add(`${m.role}|${m.name}`));
  return strip(entry);
}

function strip(entry: Building): ActivityEntry {
  const { steps: _steps, record: _record, lineModel: _lineModel, answerStatus: _status, ...out } = entry;
  return out;
}

/** Steps that did not work and were not tried again successfully in the same request: a retry that worked
 * (the same tool, later) settles the earlier failure, so a fixed request is never shown as failed; a step skipped only
 * because a step it waited on did not succeed is settled once every failure before it is (the server's
 * claims.standing: will-research, 30 Oct, a Tab switch skipped after a refused place that the brain made again). */
export function unresolvedFailures(actions: ToolRun[]): ToolRun[] {
  const out: ToolRun[] = [];
  actions.forEach((action, n) => {
    if (action.ok !== false) return;
    if (actions.slice(n + 1).some(later => later.tool === action.tool && later.ok === true)) return;
    if (DEPENDENCY_SKIPPED.test(action.summary ?? '')) {
      const earlier = actions.slice(0, n).filter(before => before.ok === false);
      if (earlier.length && !earlier.some(before => out.includes(before))) return;
    }
    out.push(action);
  });
  return out;
}

/** The brain's own words for a step it never ran because a step it waited on did not succeed (brain/actions.py). */
const DEPENDENCY_SKIPPED = /^step \d+ skipped: dependency \d+ did not succeed\b/;

/** The models of a request on one line, for the hover over its answer: "Gemini 3.8 Flash · drawn by EdgeTAM · 4.8 s". */
export function hoverLine(entry: ActivityEntry | undefined, line?: Pick<LogLine, 'model' | 'thinkMs'>): string {
  const answer = entry?.answer?.model || line?.model || '';
  const parts: string[] = answer ? [answer] : [];
  const names = new Set(parts);
  for (const m of entry?.models ?? []) {
    if (names.has(m.name)) continue;
    names.add(m.name);
    parts.push(`${m.role} ${m.name}${m.failed ? ' (it failed)' : ''}`);
  }
  const ms = entry?.answer?.thinkMs ?? line?.thinkMs;
  if (ms) parts.push(`${(ms / 1000).toFixed(1)} s`);
  return parts.join(' · ');
}

/** The entry a chat line belongs to: its request's (by turn), else the entry drawn from it. */
export function entryOf(entries: ActivityEntry[], line: Pick<LogLine, 'key' | 'turnId'>): ActivityEntry | undefined {
  return (line.turnId !== undefined ? entries.find(entry => entry.turnId === line.turnId) : undefined)
    ?? entries.find(entry => entry.lineKeys.includes(line.key));
}

/** "12:01:33" in the viewer's own clock. */
/** Activity's day separators (its entries are newest first): the first shown entry of each day carries that day's
 * words ("Today", "Yesterday", "Oct 24"); an entry with no recorded time stays in the group it is in. */
export function activityDays(entries: Pick<ActivityEntry, 'key' | 'at'>[], shown: (key: string) => boolean, now: number): Map<string, string> {
  const days = new Map<string, string>();
  let last = '';
  for (const entry of entries) {
    if (entry.at === null || !shown(entry.key)) continue;
    const label = dayLabel(entry.at, now);
    if (label !== last) { days.set(entry.key, label); last = label; }
  }
  return days;
}

export function clock(at: number | null): string {
  return at === null ? '' : new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// A model's status and kind in words, said the same on its Models card and its row in the workspace's Tools (one status
// everywhere: docs/design/2026-10-20-hooke-words.md, "Set up").
import type { CapabilitiesWire, ModelRow, ReadinessStep, ToolSetup } from './protocol';
import type { LitCellState } from './lit-cell';
import { keyOf, modelSection, runStep, sectionCell, shortName, wholeSetup, type ModelSection, type RunStep } from './run-spec';
import { sizeWords } from './size-words';

/** A model's status on its card, in one word and a colour, said the same on its Models card and its row in Tools
 * (docs/design/2026-10-23-part1-uiux-pass/README.md §B): Ready (green, and green means nothing else), Setting up
 * (yellow), Not checked and Not set up (grey: the step is on the button), Needs attention (red: a person must act),
 * Not available on this Mac (grey, with the reason). Only the words and colours are the studio's; `state` is the
 * server's face state, unchanged, and `says` its line. Not checked stays apart from Ready and from Not set up: it can
 * run, but no check has passed on this Mac yet (catalog_view.face). */
export interface FaceState { state: string; colour: string; word: string; says: string }
export function faceState(row: ModelRow, setup: CapabilitiesWire['setup']): FaceState {
  const cards = [...(setup?.shelf?.cards ?? []), ...(setup?.shelf?.behind ?? [])];
  const turning = [...cards.map(c => c.turning), ...Object.values(setup?.turning ?? {})].find(t => t && (t.model === row.id ||
    row.setups?.some(s => s.role === t.job && s.option === t.option)));
  const installing = setup?.installs?.find(i => i.model === row.id && ['queued', 'installing'].includes(i.state));
  const downloading = row.download && ['queued', 'downloading', 'checking'].includes(row.download.state) ? row.download : null;
  if (turning && turning.state !== 'failed' || installing || downloading) {
    return { state: 'working', colour: 'yellow', word: 'Setting up',
      says: turning?.state !== 'failed' && turning?.says || installing?.says ||
        (downloading ? `Downloading${downloading.total ? ` ${Math.round(100 * downloading.got / downloading.total)}%` : ''}` : 'Working') };
  }
  if (turning?.state === 'failed') return { state: 'failed', colour: 'red', word: 'Needs attention', says: turning.says };
  if (row.execution) return { state: row.execution.action === 'unsupported' ? 'unavailable' : 'unchecked', colour: 'grey',
    word: row.execution.action === 'unsupported' ? 'Not on this Mac' : 'Not set up', says: row.execution.action === 'unsupported'
      ? row.execution.why : row.execution.why || 'Compare on your picture to finish setting it up' };
  const state = row.face?.state ?? row.status;
  const colour = ['on', 'obtained'].includes(state) ? 'green' : state === 'broken' ? 'red' : 'grey';
  return { state, colour, word: ['on', 'obtained'].includes(state) ? 'Ready' : state === 'unavailable' ? 'Not on this Mac'
    : state === 'unchecked' ? 'Not checked' : state === 'broken' ? 'Needs attention' : 'Not set up',
    says: row.face?.says ?? row.word }; // Broken is its own status (readiness.py, ADR 0019), never said as Not set up (L7b)
}

/** A licence's short name, for a row or a line ("MIT", "SAM License", "Mixed component terms", "BSD-3-Clause for its
 * code"), from the catalog's own text ("<entry>: <terms>; <more terms>"): its first terms, without their parenthesis.
 * The whole text stays where a person reads it in full (Details › More about this model, Setup details). Empty when the
 * catalog states none. A non-commercial tier is said after it, since that is what limits its use. */
export function licenceName(licence: string, tier = ''): string {
  const text = licence.trim();
  if (!text) return '';
  const colon = text.indexOf(': ');
  let name = (colon > 0 && colon < 80 ? text.slice(colon + 2) : text).split(/;\s|:\s/)[0].replace(/\s*\([^)]*\)?\s*$/, '').trim() || text;
  name = name.replace(/^code (\S+)$/, '$1 for its code');
  if (name.length > 60) name = `${name.slice(0, 60).replace(/[\s,]+\S*$/, '')}…`;
  return tier === 'noncommercial' ? `${name} · non-commercial use only` : name;
}

export function kindWords(row: Pick<ModelRow, 'kind' | 'runs'>): string {
  if (row.kind === 'tool-function') return row.runs === 'cloud' ? 'Method' : 'Local method';
  if (row.kind === 'cloud-model') return 'Cloud model';
  if (row.kind === 'model') return 'Model';
  if (row.kind === 'data') return 'Data';
  return 'Tool';
}

/** "yours · on this Mac", "community: lab-models · on this Mac": whose entry it is, beside where it runs. */
export function whereWords(row: ModelRow): string {
  if (row.mine) return `yours${row.draftedByAi ? ' · drafted by AI' : ''} · ${row.where}`;
  if (row.channel === 'reviewed') return `new in the catalog · ${row.where}`;
  if (row.channel) return `community: ${row.channel} · ${row.where}`;
  return row.where;
}

// --- A Models card (workspace-feel step 8; the mock's mcard()) -------------------------------------------------------
// One card shape for every entry: its lit cell and name, what it is and where it runs, what it does in two lines, one
// facts line, one action at the bottom. Its section is run-spec.ts's `modelSection`, the same one Tools By model and a
// task's model list use, so Models' counts, its sections and Tools agree exactly (one meaning of Ready: face on or
// obtained; Not checked is its own state). Its one verb follows its state: Use it here, Download 413 MB, Add key, Accept
// licence, Turn on, Set up Fiji, Check it here, Retry. The rest of an entry (its steps, terms, facts and files) is in its
// setup sheet and its Details.

/** The card's setup button names its first step a person will notice (concept mock: the step is on the button): a key
 * to add, a licence to accept, a download; else Turn on. It opens the same sheet either way. */
export function firstStepWords(steps: { kind: string; by?: string }[]): string {
  const yours = steps.find(step => step.by !== 'app');
  if (yours?.kind === 'key') return 'Add key';
  if (yours?.kind === 'licence') return 'Accept licence';
  if (steps.some(step => step.kind === 'download')) return 'Download and turn on';
  return 'Turn on';
}

function bundled(row: ModelRow): boolean {
  return row.newKind?.proof === 'bundled with the app';
}

/** "Model, on this Mac", "Cloud model, Google", "Built-in method", "Method, on this Mac", "Model, yours · on this Mac": a
 * card's second line, what it is and where it runs. A cloud model names the service its pictures go to when its key says
 * which; otherwise "in the cloud". */
export function cardKindWords(row: ModelRow, wire: CapabilitiesWire | null): string {
  if (bundled(row)) return 'Built-in method';
  const kind = kindWords(row).replace(/^Local method$/, 'Method');
  if (row.runs === 'cloud') {
    const key = keyOf(wire, row);
    const whose = row.mine ? ' · yours' : row.channel === 'reviewed' ? ' · new in the catalog' : row.channel ? ` · community: ${row.channel}` : '';
    return `${kind}, ${key?.short || row.where || 'in the cloud'}${whose}`;
  }
  return `${kind}, ${whereWords(row)}`;
}

/** A size said in a step's own words ("Download 43 MB": the server's SI words, readiness.py `size_words`). */
function sizeIn(words: string): string {
  return /([\d.]+ [KMGT]B)\b/.exec(words)?.[1] ?? '';
}

/** The card's one facts line: whose choice it is while it needs you ("Your choice for find all like this"); a cloud
 * model's key ("Your Google key"); a download's size while it waits for it ("413 MB · free"); else where it runs and what
 * it costs ("On this Mac · free", "Built in · free", "Needs Fiji · free"). Nothing it does not know. */
export function cardFactsWords(row: ModelRow, wire: CapabilitiesWire | null, section: ModelSection, step: RunStep | null): string {
  if (section === 'needs') {
    const use = row.usedFor.find((u) => u.current && !u.fixed) ?? row.usedFor.find((u) => u.current);
    if (use) return `${use.fixed ? 'In use for' : 'Your choice for'} ${use.title.charAt(0).toLowerCase()}${use.title.slice(1)}`;
  }
  if (row.runs === 'cloud') {
    const key = keyOf(wire, row);
    return key ? `${key.present ? 'Your' : 'Needs your'} ${key.short} key` : 'Cloud';
  }
  if (step?.kind === 'download' && section !== 'ready' && section !== 'unchecked') {
    const size = step.bytes ? sizeWords(step.bytes) : row.size ? sizeWords(row.size) : sizeIn(step.label) || sizeIn(step.says);
    if (size) return `${size} · free`;
  }
  if (bundled(row)) return 'Built in · free';
  if (step?.model === 'fiji' && row.id !== 'fiji') return 'Needs Fiji · free';
  return 'On this Mac · free';
}

/** What the card offers, its one verb:
 * - `use`: Use it here (a ready model that serves a task here: its run page, actions-panel.ts `useModel`);
 * - `note`: no button, a line ("Used by the Assistant and researchers"; a step nobody can press here, "Made from your
 *   corrections");
 * - `execution`: the review a proved contribution waits for (Review comparison, Choose a comparison picture, Review
 *   results), never Use it here;
 * - `progress`: its download filling in place from the server's measured bytes (Cancel beside it);
 * - `continue`: a setup under way, Continue setup reopening its sheet;
 * - `retry`: a failed download (again, in place) or setup (its sheet, where Retry setup is);
 * - `download`: Download 413 MB, started in place;
 * - `sheet`: its setup sheet (Add key, Accept licence, Turn on, Set up Fiji, Allow…; Check it here for a Not checked
 *   model, in the workspace too: its sheet's Run the check, since trying it on a task does not record the check);
 * - `direct`: the entry's one step done at once where it has no setup plan (the older card action);
 * - `none`. */
export type CardVerb =
  | { kind: 'use' }
  | { kind: 'note'; words: string }
  | { kind: 'execution' }
  | { kind: 'progress'; words: string; model: string }
  | { kind: 'continue' }
  | { kind: 'retry'; download: string | null }
  | { kind: 'download'; label: string; model: string }
  | { kind: 'sheet'; label: string }
  | { kind: 'direct' }
  | { kind: 'none' };

export interface CardLook {
  section: ModelSection;
  cell: LitCellState; cellWord: string; fraction: number | null;
  /** What it is and where it runs; its one facts line; the face's own words (the facts line's tooltip). */
  kind: string; facts: string; says: string;
  verb: CardVerb;
  /** The setup the card's sheet opens (the one under way first), when it has one. */
  setup: ToolSetup | null;
}

export interface CardContext {
  /** The studio's workspace (Hooke): Use it here goes to a run page only there. */
  inWorkspace: boolean;
  /** How many tasks it serves in the workspace (actions-panel.ts `servedBy`); null when that is not known here. */
  served: number | null;
  /** The setup commands are here (the sheet can open). */
  shelf: boolean;
}

/** The setup a card acts on: the one under way, else the first with a plan to start, else any with a plan. */
function cardSetup(row: ModelRow, setup: CapabilitiesWire['setup']): ToolSetup | null {
  return row.setups?.find((s) => setup?.turning?.[s.role]?.option === s.option)
    ?? row.setups?.find((s) => s.plan && !s.recheck) ?? row.setups?.find((s) => s.plan) ?? null;
}

const USED_BY_AGENTS = 'Used by the Assistant and researchers';

export function cardLook(row: ModelRow, wire: CapabilitiesWire | null, context: CardContext): CardLook {
  const setupWire = wire?.setup;
  const section = modelSection(row);
  const face = faceState(row, setupWire);
  // A setup under way for it (an owned job's or this entry's own, faceState's rule), and the setup the card acts on.
  const cards = [...(setupWire?.shelf?.cards ?? []), ...(setupWire?.shelf?.behind ?? [])];
  const turning = [...cards.map((c) => c.turning), ...Object.values(setupWire?.turning ?? {})].find((t) => t && (t.model === row.id
    || row.setups?.some((s) => s.role === t.job && s.option === t.option)));
  const setup = (turning ? row.setups?.find((s) => s.role === turning.job && s.option === turning.option) : undefined) ?? cardSetup(row, setupWire);
  const raw = row.status === 'unavailable' ? null : row.next ?? (row.face?.action && row.face.action.kind !== 'check' ? row.face.action as ReadinessStep : null);
  const first = runStep(raw, row.id), reason = first.reason;
  // A download that is not the whole setup (an install follows): its card names the whole setup (run-spec wholeSetup).
  const whole = wholeSetup(first.step, row.steps, shortName(row.name), row.id);
  const step = whole.step;
  const base = sectionCell(section);
  const look = (verb: CardVerb, over: Partial<CardLook> = {}): CardLook => ({
    section, cell: base.cell, cellWord: base.word, fraction: null, kind: cardKindWords(row, wire),
    facts: whole.facts || cardFactsWords(row, wire, section, step), says: face.says, verb, setup, ...over,
  });
  const plan = !!setup?.plan && context.shelf;
  if (section === 'away') {
    // Broken is its own status (readiness.py, ADR 0019): listed where the server groups it, said as Needs attention.
    const broken = face.state === 'broken';
    return look(broken && plan ? { kind: 'sheet', label: 'Check again' } : { kind: 'none' },
      { facts: row.execution?.why || face.says || row.word, ...(broken ? { cell: 'attention' as LitCellState, cellWord: 'Needs attention' } : {}) });
  }
  if (row.execution) return look({ kind: 'execution' }, { facts: row.execution.why });
  const download = row.download;
  if (download && ['queued', 'downloading', 'checking'].includes(download.state)) {
    const words = download.state === 'queued' ? 'Waiting to download' : download.state === 'checking' ? 'Checking the files'
      : download.total > 0 ? `Downloading · ${Math.floor(100 * Math.min(download.got, download.total) / download.total)}%` : 'Downloading';
    return look({ kind: 'progress', words, model: download.model || row.id },
      { cell: 'setup', cellWord: 'Downloading', fraction: download.state === 'downloading' && download.total > 0 ? download.got / download.total : null });
  }
  if (turning && turning.state !== 'failed') {
    const waiting = turning.state === 'waiting';
    return look(setup ? { kind: 'continue' } : { kind: 'progress', words: waiting ? 'Waiting for you' : 'Setting up', model: row.id },
      { cell: waiting ? 'needs' : 'working', cellWord: waiting ? 'Needs you' : 'Setting up', facts: turning.says });
  }
  const installing = setupWire?.installs?.find((i) => i.model === row.id && ['queued', 'installing'].includes(i.state));
  if (installing) return look({ kind: 'progress', words: installing.says || 'Setting up', model: row.id }, { cell: 'working', cellWord: 'Setting up', facts: installing.says || face.says });
  if (turning?.state === 'failed' && setup) return look({ kind: 'retry', download: null }, { cell: 'attention', cellWord: 'Needs attention', facts: `Didn’t finish: ${turning.says}` });
  if (download?.state === 'failed' && section !== 'ready' && section !== 'unchecked') {
    const at = download.total > 0 && download.got > 0 ? ` at ${Math.floor(100 * download.got / download.total)}%` : '';
    return look({ kind: 'retry', download: download.model || row.id },
      { cell: 'attention', cellWord: 'Download stopped', facts: `Download stopped${at}${download.says ? ` · ${download.says}` : ''}` });
  }
  if (face.state === 'broken') {
    return look(plan ? { kind: 'sheet', label: setup!.recheck ? 'Check again' : 'Check it here' } : step ? { kind: 'direct' } : { kind: 'none' },
      { cell: 'attention', cellWord: 'Needs attention', facts: face.says });
  }
  if (section === 'ready') {
    if (context.inWorkspace) {
      if (row.kind === 'data' || context.served === 0) return look({ kind: 'note', words: USED_BY_AGENTS });
      return look({ kind: 'use' });
    }
    // Outside the workspace there is no run page to go to: the older card's setup or its own step (Turn off).
    return look(plan ? { kind: 'sheet', label: setup!.recheck ? 'Check again' : setup!.plan!.steps.every((s) => s.kind === 'check') ? 'Check it here' : firstStepWords(setup!.plan!.steps) } : { kind: 'direct' });
  }
  if (section === 'unchecked') {
    // Check it here runs Hooke's own check (its sheet's Run the check), in the workspace too: trying the model on a
    // task does not record the check, so a card that sent people to Details' Try it on left it Not checked.
    if (plan) return look({ kind: 'sheet', label: setup!.recheck ? 'Check again' : setup!.plan!.steps.every((s) => s.kind === 'check') ? 'Check it here' : firstStepWords(setup!.plan!.steps) });
    if (context.inWorkspace && context.served !== 0) return look({ kind: 'use' });
    return look({ kind: 'direct' });
  }
  // Needs you, or can be set up: its one step. The cell is its section's (blue only in Needs you, the grey outline in Can
  // be set up, as Tools By model draws the same row): a key or a licence is said by the button, never by the cell.
  if (reason && !plan) return look({ kind: 'note', words: '' }, { facts: reason });
  if (step?.does === 'download') return look({ kind: 'download', label: step.short, model: step.model });
  if (plan) return look({ kind: 'sheet', label: step?.short ?? firstStepWords(setup!.plan!.steps) });
  if (reason) return look({ kind: 'note', words: '' }, { facts: reason });
  return look(step ? { kind: 'direct' } : { kind: 'none' });
}

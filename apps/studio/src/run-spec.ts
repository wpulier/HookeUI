// One run page for every capability (workspace-feel plan, "Running a model"; the mock's runPage()): this module turns
// what the wire already carries into ONE normalized spec per task or catalog entry, and the run page (actions-panel.ts)
// renders only the spec. So any catalog folder gets its page with no app code:
// - **The task** is an Actions row (actions-panel.ts `actionRows`: a capability with its tool, fields and roles), or a
//   catalog entry that has no capability yet (`entrySpec`: it names its step, never a form it has not declared).
// - **The model choice** is the task's choosing role's options (`Role.options`, with their readiness), each with its
//   catalog entry (`ModelRow`, by `usedFor`), plus the format-2 entries whose kind implements that role but are not set
//   up yet (`ModelRow.newKind` and their `kind:<operation>` ability). Each choice says where it runs, what it does for
//   THIS task, its one step and what Run sends.
// - **Settings** are only what is declared: the task's own fields, or, when the chosen model is a format-2 entry with a
//   capability of its own (`catalog:<id>`), that entry's fields (InstanSeg's pixel size, Bright objects' smoothing), and
//   Run then runs that capability. Built-ins that declare none say so in one quiet line.
// - **On** is one row per declared data input (`newKind` input, typed ports, a volume, the Stage's picture), plus the
//   selected outline a task needs (contract/actions.json `needsTarget`) and the clicks a click-driven task takes.
// - **Makes** is the chosen model's declared output in its folder's words (`newKind.made`, its output's label: "Bright
//   regions (not cells)"), else its type in a few words (`gives`), else the role operation's returns; a finished run says
//   that output's note (`madeNote`) under its outcome. Nothing invented.
// - **Its own task:** an entry contract/actions.json `ownTasks` lists ("Find bright regions") is its own row and page,
//   never folded into the job it is a choice of (a threshold of operation "cells" is not a cell finder's row).
import type { CapabilitiesWire, DownloadRow, ModelRow, ReadinessStep, Role, RoleOption } from './protocol';
import type { Field } from './parameter-fields';
import type { LitCellState } from './lit-cell';
import type { ActionRow } from './actions-panel';
import { sizeWords } from './size-words';
import contract from '../../../contract/actions.json';

/** Catalog entries listed as a task of their own, by that task's name (contract/actions.json `ownTasks`). */
export const OWN_TASKS: Readonly<Record<string, string>> = contract.ownTasks.titles;

/** Models' sections, the same in Models, Tools By model and a task's model list (one meaning of Ready: face on or
 * obtained; Not checked is its own state). */
export type ModelSection = 'needs' | 'ready' | 'unchecked' | 'setup' | 'away';
export const MODEL_SECTIONS: readonly (readonly [ModelSection, string])[] = [
  ['needs', 'Needs you'], ['ready', 'Ready'], ['unchecked', 'Not checked'], ['setup', 'Can be set up'], ['away', 'Not on this Mac'],
];

/** An entry's section from the server's own words: its group (needs-you: a choice of yours it can't run) and its
 * card's face state (catalog_view.face). */
export function modelSection(row: Pick<ModelRow, 'group' | 'status'> & { face?: { state: string } }): ModelSection {
  if (row.group === 'needs-you') return 'needs';
  // A row without a face (an older server): its readiness alone.
  if (!row.face) return row.status === 'ready' ? 'ready' : row.status === 'unavailable' || row.group === 'unavailable' ? 'away' : 'setup';
  const face = row.face.state;
  if (row.status === 'unavailable' || row.group === 'unavailable' || face === 'unavailable') return 'away';
  if (face === 'on' || face === 'obtained') return 'ready';
  if (face === 'unchecked' || face === 'ready') return 'unchecked';
  return 'setup';
}

/** A section's lit cell and its word (the cell's accessible name). */
export function sectionCell(section: ModelSection): { cell: LitCellState; word: string } {
  return section === 'needs' ? { cell: 'needs', word: 'Needs you' } : section === 'ready' ? { cell: 'ready', word: 'Ready' }
    : section === 'unchecked' ? { cell: 'idle', word: 'Not checked' } : section === 'away' ? { cell: 'stopped', word: 'Not on this Mac' }
    : { cell: 'idle', word: 'Not set up' };
}

/** The one step that makes a model ready, said as what happens (readiness.py's step, with who performs it). */
export interface RunStep {
  kind: ReadinessStep['kind'];
  /** The run page's primary ("Download 413 MB", "Set up Fiji", "Install InstanSeg (brightfield nuclei)"). */
  label: string;
  /** A row's button, short ("Download 16 MB", "Set up Fiji", "Install"). */
  short: string;
  says: string; how: string;
  /** In place: a download the page starts and shows filling (its model's files); else the setup sheet. */
  does: 'download' | 'sheet';
  /** The model whose setup sheet it opens (`#models/<id>/setup`), or whose files it downloads. */
  model: string;
  bytes?: number;
}

/** A step as the page says it, or, for a step nobody can press here (weights made by fine-tuning, another edition), the
 * reason in plain words and no button (a dead-end button was the 0.3.0 "Set up"). */
export function runStep(step: ReadinessStep | null | undefined, entry = ''): { step: RunStep | null; reason: string } {
  if (!step) return { step: null, reason: '' };
  if (step.kind === 'weights') return { step: null, reason: 'Made from your corrections' };
  if (step.kind === 'edition') return { step: null, reason: step.says };
  const base = { kind: step.kind, says: step.says, how: step.how ?? '', ...(step.bytes ? { bytes: step.bytes } : {}) };
  const sheet = (label: string, short = label, model = entry || step.model || '') => ({ step: { ...base, label, short, does: 'sheet' as const, model }, reason: '' });
  switch (step.kind) {
    case 'download': {
      // Its size in SI words: from its bytes, else the server's own words when they say one ("Download 43 MB", readiness.py
      // `size_words`, the same formatter), else no size at all (never one guessed).
      const said = /^Download [\d.]+ [KMGT]B$/.test(step.says.trim()) ? step.says.trim() : '';
      const label = step.bytes ? `Download ${sizeWords(step.bytes)}` : said || 'Download';
      return step.model ? { step: { ...base, label, short: label, does: 'download', model: step.model }, reason: '' } : sheet(label);
    }
    case 'key': return sheet('Add key');
    case 'licence': return sheet('Accept licence');
    case 'activate': return sheet('Turn on');
    case 'check': return sheet('Check it here');
    case 'trust': return sheet('Allow');
    case 'install': {
      // Fiji first: an install the app performs (fiji_generations), opened on Fiji's own setup.
      if (step.model === 'fiji') return sheet('Set up Fiji', 'Set up Fiji', 'fiji');
      if (step.onAgain) return sheet('Turn on');
      const said = step.says.replace(/ first$/, '');
      return /^(Install|Prove|Turn on)\b/.test(said) ? sheet(said, said.split(' ')[0] === 'Turn' ? 'Turn on' : said.split(' ')[0]) : sheet(said || 'Set up', 'Set up');
    }
    default: return sheet('Set up');
  }
}

/** A download that is only the first of a model's setup (an install follows it: its own Python): the step names the
 * whole setup, "Set up <name>", and opens its setup sheet, whose Prepare and turn on runs both, so no install starts
 * behind a button that said Download; `facts` says both from the steps ("16 MB, then its own Python (35 packages)"). A
 * download-only model keeps its in-place fill (`facts` ''). */
export function wholeSetup(step: RunStep | null, steps: readonly ReadinessStep[] | undefined, name: string, entry = ''): { step: RunStep | null; facts: string } {
  const install = step?.does === 'download' ? steps?.find((s) => s.kind === 'install' && !s.onAgain && s.model !== 'fiji') : undefined;
  if (!step || !install) return { step, facts: '' };
  const size = step.bytes ? sizeWords(step.bytes) : /[\d.]+ [KMGT]B/.exec(step.label)?.[0] ?? '';
  const how = install.how ?? '';
  const packages = /(\d+) packages/.exec(how)?.[1];
  const then = /own Python/.test(how) ? `then its own Python${packages ? ` (${packages} packages)` : ''}` : 'then an install';
  const says = `${step.says}, then ${install.says.charAt(0).toLowerCase()}${install.says.slice(1)}`;
  return { step: { ...step, kind: 'install', label: `Set up ${name}`, short: 'Set up', says, does: 'sheet', model: entry || install.model || step.model },
    facts: [size, then].filter(Boolean).join(', ') };
}

/** Where a model runs and what it costs, in at most two facts ("On this Mac · free", "Cloud · your Google key"). */
export interface KeyWords { short: string; present: boolean }
export function keyOf(wire: CapabilitiesWire | null, entry: ModelRow | undefined): KeyWords | null {
  const fact = entry?.facts?.find((f) => f.kind === 'key' && f.service);
  if (!fact) return null;
  const key = Object.values(wire?.setup?.keys ?? {}).find((k) => k.service === fact.service);
  return key ? { short: key.short, present: key.present } : { short: fact.service ?? '', present: fact.ok === true };
}

function builtIn(entry: ModelRow | undefined): boolean {
  return entry?.newKind?.proof === 'bundled with the app';
}

export function whereWords(wire: CapabilitiesWire | null, entry: ModelRow | undefined, option?: RoleOption): string {
  const cloud = option?.runs === 'cloud' || (!option?.runs && entry?.runs === 'cloud');
  if (cloud) {
    const key = keyOf(wire, entry);
    return key ? `Cloud · ${key.present ? 'your' : 'needs your'} ${key.short} key` : 'Cloud';
  }
  if (builtIn(entry)) return 'Built in · free';
  if (entry || option?.runs === 'mac' || option?.runs === 'both') return 'On this Mac · free';
  return '';
}

function capital(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** What a model does for THIS task: its kind's own words when it implements the task as a catalog kind; its entry's
 * words when the option is the entry itself; else the option's own line for this role (EdgeTAM automatic masks is not
 * "Outlines what you click"). */
function abilityWords(entry: ModelRow | undefined, role: string, option: string, roleOption?: RoleOption): string {
  const kind = entry?.abilities?.find((a) => a.id.startsWith('kind:') && a.role === role && a.option === option);
  if (kind?.what) return kind.what;
  if (entry && (!roleOption || roleOption.label === entry.name)) return entry.what;
  if (roleOption?.note) return capital(roleOption.note);
  return entry?.what ?? '';
}

/** The part of a name before " · " ("Bright objects" for "Bright objects · Basic local method"): the short form the
 * strip's pill and the run's outcome say; a name without one is kept whole. */
export function shortName(name: string): string {
  const cut = name.indexOf(' · ');
  return cut > 0 ? name.slice(0, cut) : name;
}

/** One model that could do the task. */
export interface RunChoice {
  /** Unique within its task: `<role>/<option>`, or `entry:<model>` for a catalog entry's own. */
  id: string;
  role: string; option: string;
  model: string | null;
  name: string; short: string;
  ability: string;
  where: string;
  section: ModelSection;
  /** It can run here now. */
  runnable: boolean;
  /** The role's own current choice (the default, said "In use" nowhere: the With card names it). */
  current: boolean;
  cell: LitCellState; cellWord: string;
  step: RunStep | null; reason: string;
  /** Where the picture goes when it runs in the cloud ("Google"), else null. */
  cloud: string | null;
  /** The option's own words with where it runs ("micro-sam (light microscopy) on this Mac": what Activity names it by). */
  said: string;
  /** Not checked on this Mac yet: its check (Check it here), which a click-driven trial needs first. */
  check: RunStep | null;
  /** What Run sends: the capability, and `use` for a model chosen for this run only. */
  run: { capability: string; use: Record<string, string> };
  /** Its own declared fields (a format-2 entry with a capability of its own); null: the task's fields. */
  fields: Field[] | null;
  /** Its files being downloaded now, from the server's download row (measured bytes only). */
  download: { got: number; total: number; state: DownloadRow['state']; says: string } | null;
  /** Its whole setup in one line when it is more than a download ("16 MB, then its own Python (35 packages)"), else ''. */
  setup: string;
}

/** One data input the run reads (one On row each). */
export interface RunInput {
  kind: 'stage' | 'picture' | 'volume' | 'typed' | 'outline' | 'clicks';
  id: string; label: string; type?: string; required?: boolean;
}

export interface RunSpec {
  task: string; title: string;
  /** The page's first line: what the chosen model does here, else what the task does. */
  what: string;
  inputs: RunInput[];
  clicks: boolean;
  makes: string;
  choices: RunChoice[];
  chosen: RunChoice | null;
  /** Settings shown, and those under More options. */
  fields: Field[]; advanced: Field[];
  /** "none declared for Cellpose-SAM" when nothing is shown as Settings (a task's own optional fields wait in More
   * options); else ''. */
  none: string;
  /** What Run sends now. */
  run: { capability: string; use: Record<string, string> };
  /** The task itself can run here (a not-ready model's step comes first when it can't). */
  available: boolean;
  /** What the chosen model says of what it makes, after a run (its output's note: "Bright regions are not a cell
   * count"); '' when it declares none. */
  note: string;
  /** The task's own step when no model choice carries one (a fixed model, the app's own code), and why it can't run. */
  step: RunStep | null; reason: string;
  query: boolean;
  /** Its declared settings are shown but cannot change yet (a catalog entry not set up): what to do first ("Set up Fiji
   * to change these"); '' when they can. */
  locked: string;
}

/** A task as the spec reads it: an Actions row, with its gesture when it is run by clicking on the Stage. */
export type RunTask = Pick<ActionRow, 'id' | 'title' | 'what' | 'fields' | 'target' | 'available' | 'why' | 'step' | 'chooses' | 'query' | 'model'>
  & { gesture?: 'click' };

function entryOf(wire: CapabilitiesWire, role: string, option: string): ModelRow | undefined {
  return wire.models?.find((m) => m.usedFor.some((u) => u.role === role && u.option === option))
    ?? wire.models?.find((m) => m.abilities?.some((a) => a.role === role && a.option === option && a.id.startsWith('kind:')));
}

function downloadOf(entry: ModelRow | undefined): RunChoice['download'] {
  const d = entry?.download;
  return d && ['queued', 'downloading', 'checking'].includes(d.state) ? { got: d.got, total: d.total, state: d.state, says: d.says } : null;
}

/** The capability a format-2 entry brings of its own, when it is an Actions row here (its tool is here). */
function ownRow(rows: ReadonlyMap<string, RunTask> | undefined, entry: ModelRow | undefined): RunTask | undefined {
  return entry ? rows?.get(`catalog:${entry.id}`) : undefined;
}

function choiceFor(wire: CapabilitiesWire, task: RunTask, role: Role | undefined, entry: ModelRow | undefined, option: RoleOption | undefined,
  rows: ReadonlyMap<string, RunTask> | undefined, use: string): RunChoice {
  const roleId = role?.id ?? '', optionId = option?.id ?? entry?.id ?? '';
  const name = option?.label ?? entry?.name ?? task.model ?? task.title;
  const download = downloadOf(entry);
  // Runnable: the role's own word for this option; with no model choice, the task's own (a fixed model, the entry's own
  // capability); an entry that is no option of the role yet (a kind not set up, so not proven) cannot run.
  const runnable = option ? option.available : role ? false : task.available;
  const section: ModelSection = entry ? modelSection(entry) : runnable ? 'ready' : 'setup';
  const raw = runnable ? null : option?.readiness?.next ?? entry?.next ?? (option ? null : task.step);
  const first = runStep(raw, entry?.id), reason = first.reason;
  const whole = wholeSetup(first.step, option?.readiness?.steps ?? entry?.steps, shortName(name), entry?.id);
  const step = whole.step;
  // A step only the person can take (their key, a licence to accept) is theirs: the needs cell, whatever its section.
  const person = !runnable && !!step && (step.kind === 'key' || step.kind === 'licence');
  const look = download ? { cell: 'setup' as LitCellState, word: 'Downloading' } : person ? { cell: 'needs' as LitCellState, word: 'Needs you' }
    : runnable && section !== 'ready' && section !== 'unchecked' ? { cell: 'ready' as LitCellState, word: 'Ready' } : sectionCell(section);
  const face = entry?.face?.action;
  const check = section === 'unchecked' && face?.kind === 'check' ? runStep({ kind: 'check', says: face.says, how: face.how ?? '' } as ReadinessStep, entry!.id).step : null;
  const own = entry?.newKind && entry.newKind.operation === roleId ? ownRow(rows, entry) : entry && !role ? ownRow(rows, entry) : undefined;
  const cloudy = option?.runs === 'cloud' || (!option?.runs && entry?.runs === 'cloud');
  const key = cloudy ? keyOf(wire, entry) : null;
  return {
    id: role && option ? `${roleId}/${optionId}` : `entry:${entry?.id ?? task.id}`,
    role: roleId, option: role ? optionId : '', model: entry?.id ?? null, name, short: shortName(name),
    ability: abilityWords(entry, roleId, optionId, option) || (role ? '' : task.what),
    where: whereWords(wire, entry, option), section, runnable, current: !!role && role.current === optionId,
    cell: look.cell, cellWord: look.word, step, reason,
    cloud: cloudy ? key?.short || 'the cloud' : null, said: option?.said ?? '', check,
    run: own ? { capability: own.id, use: {} } : { capability: task.id, use: role && use ? { [roleId]: use } : {} },
    fields: own ? own.fields : null,
    download, setup: whole.facts,
  };
}

/** Every model that could do the task, in Models' sections (Needs you, Ready, Not checked, Can be set up, Not on this
 * Mac), the role's own order within each. */
export function taskChoices(wire: CapabilitiesWire | null, task: RunTask, rows?: ReadonlyMap<string, RunTask>, use = ''): RunChoice[] {
  if (!wire) return [];
  const role = task.chooses ? wire.roles.find((r) => r.id === task.chooses) : undefined;
  const out: RunChoice[] = [];
  if (role) {
    for (const option of role.options) {
      if (option.id === 'off') continue; // "Off" is no model
      out.push(choiceFor(wire, task, role, entryOf(wire, role.id, option.id), option, rows, use === option.id ? use : ''));
    }
    // Format-2 entries whose kind implements this role but are not one of its options yet (not set up, so not proven).
    for (const entry of wire.models ?? []) {
      if (entry.newKind?.operation !== role.id || role.options.some((o) => o.id === entry.id)) continue;
      out.push(choiceFor(wire, task, role, entry, undefined, rows, ''));
    }
  } else {
    // No model choice: a catalog entry's own capability is that entry; a fixed model is named; the app's own code is no
    // choice at all (no With row).
    const key = task.id.startsWith('catalog:') ? task.id.slice(8) : '';
    const entry = key ? wire.models?.find((m) => m.id === key) : undefined;
    if (entry) out.push(choiceFor(wire, task, undefined, entry, undefined, rows, ''));
    else if (task.model) {
      const named = wire.models?.find((m) => m.name === task.model || m.usedFor.some((u) => u.fixed && (u as { label?: string }).label === task.model));
      out.push(choiceFor(wire, task, undefined, named, undefined, rows, ''));
    }
  }
  const rank = new Map(MODEL_SECTIONS.map(([s], i) => [s, i]));
  return out.map((c, i) => ({ c, i })).sort((a, b) => rank.get(a.c.section)! - rank.get(b.c.section)! || a.i - b.i).map(({ c }) => c);
}

/** Words for what a run makes, from a kind's declared output or a role operation's returns. */
const OUTPUT_WORDS: Record<string, string> = {
  'labels.instance.2d': 'Outlines', 'image.2d.rgb8': 'A picture', 'table.objects': 'A table', 'tracks.graph': 'Tracks',
};
const RETURNS_WORDS: Record<string, string> = {
  // Find all like this returns matches, kept as a population (produces.py): outlines.
  'polygons/1': 'Outlines', 'instance/1': 'Outlines', 'matches/1': 'Outlines', 'vectors/1': 'Numbers that compare how things look',
};

/** What a task's operation returns: its model choice's role, else the fixed role its model is used in (Find all like
 * this: SAM 3.1, fixed, returning matches). */
function returnsOf(wire: CapabilitiesWire | null, role: Role | undefined, chosen: RunChoice | null): string | undefined {
  if (role) return role.operation?.returns;
  const entry = chosen?.model ? wire?.models?.find((m) => m.id === chosen.model) : undefined;
  const fixed = entry?.usedFor.find((u) => u.fixed);
  return fixed ? wire?.roles.find((r) => r.id === fixed.role)?.operation?.returns : undefined;
}

function kindOf(wire: CapabilitiesWire, model: string | null, task: string): NonNullable<ModelRow['newKind']> | undefined {
  const key = model ?? (task.startsWith('catalog:') ? task.slice(8) : '');
  if (!key) return undefined;
  for (const m of wire.models ?? []) {
    if (m.newKind && m.id === key) return m.newKind;
    const member = m.memberKinds?.find((k) => k.key === key);
    if (member) return member;
  }
  return undefined;
}

function portWords(id: string): string {
  return id.replace(/[_-]+/g, ' ').trim() || 'input';
}

/** The run page's spec for a task and the model picked for it (`pick`: an option of its role, or a choice id). */
export function runSpec(wire: CapabilitiesWire | null, task: RunTask, pick = '', rows?: ReadonlyMap<string, RunTask>): RunSpec {
  const choices = taskChoices(wire, task, rows, pick);
  const role = wire && task.chooses ? wire.roles.find((r) => r.id === task.chooses) : undefined;
  const chosen = choices.find((c) => c.option === pick || c.id === pick)
    ?? choices.find((c) => c.current) ?? choices[0] ?? null;
  const own = !!chosen?.fields;
  // A format-2 entry chosen here brings its own form; otherwise the task's. A field for some models only (`for`: Compare
  // with the ImageJ macro is Analyze Particles') shows only while one of them is chosen.
  // A task's field for some models only follows them onto their own form too (Analyze Particles' comparison: its run
  // goes through the task, its own form declares nothing).
  const all = own ? [...chosen!.fields!, ...task.fields.filter((f) => !!f.for?.length && appliesTo(f, chosen))]
    : task.fields.filter((f) => appliesTo(f, chosen));
  const shown = all.filter((f) => f.required || f.advanced === false);
  const advanced = all.filter((f) => !(f.required || f.advanced === false));
  const kind = wire ? kindOf(wire, own ? chosen!.model : null, own ? '' : task.id) : undefined;
  const inputs: RunInput[] = [];
  if (kind?.typed) for (const port of kind.inputs ?? []) inputs.push({ kind: 'typed', id: port.id, label: portWords(port.id), type: port.type });
  else if (kind?.input === 'volume.3d') inputs.push({ kind: 'volume', id: 'volume', label: 'A volume', type: kind.input });
  else if (kind) inputs.push({ kind: 'picture', id: 'image', label: 'The picture', type: kind.input });
  else if (!task.target || !task.target.required) inputs.push({ kind: 'stage', id: 'stage', label: 'What is on the Stage' });
  if (task.target) inputs.push({ kind: 'outline', id: task.target.key, label: 'The selected outline', required: task.target.required });
  if (task.gesture === 'click') inputs.push({ kind: 'clicks', id: 'clicks', label: 'Your clicks' });
  // What it makes, in the chosen model's own declared words when it has them (a format-2 entry chosen in a task too).
  const said = (wire && chosen?.model ? kindOf(wire, chosen.model, '') : undefined) ?? kind;
  const makes = said ? said.made || (said.gives ? capital(said.gives) : OUTPUT_WORDS[said.output] ?? '')
    : returnsOf(wire, role, chosen) ? RETURNS_WORDS[returnsOf(wire, role, chosen)!] ?? '' : task.query ? 'An answer' : '';
  const run = chosen ? (own ? chosen.run : { capability: task.id, use: role && pick && chosen.option === pick ? { [role.id]: pick } : {} })
    : { capability: task.id, use: {} };
  const named = chosen?.name ?? task.title;
  const { step, reason } = chosen ? { step: null, reason: '' } : runStep(task.available ? null : task.step);
  return {
    task: task.id, title: task.title, what: chosen?.ability || task.what, inputs, clicks: task.gesture === 'click', makes,
    choices, chosen, fields: shown, advanced, none: shown.length ? '' : `none declared for ${named}`, run,
    available: task.available, step, reason: reason || (!task.available && !step && !chosen ? task.why : ''), query: task.query,
    note: said?.madeNote ?? '', locked: '',
  };
}

/** Whether a field applies to the chosen model (`for` names the options it is for; none: every model). */
export function appliesTo(field: Pick<Field, 'for'>, chosen: Pick<RunChoice, 'option' | 'model'> | null): boolean {
  return !field.for?.length || (!!chosen && field.for.some((o) => o === chosen.option || o === chosen.model));
}

/** A catalog entry with no capability yet (a format-2 kind not set up, so not proven): its page names its step and
 * shows the settings its folder declares (catalog_view.py `fields`, S11), disabled with "Set up Fiji to change these",
 * or says they come with it when the wire has none; nothing is invented. */
export function entrySpec(wire: CapabilitiesWire | null, entry: ModelRow): RunSpec {
  const task: RunTask = { id: `catalog:${entry.id}`, title: entry.name, what: entry.what, fields: [], target: null,
    available: false, why: entry.word, step: entry.next, chooses: null, query: false, model: entry.name };
  const spec = runSpec(wire, task);
  const declared = entry.newKind?.fields ?? [];
  if (declared.length) {
    const step = spec.step ?? spec.chosen?.step ?? null;
    return { ...spec, fields: declared.filter((f) => !f.advanced), advanced: declared.filter((f) => f.advanced), none: '',
      locked: `${step?.label ?? 'Set it up'} to change these` };
  }
  return { ...spec, none: entry.newKind ? `its settings show once it is set up` : spec.none };
}

/** The tasks a model serves (tools-catalog.ts `modelTasks`), ordered for Use it here: the one where it is the role's
 * current choice first (EdgeTAM's clicks before its automatic masks), then in the list's order; a format-2 entry's own
 * capability is left out when it implements another of them (one page for one thing: Bright objects' fields show in
 * Outline every cell). */
export function orderServed<T extends { task: string; choice: { role: string; option: string } | null }>(wire: CapabilitiesWire | null, model: string, served: readonly T[]): T[] {
  const entry = wire?.models?.find((m) => m.id === model);
  const operation = entry?.newKind?.operation;
  const ownTask = !!OWN_TASKS[`catalog:${model}`];
  // An entry naming its own task opens that task first (Use it here on Bright objects: Find bright regions).
  const kept = operation && !ownTask && served.some((t) => t.choice?.role === operation) ? served.filter((t) => t.task !== `catalog:${model}`) : [...served];
  const first = (t: T) => (ownTask && t.task === `catalog:${model}`) || (!!t.choice && wire?.roles.find((r) => r.id === t.choice!.role)?.current === t.choice.option);
  return kept.map((t, i) => ({ t, i })).sort((a, b) => Number(first(b.t)) - Number(first(a.t)) || a.i - b.i).map(({ t }) => t);
}

/** Whether a catalog row is an entry implementing another task's model choice (so the list shows it there, not as a
 * second row): its kind's operation is a listed task's choosing role. */
export function implementsTask(wire: CapabilitiesWire | null, task: RunTask, tasks: readonly RunTask[]): RunTask | undefined {
  if (!wire || !task.id.startsWith('catalog:')) return undefined;
  const entry = wire.models?.find((m) => `catalog:${m.id}` === task.id);
  const operation = entry?.newKind?.operation;
  // One that names its own task is that task's row, never folded into the job it is a choice of.
  return operation && !OWN_TASKS[task.id] ? tasks.find((t) => t !== task && t.chooses === operation) : undefined;
}

/** What a finished run says first: "<model> found N <its output words>" from the server's own outcome ("Outlined and
 * measured 271 bright regions with …", "Found no cells …"), with the rest of its words kept for the line under it;
 * words of any other shape are said as they are. */
export function outcomeWords(model: string, said: string): { title: string; rest: string } {
  const match = /^(?:Outlined and measured|Found) (no|\d+) ([^.]+?)(?: with [^.]*)?\.\s*(.*)$/s.exec(said.trim());
  if (match && model) return { title: `${model} found ${match[1]} ${match[2]}`, rest: match[3].trim() };
  // run_capability's own summary ("… found 12 outlines on the view now, kept as e3 …"): the count; its ids stay in Activity.
  const found = /^.+? found (no|\d+) (outlines?|[a-z ]+?) on /.exec(said.trim());
  if (found && model) return { title: `${model} found ${found[1]} ${found[2]}`, rest: '' };
  return { title: said.trim(), rest: '' };
}

/** "2.6 s", "1:12": how long a run took, from the studio's clock. */
export function tookWords(ms: number): string {
  if (ms < 60_000) return `${(Math.max(0, ms) / 1000).toFixed(1)} s`;
  const t = Math.round(ms / 1000), m = Math.floor(t / 60), s = t % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

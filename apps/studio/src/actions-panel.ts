import { findByTask, taskScore } from './task-search';
import { dockControls, type ControlHandle } from './capability-controls';
import { ModelComparison, comparable, type ComparisonResult, type ComparisonView } from "./model-comparison";
import { lastUsed, modelTasks, rememberUse, taskModels, type ModelTask, type TaskModel } from './tools-catalog';
import { faceState, kindWords } from './model-status';
import type { MeasurementPin } from './measurements';
import type { RetainedInput } from './modelspage';
import { litCell, type LitCellState } from './lit-cell';
import { tabMark, type TabMark } from './tab-marks';
import { clock } from './working-strip';
import { progressWords } from './size-words';
import { specimenName } from './camera-label';
import { MODEL_SECTIONS, OWN_TASKS, appliesTo, entrySpec, implementsTask, modelSection, orderServed, outcomeWords, runSpec, runStep, sectionCell, shortName, taskChoices, tookWords, whereWords, wholeSetup,
  type RunChoice, type RunInput, type RunSpec, type RunStep } from './run-spec';
// The Actions panel (docs/design/2026-10-13-actions-panel.md; Will's ask of 2026-10-12): the lower half of the side bar,
// everything the agent can run on the view, run by the user's own hand. It keeps no list of its own:
// - **Rows** are the capability list's (the `capabilities` message): the agent's capabilities with a tool, in the
//   families that act on the view, titled and worded as the Capabilities page words them.
// - **Forms** are each tool's own arguments (`GET /api/actions`, the live registry's JSON schemas), with the
//   capability's presets.
// - **Whether a row can run here,** and the next step when it can't, are the capabilities message's (readiness.py says
//   them underneath; the panel never asks it).
// - **Running one** is the studio command `action` (commands.py), the tool the agent would call: kept, checked and said
//   in Activity as the user's ("You ran “Outline every cell”: 23 cells").
// An action acts on the selected outline, else on the view (Will's answer of 2026-10-12). A row whose model runs in the
// cloud asks once per session before its first run. "Run with…" chooses a model for that run only, as the agent's `use`
// does.
// It is also the workspace's Tools (docs/design/2026-10-20-hooke-words.md, "Tool or model?"; the workspace-feel plan,
// "Running a model"; the mock's toolsDrawer() and runPage()): By task is one row a task (a fixed icon column, its name,
// the model it will use beside its lit cell, "+n more"), tasks and catalog kinds that can't run yet under Can be set up
// with their one step; By model is the Models page's entries in its sections and counts. A task opens its run page, one
// anatomy for every capability, rendered only from its spec (run-spec.ts): what it does, On (one row per data input),
// With (the model, Change opens the models in place), Makes, the declared Settings, More options, Results go to, and a
// sticky footer whose one primary is Run <model>, the chosen model's step (a download filling in place), Click in the
// view, or the run's state in words. Compare… and the model in Models are in the page's ⋯.
import contract from '../../../contract/actions.json';
import type { ActivityEvent, CapabilitiesWire, Capability, ModelRow, ReadinessStep, Role } from './protocol';

/** A tool's argument schema as the registry gives it (a JSON schema object). */
export interface JsonProp { type?: string | string[]; enum?: unknown[]; description?: string }
export interface JsonSchema { type?: string; properties?: Record<string, JsonProp>; required?: string[] }
export interface ToolInfo { params: JsonSchema; kind: string; family: string }
/** A capability input bound at Run to one saved resource (H01 slice d); `fits` and `says` are the server's own check. */
export type TypedReference = { kind: 'array'; session: string; id: string; revision: number; component: string };
export interface TypedPort { port: string; type: string; choices: { title: string; reference: TypedReference; shape: number[]; dtype: string; fits: boolean; says: string }[] }
export type ToolList = Record<string, ToolInfo>;

/** The panel's groups, in order: a capability family, said as what the user wants done. Families not here (talking,
 * researchers and their work, memory, the library, choosing) are the agent's: they need words, or have a page. The
 * lists are contract/actions.json's, which the server's tests read too. */
export const ACTION_GROUPS: readonly { family: string; title: string }[] = contract.groups;

/** Capabilities in those families that stay the agent's (contract/actions.json `agentOnly`, with why). */
export const AGENT_ONLY: ReadonlySet<string> = new Set(contract.agentOnly.ids);
/** Actions that act on an outline, never on the whole view: their target is required though their tool's is not. */
const NEEDS_TARGET: ReadonlySet<string> = new Set(contract.needsTarget.ids);
/** The words a search finds the Measurements table row by. */
const TABLE_WORDS = 'measurements table numbers csv export spreadsheet data download sizes speeds';
/** What a researcher reaches for most, in its order: the panel's Common group, first. */
export const COMMON: readonly string[] = contract.common.ids;
/** Each tool argument's words, choices said plainly, default or hidden (contract/actions.json `fields`). */
/** A field's words for a person (contract/actions.json `fields`): its label, its choices said plainly, a `hint` under it
 * where one helps, its default, hidden, and the options it is `for`. The tool schema's own description is written for
 * the model and never shown. */
interface FieldWords { label?: string; hint?: string; choices?: Record<string, string>; default?: unknown; hidden?: boolean; for?: string[] }
const FIELDS = contract.fields.tools as Record<string, Record<string, FieldWords>>;

/** Arguments that name the thing acted on: filled from the selection. */
const TARGET_KEYS = ['object', 'a'];
/** A preset that stands for "the one meant" (an id such as o1 or e5, or "that"): the row's target, never sent as is. */
const PLACEHOLDER = /^(?:[a-z]{1,2}\d+|that)$/;
/** Arguments that are the agent's own machinery (lists of ids, its checks, how it lays windows out): never a field. */
const HIDDEN_KEYS = new Set(['keep', 'keep_container', 'checks', 'members', 'objects', 'rest', 'order', 'outliers', 'where',
  'things', 'search_terms', 'beside', 'hold', 'picture', 'live', 'show', 'description', 'n', 'why', 'inside', 'like', 'b',
  'ask', 'open', 'all', 'of']);
/** A field's words where the argument's own name would not say it. */
const LABELS: Record<string, string> = {
  what: 'What', label: 'What to count', micrometres: 'Its length (µm)', objective: 'Objective (×)', field_number: 'Field number (mm)',
  seconds: 'Over the last (s)', level: 'Zoom', how: 'How', note: 'Note', color: 'Colour', style: 'Style', name: 'Name',
  subject: 'What to look for', change: 'Change', groups: 'Groups', minutes: 'For (min)', by: 'By', which: 'Which',
  below: 'Below', above: 'Above', question: 'Question', labels: 'With labels', overlays: 'With outlines', names: 'With names',
  group: 'Group', of: 'Of', compare_reference: 'Compare with the ImageJ macro',
};
/** The model roles "Run with…" may choose for one run: what the agent's `use` takes for a request (names.roles; sight
 * and zero-shot are chosen from now on only). */
const RUN_WITH_ROLES = new Set(['outline', 'cells', 'find-like', 'looks', 'embed', 'vision', 'fast', 'outline_check', 'research', 'image', 'presentation']);

export type { Field, FieldKind } from './parameter-fields';
import { parameterControl, parameterValues, parameterError, type FieldKind, type Field } from './parameter-fields';
export interface Choice { id: string; label: string }
export interface ActionRow {
  id: string; title: string; what: string; say: string; group: string; tool: string; query: boolean;
  /** The argument filled from the selection, and whether the action needs one. */
  target: { key: string; required: boolean } | null;
  fields: Field[];
  available: boolean; why: string; chooseRequired?: boolean;
  /** What to do first when it can't run here (the Models page's step for the model it needs). */
  step: ReadinessStep | null;
  /** The cloud model it sends the view to (asked once per session), or null when it runs on this Mac. */
  cloud: string | null;
  /** The model it runs now, by its one name, for its run button ("Run Cellpose-SAM"); null when no model runs it. */
  model: string | null;
  /** The role it chooses its model by (its "Run with…" job), even when none of its models can run here. */
  chooses: string | null;
  /** "Run with…": the role and its models that can run here, the one in use first. */
  runWith: { role: string; title: string; current: string | null; options: Choice[]; clouds: Record<string, string> } | null;
  /** What it runs on, as the Capabilities page's "What powers it" says it: each model role's model in use (its job, its
   * name with where it runs), each method's way in use, and the algorithms of its code. Shown when the row is opened. */
  powers: Power[];
  /** A task the user performs by clicking on the Stage (Click to outline): its page arms a click, never runs a tool. */
  gesture?: 'click';
}

/** One thing a row runs on: its job ("Cell finder") and what does it now ("Cellpose-SAM (on this Mac)"). */
export interface Power { job: string; name: string; kind: 'model' | 'method' | 'algorithm'; cloud: boolean }

/** A capability's powers, from its roles and parts: models first, then methods, then algorithms. Policies (when to
 * act, what to ask) are left to the Capabilities page: they decide, they run nothing. */
export function powersOf(wire: CapabilitiesWire, capability: { roles?: string[]; parts?: string[]; runsOn?: string }, choice?: { role: string; option: string }): Power[] {
  const powers: Power[] = [];
  // A capability the catalog added runs its makers' code (or Fiji), never the app's own: said as what runs it.
  if (capability.runsOn) powers.push({ job: 'Runs', name: capability.runsOn, kind: 'method', cloud: false });
  for (const id of capability.roles ?? []) {
    const original = roleOf(wire, id);
    const role = original && choice?.role === id ? { ...original, current: choice.option } : original;
    if (!role || role.kind === 'policy') continue;
    const model = role.kind === 'model' ? inUse(role) : null;
    const option = role.options.find((o) => o.id === role.current);
    const name = model?.label ?? option?.said ?? option?.label ?? role.label ?? '';
    if (!name) continue;
    powers.push({ job: role.title, name, kind: role.kind, cloud: (model?.runs ?? option?.runs) === 'cloud' });
  }
  for (const id of capability.parts ?? []) {
    const part = (wire.parts ?? []).find((p) => p.id === id);
    if (part?.kind === 'algorithm') powers.push({ job: 'Algorithm', name: part.title, kind: 'algorithm', cloud: false });
  }
  const rank = { model: 0, method: 1, algorithm: 2 };
  return powers.sort((a, b) => rank[a.kind] - rank[b.kind]);
}

function types(prop: JsonProp): string[] {
  return Array.isArray(prop.type) ? prop.type : prop.type ? [prop.type] : [];
}

/** A description that only lists its choices ("motion | appears | look"): the choices. */
const LISTED = /^\s*[\w-]+(?:\s*\|\s*[\w-]+)+\s*$/;
export function choicesOf(prop: JsonProp): string[] {
  if (prop.enum && prop.enum.every((v) => typeof v === 'string')) return prop.enum as string[];
  const said = prop.description ?? '';
  return types(prop).includes('string') && LISTED.test(said) ? said.split('|').map((c) => c.trim()) : [];
}

function primitive(prop: JsonProp): FieldKind | null {
  const kinds = types(prop);
  if (choicesOf(prop).length) return 'choice';
  if (kinds.includes('boolean')) return 'boolean';
  if (kinds.includes('number') || kinds.includes('integer')) return 'number';
  if (kinds.includes('string')) return 'text';
  return null;
}

export function fieldLabel(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** A capability's form: the argument the selection fills, and the fields the user fills in. Null when it needs what
 * only words give (a required list or object no preset fills). */
export function formOf(capability: Capability, tool: ToolInfo): { target: ActionRow['target']; fields: Field[] } | null {
  const props = tool.params.properties ?? {};
  const required = new Set(tool.params.required ?? []);
  const presets = capability.how.args ?? {};
  const placeholder = Object.entries(presets).find(([, v]) => typeof v === 'string' && PLACEHOLDER.test(v))?.[0];
  const fixed = new Set(Object.keys(presets).filter((k) => k !== placeholder));
  const targetKey = placeholder ?? TARGET_KEYS.find((k) => k in props && !fixed.has(k)) ?? null;
  const target = targetKey ? { key: targetKey, required: placeholder !== undefined || required.has(targetKey) || NEEDS_TARGET.has(capability.id) } : null;
  const fields: Field[] = [];
  const words = FIELDS[capability.how.tool ?? ''] ?? {};
  for (const [key, prop] of Object.entries(props)) {
    if (key === targetKey || fixed.has(key) || TARGET_KEYS.includes(key)) continue;
    const own = words[key] ?? {};
    const listed = own.choices ? Object.keys(own.choices) : [];
    const kind = listed.length ? 'choice' : primitive(prop);
    if (!kind) {
      if (required.has(key)) return null; // a list or an object the user would say in words
      continue;
    }
    if ((HIDDEN_KEYS.has(key) || own.hidden) && !required.has(key)) continue;
    fields.push({ key, label: own.label ?? fieldLabel(key), kind, required: required.has(key),
      choices: kind === 'choice' ? (listed.length ? listed : choicesOf(prop)) : [], said: own.choices ?? {},
      numeric: types(prop).some((t) => t === 'number' || t === 'integer'), initial: own.default,
      hint: (own.hint ?? '').trim(), ...(own.for ? { for: own.for } : {}) });
  }
  return { target, fields };
}

function roleOf(wire: CapabilitiesWire, id: string): Role | undefined {
  return wire.roles.find((r) => r.id === id);
}

/** The model a role uses now, with where it runs. */
function inUse(role: Role): { label: string; name: string; runs: string; step: ReadinessStep | null } | null {
  if (role.fixed) {
    const model = role.fallback?.active ? role.fallback : role.primary;
    return model ? { label: model.label, name: model.label, runs: model.runs ?? '', step: model.readiness?.next ?? null } : null;
  }
  const option = role.options.find((o) => o.id === role.current);
  return option ? { label: option.said || option.label, name: option.label, runs: option.runs ?? '', step: option.readiness?.next ?? null } : null;
}

/** File capabilities open their exact-input form; they never dispatch a live-view action. */
export function workflowTools(wire:CapabilitiesWire|null,query:string):Capability[] {
  const words=query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return (wire?.capabilities??[]).filter(c=>c.actor==='you'&&/^#workflows\?capability=[a-z0-9-]+$/.test(c.how.href??'')
    &&words.every(w=>`${c.title} ${c.what}`.toLowerCase().includes(w)));
}

/** Every action the panel offers, in its groups' order, from the capability list and the agent's tools here. A
 * capability whose tool is not here (what it acts on does not exist in this app) is left out. */
export function actionRows(wire: CapabilitiesWire, tools: ToolList): ActionRow[] {
  const order = new Map(ACTION_GROUPS.map((g, i) => [g.family, i]));
  const rows: ActionRow[] = [];
  for (const capability of wire.capabilities) {
    if(capability.how.href?.startsWith('#workflows?'))continue; // File inputs use the linked retained-file form.
    const name = capability.how.tool;
    if (capability.actor !== 'agent' || !name || !order.has(capability.family) || AGENT_ONLY.has(capability.id)) continue;
    const tool = tools[name];
    if (!tool) continue;
    // A capability the catalog added brings its own form: its declared parameters (the one door, ADR 0024).
    const form = capability.generated && capability.fields ? { target: null, fields: capability.fields as Field[] }
      : formOf(capability, tool);
    if (!form) continue;
    const roles = (capability.roles ?? []).map((id) => roleOf(wire, id)).filter((r): r is Role => !!r);
    const used = roles.map((r) => ({ role: r, model: inUse(r) }));
    const cloud = used.find((u) => u.role.kind === 'model' && u.model?.runs === 'cloud')?.model?.label ?? null;
    const step = capability.available ? null : used.find((u) => u.model?.step)?.model?.step ?? null;
    const choosing = roles.find((r) => RUN_WITH_ROLES.has(r.id) && r.kind === 'model' && !r.fixed);
    const options = choosing ? choosing.options.filter((o) => o.available && o.id !== 'off') : [];
    options.sort((a, b) => Number(b.id === choosing?.current) - Number(a.id === choosing?.current));
    const alternative = roles.length === 1 && options.length > 0;
    const entry = capability.generated && capability.catalog ? wire.models?.find((m) => m.id === capability.catalog) : undefined;
    const named = choosing ? choosing.options.find((o) => o.id === choosing.current && o.available)?.label ?? null
      : entry ? entry.name : used.find((u) => u.role.kind === 'model' && u.model)?.model?.name ?? null;
    const available = capability.available || alternative;
    rows.push({
      // An entry listed as a task of its own goes by that task (contract/actions.json `ownTasks`: "Find bright regions"),
      // its model named on the row.
      id: capability.id, title: OWN_TASKS[capability.id] || capability.title, what: capability.what, say: capability.how.say ?? '',
      group: capability.family, tool: name, query: tool.kind === 'query', target: form.target, fields: form.fields,
      available, chooseRequired: !capability.available && alternative,
      // Its readiness in the Toolbox's words ("Needs setup: Download 1.2 GB"), when that is why it can't run (L7b).
      why: capability.viaFallback || (capability.available ? '' : (capability as { said?: string }).said || capability.why),
      step, cloud, powers: powersOf(wire, capability), model: named,
      chooses: roles.find((r) => RUN_WITH_ROLES.has(r.id) && r.kind === 'model' && !r.fixed)?.id ?? null,
      runWith: choosing && options.length > 0
        ? { role: choosing.id, title: choosing.title, current: choosing.current,
            clouds: Object.fromEntries(options.filter((o) => o.runs === 'cloud').map((o) => [o.id, o.said || o.label])), options: options.map((o) => ({ id: o.id, label: o.said || o.label })) } : null,
    });
  }
  return rows.sort((a, b) => (order.get(a.group)! - order.get(b.group)!));
}

/** The words a search matches: its title, what it does, how it is asked and its ready methods. */
export function rowWords(row: ActionRow): string {
  return `${row.title} ${row.what} ${row.say} ${row.runWith?.options.map(option => option.label).join(' ') ?? ''}`;
}
/** Every task word of the search is among the row's declared words, matched by stem (task-search.ts). */
export function matches(row: ActionRow, query: string): boolean {
  const found = taskScore(rowWords(row), query);
  return found.matched === found.terms;
}

export interface Selected { id: string; label: string }

/** Why it can't run now, or null: not here (its step), a required field empty, or an outline it needs not selected. */
export function blocked(row: ActionRow, values: Record<string, unknown>, selected: Selected | null, use = ''): string | null {
  if (!row.available) return row.why || 'It can’t run here';
  if (row.chooseRequired && !row.runWith?.options.some((o) => o.id === use)) return 'Choose a ready model below';
  if (row.target?.required && !selected) return 'Select an outline in the view first';
  return parameterError(row.fields, values);
}

/** The `action` command's arguments: the fields filled in (numbers as numbers, empty ones left out) and the selected
 * outline as its target. Its presets are the server's to add. */
export function argsFor(row: ActionRow, values: Record<string, unknown>, selected: Selected | null): Record<string, unknown> {
  const args = parameterValues(row.fields, values);
  if (row.target && selected) args[row.target.key] = selected.id;
  return args;
}

/** What a step's button says (the Models page has the button that does it). A download names its size, so the
 * choice is made knowing what it costs (it used to live only in the tooltip). */
/** What "On …" names: a picture or recording by its whole title (before its credit: "Human cells" of "Human cells ·
 * David Root (CC0)"), never cut in code (the card's one line ends in an ellipsis, the whole name in its tooltip); the
 * camera's own words whole ("Live · iPhone", "Camera · needs attention"), so its state is never cut to one word. */
export function specimenOrCamera(words: string): string {
  return /^(Live\b|Camera\b|Test footage\b|Choose a specimen\b)/.test(words) ? words : words.split(' · ')[0].trim() || words;
}

/** "Results go to Main": the work Tab in view, by its name (the Tab row's own words), as This Tab says it on the run
 * page; on a researcher's Tab, which keeps its own work, it says to choose New Tab. */
export function destinationWords(): string {
  if (onResearcher()) return 'A researcher’s Tab keeps its own work: choose New Tab';
  const tab = tabName();
  return tab ? `Results go to ${tab}` : 'Results go to this Tab';
}

/** An input by its name, never its raw id: "cell_masks" says "cell masks". */
export function portWords(port: { id: string }): string {
  return port.id.replace(/[_-]+/g, ' ').trim() || 'input';
}

/** What a run that never reached the server says: in words, with the browser's own message kept after them. */
export function plainFailure(message: string): string {
  return /failed to fetch|networkerror|load failed|network request failed/i.test(message)
    ? `Hooke's server didn't answer, so nothing ran. Check that Hooke is still open, then run it again. (${message})`
    : message;
}

/** What a step's button says, in the server's own SI size words ("Download 1.3 GB": one download never shows two sizes). */
export function stepButton(step: ReadinessStep): string {
  return runStep(step).step?.short ?? (step.kind === 'weights' ? 'Made from your corrections' : 'Set up');
}

export interface ActionReply {
  comparison?: ComparisonResult;
  ok: boolean; summary?: string; error?: string; said?: string; table?: string; tableIdentity?: MeasurementPin;
  /** A This Tab run on the Stage's still (S5a, the server's `_placed`): 'specimen' while its outlines are checked on the
   * specimen or once drawn there (no window: kept in Data), 'tab' once its masks window was placed on its Tab instead.
   * Absent for every other run, placed as before. Later decisions come as `action-placed` (`placed`). */
  placed?: 'specimen' | 'tab';
  /** The user pressed Stop and its work ended (`action-cancel`); `stopping`: pressed, its work not ended yet. */
  stopped?: boolean; stopping?: boolean;
  /** What the Stop did (user_stop.report): what stopped, what was kept, what was discarded, what carries on. */
  /** `listed`: false when only the server's words came (a settlement), so its Kept list is not known here. */
  stop?: { outcome: string; words: string; kept: string[]; lost: string[]; carriesOn: string[]; listed?: boolean };
}

/** A result as the panel says it: what it measured in plain words when the server wrote them (tables.made_words), else
 * the tool's summary without the app's own ids ("(e1)", "(o3)": a step away, in Activity). */
/** A model-load line said in one line, its model with where it runs, then how long it usually takes: "Loading
 * Cellpose-SAM (on this Mac) · usually about 3.5 s" (the server's line, activity.py model_loading, puts the time in a
 * second pair of parentheses). Any other line as it is. */
export function loadLine(event: Pick<ActivityEvent, 'kind' | 'text' | 'ref'>): string {
  const model = event.kind === 'model' ? event.ref?.model ?? '' : '';
  if (!model) return event.text;
  const usual = /\((usually about [^)]+)\)\s*$/.exec(event.text)?.[1] ?? (/ for the first time\s*$/.test(event.text) ? 'the first time on this Mac' : '');
  return `Loading ${model}${usual ? ` · ${usual}` : ''}`;
}

/** After Stop, the footer's one line: "Stopped · nothing kept" or "Stopped · kept 2", from the server's Kept list when it
 * sent one (its whole sentence is in Activity, one press away); just "Stopped" when no list came. */
export function stoppedWords(reply: ActionReply | null | undefined): string {
  const stop = reply?.stop;
  if (!stop || stop.listed === false) return 'Stopped';
  return stop.kept.length ? `Stopped · kept ${stop.kept.length}` : 'Stopped · nothing kept';
}

export function resultWords(reply: ActionReply): string {
  if (!reply.ok) return reply.error || 'It did not work';
  // What the Stop did, as the server recorded it: what stopped, was kept, was discarded and carries on (user_stop.report).
  // The server's Stop summary is that report (commands._action); main.ts passes the summary, not `stop`.
  if (reply.stopped) return reply.stop?.words || reply.summary || 'Stopped';
  if (reply.stopping) return reply.stop?.words || reply.summary || "Stop requested: it hasn't ended yet";
  const words = reply.said || reply.summary || 'Done';
  return words.replace(/\s*\((?:[a-z]{1,2}\d+)\)/g, '').replace(/\s{2,}/g, ' ').trim();
}
export interface ActionsDeps {
  /** Sends `action` with the run's own id (`run`, what its Stop names) and resolves with its reply. */
  run: (capability: string, args: Record<string, unknown>, use: Record<string, string>, run: string, resource?: RetainedInput['reference'], volume?: string, inputs?: Record<string, TypedReference>, destination?: {newTab:boolean}) => Promise<ActionReply>;
  inputVolumes?: (session: string) => Promise<import('./volumespanel').VolumeEntry[]>;
  inputResources?: (session: string, capability?: string) => Promise<{ inputs: RetainedInput[]; typed?: TypedPort[] }>;
  /** Sends `action-cancel` for that run: the user's Stop. The action's own reply then says how it ended. */
  stop: (run: string) => Promise<{ ok: boolean; error?: string }>;
  /** The outline the user selected in the view, if any. */
  selected: () => Selected | null;
  /** The session open now (a cloud action asks once per session). */
  session: () => string;
  /** Opens the Models page, at a model when the step names one (`<id>/setup`: its setup sheet over the Project). */
  /** `opener`: the run page's task whose step opens a setup route (`<model>/setup`), so the sheet's Use it here comes back. */
  openModels: (model?: string, opener?: { task: string }) => void;
  /** Opens Activity in the side bar. */
  openActivity: () => void;
  /** Opens the Measurements page at an item (null: the newest). */
  openTable: (id: string | null, pin?: MeasurementPin) => void;
  /** Inspect an exact retained comparison result without changing acquisition or model defaults. */
  openComparisonMasks?: (id: string, pin: MeasurementPin, title: string) => Promise<void>;
  /** Sends a question to the Assistant, as if typed in its chat ("Ask the Assistant which to trust"). */
  ask?: (text: string) => void;
  /** Draws a comparison on the Stage: each model's marks in its own colour, each one hideable. */
  showComparison?: (view: ComparisonView) => void;
  /** The camera's own controls (the header's camera control's panel) and the camera page. */
  openCamera: () => void;
  cameraPage: () => string;
  /** Starts (or, `cancel`, stops) a model's download, as the Models page's Download does (downloads.py). Setting a model
   * up in place on its run page needs it; without it the step opens the setup sheet. */
  download?: (model: string, cancel?: boolean) => Promise<unknown>;
  /** Arms the next click or box on the Stage to outline with this option (the guided Try's click, capabilitytry.ts):
   * never a change of the default. A click-driven task's "Click in the view" needs it. */
  tryClicks?: (role: string, option: string) => Promise<void>;
  /** Whether that armed click is still waiting. */
  clicksArmed?: () => boolean;
  /** Whether the studio draws this population on the specimen now, placed by the Stage's still's fit (view.ts
   * `stillDrawn`): a finished run's footer says its outlines are on the specimen only then (S5a). */
  stillDrawn?: (population: string) => boolean;
}

interface RowState {
  open: boolean; options: boolean; values: Record<string, unknown>; use: string; running: boolean; result: ActionReply | null; asking: boolean;
  /** The run going now (its Stop names it), and whether Stop was pressed on it. A run stays the row's until it is
   * settled: its reply, or, when that said "stopping", the server's `action-settled` for it (`settled`). */
  run: string | null; stopping: boolean;
  /** Its reply has come; a settlement that came first (never expected: the reply is sent before it) waits in `early`. */
  replied: boolean; early: Settlement | null;
  volumeSession?: string; volumes?: import('./volumespanel').VolumeEntry[]; volume?: string;
  inputSession?: string; inputs?: RetainedInput[]; resource?: RetainedInput;
  typedPorts?: TypedPort[]; typedChosen?: Record<string, TypedReference>;
  newTabAfterConsent?: boolean;
  /** The model list is open in place (Change). */
  list: boolean;
  /** Not on this Mac, unfolded in that list. */
  away: boolean;
  /** The saved-input chooser is open on the On row. */
  choosing: boolean;
  /** Results go to: this Tab, or a new Tab; on a researcher's Tab only a new Tab chosen there counts. */
  dest: 'tab' | 'new'; destResearch: 'new' | '';
  /** Why Run did not start (a field to fill, an outline to select), said in the footer; the page stays ready. */
  blocked: string;
  /** The run on the studio's clock: when it started and ended, the Tab it ran on, and whether a new Tab gets it. */
  started: number; ended: number; tab: string; tabId: string; newTab: boolean;
  /** The model it ran with, short ("Bright objects"), its choice's id, and the run's latest Activity line. */
  ranWith: string; ranId: string; note: string;
  /** A click-driven task waiting for clicks: its own model's (every click), or one click armed with another model. */
  armed: boolean; armedTrial: boolean;
  /** The finished run's population once the studio drew it on the specimen (`stillDrawn`): said so from then on. */
  drawnOn?: string;
  /** Where the server placed this run's result when its window waited on the specimen (the reply's `placed`, then
   * `action-placed` for `placedRun`, the run this page shows, kept after it finished); 'tab' is final. */
  placedOn?: 'specimen' | 'tab'; placedRun?: string;
  /** A download being asked for (before its row shows), and a step that could not start. */
  starting: string; stepError: string;
  /** Each model's own values (a format-2 entry's fields are its own). */
  valuesBy: Map<string, Record<string, unknown>>;
  /** The task menu (a model serving four or more tasks) is open; What it runs on is shown (from the page's ⋯). */
  taskMenu: boolean; powers: boolean;
  /** The names Activity says the run's model by (its name, and its option's words with where it runs). */
  names: string[];
  /** Why a click could not be armed (the server's words): said in the Failed footer's anatomy. */
  armError: string;
}

/** The running footer's live line, as it is drawn and as each Activity line rewrites it in place (`activity`): the run's
 * latest line in one line (`loadLine`: "Loading Cellpose-SAM (on this Mac) · usually about 3.5 s"), or, once a Stop's
 * reply came, what the Stop did. One source for both, so the line never changes wording mid-run. */
function liveWords(state: RowState): string {
  return state.stopping && state.replied && state.result ? resultWords(state.result) : state.note;
}

/** How a run the user stopped really ended, once its work ended after its "stopping" reply (`action-settled`). */
export interface Settlement { outcome: 'stopped' | 'completed' | 'failed'; text: string; said?: string }

/** What a row says once its stopped run settles. */
export function settledReply(settlement: Settlement): ActionReply {
  return settlement.outcome === 'failed' ? { ok: false, error: settlement.text }
    : settlement.outcome === 'stopped' ? { ok: true, stopped: true, ...(settlement.said ? { stop: { outcome: 'stopped', words: settlement.said, kept: [], lost: [], carriesOn: [], listed: false } } : {}) }
    : { ok: true, said: settlement.said || settlement.text }; // what the Stop did, when the server said it (L7b)
}

/** What a row says when its connection was lost before its stop settled: how it ended is not known here. */
export const LOST_WHILE_STOPPING = "Can't tell how it ended: the connection was lost while it was stopping (Activity will say)";

/** One run's own id, made by the studio (crypto.randomUUID where the page has it). */
export function newRunId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  return c?.randomUUID ? c.randomUUID() : `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & Record<string, unknown> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith('data-') || key.startsWith('aria-') || key === 'role') node.setAttribute(key, String(value));
    else (node as unknown as Record<string, unknown>)[key] = value;
  }
  node.append(...children);
  return node;
}

/** A button with a class and a click (stopped from reaching the row under it). */
function act(text: string, className: string, run: () => void, props: Record<string, unknown> = {}): HTMLButtonElement {
  const b = el('button', { type: 'button', className, textContent: text, ...props });
  b.addEventListener('click', (event) => { event.stopPropagation?.(); run(); });
  return b;
}

/** The task's mark in Tools' fixed icon column, from its family and title (one icon set: tab-marks.ts). */
function taskMark(row: Pick<ActionRow, 'group' | 'title'>): SVGSVGElement {
  const t = row.title.toLowerCase();
  const has = (words: string) => new RegExp(`\\b(?:${words})`).test(t);
  const name: TabMark = has('count|how many') ? 'count' : has('camera') ? 'camera' : has('measur|table|size|how big') ? 'measure'
    : has('outline|cell|segment|mask|nucle') ? 'outline' : has('bright') ? 'bright'
    : has('follow|track|moves?\\b|motion|over time|trail') ? 'track' : has('find|search|pick') ? 'find'
    : has('clearer|sharp|enhance|enlarge|blur|smooth|background') ? 'clearer'
    : row.group === 'outlining' ? 'outline' : row.group === 'finding' ? 'find' : row.group === 'measuring' ? 'measure'
    : row.group === 'tracking' || row.group === 'time' ? 'track' : row.group === 'slide' ? 'slide' : row.group === 'naming' ? 'name' : 'look';
  return tabMark(name, 'study-tool-icon trow-icon');
}

/** The name of the work Tab in view (the Tab row's own words), or '' when none is known here. */
function tabName(): string {
  return typeof document !== 'undefined' ? document.querySelector?.('.workspace-view-tabs [aria-selected=true]')?.textContent?.trim() ?? '' : '';
}
const inWorkspace = () => typeof document !== 'undefined' && document.body?.dataset?.workspaceStudy === 'true';
const onResearcher = () => inWorkspace() && document.body.dataset.researchOpen === 'true';

/** The run page's phase: what its footer shows. */
type Phase = 'ready' | 'setup' | 'downloading' | 'running' | 'stopping' | 'armed' | 'done' | 'failed' | 'stopped' | 'comparing';

export class ActionsPanel {
  readonly root: HTMLElement;
  private readonly deps: ActionsDeps;
  private readonly search: HTMLInputElement;
  private readonly list: HTMLElement;
  private readonly view: HTMLElement;
  private rows: ActionRow[] = [];
  private rowMap = new Map<string, ActionRow>();
  private wire: CapabilitiesWire | null = null;
  private tools: ToolList = {};
  private readonly state = new Map<string, RowState>();
  /** Sessions in which the user agreed to send the view to a cloud model. */
  private readonly cloudAgreed = new Set<string>();
  private pending = false;
  private readonly comparison: ModelComparison;
  /** The task whose models are being compared now (its page shows the comparison), or null. */
  private comparing: string | null = null;
  private readonly controlWindows = new Map<string, ControlHandle>();
  /** By task (each task naming the model it will use) or By model (the Models page's entries, in its sections). */
  private mode: 'task' | 'model' = 'task';
  private readonly switcher: HTMLElement;
  private readonly models: HTMLElement;
  /** By model sections shown whole ("Show all N"). */
  private readonly whole = new Set<string>();
  /** The clock of a run's footer: one interval only while a run is under way or a click is armed. */
  private ticker: ReturnType<typeof setInterval> | null = null;

  constructor(root: HTMLElement, deps: ActionsDeps) {
    this.root = root;
    this.deps = deps;
    root.classList.add('actions-panel');
    // Results go to the Tab in view (a run keeps the Tab it was started from): its name follows the Tab row.
    const tabChanged = () => { this.updateVeil(); if ([...this.state.values()].some((s) => s.open)) this.render(); };
    if (typeof window !== 'undefined') window.addEventListener?.('workspace-tabs-changed', tabChanged);
    if (typeof MutationObserver !== 'undefined' && document.body) new MutationObserver(tabChanged).observe(document.body, { attributes: true, attributeFilter: ['data-research-open'] });
    this.search = el('input', { type: 'search', className: 'actions-search', placeholder: 'Search actions', autocomplete: 'off',
      'aria-label': 'Search actions' });
    this.search.addEventListener('input', () => this.render());
    // The view's own tools (the cells' layer, whole frame, spots, the map, clear) move in here (adoptTools).
    this.view = el('section', { className: 'actions-group actions-view', 'aria-label': 'The view' }, el('h3', { textContent: 'The view' }));
    this.list = el('div', { className: 'actions-list' });
    const foot = el('nav', { className: 'actions-foot', 'aria-label': 'Where actions come from' });
    const models = el('button', { type: 'button', textContent: 'Models' });
    models.addEventListener('click', () => deps.openModels());
    const all = el('a', { href: '#capabilities', textContent: 'All capabilities' });
    foot.append(models, all);
    this.comparison = new ModelComparison(deps);
    this.comparison.onCancel = () => { this.comparing = null; this.render(); };
    this.switcher = el('div', { className: 'tools-view mm-dtabs', role: 'group', 'aria-label': 'Show tools' });
    for (const [mode, words] of [['task', 'By task'], ['model', 'By model']] as const) {
      const b = el('button', { type: 'button', textContent: words, 'data-mode': mode });
      b.addEventListener('click', () => this.setMode(mode));
      this.switcher.append(b);
    }
    this.models = el('div', { className: 'tools-models', 'aria-label': 'Models, in their sections' });
    root.replaceChildren(this.switcher, this.search, this.view, this.list, this.models, foot);
    // A change of the list while the user types in a field waits until they leave it (their words are never redrawn away).
    root.addEventListener('focusout', () => queueMicrotask(() => { if (this.pending && !this.typing()) this.render(); }));
  }

  /** The dock's tool row, moved in as the panel's first group (its buttons keep their own handlers). */
  adoptTools(tools: HTMLElement): void {
    tools.classList.add('actions-tools');
    this.view.append(tools);
  }

  /** The capabilities message last given (the Stage chip names a Result's model from it). */
  capabilities(): CapabilitiesWire | null {
    return this.wire;
  }

  /** Whether a task (or a catalog entry's page) can be opened here. */
  has(id: string): boolean {
    return this.rowMap.has(id) || !!this.entryOf(id);
  }

  /** A run's name, for its pill on the Working strip: the model it runs with (the one chosen in `use`, else the one in
   * use), else the task's own title; a catalog entry's run (`catalog:<id>`) is that entry's name. */
  runName(id: string, use: Record<string, string> = {}): string {
    if (id.startsWith('catalog:')) { const model = id.slice(8); return this.wire?.models?.find(m => m.id === model)?.name ?? model; }
    const row = this.rows.find(r => r.id === id);
    if (!row) return id;
    const role = row.runWith?.role, chosen = role ? use[role] ?? row.runWith?.current : null;
    // The model's one name (its label), not the "(on this Mac)" form the Run with… list says it in.
    const option = role && chosen ? this.wire?.roles.find(r => r.id === role)?.options.find(o => o.id === chosen) : undefined;
    return option?.label || row.model || row.title;
  }

  get viewMode(): 'task' | 'model' {
    return this.mode;
  }

  setMode(mode: 'task' | 'model'): void {
    this.mode = mode;
    this.render();
    this.root.dispatchEvent(new Event('tools-mode'));
  }

  setCapabilities(wire: CapabilitiesWire): void {
    this.wire = wire;
    this.comparison.update(wire);
    this.rebuild();
  }

  setTools(tools: ToolList): void {
    this.tools = tools;
    this.rebuild();
  }

  /** What is on the Stage now, as the header says it ("Live · iPhone 17 Pro", "cells.png", "No camera"). */
  private inputWords = '';
  /** The Stage's input changed: opened pages say what is on it (drawn again only when it changed). */
  setInput(words: string): void {
    if (words === this.inputWords) return;
    this.inputWords = words;
    this.render();
  }

  /** The selection changed: pages that act on it say what they act on. */
  selectionChanged(): void {
    this.comparison.update(this.wire);
    this.render();
  }

  rowsShown(): ActionRow[] {
    // Described tasks ("segmentation") find tools by their declared words; with no row having every word, the closest show.
    return findByTask(this.rows, rowWords, this.search.value).rows;
  }

  /** An Activity line (main.ts relays each): a run under way says its latest one under its clock ("Loading micro-sam,
   * usually about 2 min"); only lines of that run, or its own model loading since it started (the line names that
   * model: a researcher's or another run's load is never said under this run), are taken. */
  /** Whether this model-load line is a hand-started run's own (its model, loading since the run started): the run's
   * pill and footer carry it, so the strip makes no second pill for it (strip-sources.ts LoadPills). */
  claimsLoad(event: ActivityEvent): boolean {
    return [...this.state.values()].some((state) => state.running && !!state.run && this.loadOf(state, event));
  }

  private loadOf(state: RowState, event: ActivityEvent): boolean {
    // Activity names a model as `use` does ("micro-sam (light microscopy) on this Mac"): the option's own words, or
    // its name with where it runs; never a prefix ("Cellpose-SAM" is not "Cellpose-SAM fine-tuned …").
    const named = event.ref?.model ?? '';
    const bare = named.replace(/\s*\((?:on this Mac|in the cloud)\)$|\s+on this Mac$/, '');
    const its = !!named && state.names.some((n) => n === named || n === bare);
    return event.kind === 'model' && event.state === 'running' && event.at * 1000 >= state.started - 1000 && its;
  }

  activity(event: ActivityEvent): void {
    for (const [id, state] of this.state) {
      if (!state.running || !state.run) continue;
      const mine = event.details?.ids?.run === state.run;
      const loading = this.loadOf(state, event);
      if (!mine && !loading) continue;
      state.note = loadLine(event);
      const said = liveWords(state);
      for (const host of this.hostsOf(id)) host.querySelectorAll<HTMLElement>('.run-note').forEach((n) => { if (n.dataset.live) { n.textContent = said; n.hidden = !said; } });
    }
  }

  /** Use it here (Models, Tools By model): the first task this model serves opens with it chosen (tools-catalog.ts
   * `modelTasks`; the one where it is the current choice first). A catalog entry that has no capability yet opens its
   * own page, with its step. False when it serves no task here. */
  useModel(model: string, task?: string): boolean {
    const entry = this.wire?.models?.find((m) => m.id === model);
    if (!entry) return false;
    // A task named (Models' Details: Try on <specimen> for one of its tasks) opens that task's page; else its first.
    const served = this.servedBy(model);
    const first = (task ? served.find((t) => t.task === task) : undefined) ?? served[0];
    if (first) { this.open(first.task, first.choice ?? undefined); return true; }
    if (entry.newKind && !this.rowMap.has(`catalog:${model}`)) { this.open(`catalog:${model}`); return true; }
    return false;
  }

  /** The tasks a model serves here, in Use it here's order, by their names (Models: Use it here and Try on … show only
   * where they lead to a run page). */
  served(model: string): { task: string; title: string }[] {
    return this.servedBy(model).map((t) => ({ task: t.task, title: t.title }));
  }

  /** What is on the Stage, by its name as the run page's On row says it ("Human cells"); '' when nothing named is. */
  stageName(): string {
    const words = this.inputWords.trim();
    if (!words || /^(Live\b|Camera\b|Test footage\b|Choose a specimen\b|No camera\b)/.test(words)) return '';
    // The Stage bar's short name (camera-label.ts), without a trailing kind the title carries ("… (picture)").
    return specimenName(specimenOrCamera(words).replace(/\s*\((?:picture|recording|video|still)\)$/i, ''));
  }

  /** The tasks a model serves here, in Use it here's order. */
  private servedBy(model: string): ModelTask[] {
    const entry = this.wire?.models?.find((m) => m.id === model);
    if (!entry) return [];
    return orderServed(this.wire, model, modelTasks(entry, this.rows.map((r) => ({ id: r.id, title: r.title, available: r.available, runWith: r.runWith }))));
  }

  /** A download a run page started, shown again (the strip's download pill opens it). */
  showDownload(model: string): boolean {
    for (const [id, state] of this.state) {
      if (!state.open) continue;
      const row = this.rowMap.get(id);
      const spec = row ? this.specOf(row, state) : null;
      if (spec?.chosen?.model === model || (spec?.chosen?.step?.model === model)) { this.controlWindows.get(id)?.show(); return true; }
    }
    return false;
  }

  private rebuild(): void {
    const rows = this.wire ? actionRows(this.wire, this.tools) : [];
    // Click-driven tasks (Click to outline): the user's own gesture with a model to choose, when the studio can arm a click.
    if (this.wire && this.deps.tryClicks) rows.push(...gestureRows(this.wire));
    const order = new Map(ACTION_GROUPS.map((g, i) => [g.family, i]));
    this.rows = rows.map((r, i) => ({ r, i })).sort((a, b) => (order.get(a.r.group) ?? 99) - (order.get(b.r.group) ?? 99) || a.i - b.i).map(({ r }) => r);
    this.rowMap = new Map(this.rows.map((r) => [r.id, r]));
    this.render();
  }

  private typing(): boolean {
    const active = document.activeElement;
    return !!active && (this.list.contains(active)||[...this.controlWindows.values()].some(control=>control.options.content.contains(active))) && (active.tagName === 'INPUT' || active.tagName === 'SELECT' || active.tagName === 'TEXTAREA');
  }

  /** Opens a task's run page, with a model chosen for it when `choice` names one (never a change of the default). An
   * entry's own capability that implements a task opens that task with it chosen: one page for one thing. */
  open(id: string, choice?: { role: string; option: string }): void {
    const row = this.rowMap.get(id);
    if (row && !choice) {
      const host = implementsTask(this.wire, row, this.rows);
      const option = host ? taskChoices(this.wire, host, this.rowMap).find((c) => c.model === row.id.slice(8)) : undefined;
      if (host && option?.runnable) { this.open(host.id, { role: option.role, option: option.option }); return; }
    }
    if (choice && (!row?.runWith || row.runWith.role !== choice.role || !row.runWith.options.some(o => o.id === choice.option)))
      throw new Error('That exact model is no longer available for this operation. Check its setup and try again.');
    if (!row && !this.entryOf(id)) { this.deps.openModels(); return; }
    this.mode = 'task';
    const state = this.stateOf(id);
    state.open = true; state.list = false;
    if (choice) state.use = choice.option;
    this.render();
    const control = this.controlWindows.get(id);
    if (control) control.show(); else this.root.scrollIntoView?.({ block: 'nearest' });
  }

  private stateOf(id: string): RowState {
    let state = this.state.get(id);
    if (!state) {
      state = { open: false, options: false, values: {}, use: '', running: false, result: null, asking: false, run: null, stopping: false,
        replied: false, early: null, list: false, away: false, choosing: false, dest: 'tab', destResearch: '', blocked: '',
        started: 0, ended: 0, tab: '', tabId: '', newTab: false, ranWith: '', ranId: '', note: '', armed: false, armedTrial: false, starting: '', stepError: '',
        valuesBy: new Map(), taskMenu: false, powers: false, names: [], armError: '' };
      this.state.set(id, state);
    }
    return state;
  }

  /** A catalog entry with no capability here yet (a kind not set up), whose own page names its step. */
  private entryOf(id: string): ModelRow | undefined {
    if (!id.startsWith('catalog:') || this.rowMap.has(id)) return undefined;
    return this.wire?.models?.find((m) => `catalog:${m.id}` === id && !!m.newKind);
  }

  /** The spec of a task's page, for the model chosen on it. */
  private specOf(row: ActionRow, state: RowState): RunSpec {
    return runSpec(this.wire, row, state.use, this.rowMap);
  }

  /** The values the chosen model's fields read (a format-2 entry's own, else the task's). */
  private valuesOf(state: RowState, spec: RunSpec): Record<string, unknown> {
    if (!spec.chosen?.fields) return state.values;
    let values = state.valuesBy.get(spec.chosen.id);
    if (!values) { values = {}; state.valuesBy.set(spec.chosen.id, values); }
    return values;
  }

  /** The row whose tool, form and kind a run uses: the task's, or the chosen entry's own capability. */
  private runRow(row: ActionRow, spec: RunSpec): ActionRow {
    return this.rowMap.get(spec.run.capability) ?? row;
  }

  render(): void {
    if (this.typing()) { this.pending = true; return; }
    this.pending = false;
    // Open pages get their control window first, so the list knows which pages sit under their rows (no deck here).
    for (const [id, state] of this.state) if (state.open) this.controlFor(id);
    // Entries implementing another task's model choice are found there (one page for one thing), not as a second row.
    const listed = this.rows.filter((r) => !implementsTask(this.wire, r, this.rows));
    const found = findByTask(listed, rowWords, this.search.value);
    const shown = found.rows;
    const groups: Node[] = [];
    // Closest guesses are said to be guesses (coordinator review of #363): none of these does every word asked for.
    this.root.dataset.taskPartial = String(found.partial && shown.length > 0);
    if (found.partial && shown.length) groups.push(el('p', { className: 'actions-partial', role: 'status',
      textContent: `Closest matches: none does all of “${this.search.value.trim()}”.` }));
    const ready = shown.filter((r) => r.available);
    // Common first: the Measurements table, then what a researcher reaches for most (contract/actions.json `common`).
    const common = COMMON.map((id) => ready.find((r) => r.id === id)).filter((r): r is ActionRow => !!r);
    const searching = this.search.value.trim().toLowerCase();
    const tableRow = !searching || searching.split(/\s+/).every((w) => TABLE_WORDS.includes(w));
    if (common.length || tableRow) groups.push(this.section('Common', [...(tableRow ? [this.drawTableRow()] : []), ...common.map((r) => this.drawRow(r))]));
    for (const group of ACTION_GROUPS) {
      const rows = ready.filter((r) => r.group === group.family && !COMMON.includes(r.id));
      if (rows.length) groups.push(this.section(group.title, rows.map((r) => this.drawRow(r))));
    }
    const fileTools=workflowTools(this.wire,this.search.value);
    if(fileTools.length)groups.push(this.section('Scientific files',fileTools.map(c=>{
      const link=el('a',{className:'trow-hit','aria-label':`Open ${c.title}`,href:c.how.href!});
      return el('div',{className:'trow','data-action':c.id,'data-available':String(c.available)},
        el('span',{className:'trow-lead'},tabMark('output')),
        el('span',{className:'trow-words'},el('span',{className:'trow-title',textContent:c.title}),
          el('span',{className:'trow-sub',textContent:'Retained Project files · check inputs and readiness'})),link);
    })));
    // Can be set up: tasks that can't run here yet, each with its one step, and catalog kinds not set up yet (the mock's
    // "Can be set up" waits at the bottom).
    const waiting = shown.filter((r) => !r.available);
    const kinds = (this.wire?.models ?? []).filter((m) => m.newKind && m.status !== 'ready' && modelSection(m) !== 'away'
      && !this.rowMap.has(`catalog:${m.id}`) && this.found(`${m.name} ${m.what}`));
    if (waiting.length || kinds.length) {
      groups.push(this.section('Can be set up', [...waiting.map((r) => this.drawRow(r)), ...kinds.map((m) => this.drawKindRow(m))], waiting.length + kinds.length));
    }
    if (!this.search.value.trim() || 'camera'.includes(this.search.value.trim().toLowerCase())) groups.push(this.drawCamera());
    if (!groups.length) groups.push(el('p', { className: 'actions-none', textContent: this.wire ? 'No action matches.' : 'Loading the actions…' }));
    this.list.replaceChildren(...groups);
    const byModel = this.mode === 'model';
    this.switcher.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === this.mode)));
    this.list.hidden = byModel;
    this.view.hidden = byModel;
    this.models.hidden = !byModel;
    if (byModel) this.models.replaceChildren(...this.drawModels());
    // Open pages: each in its control window (the workspace); anywhere else drawRow put it under its row.
    for (const [id, state] of this.state) if (state.open && this.controlWindows.has(id)) this.fillControl(id);
    for (const [id, control] of this.controlWindows) if (!this.rowMap.has(id) && !this.entryOf(id))
      control.options.content.replaceChildren(el('p', { role: 'status', textContent: 'This tool is no longer available in the current catalog. Reopen Tools to choose an available capability. Existing work is retained.' }));
    this.tick();
  }

  private section(title: string, rows: Node[], count?: number): HTMLElement {
    const head = el('h3', { className: 'mm-dsection' }, title);
    if (count !== undefined) head.append(' ', el('span', { className: 'mm-num', textContent: String(count) }));
    return el('section', { className: 'actions-group', 'aria-label': title }, head, ...rows);
  }

  /** The search's words, for the entries (By model, kinds to set up). */
  private found(text: string): boolean {
    const words = this.search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return words.every((w) => text.toLowerCase().includes(w));
  }

  /** One row of Tools (the mock's trow): a fixed icon column, the task's name, the model it will use beside its lit
   * cell and "+n more", then a chevron, or its one step when it can't run here, or Stop while it runs. */
  private drawRow(row: ActionRow): HTMLElement {
    const state = this.stateOf(row.id);
    const spec = this.specOf(row, state);
    const box = el('div', { className: 'trow', 'data-action': row.id, 'data-available': String(row.available), 'data-family': row.group,
      'data-open': String(state.open) });
    const sub = el('span', { className: 'trow-sub' });
    const chosen = spec.chosen;
    if (state.running) sub.append(litCell(state.stopping ? 'still' : 'working', { size: 11, decorative: true }), el('b', { textContent: state.stopping ? 'Stop requested' : `Running ${state.ranWith}` }));
    else if (chosen) {
      // The model by its short name ("Bright objects"), its whole name in the tooltip; "+n more", or "built in".
      sub.append(litCell(chosen.cell, { size: 11, label: chosen.cellWord }), el('b', { textContent: chosen.short, title: chosen.name }));
      const more = spec.choices.length - 1;
      if (more > 0) sub.append(el('span', { textContent: `+${more} more` }));
      else if (chosen.where.startsWith('Built in')) sub.append(el('span', { textContent: 'built in' }));
    } else sub.append(litCell(row.available ? 'ready' : 'idle', { size: 11, label: row.available ? 'Ready' : 'Not set up' }), el('b', { textContent: 'Built in' }));
    const words = el('span', { className: 'trow-words' }, el('span', { className: 'trow-title', textContent: row.title }), sub);
    box.append(el('span', { className: 'trow-lead' }, taskMark(row)), words);
    const step = !row.available ? (chosen?.step ?? spec.step ?? runStep(row.step).step) : null;
    if (state.running && state.run) box.append(act(state.stopping ? 'Stop requested' : 'Stop', 'trow-step mm-btn action-stop', () => void this.stop(state), { disabled: state.stopping, 'aria-label': `Stop: ${row.title}` }));
    else if (step) box.append(act(step.short, 'trow-step mm-btn', () => this.doStep(row.id, step), { title: step.says }));
    else box.append(el('span', { className: 'trow-chev', 'aria-hidden': 'true' }, tabMark('right', 'trow-chev-icon')));
    const hit = act('', 'trow-hit action-words', () => this.flip(row.id), { 'aria-label': `${row.available ? 'Open' : 'Set up'} ${row.title}`, 'aria-expanded': String(state.open) });
    hit.title = row.available ? row.what : (row.step?.says || row.why || row.what);
    box.append(hit);
    if (!this.controlWindows.get(row.id) && state.open) box.append(this.drawPage(row.id));
    return box;
  }

  /** A catalog kind not set up yet, listed under Can be set up with its step: its page names that step. */
  private drawKindRow(model: ModelRow): HTMLElement {
    const id = `catalog:${model.id}`;
    const spec = entrySpec(this.wire, model);
    const chosen = spec.chosen;
    const box = el('div', { className: 'trow', 'data-action': id, 'data-available': 'false', 'data-entry': model.id });
    const sub = el('span', { className: 'trow-sub' }, litCell(chosen?.cell ?? 'idle', { size: 11, label: chosen?.cellWord ?? 'Not set up' }),
      el('b', { textContent: model.name }));
    box.append(el('span', { className: 'trow-lead' }, taskMark({ group: '', title: `${model.name} ${model.what}` })),
      // Its title is its "what" sentence until the catalog gives entries a short task name (proposed to the catalog lane):
      // one line with an ellipsis, its whole words in the tooltip, never two lines.
      el('span', { className: 'trow-words' }, el('span', { className: 'trow-title trow-title-line', textContent: model.what || model.name, title: model.what || model.name }), sub));
    const step = chosen?.step;
    if (step) box.append(act(step.short, 'trow-step mm-btn', () => this.doStep(id, step), { title: step.says }));
    else box.append(el('span', { className: 'trow-chev', 'aria-hidden': 'true' }, tabMark('right', 'trow-chev-icon')));
    box.append(act('', 'trow-hit action-words', () => this.openKind(model), { 'aria-label': `Set up ${model.name}`, 'aria-expanded': String(this.stateOf(id).open) }));
    if (!this.controlWindows.get(id) && this.stateOf(id).open) box.append(this.drawPage(id));
    return box;
  }

  /** A kind not set up yet: the task it implements opens with it chosen (its step is that page's primary); a kind of a
   * job no task here does opens its own page. */
  private openKind(model: ModelRow): void {
    const host = this.rows.find((t) => t.chooses && t.chooses === model.newKind?.operation && !implementsTask(this.wire, t, this.rows));
    if (!host) { this.flip(`catalog:${model.id}`); return; }
    const state = this.stateOf(host.id);
    state.use = model.id; state.open = true; state.list = false; state.result = null; state.blocked = '';
    this.mode = 'task';
    this.render();
    this.controlWindows.get(host.id)?.show();
  }

  /** A row's click: its page (shown again when it is open elsewhere). */
  private flip(id: string): void {
    const control = this.controlWindows.get(id);
    const state = this.stateOf(id);
    if (control && state.open) { control.show(); return; }
    state.open = control ? true : !state.open;
    this.render();
    this.controlWindows.get(id)?.show();
  }

  /** By model: the Models page's entries, in its sections and counts (Needs you, Ready, Not checked, Can be set up, then
   * Not on this Mac folded and the New ones), five each until Show all; each row its lit cell and its next step. */
  private drawModels(): HTMLElement[] {
    const rows = (this.wire?.models ?? []).filter((m) => this.found(`${m.name} ${m.what} ${kindWords(m)}`));
    if (!this.wire) return [el('p', { className: 'tools-note', textContent: 'Loading the models…' })];
    const out: HTMLElement[] = [];
    for (const [section, title] of MODEL_SECTIONS) {
      const all = rows.filter((m) => modelSection(m) === section);
      if (!all.length) continue;
      const shown = section === 'away' || this.whole.has(section) ? all : all.slice(0, 5);
      const head = el('h3', { className: 'mm-dsection' }, title, ' ', el('span', { className: 'mm-num', textContent: String(all.length) }));
      const items = shown.map((m) => this.drawModel(m));
      if (section === 'away') {
        const fold = el('details', { className: 'tools-section tools-away' }, el('summary', { className: 'mm-dsection' }, title, ' ', el('span', { className: 'mm-num', textContent: String(all.length) })), ...items);
        out.push(fold);
        continue;
      }
      const block = el('section', { className: `tools-section tools-${section}`, 'aria-label': title, 'data-section': section }, head, ...items);
      if (shown.length < all.length) block.append(act(`Show all ${all.length}`, 'mm-show-all', () => { this.whole.add(section); this.render(); }));
      out.push(block);
    }
    const news = (this.wire.setup?.news?.new ?? []).filter((n) => this.found(`${n.name} ${n.what}`));
    if (news.length) {
      out.push(el('section', { className: 'tools-section tools-new', 'aria-label': 'New' },
        el('h3', { className: 'mm-dsection' }, 'New ', el('span', { className: 'mm-num', textContent: String(news.length) })),
        ...news.map((n) => {
          const box = el('div', { className: 'trow trow-model', 'data-entry': n.key, 'data-status': 'new' });
          box.append(el('span', { className: 'trow-lead' }, litCell('idle', { size: 13, label: 'New' })),
            el('span', { className: 'trow-words' }, el('span', { className: 'trow-title', textContent: n.name }),
              el('span', { className: 'trow-sub' }, el('span', { textContent: `${n.badge === 'reviewed' ? 'Reviewed' : 'Community'} · by ${n.by}` }))),
            act('Details', 'trow-step mm-btn', () => this.deps.openModels(n.key), { title: 'Open its card in Models' }));
          return box;
        })));
    }
    if (!out.length) out.push(el('p', { className: 'tools-note', textContent: 'No model matches.' }));
    return out;
  }

  /** One entry: its lit cell, name and one quiet line (where it runs, or its download, or why it is quiet), its next
   * step, and a click that uses it here (its first task's page with it chosen) or opens its card in Models. */
  private drawModel(model: ModelRow): HTMLElement {
    const section = modelSection(model);
    const face = faceState(model, this.wire?.setup);
    const download = model.download && ['queued', 'downloading', 'checking'].includes(model.download.state) ? model.download : null;
    const look = download ? { cell: 'setup' as LitCellState, word: 'Downloading' } : sectionCell(section);
    const tasks = section === 'ready' || section === 'unchecked' ? this.servedBy(model.id) : [];
    const { step, reason } = section === 'ready' ? { step: null, reason: '' } : section === 'unchecked'
      ? { step: model.face?.action?.kind === 'check' ? runStep({ kind: 'check', says: model.face.action.says }, model.id).step : null, reason: '' }
      : (({ step, reason }) => ({ step: wholeSetup(step, model.steps, shortName(model.name), model.id).step, reason }))(runStep(model.next, model.id));
    const sub = download ? (download.total ? `Downloading · ${progressWords(download.got, download.total)}` : 'Downloading')
      : reason || (tasks.length || section !== 'ready' && section !== 'unchecked' ? whereWords(this.wire, model).replace(' · free', '') : 'Used by the Assistant and researchers');
    const box = el('div', { className: `trow trow-model${reason ? ' is-quiet' : ''}`, 'data-entry': model.id, 'data-status': model.status, 'data-section': section });
    box.append(el('span', { className: 'trow-lead' }, litCell(look.cell, { size: 13, label: look.word, fraction: download?.total ? download.got / download.total : null })),
      el('span', { className: 'trow-words' }, el('span', { className: 'trow-title', textContent: model.name, title: face.says }),
        el('span', { className: 'trow-sub' }, el('span', { textContent: sub }))));
    if (download && this.deps.download) box.append(act('Cancel', 'trow-step mm-btn', () => void this.deps.download!(model.id, true).then(() => this.render(), () => this.render()), { title: `Stop downloading ${model.name}` }));
    // Its one step, Check it here included: the setup sheet (Run the check), as the Models card does; never Details, whose
    // Try it on does not record the check.
    else if (step) box.append(act(step.short, 'trow-step mm-btn', () => this.doStep(`model:${model.id}`, step), { title: step.says }));
    else if (!reason) box.append(el('span', { className: 'trow-chev', 'aria-hidden': 'true' }, tabMark('right', 'trow-chev-icon')));
    if (!reason) {
      const label = tasks.length ? `Use ${model.name} here` : `Open ${model.name} in Models`;
      box.append(act('', 'trow-hit', () => { if (!tasks.length || !this.useModel(model.id)) this.deps.openModels(model.id); }, { 'aria-label': label, title: tasks.length ? `${tasks[0].title} with ${model.name}` : face.says }));
    }
    return box;
  }

  /** A model's one step: a download the page starts in place (and shows filling), else its setup sheet over the Project,
   * which comes back here when it closes. */
  private doStep(id: string, step: RunStep): void {
    const state = this.stateOf(id);
    state.stepError = '';
    if (step.does === 'download' && this.deps.download) {
      state.starting = step.model;
      this.render();
      // "Starting the download…" until the server's download row shows it (then the primary is its progress), or 15 s.
      const settle = () => { if (state.starting === step.model) { state.starting = ''; this.render(); } };
      void this.deps.download(step.model).then(() => { setTimeout(settle, 15_000); },
        (error: unknown) => { state.starting = ''; state.stepError = plainFailure(error instanceof Error ? error.message : String(error)); this.render(); });
      return;
    }
    // From a task's run page or row, the sheet's Use it here comes back to that task; from Tools By model, to its first.
    this.deps.openModels(`${step.model}/setup`, id.startsWith('model:') ? undefined : { task: id });
  }

  /** The tasks a row's chosen model serves (the task switch under the header), when it serves more than one. */
  private switchFor(row: ActionRow | undefined, spec: RunSpec): ModelTask[] {
    const model = spec.chosen?.model;
    if (!row || !model) return [];
    // An entry on its own task's page (Find bright regions) offers no switch to the job it is also a choice of: that
    // page is what it is listed as.
    if (row.id === `catalog:${model}` && OWN_TASKS[row.id]) return [];
    const served = this.servedBy(model).filter((t) => t.choice || t.task === `catalog:${model}`);
    return served.length > 1 && served.some((t) => t.task === row.id) ? served : [];
  }

  /** An open page's control window, made on first open (none where no deck is configured: the page sits under its row). */
  private controlFor(id: string): ControlHandle | undefined {
    const row = this.rowMap.get(id);
    const entry = row ? undefined : this.entryOf(id);
    if (!row && !entry) return this.controlWindows.get(id);
    let control = this.controlWindows.get(id);
    if (control) return control;
    const content = el('div', { className: 'run-host' }); this.root.append(content);
    control = dockControls({ id: `action:${id}`, title: row?.title ?? entry!.name, content, rememberOpenPerScope: true });
    if (!control) { content.remove(); return undefined; }
    control.options.content.addEventListener('focusout', () => queueMicrotask(() => { if (this.pending && !this.typing()) this.render(); }));
    this.controlWindows.set(id, control);
    return control;
  }

  /** Draws an open page into its control window, keeping the focused control focused across the redraw. */
  private fillControl(id: string): void {
    const control = this.controlWindows.get(id);
    if (!control || (!this.rowMap.has(id) && !this.entryOf(id))) return;
    const page = this.drawPage(id);
    const active = document.activeElement as HTMLElement | null;
    const focused = active && control.options.content.contains(active) ? active.dataset?.focus : undefined;
    control.options.content.replaceChildren(page);
    if (focused) page.querySelector<HTMLElement>(`[data-focus="${focused}"]`)?.focus?.({ preventScroll: true });
    control.setActions?.(this.pageActions(id));
  }

  /** The page's own items in its ⋯: Compare… (a task that compares, two or more ready models) and its model in Models. */
  private pageActions(id: string): { label: string; run: () => void; title?: string; disabled?: boolean }[] {
    const row = this.rowMap.get(id);
    const state = this.stateOf(id);
    const items: { label: string; run: () => void; title?: string; disabled?: boolean }[] = [];
    if (row && this.canCompare(row, state)) items.push({ label: 'Compare models…', run: () => { this.comparing = row.id; this.render(); this.controlWindows.get(id)?.show(); }, title: 'Up to 3 models on the same picture, each in its own colour' });
    const spec = row ? this.specOf(row, state) : this.entryOf(id) ? entrySpec(this.wire, this.entryOf(id)!) : null;
    const model = spec?.chosen?.model;
    if (model) items.push({ label: `${spec!.chosen!.short} in Models`, run: () => this.deps.openModels(model), title: 'Its card in Models: what it is, its facts and its setup' });
    if (row) items.push({ label: state.powers ? 'Hide what it runs on' : 'What it runs on', title: 'Each model it runs on by its job, its methods and its algorithms',
      run: () => { state.powers = !state.powers; this.render(); this.controlWindows.get(id)?.show(); if (state.powers) this.hostsOf(id)[0]?.querySelector('.run-powers')?.scrollIntoView?.({ block: 'nearest' }); } });
    return items;
  }

  private canCompare(row: ActionRow, state: RowState): boolean {
    const models = this.taskModelsOf(row).filter((m) => m.choosable && m.ready && row.runWith?.options.some((o) => o.id === m.option));
    return comparable(row.chooses ?? undefined) && models.length >= 2 && !state.running && this.comparing !== row.id;
  }

  /** The models that can do a task, as the comparison lists them. */
  private taskModelsOf(row: ActionRow): TaskModel[] {
    const role = row.chooses;
    return taskModels(this.wire, row.id, role, this.stateOf(row.id).use || (role ? lastUsed(role) : null));
  }

  private phaseOf(row: ActionRow | undefined, state: RowState, spec: RunSpec): Phase {
    if (row && this.comparing === row.id) return 'comparing';
    if (state.running) return state.stopping ? 'stopping' : 'running';
    if (state.armed) return 'armed';
    // A run that didn't run belongs to the Tab it was asked on: on another Tab the row is ready to run there.
    if (state.result && !state.result.ok && !state.result.stopped && state.tabId && typeof document !== 'undefined'
      && (document.body?.dataset?.activeWorkspaceView ?? state.tabId) !== state.tabId) state.result = null;
    if (state.result) return state.result.stopped ? 'stopped' : state.result.ok ? 'done' : 'failed';
    const chosen = spec.chosen;
    if (chosen?.download || chosen?.runnable) state.starting = '';
    if (chosen && !chosen.runnable) return chosen.download ? 'downloading' : 'setup';
    if (!chosen && row && !row.available) return 'setup';
    return 'ready';
  }

  /** The run page (the mock's runPage): what it does, On, With and its models in place, Makes, the declared Settings,
   * More options, Results go to, and a sticky footer whose one primary spans it. */
  private drawPage(id: string): HTMLElement {
    const row = this.rowMap.get(id);
    const entry = row ? undefined : this.entryOf(id);
    const state = this.stateOf(id);
    const spec = row ? this.specOf(row, state) : entrySpec(this.wire, entry!);
    const phase = this.phaseOf(row, state, spec);
    const busy = phase === 'running' || phase === 'stopping';
    const page = el('div', { className: 'run', 'data-action': id, 'data-phase': phase });
    const tasks = this.switchFor(row, spec);
    if (tasks.length) page.append(this.drawTaskSwitch(id, tasks));
    const body = el('div', { className: 'run-body' });
    body.append(el('p', { className: 'run-what', textContent: spec.what }));
    if (phase === 'comparing' && row) {
      body.append(this.drawComparison(row, state));
      page.append(body);
      return page;
    }
    for (const input of spec.inputs) body.append(this.drawInput(id, row, state, spec, input, busy));
    if (spec.chosen && (spec.choices.length > 1 || spec.chosen.model || spec.chosen.role)) body.append(this.drawWith(id, state, spec, busy));
    if (state.list && spec.choices.length > 1) body.append(this.drawModelList(id, state, spec));
    // Where the picture goes, said before any key is added (the mock's run-key frame).
    if (spec.chosen?.cloud) body.append(el('p', { className: 'run-quiet', textContent: `Sends this picture to ${spec.chosen.cloud}, billed per call` }));
    if (spec.makes) body.append(el('div', { className: 'run-line run-makes-line' }, el('span', { className: 'run-key', textContent: 'Makes' }), el('span', { className: 'run-makes', textContent: spec.makes })));
    body.append(el('div', { className: 'run-hr' }));
    const values = this.valuesOf(state, spec);
    // A chosen entry's own fields keep its own values; a task's field shown with them (its run goes through the task)
    // keeps the task's.
    const own = new Set(spec.chosen?.fields?.map((f) => f.key) ?? []);
    const valuesFor = (field: Field) => (spec.chosen?.fields && !own.has(field.key) ? state.values : values);
    const fields = el('div', { className: 'run-fields' });
    if (spec.fields.length) {
      fields.append(el('h3', { className: 'run-h', textContent: 'Settings' }));
      // Declared but not changeable yet (a catalog entry not set up): shown disabled, with what to do first.
      for (const field of spec.fields) fields.append(this.field(id, field, valuesFor(field), busy || !!spec.locked));
      if (spec.locked) fields.append(el('p', { className: 'run-quiet run-locked', textContent: spec.locked }));
    } else if (spec.none) fields.append(el('p', { className: 'run-hline' }, el('b', { textContent: 'Settings' }), el('span', { textContent: spec.none })));
    if (busy && (spec.fields.length || spec.advanced.length)) fields.append(el('p', { className: 'run-quiet', textContent: 'Changes apply to the next run' }));
    if (fields.children.length) body.append(fields);
    // More options only when the chosen model declares advanced fields; What it runs on is the page's ⋯ item.
    if (spec.advanced.length) body.append(this.drawMore(id, state, spec, valuesFor, busy));
    // Results go to for every run (a query's too: on a researcher's Tab only a New Tab chosen here can run it); a click
    // arms the Stage instead.
    // A catalog entry's own page has it too: its run is a capability-run, which takes a workspace origin.
    if ((row || entry) && inWorkspace() && !spec.clicks) body.append(this.drawDestination(state, busy));
    if (row && state.powers) body.append(this.drawPowers(row, spec));
    if (!this.controlWindows.get(id)) {
      // No ⋯ here (the page sits under its row): its own items as quiet links.
      const links = this.pageActions(id);
      if (links.length) body.append(el('div', { className: 'run-links' }, ...links.map((l) => act(l.label, `btn-text${l.label === 'Compare models…' ? ' action-compare' : ''}`, l.run, { title: l.title ?? '' }))));
    }
    page.append(body, this.drawFoot(id, row, state, spec, phase));
    return page;
  }

  private drawTaskSwitch(id: string, tasks: ModelTask[]): HTMLElement {
    const state = this.stateOf(id);
    // The new task's page is made and shown first, then this one closes (nothing is revealed underneath, and a redraw a
    // focused control deferred can't leave neither page on screen).
    const go = (t: ModelTask) => {
      state.taskMenu = false;
      if (t.task === id) { this.render(); return; }
      const old = this.controlWindows.get(id);
      const active = typeof document !== 'undefined' ? document.activeElement as HTMLElement | null : null;
      if (active && this.hostsOf(id).some((h) => h.contains(active))) active.blur?.();
      this.open(t.task, t.choice ?? undefined);
      state.open = false; state.list = false;
      if (old && old !== this.controlWindows.get(t.task)) old.dismiss();
      this.render();
    };
    const current = tasks.find((t) => t.task === id) ?? tasks[0];
    // Two or three short names share a segmented switch (the mock's); four or more, or names a segment would cut, are the
    // app's own menu under a content-width button ("Task: Click to outline ⌄").
    const long = tasks.reduce((n, t) => n + t.title.length, 0) > 40;
    if (tasks.length >= 4 || (tasks.length === 3 && long)) {
      const button = act(`Task: ${current.title}`, 'mm-btn btn-sm run-task-button', () => {
        state.taskMenu = !state.taskMenu; this.render();
        if (state.taskMenu) this.hostsOf(id)[0]?.querySelector<HTMLElement>('.run-task-pop [aria-checked="true"]')?.focus?.();
      },
        { 'aria-haspopup': 'menu', 'aria-expanded': String(state.taskMenu), 'data-focus': `${id}:task`, title: current.title });
      button.append(tabMark('down', 'run-task-chevron'));
      const box = el('div', { className: 'run-tasks' }, button);
      if (state.taskMenu) {
        const menu = el('div', { className: 'capability-controls-menu run-task-pop', role: 'menu', 'aria-label': 'Task' },
          ...tasks.map((t) => act(t.title, '', () => go(t), { role: 'menuitemradio', 'aria-checked': String(t.task === id), 'data-focus': `${id}:task:${t.task}` })));
        menu.addEventListener('keydown', (e) => {
          const items = [...menu.querySelectorAll<HTMLButtonElement>('button')];
          const at = items.indexOf(document.activeElement as HTMLButtonElement);
          if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation?.(); state.taskMenu = false; this.render(); this.hostsOf(id)[0]?.querySelector<HTMLElement>('.run-task-button')?.focus?.(); }
          else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus?.(); }
        });
        menu.addEventListener('focusout', (e) => { const to = (e as FocusEvent).relatedTarget as Node | null; if (to && !box.contains(to)) { state.taskMenu = false; this.render(); } });
        box.append(menu);
      }
      return box;
    }
    return el('div', { className: 'run-tasks' }, el('span', { className: 'seg seg-wide', role: 'group', 'aria-label': 'Task' },
      ...tasks.map((t) => act(t.title, '', () => go(t), { 'aria-pressed': String(t.task === id), title: t.title }))));
  }

  /** A run-card: its lead (a thumbnail, a mark, a lit cell), a title and one quiet line, and Change. */
  private card(lead: Node, title: string, sub: string, change?: { label: string; run: () => void; off: boolean; expanded?: boolean; words?: string; focus: string }): HTMLElement {
    const card = el('div', { className: 'run-card' }, lead, el('span', { className: 'run-val-words' },
      el('b', { className: 'run-val-title', textContent: title, title }), el('span', { className: 'run-val-sub', textContent: sub })));
    if (change) {
      const b = act(change.words ?? 'Change', `btn-text run-change${change.off ? ' is-off' : ''}`, () => { if (!change.off) change.run(); },
        { 'aria-label': change.label, 'data-focus': change.focus, disabled: change.off });
      if (change.expanded !== undefined) b.setAttribute('aria-expanded', String(change.expanded));
      card.append(b);
    }
    return card;
  }

  private line(key: string, content: Node, className = ''): HTMLElement {
    return el('div', { className: `run-line${className ? ` ${className}` : ''}` }, el('span', { className: 'run-key', textContent: key }), content);
  }

  /** One On row per data input the run reads. */
  private drawInput(id: string, row: ActionRow | undefined, state: RowState, spec: RunSpec, input: RunInput, busy: boolean): HTMLElement {
    const first = spec.inputs[0] === input;
    const key = first ? 'On' : '';
    const thumb = () => {
      const shown = typeof document !== 'undefined' ? document.querySelector?.('.workspace-stage-thumb') as HTMLElement | null | undefined : null;
      const image = shown && !shown.hidden ? shown.style?.backgroundImage ?? '' : '';
      const t = el('span', { className: `run-thumb${image ? '' : ' is-empty'}` });
      if (image) t.style.backgroundImage = image;
      return t;
    };
    const where = tabName();
    const onTab = where ? `on ${where}` : 'on this Tab';
    if (input.kind === 'stage' || input.kind === 'picture') {
      const words = this.inputWords;
      const title = words ? specimenOrCamera(words) : 'What is on the Stage';
      const kind = row ? this.kindOf(this.runRow(row, spec)) : undefined;
      const retained = input.kind === 'picture' && !!kind && kind.output !== 'tracks.graph' && (kind.output !== 'labels.instance.2d' || kind.operation === 'cells') && !!this.deps.inputResources;
      const chosen = state.inputSession === this.deps.session() ? state.resource : undefined;
      const sub = chosen ? 'A saved result' : `${/^(Live\b|Camera\b)/.test(words) ? 'The camera’s view' : 'Whole image'} · ${onTab}`;
      const change = retained ? { label: 'Choose a saved input', run: () => { state.choosing = !state.choosing; this.render(); }, off: busy, expanded: state.choosing, focus: `${id}:on` }
        : inWorkspace() ? { label: 'Change what is on the Stage', run: () => window.dispatchEvent(new CustomEvent('workspace-study-section', { detail: 'inputs' })), off: busy, focus: `${id}:on` } : undefined;
      const line = this.line(key, this.card(thumb(), chosen?.title ?? title, sub, change), 'action-input');
      line.title = words ? `On the Stage: ${words}` : '';
      const box = el('div', { className: 'run-input' }, line);
      if (retained && (state.choosing || chosen)) box.append(this.retainedChooser(row!, spec, state));
      return box;
    }
    if (input.kind === 'volume') return this.volumeInput(row!, spec, state, key);
    if (input.kind === 'typed') return this.typedInput(row!, spec, state, input, key);
    if (input.kind === 'outline') {
      const selected = this.deps.selected();
      const title = selected ? selected.label || selected.id : input.required ? 'Select an outline in the view first' : 'The whole view';
      const card = this.card(el('span', { className: 'run-lead' }, tabMark('outline', 'run-lead-icon')), title, selected ? 'The selected outline' : input.required ? 'Click one on the Stage' : 'Or select an outline in the view');
      card.dataset.none = String(!selected);
      return this.line(key, card, 'action-target');
    }
    return this.line(key, this.card(el('span', { className: 'run-lead' }, tabMark('eyepiece', 'run-lead-icon')), 'Your clicks', 'Click or box things on the Stage'));
  }

  /** The chosen model, certain: its lit cell, its whole name, where it runs; Change opens the models in place. */
  private drawWith(id: string, state: RowState, spec: RunSpec, busy: boolean): HTMLElement {
    const c = spec.chosen!;
    const fraction = c.download?.total ? c.download.got / c.download.total : null;
    const cell = litCell(c.cell, { size: 14, label: c.cellWord, fraction });
    const lead = el('span', { className: 'run-cell' }, cell);
    const change = spec.choices.length > 1 ? { label: 'Change model', run: () => { state.list = !state.list; this.render(); }, off: busy, expanded: state.list, words: state.list ? 'Done' : 'Change', focus: `${id}:with` } : undefined;
    // A model not checked on this Mac yet says so where it is named, so its Run is not a surprise. Once this page ran it
    // here, that is said too: Hooke's own check (one run on the public example, origin "check": contract/AGENTS.md "The
    // app's own check") is a record of its own that a run of yours does not make, so it stays Not checked.
    const ranHere = c.section === 'unchecked' && !state.running && state.ranId === c.id && state.result?.ok === true;
    const unchecked = ranHere ? 'ran here · its check not run yet' : 'not checked on this Mac yet';
    const facts = c.reason || (!c.download && c.setup) || (c.section === 'unchecked' && !c.download ? [c.where, unchecked].filter(Boolean).join(' · ') : c.where);
    const line = this.line('With', this.card(lead, c.name, facts, change), 'run-with');
    if (c.section === 'unchecked' && !c.download && !c.reason) line.title = ranHere
      ? `${c.name} ran here on your picture. Not checked means Hooke’s own check of it (one run on the public example) is not recorded on this Mac yet; Check it here in its setup records it.`
      : `Not checked: Hooke’s own check of ${c.name} (one run on the public example) is not recorded on this Mac yet.`;
    return line;
  }

  /** The models in place (the mock's modelList): Needs you, Ready, Not checked, Can be set up, then Not on this Mac
   * folded; each row a lit cell, the whole name, what it does for this task, and at most one action under the name. */
  private drawModelList(id: string, state: RowState, spec: RunSpec): HTMLElement {
    const box = el('div', { className: 'run-models', role: 'group', 'aria-label': 'Models for this task' });
    // Escape closes the list and gives focus back to its Change model.
    box.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault(); event.stopPropagation();
      state.list = false; this.render();
      for (const host of this.hostsOf(id)) { const change = host.querySelector?.<HTMLElement>(`[data-focus="${id}:with"]`); if (change) { change.focus?.({ preventScroll: true }); break; } }
    });
    for (const [section, title] of MODEL_SECTIONS) {
      const choices = spec.choices.filter((c) => c.section === section);
      if (!choices.length) continue;
      if (section === 'away') {
        box.append(act(`Not on this Mac ${choices.length}`, 'run-models-h run-models-fold', () => { state.away = !state.away; this.render(); }, { 'aria-expanded': String(state.away) }));
        if (!state.away) continue;
      } else box.append(el('div', { className: 'run-models-h', textContent: title }));
      for (const c of choices) box.append(this.drawChoice(id, state, spec, c));
    }
    return box;
  }

  private drawChoice(id: string, state: RowState, spec: RunSpec, c: RunChoice): HTMLElement {
    const words = el('span', { className: 'mrow-words' }, el('b', { textContent: c.name }));
    if (c.reason) {
      words.append(el('span', { className: 'mrow-why', textContent: c.reason }));
      return el('div', { className: 'mrow is-quiet', 'aria-disabled': 'true', 'data-option': c.option || c.id },
        litCell('idle', { size: 13, label: c.cellWord }), words);
    }
    if (c.ability) words.append(el('span', { className: 'mrow-what', textContent: c.ability, title: c.ability }));
    if (c.download) words.append(el('span', { className: 'mrow-step' }, el('span', { className: 'mrow-progress', textContent: c.download.total ? `Downloading · ${progressWords(c.download.got, c.download.total)}` : 'Downloading' })));
    else if (c.step) words.append(el('span', { className: 'mrow-step' }, el('span', { className: 'mm-btn btn-sm', textContent: c.step.short })));
    const pick = act('', 'mrow', () => {
      state.use = c.option || c.id; state.list = false; state.result = null; state.blocked = ''; state.stepError = ''; state.armError = '';
      this.render();
    }, { 'aria-pressed': String(spec.chosen?.id === c.id), 'data-option': c.option || c.id, 'data-focus': `${id}:pick:${c.id}`,
      'aria-label': `${c.name}${c.step ? `, ${c.step.short}` : ''}`, title: c.where });
    pick.append(litCell(c.cell, { size: 13, label: c.cellWord, fraction: c.download?.total ? c.download.got / c.download.total : null }), words);
    return pick;
  }

  /** A declared field, through the one parameterControl, disabled while a run is under way. */
  private field(id: string, field: Field, values: Record<string, unknown>, off: boolean): HTMLElement {
    const box = parameterControl(`action-${id.replace(/[^a-z0-9-]+/gi, '-')}`, field, values, { run: true });
    if (off) for (const tag of ['input', 'select']) box.querySelectorAll<HTMLInputElement>(tag).forEach((i) => { i.disabled = true; });
    box.classList.add('run-field');
    return box;
  }

  /** More options: the declared fields marked advanced (only those of the chosen model). */
  private drawMore(id: string, state: RowState, spec: RunSpec, values: (field: Field) => Record<string, unknown>, busy: boolean): HTMLElement {
    const more = el('details', { className: `run-more${busy ? ' is-off' : ''}` }, el('summary', { textContent: 'More options' }));
    more.open = state.options;
    more.addEventListener('toggle', () => { state.options = more.open; });
    for (const field of spec.advanced) more.append(this.field(id, field, values(field), busy));
    return more;
  }

  /** What it runs on (the page's ⋯, What it runs on; Will, 2026-10-15, "crucial"): each model role's model in use by
   * its job, then methods, then the algorithms of its code, and Models ›. */
  private drawPowers(row: ActionRow, spec: RunSpec): HTMLElement {
    const box = el('section', { className: 'action-details run-powers', 'aria-label': 'What it runs on' }, el('h3', { className: 'run-h', textContent: 'What it runs on' }));
    if (!row.available && (row.step?.says || row.why)) box.append(el('p', { className: 'action-why', textContent: row.step?.says || row.why }));
    const use = this.stateOf(row.id).use;
    const target = this.runRow(row, spec);
    const capability = this.wire?.capabilities.find(c => c.id === target.id);
    const powers = target === row && use && row.runWith && this.wire && capability
      ? powersOf(this.wire, capability, { role: row.runWith.role, option: use }) : target.powers;
    if (powers.length) {
      const list = el('dl', { className: 'action-powers', 'aria-label': 'What it runs on' });
      for (const power of powers) list.append(el('dt', { textContent: power.job }), el('dd', { 'data-kind': power.kind, 'data-cloud': String(power.cloud), textContent: power.name }));
      box.append(list);
    } else box.append(el('p', { className: 'action-powers-none', textContent: 'No model: the app’s own code' }));
    box.append(act('Models ›', 'btn-text action-link', () => this.deps.openModels()));
    return box;
  }

  /** Results go to: This Tab | New Tab. On a researcher's Tab this Tab can't take results (an explicit New Tab is
   * required there). */
  private drawDestination(state: RowState, off: boolean): HTMLElement {
    const research = onResearcher();
    const chosen = research ? (state.destResearch === 'new' ? 'new' : '') : state.dest;
    const seg = el('span', { className: 'seg', role: 'group', 'aria-label': 'Results go to' });
    const tab = act('This Tab', '', () => { state.dest = 'tab'; this.render(); }, { 'aria-pressed': String(chosen === 'tab'),
      disabled: off || research, title: destinationWords() });
    const fresh = act('New Tab', 'action-new-tab', () => { if (research) state.destResearch = 'new'; else state.dest = 'new'; state.blocked = ''; this.render(); },
      { 'aria-pressed': String(chosen === 'new'), disabled: off, title: 'Results go to a new Tab, beside this one' });
    seg.append(tab, fresh);
    return el('div', { className: `run-dest${off ? ' is-off' : ''}` }, el('span', { className: 'run-label', textContent: 'Results go to' }), seg);
  }

  private drawComparison(row: ActionRow, state: RowState): HTMLElement {
    const models = this.taskModelsOf(row).filter((m) => m.choosable);
    this.comparison.open(row.chooses as 'cells' | 'outline', row.title, models, state.use || row.runWith?.current || undefined);
    return this.comparison.root;
  }

  /** The sticky footer: the one primary spanning it, or the run's state in words (never an invented percent). */
  private drawFoot(id: string, row: ActionRow | undefined, state: RowState, spec: RunSpec, phase: Phase): HTMLElement {
    const foot = el('div', { className: 'run-foot' });
    const rowOf = (...nodes: Node[]) => el('div', { className: 'run-foot-row' }, ...nodes);
    const note = (text: string, className = 'run-note') => el('div', { className, textContent: text });
    // What went wrong, in the Failed footer's anatomy: the needs-attention cell, a plain line, the server's words under it
    // (never red body text).
    const fail = (title: string, words: string) => el('div', { className: 'run-fail', role: 'status' }, litCell('attention', { size: 14, label: 'Didn’t run' }),
      el('div', {}, el('b', { textContent: title }), ...(words ? [el('p', { className: 'action-result-text', textContent: words })] : [])));
    const c = spec.chosen;
    const name = c?.name ?? row?.model ?? spec.title;
    const short = c?.short ?? shortName(name);
    if (state.asking && row) { foot.append(this.drawCloudAsk(row, state)); return foot; }
    switch (phase) {
      case 'running': case 'stopping': {
        const stopping = phase === 'stopping';
        const status = el('span', { className: 'run-status' },
          litCell(stopping ? 'still' : 'working', { size: 14, ...(stopping ? { title: 'Asked to stop; it is Stopped once the server confirms.' } : {}) }),
          el('b', { textContent: stopping ? 'Stop requested' : `Running ${state.ranWith || short} on ${state.newTab ? 'a new Tab' : state.tab || 'this Tab'}` }),
          el('span', { className: 'run-clock mm-num', 'data-since': String(state.started), textContent: clock(Date.now() - state.started) }));
        const stop = act(stopping ? 'Waiting to confirm' : 'Stop', `mm-btn btn-sm action-stop${stopping ? ' is-off' : ''}`, () => void this.stop(state),
          { disabled: stopping, 'aria-label': stopping ? `Stop requested: ${spec.title}` : `Stop: ${spec.title}`, 'data-focus': `${id}:stop` });
        if (!stopping) stop.prepend(tabMark('stop', 'run-stop-icon'));
        foot.append(rowOf(status, stop));
        const said = liveWords(state);
        const live = note(said, 'run-note action-result-text'); live.dataset.live = 'true'; live.hidden = !said;
        foot.append(live);
        return foot;
      }
      case 'armed': {
        foot.append(rowOf(el('span', { className: 'run-status' }, litCell('working', { size: 14, label: 'Waiting for your click' }),
          el('b', { textContent: state.armedTrial ? 'Click or box a thing on the Stage' : 'Click or box things on the Stage' })),
          act('Done', 'mm-btn btn-sm', () => { state.armed = false; this.render(); }, { 'data-focus': `${id}:disarm` })));
        foot.append(note(state.armedTrial ? `${short} outlines the next one and keeps it in Data` : `${short} outlines each one and keeps it in Data`));
        return foot;
      }
      case 'done': {
        const reply = state.result!;
        const words = resultWords(reply);
        // Kept only when the reply shows a kept result (what it measured, in the server's words, or its table): an effect
        // (Clear outlines, Set the objective) and an answer (Count them) save nothing, and never say they did (ADR 0006).
        const kept = !!(reply.said || reply.table || reply.tableIdentity);
        const outcome = kept ? outcomeWords(state.ranWith || short, words) : { title: words, rest: '' };
        foot.append(rowOf(el('span', { className: `run-status${kept ? '' : ' is-said'}` }, litCell('ready', { size: 14, settle: true }), el('b', { className: 'action-result-text', textContent: outcome.title }))));
        const took = state.ended && state.started ? tookWords(state.ended - state.started) : '';
        // A This Tab run on the Stage's still (S5a): the server keeps its window for the specimen (`placedOn`), so it is
        // said as on a Tab only once the server placed its masks there; drawn on the specimen once the studio draws it.
        const specimen = state.placedOn === 'specimen';
        if (specimen && reply.table && state.drawnOn !== reply.table && this.deps.stillDrawn?.(reply.table)) state.drawnOn = reply.table;
        const onSpecimen = specimen && !!reply.table && state.drawnOn === reply.table;
        const saved = !kept ? '' : onSpecimen ? 'drawn on the specimen and saved in Data'
          : specimen ? 'saved in Data; drawn on the specimen once checked there'
          : inWorkspace() ? `saved on ${state.newTab ? 'a new Tab' : state.tab || 'this Tab'} and in Data` : 'saved in Data';
        const line = [took, saved].filter(Boolean).join(', ');
        if (line) foot.append(note(line, 'run-note is-2'));
        if (outcome.rest) foot.append(note(outcome.rest));
        // What its model says of what it makes (its output's note: "Bright regions are not a cell count").
        if (kept && spec.note) foot.append(note(spec.note, 'run-note run-made-note'));
        const table = reply.table;
        const buttons = rowOf();
        if (kept) buttons.append(act('Open in Data', 'mm-btn btn-sm', () => this.openData(), { 'data-focus': `${id}:data` }));
        if (table) buttons.append(act('Table', 'mm-btn btn-sm btn-quiet action-link', () => this.deps.openTable(table, state.result?.tableIdentity)));
        buttons.append(act('Run again', `mm-btn btn-sm${kept ? ' btn-quiet' : ''} action-again`, () => { state.result = null; this.render(); }, { 'data-focus': `${id}:again` }));
        // No table to open: what ran, and with which model, is one press away in Activity (0.3.0's Activity ›), on the
        // same row as Run again (the row never wraps).
        if (!table) buttons.append(act('Details', 'mm-btn btn-sm btn-quiet run-done-details', () => this.deps.openActivity(), { title: 'Opens Activity', 'data-focus': `${id}:details` }));
        foot.append(buttons);
        return foot;
      }
      case 'failed': {
        const words = resultWords(state.result!);
        foot.append(el('div', { className: 'run-fail', role: 'status' }, litCell('attention', { size: 14, label: 'Didn’t run' }),
          el('div', {}, el('b', { textContent: `${state.ranWith || short} didn’t run` }), el('p', { className: 'action-result-text', textContent: words }))));
        // Run again always (the next view may be one it can run on); another model too when the task has one to choose.
        const again = act('Run again', 'mm-btn btn-sm action-again', () => { state.result = null; this.render(); }, { 'data-focus': `${id}:again` });
        const another = spec.choices.length > 1
          ? [act('Choose another model', 'mm-btn btn-sm btn-quiet', () => { state.result = null; state.list = true; this.render(); }, { 'data-focus': `${id}:another` })]
          : [];
        foot.append(rowOf(again, ...another, act('Details', 'mm-btn btn-sm btn-quiet', () => this.deps.openActivity())));
        return foot;
      }
      case 'stopped': {
        // One line ("Stopped · nothing kept"); the server's whole sentence (what stopped, was kept, discarded, carries on)
        // is its Activity line, behind Details, and read to a screen reader here.
        const words = resultWords(state.result!);
        const status = el('span', { className: 'run-status' }, litCell('stopped', { size: 14 }), el('b', { textContent: stoppedWords(state.result) }));
        status.title = words;
        foot.append(rowOf(status, act('Run again', 'mm-btn btn-sm action-again', () => { state.result = null; this.render(); }, { 'data-focus': `${id}:again` })));
        foot.append(el('div', { className: 'run-note action-result-text visually-hidden', textContent: words }));
        if (words !== 'Stopped') foot.append(rowOf(act('Details', 'mm-btn btn-sm btn-quiet run-stop-details', () => this.deps.openActivity(), { title: 'Opens Activity, where what the Stop did is said in full' })));
        return foot;
      }
      case 'downloading': {
        const d = c!.download!;
        const fraction = d.total ? Math.max(0, Math.min(1, d.got / d.total)) : null;
        const words = d.state === 'checking' ? 'Checking the files…' : d.state === 'queued' ? 'Waiting to download' : d.total ? `Downloading · ${progressWords(d.got, d.total)}` : 'Downloading';
        // The label twice: in the text colour over the track, and white clipped to the filled part, so it reads over both
        // (light theme's pale track under white text was 1.4:1).
        const bar = el('span', { className: 'mm-btn run-go dl', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(d.total || 0),
          'aria-valuenow': String(d.got), 'aria-label': `${c!.name}: ${words}` }, el('span', { className: 'dl-words', textContent: words }),
          el('span', { className: 'dl-words dl-on', 'aria-hidden': 'true', textContent: words }));
        if (fraction !== null) bar.style.setProperty('--p', `${Math.round(fraction * 1000) / 10}%`);
        const buttons = rowOf(bar);
        if (this.deps.download && d.state !== 'checking') buttons.append(act('Cancel', 'mm-btn', () => void this.deps.download!(c!.step?.model ?? c!.model ?? '', true).then(() => this.render(), () => this.render()), { 'data-focus': `${id}:cancel` }));
        foot.append(buttons);
        return foot;
      }
      case 'setup': {
        const step = c?.step ?? spec.step;
        const reason = c?.reason || spec.reason;
        if (step) {
          const starting = !!state.starting && state.starting === step.model;
          const go = act(starting ? 'Starting the download…' : step.label, 'mm-btn mm-btn-primary run-go action-step', () => this.doStep(id, step),
            { title: step.says, disabled: starting, 'data-focus': `${id}:step` });
          foot.append(rowOf(go));
          if (state.stepError) foot.append(fail(step.does === 'download' ? 'The download didn’t start' : `${short} couldn’t be set up`, state.stepError));
        } else foot.append(rowOf(el('span', { className: 'mm-btn run-go is-off', 'aria-disabled': 'true' }, el('span', { textContent: reason || 'It can’t run here' }))));
        return foot;
      }
      default: {
        if (!row) return foot;
        if (spec.clicks) {
          // A model another than the clicks' own that has not passed its check here can't be tried by a click (the
          // server refuses it): its check is the primary, as any not-ready model's step is.
          const check = c && !c.current ? c.check : null;
          if (state.armError) {
            foot.append(fail(`${short} didn’t start`, state.armError));
            foot.append(rowOf(...(check ? [act(check.label, 'mm-btn btn-sm action-step', () => this.doStep(id, check), { title: check.says, 'data-focus': `${id}:step` })] : []),
              spec.choices.length > 1 ? act('Choose another model', 'mm-btn btn-sm', () => { state.armError = ''; state.list = true; this.render(); }, { 'data-focus': `${id}:another` })
                : act('Try again', 'mm-btn btn-sm', () => { state.armError = ''; this.render(); }, { 'data-focus': `${id}:again` })));
            return foot;
          }
          if (check) {
            foot.append(rowOf(act(check.label, 'mm-btn mm-btn-primary run-go action-step', () => this.doStep(id, check),
              { title: `${name} hasn’t passed its check on this Mac: ${check.says}`, 'data-focus': `${id}:step` })));
            foot.append(note(`${short} hasn’t passed its check on this Mac`));
            return foot;
          }
          const go = act('Click in the view', 'mm-btn mm-btn-primary run-go action-go', () => void this.arm(state, spec), { title: `Outline what you click with ${name}`, 'data-focus': `${id}:go` });
          foot.append(rowOf(go));
          return foot;
        }
        foot.append(rowOf(this.runButton(row, state, spec)));
        // Why Run did not start (a field to fill, an outline to select): a plain line, the page stays ready.
        if (state.blocked) foot.append(note(state.blocked, 'run-note action-result-text'));
        return foot;
      }
    }
  }

  /** Opens Data, where every result lives (the workspace's Data window), else the newest table. */
  private openData(): void {
    if (inWorkspace() && typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('workspace-study-section', { detail: 'results' }));
    else this.deps.openTable(null);
  }

  /** Run, naming the model it runs, on one line (an ellipsis, the whole name in its tooltip): "Run Cellpose-SAM". */
  private runButton(row: ActionRow, state: RowState, spec: RunSpec): HTMLButtonElement {
    const target = this.runRow(row, spec);
    const selected = this.deps.selected();
    const values = this.valuesOf(state, spec);
    const why = blocked(target, values, selected, target === row ? state.use : '');
    const needsSelection = why === 'Select an outline in the view first';
    const verb = 'Run'; // one verb for every capability (the mock's Run Gemini 3.8 Flash), a question too
    const model = spec.chosen ? spec.chosen.name : row.model;
    // One line: the model's short name ("Run Bright objects"), its whole name in the tooltip.
    const words = model ? `${verb} ${shortName(model)}` : verb;
    const button = el('button', { type: 'button', className: 'mm-btn mm-btn-primary run-go action-go', 'data-focus': `${row.id}:go`,
      'aria-label': `${verb}: ${row.title}${model ? ` with ${model}` : ''}`, disabled: needsSelection }, el('span', { textContent: words }));
    button.title = needsSelection ? why! : why ? `${why}, then ${verb.toLowerCase()}` : model ? `${verb} ${model}` : words;
    button.addEventListener('click', () => void this.start(row, state));
    return button;
  }

  /** "Click in the view": the next click or box on the Stage outlines with the chosen model (never a change of default). */
  private async arm(state: RowState, spec: RunSpec): Promise<void> {
    const choice = spec.chosen;
    if (!choice) return;
    state.armError = '';
    // The role's own model outlines every click already: nothing to arm, the page just says so. Another model arms the
    // next click with it (the guided Try's click), never a change of the default.
    if (choice.current) { state.armed = true; state.armedTrial = false; this.render(); return; }
    if (!this.deps.tryClicks) return;
    try {
      await this.deps.tryClicks(choice.role, choice.option);
      state.armed = true; state.armedTrial = true;
    } catch (error) {
      state.armError = plainFailure(error instanceof Error ? error.message : String(error));
    }
    this.render();
  }

  /** The footer's clock, ticking once a second only while a run is under way; an armed click is let go once taken. */
  private tick(): void {
    const live = [...this.state.values()].some((s) => s.running || s.armed);
    if (live && !this.ticker && typeof setInterval === 'function') {
      this.ticker = setInterval(() => {
        const now = Date.now();
        for (const host of [...[...this.controlWindows.values()].map((c) => c.options.content), this.root])
          host.querySelectorAll<HTMLElement>('.run-clock').forEach((n) => { if (n.dataset.since) n.textContent = clock(now - Number(n.dataset.since)); });
        let changed = false;
        for (const s of this.state.values()) if (s.armed && s.armedTrial && this.deps.clicksArmed && !this.deps.clicksArmed()) { s.armed = false; changed = true; }
        if (changed) this.render();
        if (![...this.state.values()].some((s) => s.running || s.armed)) { clearInterval(this.ticker!); this.ticker = null; }
      }, 1000);
      (this.ticker as { unref?: () => void }).unref?.(); // never what keeps a test (or Node) alive
    } else if (!live && this.ticker) { clearInterval(this.ticker); this.ticker = null; }
  }

  /** Where a row's page is drawn: its control window, else under its row. */
  private hostsOf(id: string): HTMLElement[] {
    const control = this.controlWindows.get(id);
    const inline = [...(this.root.querySelectorAll?.('.run') ?? [])] as HTMLElement[];
    return [...(control ? [control.options.content] : []), ...inline.filter((n) => n.dataset.action === id)];
  }

  /** The faint veil over the origin Tab's specimen while its run is under way (CSS only, pointer-events none). */
  private updateVeil(): void {
    if (typeof document === 'undefined' || !document.body?.dataset) return;
    const active = document.body.dataset.activeWorkspaceView ?? '';
    const on = inWorkspace() && [...this.state.values()].some((s) => s.running && s.tabId === active);
    if (on) document.body.dataset.runVeil = 'true'; else delete document.body.dataset.runVeil;
  }

  /** The panel's own row: every measurement of the session as a table (measurements.ts). */
  private drawTableRow(): HTMLElement {
    const box = el('div', { className: 'trow', 'data-action': 'measurements-table', 'data-family': 'measuring', 'data-available': 'true' });
    box.append(el('span', { className: 'trow-lead' }, tabMark('table', 'study-tool-icon trow-icon')),
      el('span', { className: 'trow-words' }, el('span', { className: 'trow-title', textContent: 'Measurements table' }),
        el('span', { className: 'trow-sub' }, litCell('ready', { size: 11, label: 'Ready' }), el('b', { textContent: 'Measurements' }), el('span', { textContent: 'built in' }))),
      el('span', { className: 'trow-chev', 'aria-hidden': 'true' }, tabMark('right', 'trow-chev-icon')),
      act('', 'trow-hit', () => this.deps.openTable(null), { 'aria-label': 'Open the measurements table', title: 'Measurements of particles, cells and tracks, to sort and download as CSV' }));
    return box;
  }

  private drawCamera(): HTMLElement {
    // Its click stops here: the phone panel closes on any click outside it, and this one would reach the document after
    // opening it.
    const open = act('Open', 'trow-step mm-btn action-go', () => this.deps.openCamera(), { 'aria-label': 'Open the camera controls' });
    const page = el('a', { className: 'trow-step mm-btn action-go', href: this.deps.cameraPage(), target: '_blank', rel: 'noopener', textContent: 'Open ↗',
      'aria-label': 'Open the camera page in a new tab' });
    const row = (title: string, what: string, button: HTMLElement) => el('div', { className: 'trow', 'data-family': 'camera' },
      el('span', { className: 'trow-lead' }, tabMark('camera', 'study-tool-icon trow-icon')),
      el('span', { className: 'trow-words' }, el('span', { className: 'trow-title', textContent: title }), el('span', { className: 'trow-sub' }, el('span', { textContent: what }))), button);
    return this.section('Camera', [row('Camera controls', 'Focus, exposure, lens and recording on the phone', open), row('Camera page', 'Its checklist and first-time setup', page)]);
  }

  private kindOf(row: ActionRow) {
    const key = row.id.replace(/^catalog:/, '');
    return this.wire?.models?.flatMap(model => [
      ...(model.newKind && model.id === key ? [model.newKind] : []),
      ...(model.memberKinds?.filter(member => member.key === key) ?? []),
    ])[0];
  }

  /** A saved input instead of the Stage's picture (a catalog image capability): the current still image, or a retained
   * result, each checked by the server (`fits`, `says`). */
  private retainedChooser(row: ActionRow, spec: RunSpec, state: RowState): HTMLElement {
    const target = this.runRow(row, spec);
    const kind = this.kindOf(target);
    const session = this.deps.session();
    if (state.inputSession !== session) { state.inputSession = session; state.inputs = []; state.resource = undefined; }
    const box = el('div', { className: 'run-choose' });
    const select = el('select', { className: 'action-retained-input', 'aria-label': `Input for ${row.title}` },
      el('option', { value: '', textContent: 'Current still image' }),
      ...[...(state.inputs ?? []), ...(state.resource && !(state.inputs ?? []).some(item => JSON.stringify(item.reference) === JSON.stringify(state.resource!.reference)) ? [state.resource] : [])].map(item => el('option', { value: JSON.stringify(item.reference), textContent: item.title })));
    select.value = state.resource ? JSON.stringify(state.resource.reference) : '';
    select.addEventListener('change', () => { state.resource = state.inputs?.find(item => JSON.stringify(item.reference) === select.value); this.render(); });
    const read = act('Find retained results', 'btn-text action-link', () => {
      read.disabled = true; read.textContent = 'Reading retained results…';
      void this.deps.inputResources!(session, target.id.replace(/^catalog:/, '')).then(reply => {
        if (this.deps.session() !== session) return;
        state.inputs = reply.inputs;
        state.blocked = reply.inputs.length ? '' : 'No supported retained image results yet.';
      }).catch((error: unknown) => { state.blocked = error instanceof Error ? error.message : String(error); })
        .finally(() => this.render());
    });
    box.append(select, read);
    // What it takes, kept but folded: a fact for when a saved image is refused, not the first thing read.
    if (kind?.output === 'labels.instance.2d') box.append(el('details', { className: 'action-input-facts' },
      el('summary', { textContent: 'Which saved images it takes' }), el('p', { textContent: 'Saved inputs: single-plane uint8 intensity with Y/X axes, up to one megapixel. Shows up to 64 saved documents at their latest accepted revision. Earlier revisions stay saved. No resizing or value conversion.' })));
    if (state.resource?.compatibility) box.append(el('p', { role: 'status', className: 'run-quiet', textContent: state.resource.compatibility.says }));
    return box;
  }

  /** A volume (volume.3d): chosen from this Project's volumes. */
  private volumeInput(row: ActionRow, spec: RunSpec, state: RowState, key: string): HTMLElement {
    const target = this.runRow(row, spec);
    const session = this.deps.session();
    if (state.volumeSession !== session) { state.volumeSession = session; state.volumes = []; state.volume = undefined; }
    const select = el('select', { className: 'action-volume-input', 'aria-label': `Volume for ${row.title}` },
      el('option', { value: '', textContent: 'Choose a volume' }),
      ...(state.volumes ?? []).map(item => el('option', { value: item.id, textContent: item.title })));
    select.value = state.volume ?? '';
    select.addEventListener('change', () => { state.volume = select.value || undefined; });
    const read = act('Find project volumes', 'btn-text action-link', () => {
      if (!this.deps.inputVolumes) return;
      read.disabled = true; read.textContent = 'Reading volumes…';
      void this.deps.inputVolumes(session).then(items => {
        if (this.deps.session() !== session) return;
        state.volumes = items.filter(item => item.kind === 'volume' && item.day === session);
        state.blocked = state.volumes.length ? '' : 'Add a volume in Models first.';
        this.render();
      }).catch(error => { if (this.deps.session() === session) { state.blocked = String(error); this.render(); } });
    });
    void target;
    return el('div', { className: 'run-input' }, this.line(key, this.card(el('span', { className: 'run-lead' }, tabMark('slide', 'run-lead-icon')), 'A volume', 'From this Project')),
      el('div', { className: 'run-choose' }, select, read));
  }

  /** One saved resource per typed input: only those it takes as stored can be chosen; the others say why. */
  private typedInput(row: ActionRow, spec: RunSpec, state: RowState, input: RunInput, key: string): HTMLElement {
    const target = this.runRow(row, spec);
    const session = this.deps.session();
    if (state.inputSession !== session) { state.inputSession = session; state.typedPorts = []; state.typedChosen = {}; }
    const listed = state.typedPorts?.find(item => item.port === input.id)?.choices ?? [];
    const select = el('select', { className: 'action-typed-input', 'aria-label': `${input.label} for ${row.title}`, title: `Takes ${input.type}` },
      el('option', { value: '', textContent: `Choose the ${input.label}` }),
      ...listed.map(choice => el('option', { value: JSON.stringify(choice.reference), disabled: !choice.fits,
        textContent: choice.fits ? choice.title : `${choice.title} — ${choice.says}` })));
    const chosen = state.typedChosen?.[input.id];
    select.value = chosen ? JSON.stringify(chosen) : '';
    select.addEventListener('change', () => {
      const choice = listed.find(item => JSON.stringify(item.reference) === select.value);
      state.typedChosen = { ...(state.typedChosen ?? {}) };
      if (choice) state.typedChosen[input.id] = choice.reference; else delete state.typedChosen[input.id];
      this.render();
    });
    const choose = el('div', { className: 'run-choose' }, select);
    const box = el('div', { className: 'run-input' }, this.line(key, this.card(el('span', { className: 'run-lead' }, tabMark('output', 'run-lead-icon')),
      input.label.charAt(0).toUpperCase() + input.label.slice(1), input.type ?? '')), choose);
    // One reading for every input, on the first one's row.
    if (spec.inputs.find((i) => i.kind === 'typed') === input && this.deps.inputResources) {
      const read = act('Find saved inputs', 'btn-text action-link', () => {
        read.disabled = true; read.textContent = 'Reading saved inputs…';
        void this.deps.inputResources!(session, target.id.replace(/^catalog:/, '')).then(reply => {
          if (this.deps.session() !== session) return;
          state.typedPorts = reply.typed ?? [];
          const usable = state.typedPorts.every(item => item.choices.some(choice => choice.fits));
          state.blocked = usable ? '' : 'Some inputs have no saved resource they take as stored yet; save one, or convert it explicitly first.';
        }).catch((error: unknown) => { state.blocked = error instanceof Error ? error.message : String(error); })
          .finally(() => this.render());
      });
      choose.append(read);
    }
    return box;
  }

  /** Asks the server to stop the row's run. Its own reply (to `action`) says how it ended; a refused Stop is said. */
  async stop(state: RowState): Promise<void> {
    const run = state.run;
    if (!state.running || !run || state.stopping) return;
    state.stopping = true;
    this.render();
    try {
      const reply = await this.deps.stop(run);
      if (!reply.ok && state.run === run) {
        state.stopping = false;
        state.blocked = reply.error || 'It could not be stopped';
      }
    } catch (error) {
      if (state.run === run) {
        state.stopping = false;
        state.blocked = error instanceof Error ? error.message : String(error);
      }
    }
    this.render();
  }

  /** Whether a chosen format-2 entry, all its settings at their defaults, runs as the task's own model choice. */
  private asTaskModel(row: ActionRow, spec: RunSpec, values: Record<string, unknown>): boolean {
    const c = spec.chosen;
    if (!c?.fields || !c.role || !row.runWith?.options.some((o) => o.id === c.option)) return false;
    return c.fields.every((f) => {
      const v = values[f.key];
      if (v === undefined || v === '') return true;
      return f.initial !== undefined && (f.numeric || f.kind === 'number' ? Number(v) === Number(f.initial) : String(v) === String(f.initial));
    });
  }

  private chosenCloud(row: ActionRow, state: RowState, spec: RunSpec): string | null {
    if (spec.chosen?.fields) return spec.chosen.cloud;
    return state.use && row.runWith ? row.runWith.clouds[state.use] ?? null : row.cloud;
  }

  private drawCloudAsk(row: ActionRow, state: RowState): HTMLElement {
    const cloud = this.chosenCloud(row, state, this.specOf(row, state));
    const yes = act('Send', 'mm-btn mm-btn-primary', () => { this.cloudAgreed.add(this.deps.session()); state.asking = false; const newTab = state.newTabAfterConsent === true; state.newTabAfterConsent = false; void this.start(row, state, newTab || undefined); });
    const no = act('Cancel', 'mm-btn btn-quiet action-link', () => { state.asking = false; state.newTabAfterConsent = false; this.render(); });
    return el('div', { className: 'action-ask', role: 'alertdialog', 'aria-label': 'Send the view to a cloud model?' },
      el('p', { textContent: `Send the view to ${cloud}${/cloud/i.test(cloud ?? '') ? '' : ' in the cloud'}? Asked once a project.` }),
      el('div', { className: 'action-ask-buttons' }, no, yes));
  }

  /** Runs it: what blocks it is said in its footer; a cloud model asks first, once per session. Results go where the
   * page says (This Tab, or a New Tab; on a researcher's Tab only a New Tab chosen there). */
  async start(row: ActionRow, state = this.stateOf(row.id), newTab?: boolean): Promise<void> {
    if (state.running) return;
    state.open = true; state.blocked = '';
    const research = onResearcher();
    const toNew = newTab ?? (research ? state.destResearch === 'new' : inWorkspace() && state.dest === 'new');
    if (research && !toNew) {
      state.blocked = 'Choose New Tab under Results go to: a researcher’s Tab keeps its own work.';
      this.render(); this.controlWindows.get(row.id)?.show(); return;
    }
    const spec = this.specOf(row, state);
    // A format-2 entry chosen in a task runs its own capability only when one of its settings was changed (that is the
    // one path that takes them); left at its defaults it runs as the task's model for this run, as before, so a run
    // makes one result, not that one and its capability's own record too.
    const plain = this.asTaskModel(row, spec, this.valuesOf(state, spec));
    const ran = plain ? row : this.runRow(row, spec);
    // A field for other models only (Compare with the ImageJ macro, Analyze Particles') is neither checked nor sent.
    const target = ran.fields.every((f) => appliesTo(f, spec.chosen)) ? ran : { ...ran, fields: ran.fields.filter((f) => appliesTo(f, spec.chosen)) };
    const values = plain ? state.values : this.valuesOf(state, spec);
    if (this.kindOf(target)?.input === 'volume.3d' && (state.volumeSession !== this.deps.session() || !state.volume)) {
      state.blocked = 'Choose a volume before running.';
      this.render(); this.hostsOf(row.id)[0]?.querySelector<HTMLSelectElement>('.action-volume-input')?.focus?.(); return;
    }
    const selected = this.deps.selected();
    const why = blocked(target, values, selected, ran === row ? state.use : '');
    if (why) { state.blocked = why; this.render(); return; }
    if (this.chosenCloud(row, state, spec) && !this.cloudAgreed.has(this.deps.session())) { state.asking = true; state.newTabAfterConsent = toNew; this.render(); return; }
    const run = newRunId();
    if (row.runWith && (state.use || row.runWith.current) && ran === row) rememberUse(row.runWith.role, state.use || row.runWith.current!);
    state.running = true;
    state.run = run;
    state.stopping = false;
    state.replied = false;
    state.early = null;
    state.result = null;
    state.placedOn = undefined; state.placedRun = run;
    state.note = '';
    state.started = Date.now(); state.ended = 0;
    state.tab = tabName(); state.tabId = typeof document !== 'undefined' ? document.body?.dataset?.activeWorkspaceView ?? '' : '';
    state.newTab = toNew; state.ranWith = spec.chosen?.short ?? shortName(row.model ?? row.title);
    state.ranId = spec.chosen?.id ?? '';
    state.names = [spec.chosen?.name ?? row.model ?? '', spec.chosen?.said ?? ''].filter(Boolean);
    this.updateVeil();
    this.render();
    let reply: ActionReply;
    try {
      const args = structuredClone(argsFor(target, values, selected));
      const use = plain ? { [spec.chosen!.role]: spec.chosen!.option } : spec.run.use;
      const capability = plain ? row.id : spec.run.capability;
      const resource = state.inputSession === this.deps.session() ? state.resource ? structuredClone(state.resource.reference) : undefined : undefined;
      const volume = state.volumeSession === this.deps.session() ? state.volume : undefined;
      if (state.volumeSession === this.deps.session() && !volume) throw new Error('Choose a volume before running.');
      const kind = this.kindOf(target);
      if (kind?.typed) {
        const chosen = state.inputSession === this.deps.session() ? state.typedChosen ?? {} : {};
        const missing = (kind.inputs ?? []).filter(port => !chosen[port.id]).map(port => `the ${portWords(port)}`);
        if (missing.length) throw Object.assign(new Error(`Choose ${missing.join(' and ')} above before running.`), { blocked: true });
        reply = await this.deps.run(capability, args, use, run, undefined, undefined, structuredClone(chosen), ...(toNew ? [{ newTab: true }] : []));
      } else if (toNew) reply = await this.deps.run(capability, args, use, run, resource, volume, undefined, { newTab: true });
      else reply = volume ? await this.deps.run(capability, args, use, run, undefined, volume) : resource ? await this.deps.run(capability, args, use, run, resource) : await this.deps.run(capability, args, use, run);
    } catch (error) {
      if ((error as { blocked?: boolean })?.blocked && state.run === run) {
        state.blocked = (error as Error).message; this.finish(state); this.render(); return;
      }
      reply = { ok: false, error: plainFailure(error instanceof Error ? error.message : String(error)) };
    }
    if (state.run !== run) return; // settled already (the connection was lost meanwhile)
    state.result = reply;
    if (reply.placed) state.placedOn = state.placedOn === 'tab' ? 'tab' : reply.placed;
    state.replied = true;
    if (reply.stopping) {
      // Stopped, its work not ended yet: still this run's (Stop requested, no Run) until the server settles it.
      state.stopping = true;
      if (state.early) this.settle(state, state.early);
    } else {
      this.finish(state);
    }
    this.render();
  }

  /** The population layer changed (main.ts relays each `layer`): a finished run whose population the studio now draws
   * on the specimen (found on the Stage's still) says so in its footer; drawn again only when that changes. */
  layerChanged(): void {
    const drawn = this.deps.stillDrawn;
    if (!drawn) return;
    let changed = false;
    for (const state of this.state.values()) {
      const table = !state.running && state.result?.ok && state.placedOn === 'specimen' ? state.result.table : undefined;
      if (table && state.drawnOn !== table && drawn(table)) { state.drawnOn = table; changed = true; }
    }
    if (changed) this.render();
  }

  /** The server decided where a This Tab run's result is (`action-placed`, S5a): 'specimen', its outlines verified on the
   * Stage's still (kept in Data, no window), or 'tab', its masks window placed on its Tab instead (its outlines were not
   * checked there in time, or the layer was hidden or replaced first). 'tab' is final. Heard before the reply, it is
   * kept for it. */
  placed(run: string, where: 'specimen' | 'tab'): void {
    const state = [...this.state.values()].find((s) => s.placedRun === run);
    if (!state) return;
    const next = state.placedOn === 'tab' || where === 'tab' ? 'tab' : 'specimen';
    if (next === state.placedOn) return;
    state.placedOn = next;
    if (state.result) this.render();
  }

  /** The server's settlement of a run the user stopped (`action-settled`): only the page of that exact run changes. */
  settled(run: string, settlement: Settlement): void {
    this.comparison.settled(run, settlement);
    const state = [...this.state.values()].find((s) => s.run === run);
    if (!state) return;
    if (!state.replied) { state.early = settlement; return; }
    this.settle(state, settlement);
    this.render();
  }

  /** The connection was lost: a page still stopping can hear no settlement, and says so (never left stale). */
  connectionLost(): void {
    this.comparison.connectionLost();
    let changed = false;
    for (const state of this.state.values()) {
      if (!state.running || !state.replied || !state.run) continue;
      state.result = { ok: false, error: LOST_WHILE_STOPPING };
      this.finish(state);
      changed = true;
    }
    if (changed) this.render();
  }

  private settle(state: RowState, settlement: Settlement): void {
    state.result = settledReply(settlement);
    this.finish(state);
  }

  private finish(state: RowState): void {
    state.running = false;
    state.run = null;
    state.stopping = false;
    state.replied = false;
    state.early = null;
    state.ended = Date.now();
    this.updateVeil();
  }
}

/** The click-driven tasks (the user's own gesture with a model to choose: Click to outline), as rows of the list: the
 * capability the user performs by clicking, its outlining role's models to choose among. */
function gestureRows(wire: CapabilitiesWire): ActionRow[] {
  const rows: ActionRow[] = [];
  for (const capability of wire.capabilities) {
    if (capability.actor !== 'you' || capability.how.gesture !== 'click') continue;
    const role = wire.roles.find((r) => r.kind === 'model' && !r.fixed && (capability.roles ?? []).includes(r.id) && r.operation?.returns === 'instance/1');
    if (!role) continue;
    const options = role.options.filter((o) => o.available && o.id !== 'off');
    const current = role.options.find((o) => o.id === role.current);
    rows.push({
      id: capability.id, title: capability.title, what: capability.what, say: '', group: capability.family, tool: '', query: false,
      target: null, fields: [], available: capability.available && options.length > 0, why: capability.why,
      step: current && !current.available ? current.readiness?.next ?? null : null, cloud: null, model: current?.label ?? null,
      chooses: role.id, gesture: 'click', powers: powersOf(wire, capability),
      runWith: options.length ? { role: role.id, title: role.title, current: role.current,
        clouds: Object.fromEntries(options.filter((o) => o.runs === 'cloud').map((o) => [o.id, o.said || o.label])),
        options: options.map((o) => ({ id: o.id, label: o.said || o.label })) } : null,
    });
  }
  return rows;
}

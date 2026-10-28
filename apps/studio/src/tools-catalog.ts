// Tools and Models are one list in two places (docs/design/2026-10-20-hooke-words.md, "Tool or model?"; Will,
// 2026-10-20: "a researcher will reach to run a model"; the narrative name may sit on top of the model, never replace
// it). Models is the catalog, to see, set up and configure; Tools is the same entries in the workspace, to use. This
// module keeps no list of its own:
// - **The entries** are the capabilities wire's `models` (orchestrator/catalog_view.py, the rows the Models page shows),
//   and its New-for-you items (`setup.news`), in the server's order: ready ones first, those to set up and New ones at the
//   bottom.
// - **A task's models** are the entries whose `abilities` name that task (the capability), each with the role and
//   option that does it, so a click on a model's name can pick the task and that model at once.
// - **What a comparison says** is facts only: each model's count, and how each works in the catalog's own words. Which
//   to trust is the Assistant's to judge ("The models judge; code gives them facts").
import type { CapabilitiesWire, ModelRow, Role } from './protocol';
import type { NewItem } from './capabilityshelf';
import { modelSection } from './run-spec';

/** The workspace's Tools, one to one with Models and in its sections (run-spec.ts `modelSection`: one meaning of Ready):
 * ready to use here, not checked here yet, to set up (red, Needs you too), not on this Mac, and New. */
export interface ToolEntries { ready: ModelRow[]; unchecked: ModelRow[]; setup: ModelRow[]; unavailable: ModelRow[]; news: NewItem[] }

export function toolEntries(wire: CapabilitiesWire | null): ToolEntries {
  const rows = wire?.models ?? [];
  const section = (r: ModelRow) => modelSection(r);
  return {
    ready: rows.filter((r) => section(r) === 'ready'),
    unchecked: rows.filter((r) => section(r) === 'unchecked'),
    setup: rows.filter((r) => section(r) === 'setup' || section(r) === 'needs'),
    unavailable: rows.filter((r) => section(r) === 'away'),
    news: wire?.setup?.news?.new ?? [],
  };
}

/** One model that can do a task: its entry (Card ↗), its name as the run button and Result say it, and the role and
 * option that pick it for one run (`choosable`: the task's own model choice; otherwise a model the task also uses). */
export interface TaskModel {
  model: string | null; name: string; role: string; option: string; ready: boolean; choosable: boolean; current: boolean;
  why: string; // why it can't run here, in the readiness words (empty when it can)
}

function roleOf(wire: CapabilitiesWire, id: string): Role | undefined {
  return wire.roles.find((r) => r.id === id);
}

/** The display name of a role's option: its one name (names.py), never with where it runs (that is said beside it). */
function optionName(wire: CapabilitiesWire | null, role: string, option: string): string {
  const found = wire ? roleOf(wire, role)?.options.find((o) => o.id === option) : undefined;
  if (found) return found.label;
  const row = wire?.models?.find((m) => m.usedFor.some((u) => u.role === role && u.option === option));
  return row?.name ?? option;
}

/** The models that can do a task (a capability): its own model choice's options, each with its catalog entry, the one
 * preferred first (the last one used for that job here, else the one in use), then the ready ones, then those to set
 * up; then the models it also uses, from the entries' abilities, as they are in use now. A catalog entry's own task
 * (`catalog:<id>`) is that entry. */
export function taskModels(wire: CapabilitiesWire | null, task: string, choosing: string | null, preferred?: string | null): TaskModel[] {
  if (!wire) return [];
  const out: TaskModel[] = [];
  const seen = new Set<string>();
  const entryOf = (role: string, option: string) => wire.models?.find((m) => m.usedFor.some((u) => u.role === role && u.option === option));
  const role = choosing ? roleOf(wire, choosing) : undefined;
  for (const option of role?.options ?? []) {
    if (option.id === 'off') continue; // "Off" is no model
    seen.add(`${role!.id}/${option.id}`);
    out.push({ model: entryOf(role!.id, option.id)?.id ?? null, name: option.label, role: role!.id, option: option.id,
      ready: option.available, choosable: true, current: role!.current === option.id,
      why: option.available ? '' : option.readiness?.word || option.why || entryOf(role!.id, option.id)?.word || '' });
  }
  for (const row of wire.models ?? []) {
    for (const ability of row.abilities ?? []) {
      // Another job's model the task also uses: only the one doing it now (a different one is chosen on its card).
      if (ability.id !== task || !ability.current || seen.has(`${ability.role}/${ability.option}`)) continue;
      seen.add(`${ability.role}/${ability.option}`);
      const option = roleOf(wire, ability.role)?.options.find((o) => o.id === ability.option);
      const ready = option ? option.available : row.status === 'ready';
      out.push({ model: row.id, name: option?.label ?? row.name, role: ability.role, option: ability.option, ready, choosable: false,
        current: true, why: ready ? '' : option?.readiness?.word || option?.why || row.word });
    }
  }
  const own = task.startsWith('catalog:') ? wire.models?.find((m) => `catalog:${m.id}` === task) : undefined;
  if (own && !out.some((m) => m.model === own.id)) {
    out.push({ model: own.id, name: own.name, role: '', option: '', ready: own.status === 'ready', choosable: false,
      current: true, why: own.status === 'ready' ? '' : own.word });
  }
  const first = preferred ?? (choosing ? roleOf(wire, choosing)?.current ?? null : null);
  const rank = (m: TaskModel) => (m.choosable ? 0 : 3) + (m.choosable && m.ready && m.option === first ? 0 : m.ready ? 1 : 2);
  return out.map((m, i) => ({ m, i })).sort((a, b) => rank(a.m) - rank(b.m) || a.i - b.i).map(({ m }) => m);
}

/** The tasks a model can be used for here: each task (an Actions row) its abilities name, with the choice that picks
 * it for that task when the task chooses among models. */
export interface ModelTask { task: string; title: string; choice: { role: string; option: string } | null }
export function modelTasks(row: ModelRow, tasks: { id: string; title: string; available: boolean; runWith: { role: string; options: { id: string }[] } | null }[]): ModelTask[] {
  const out: ModelTask[] = [];
  for (const task of tasks) {
    if (!task.available) continue;
    if (task.id === `catalog:${row.id}`) { out.push({ task: task.id, title: task.title, choice: null }); continue; }
    const ability = (row.abilities ?? []).find((a) => a.id === task.id && (task.runWith?.role === a.role || a.current));
    if (!ability) continue;
    const choosable = task.runWith?.role === ability.role && task.runWith.options.some((o) => o.id === ability.option);
    if (task.runWith?.role === ability.role && !choosable) continue; // its option can't run here: not usable from here
    out.push({ task: task.id, title: task.title, choice: choosable ? { role: ability.role, option: ability.option } : null });
  }
  return out;
}

/** The model that made a Result, by its name (a population's `madeBy`: its operation and implementation). */
export function madeByName(wire: CapabilitiesWire | null, madeBy: Record<string, unknown> | undefined): string {
  const implementation = typeof madeBy?.implementation === 'string' ? madeBy.implementation : '';
  const operation = typeof madeBy?.operation === 'string' ? madeBy.operation : '';
  return implementation ? optionName(wire, operation, implementation) : '';
}

/** Each compared model's colour on the Stage, A, B and C: used for nothing else in the studio (a test keeps it so). */
export const COMPARE_COLOURS = ['#2dd4bf', '#a78bfa', '#a3e635'] as const;
export const COMPARE_LETTERS = ['A', 'B', 'C'] as const;

/** What the catalog says each model does, for the line under a comparison (ModelRow `what`, its own words). */
export function howItWorks(wire: CapabilitiesWire | null, operation: string, implementation: string): string {
  const row = wire?.models?.find((m) => m.usedFor.some((u) => u.role === operation && u.option === implementation));
  return row?.what ?? '';
}

/** One plain line on why the counts differ: a fact about how each model works, never which is right. */
export function countsLine(arms: { name: string; count?: number; how: string; failed?: string }[]): string {
  const ran = arms.filter((a) => !a.failed && a.count !== undefined);
  if (ran.length < 2) return '';
  const counts = new Set(ran.map((a) => a.count));
  const ways = ran.map((a) => (a.how ? `${a.name} ${a.how.charAt(0).toLowerCase()}${a.how.slice(1)}` : `${a.name} works its own way`));
  if (counts.size === 1) return `They found the same number (${ran[0].count}), each its own way: ${ways.join('; ')}.`;
  return `The counts differ because each model finds them its own way: ${ways.join('; ')}.`;
}

/** The question "Ask the Assistant which to trust" sends: the counts as facts, the judgment the Assistant's. */
export function trustQuestion(task: string, arms: { name: string; count?: number; failed?: string }[]): string {
  const said = arms.filter((a) => !a.failed && a.count !== undefined)
    .map((a, i) => `${COMPARE_LETTERS[i]}. ${a.name}: ${a.count}`).join(', ');
  return `I compared models for “${task}” on the same picture (${said}). Which should I trust here, and why?`;
}

/** The model last chosen for a job in this browser, so a task lists it first next time (a per-viewer convenience). */
const LAST_USED = 'tools:last-used';
export function lastUsed(role: string): string | null {
  try { return (JSON.parse(localStorage.getItem(LAST_USED) ?? '{}') as Record<string, string>)[role] ?? null; } catch { return null; }
}
export function rememberUse(role: string, option: string): void {
  try {
    const saved = JSON.parse(localStorage.getItem(LAST_USED) ?? '{}') as Record<string, string>;
    localStorage.setItem(LAST_USED, JSON.stringify({ ...saved, [role]: option }));
  } catch { /* private window or blocked storage: the one in use is listed first instead */ }
}

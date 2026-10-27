/** Compare models (A/B/C) inside a task (docs/design/2026-10-20-hooke-words.md; Will, 2026-10-20: "not dynamic, intuitive
 * or clear" as a folded section). The task's own models are listed with a box each: up to three ready ones, each given
 * its letter and its colour (tools-catalog.ts COMPARE_COLOURS, used for nothing else) as it is ticked. One run uses the
 * existing Actions admission, runner and retained results (`compare-models`); the results are each model's count and
 * marks, drawn on the Stage in its colour (deps.showComparison), one plain line of facts on why the counts differ, and
 * "Ask the Assistant which to trust": the judgment is the Assistant's, never this code's. */
import type { CapabilitiesWire } from './protocol';
import { newRunId, type ActionReply, type ActionsDeps, type Settlement } from './actions-panel';
import { COMPARE_COLOURS, COMPARE_LETTERS, countsLine, howItWorks, trustQuestion, type TaskModel } from './tools-catalog';

export interface ComparisonResult {
  id?: string; operation: string; capture: number; pairs: string[];
  arms: { impl: string; label: string; failed?: string; population?: string; revision?: number;
    count?: number; areaPx?: number; ms?: number | null; reused?: boolean; executed?: string }[];
}
/** The tasks a comparison can be made for: the server compares models at these two operations only. */
export const COMPARISON_CLASSES = [
  { id: 'cells', title: 'Segment every cell', input: 'The current image, or the video frame captured when you press Compare.' },
  { id: 'outline', title: 'Outline one object', input: 'The selected outline’s original image and box, shared by every model.' },
] as const;
export type Operation = typeof COMPARISON_CLASSES[number]['id'];
export function comparable(role: string | undefined): role is Operation {
  return COMPARISON_CLASSES.some((c) => c.id === role);
}
/** What the Stage draws: each model's retained masks, in its own colour, on the shared picture. */
export interface ComparisonView {
  key: string; session: string; task: string; operation: string; line: string; pairs: string[]; question: string;
  arms: { letter: string; colour: string; name: string; impl: string; population?: string; revision: number; count?: number; failed?: string }[];
}
export function comparisonProblem(models: TaskModel[], selected: string[], operation: Operation, target: boolean): string | null {
  if (selected.length < 2 || selected.length > 3) return 'Choose two or three models for this task.';
  if (selected.some(id => !models.some(model => model.option === id && model.ready))) return 'A selected model needs setup. Set it up or choose another.';
  if (operation === 'outline' && !target) return 'Select an outline in the workspace first.';
  return null;
}
function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag); if (text) element.textContent = text; if (className) element.className = className; return element;
}
function button(text: string, click: () => void, className?: string): HTMLButtonElement {
  const element = node('button', text, className); element.type = 'button'; element.addEventListener('click', click); return element;
}
function swatch(index: number): HTMLElement {
  const mark = node('span', COMPARE_LETTERS[index], 'comparison-swatch');
  mark.style.background = COMPARE_COLOURS[index]; mark.setAttribute('aria-hidden', 'true');
  return mark;
}

export class ModelComparison {
  readonly root = node('div');
  private wire: CapabilitiesWire | null = null;
  private operation: Operation = 'cells';
  private task = '';
  private models: TaskModel[] = [];
  private selected: string[] = [];
  private session = '';
  private run: string | null = null;
  private stopping = false;
  private message = '';
  private result: ComparisonResult | null = null;
  private resultSession = '';
  private resultTask = '';
  private cloudConsent = false;
  private report = '';
  private signature = '';
  private readonly deps: ActionsDeps;
  /** Called when Cancel leaves compare mode (the task's own model list comes back). */
  onCancel: () => void = () => {};
  constructor(deps: ActionsDeps) {
    this.deps = deps;
    this.root.className = 'model-comparison';
    this.root.setAttribute('aria-label', 'Compare models');
    this.render();
  }
  update(wire: CapabilitiesWire | null) {
    this.wire = wire;
    const session = this.deps.session();
    if (this.session !== session) {
      this.session = session; this.selected = []; this.cloudConsent = false;
      // A running comparison retains its origin. Its late reply cannot enable Keep in another Project.
    }
    const signature = JSON.stringify([session, this.models, this.deps.selected()]);
    if (signature !== this.signature) { this.signature = signature; this.render(); }
  }
  get running(): boolean { return !!this.run; }
  get operationNow(): Operation { return this.operation; }
  /** Compare mode for a task: its models, the one chosen now ticked as A. */
  open(operation: Operation, task: string, models: TaskModel[], chosen?: string) {
    const changed = operation !== this.operation || task !== this.task;
    if (!this.run && changed) { this.operation = operation; this.task = task; this.selected = []; this.cloudConsent = false; }
    this.models = models.filter((m) => m.choosable);
    if (!this.run && !this.selected.length && chosen && this.models.some((m) => m.option === chosen && m.ready)) this.selected = [chosen];
    this.render();
  }
  settled(run: string, settlement: Settlement) {
    if (this.run !== run) return;
    this.run = null; this.stopping = false; this.message = settlement.text; this.render();
  }
  connectionLost() {
    if (!this.run) return;
    this.run = null; this.stopping = false;
    this.message = 'Connection lost. Check Activity before retrying; the comparison may still be running.'; this.render();
  }
  private render() {
    const list = node('fieldset', undefined, 'comparison-models'); list.disabled = !!this.run;
    list.append(node('legend', 'Models to compare (up to 3)'));
    if (!this.models.length) list.append(node('p', 'Loading the models for this task…'));
    for (const model of this.models) {
      const index = this.selected.indexOf(model.option);
      const row = node('label', undefined, 'comparison-option'); row.dataset.option = model.option;
      const check = node('input'); check.type = 'checkbox'; check.value = model.option; check.checked = index >= 0;
      check.disabled = !model.ready || (index < 0 && this.selected.length >= 3);
      check.addEventListener('change', () => {
        this.selected = check.checked ? [...this.selected, model.option] : this.selected.filter(id => id !== model.option);
        this.cloudConsent = false; this.render();
        this.root.querySelectorAll<HTMLInputElement>('input[type="checkbox"]').forEach(input => { if (input.value === model.option) input.focus(); });
      });
      row.append(check, node('span', model.name, 'comparison-name'));
      if (index >= 0) row.append(swatch(index));
      if (!model.ready) row.append(node('small', model.why || 'Needs setup', 'comparison-unready'));
      if (!model.ready && model.model) row.append(button('Set up', () => this.deps.openModels(model.model!), 'action-step comparison-setup'));
      list.append(row);
    }
    const option = (id: string) => this.wire?.roles.find(role => role.id === this.operation)?.options.find(o => o.id === id);
    const cloud = this.selected.filter(id => option(id)?.runs === 'cloud');
    const consent = node('label', undefined, 'comparison-cloud');
    if (cloud.length) {
      const check = node('input'); check.type = 'checkbox'; check.checked = this.cloudConsent; check.disabled = !!this.run;
      check.addEventListener('change', () => { this.cloudConsent = check.checked; this.render(); });
      consent.append(check, node('span', `Send this input to ${cloud.map(id => this.models.find(m => m.option === id)?.name ?? id).join(', ')}. Provider charges apply.`));
    }
    const problem = comparisonProblem(this.models, this.selected, this.operation, !!this.deps.selected());
    const start = button(this.run ? 'Comparing…' : `Compare${this.selected.length >= 2 ? ` ${this.selected.length} models` : ' models'}`, () => void this.start(), 'action-go comparison-start');
    start.disabled = !!this.run || !!problem || (!!cloud.length && !this.cloudConsent);
    const controls = node('div', undefined, 'comparison-controls'); controls.append(start);
    if (this.run) {
      const stop = button(this.stopping ? 'Stop requested · waiting to confirm' : 'Stop comparison', () => void this.stop(), 'action-stop'); stop.disabled = this.stopping; controls.append(stop);
    } else controls.append(button('Cancel', () => this.onCancel(), 'action-link'));
    // One short line under the list (Calm first view): what blocks it, or what it does.
    const status = node('p', this.message || problem || 'Same picture for each. Your default model stays the same.');
    status.setAttribute('role', 'status'); status.className = 'comparison-status';
    this.root.replaceChildren(list, consent, controls, status);
    if (this.result) this.renderResult();
  }
  /** The models' names, counts and colours, as the Stage draws them. */
  view(): ComparisonView | null {
    const result = this.result; if (!result) return null;
    const arms = result.arms.map((arm, index) => ({ letter: COMPARE_LETTERS[index], colour: COMPARE_COLOURS[index], name: this.nameOf(arm),
      impl: arm.impl, population: arm.population, revision: arm.revision ?? 0, count: arm.count, failed: arm.failed }));
    const facts = arms.map(arm => ({ ...arm, how: howItWorks(this.wire, result.operation, arm.impl) }));
    return { key: result.id ?? `${result.capture}:${arms.map(a => `${a.impl}@${a.population ?? ''}`).join(',')}`, session: this.resultSession,
      task: this.resultTask, operation: result.operation, line: countsLine(facts), pairs: result.pairs, question: trustQuestion(this.resultTask, arms), arms };
  }
  private nameOf(arm: ComparisonResult['arms'][number]): string {
    return this.models.find(m => m.option === arm.impl)?.name
      ?? this.wire?.roles.find(role => role.id === this.operation)?.options.find(o => o.id === arm.impl)?.label ?? arm.label;
  }
  private renderResult() {
    const result = this.result!, view = this.view()!;
    const section = node('section', undefined, 'comparison-results'); section.setAttribute('aria-label', 'Comparison results');
    section.append(node('h3', `${view.task || 'Compared'} · same input`));
    result.arms.forEach((arm, index) => {
      const card = node('div', undefined, 'comparison-arm'); card.dataset.impl = arm.impl;
      const head = node('h4'); head.append(swatch(index), node('span', view.arms[index].name));
      card.append(head);
      card.append(node('p', arm.failed || [arm.count === undefined ? null : `${arm.count} found`, arm.areaPx === undefined ? null : `${arm.areaPx} px²`, arm.reused ? 'Reused saved result · not timed' : arm.ms === undefined || arm.ms === null ? null : `${arm.ms.toFixed(0)} ms`].filter(Boolean).join(' · ')));
      if (arm.revision) card.append(node('small', `Includes your corrections · revision ${arm.revision}`));
      const actions = node('div', undefined, 'comparison-arm-actions');
      if (!arm.failed && arm.population) actions.append(button('Measurements', () => this.deps.openTable(arm.population!, { session: this.resultSession, revision: arm.revision ?? 0 }), 'action-link'));
      if (!arm.failed && arm.population && this.deps.openComparisonMasks) {
        // Choosing is continuing: the chosen model's own saved result opens, to correct, measure and animate from it.
        const inspect = button('Continue with this one', () => void this.inspect(arm), 'action-link comparison-continue');
        inspect.title = `Open ${view.arms[index].name}'s saved outlines to correct, measure and animate them`;
        inspect.disabled = !!this.run || this.resultSession !== this.deps.session(); actions.append(inspect);
      } else if (!arm.failed && result.id) {
        const keep = button('Use this outline', () => void this.keep(arm.impl), 'action-link');
        keep.title = `Show ${view.arms[index].name}'s outline on the Stage in place of the one shown now`;
        keep.disabled = !!this.run || this.resultSession !== this.deps.session(); actions.append(keep);
      }
      card.append(actions); section.append(card);
    });
    if (view.line) section.append(node('p', view.line, 'comparison-why'));
    if (this.deps.openComparisonMasks && result.arms.some(arm => arm.population && !arm.failed))
      section.append(node('p', 'Each model’s result is kept in Data. Continue with the one you prefer; the others stay there to compare again.', 'comparison-why'));
    for (const pair of result.pairs) section.append(node('small', pair, 'comparison-pair'));
    const next = node('div', undefined, 'comparison-next');
    if (this.deps.showComparison && result.arms.some(arm => arm.population && !arm.failed)) {
      const stage = button('Show on the Stage', () => this.show(), 'action-link'); stage.disabled = this.resultSession !== this.deps.session(); next.append(stage);
    }
    if (this.deps.ask) next.append(button('Ask the Assistant which to trust', () => this.deps.ask!(view.question), 'action-link comparison-ask'));
    next.append(button('Activity', () => this.deps.openActivity(), 'action-link'));
    section.append(next);
    section.append(node('small', result.operation === 'cells' ? 'Each result is kept in Data.' : 'Use this outline replaces only the outline shown, and works for five minutes after comparing.'));
    if (this.report) { const details = node('details'); details.append(node('summary', 'Run details'), node('p', this.report)); section.append(details); }
    this.root.append(section);
  }
  private show() {
    const view = this.view();
    if (view && view.session === this.deps.session() && view.arms.some(arm => arm.population && !arm.failed)) this.deps.showComparison?.(view);
  }
  async start() {
    if (this.run) return;
    const problem = comparisonProblem(this.models, this.selected, this.operation, !!this.deps.selected());
    if (problem) { this.message = problem; this.render(); return; }
    const option = (id: string) => this.wire?.roles.find(role => role.id === this.operation)?.options.find(o => o.id === id);
    if (this.selected.some(id => option(id)?.runs === 'cloud') && !this.cloudConsent) return;
    const args: Record<string, unknown> = { operation: this.operation, implementations: [...this.selected], policy: 'raw', exact: true };
    if (this.operation === 'outline') args.object = this.deps.selected()!.id; else args.input = 'view';
    this.result = null; await this.execute(args);
  }
  private async keep(implementation: string) {
    if (this.run || !this.result?.id || this.resultSession !== this.deps.session()) return;
    await this.execute({ keep: implementation, comparison: this.result.id });
  }
  private async inspect(arm: ComparisonResult['arms'][number]) {
    if (this.run || !arm.population || this.resultSession !== this.deps.session()) return;
    try { await this.deps.openComparisonMasks?.(arm.population, {session:this.resultSession,revision:arm.revision ?? 0}, this.nameOf(arm)); }
    catch (error) { this.message = error instanceof Error ? error.message : String(error); this.render(); }
  }
  private async execute(args: Record<string, unknown>) {
    const session = this.deps.session(), task = this.task, run = newRunId(); this.run = run; this.message = 'Running the selected models. Progress and loading details are in Activity.'; this.render();
    let reply: ActionReply;
    try { reply = await this.deps.run('compare-models', args, {}, run); }
    catch (error) { reply = { ok: false, error: error instanceof Error ? error.message : String(error) }; }
    if (this.run !== run) return;
    this.message = reply.error || reply.summary || (reply.stopped ? 'Stopped.' : 'Finished.');
    let fresh = false;
    if (reply.comparison) {
      this.result = reply.comparison; this.resultSession = session; this.resultTask = task; fresh = !args.keep;
      this.report = reply.summary || '';
      const successes = this.result.arms.filter(arm => !arm.failed).length;
      // Never said as shown when nothing finished (ADR 0006): each failure is said below, with its reason.
      const next = !successes ? 'Each one’s reason is below; Activity has the details.'
        : !reply.ok ? reply.error || 'The comparison did not finish. Check Activity; saved results remain available.'
        : this.deps.showComparison ? 'Opening each one’s marks on the Stage, in its colour.' : 'Compare their outputs below.';
      this.message = `${successes} of ${this.result.arms.length} models finished. ${next}`;
    }
    if (args.keep && reply.ok && !reply.stopped && !reply.stopping && this.result) this.result.id = undefined;
    if (reply.stopping) this.stopping = true; else { this.run = null; this.stopping = false; }
    this.render();
    if (fresh) this.show();
  }
  private async stop() {
    const run = this.run; if (!run || this.stopping) return; this.stopping = true; this.render();
    try { const reply = await this.deps.stop(run); if (!reply.ok && this.run === run) { this.stopping = false; this.message = reply.error || 'Could not stop. Check Activity.'; } }
    catch (error) { if (this.run === run) { this.stopping = false; this.message = String(error); } }
    this.render();
  }
}

import { mountStudyWorkflows } from './study-workflows';
import { mountLocalStudyResearcher } from './study-local-researcher';
import { StudyRefused, studyInputDescription, dollars, studyNeeds, studyState, taskState, taskPhase, providerName, pendingProviderTask, type Effort, type StudyEvidence, type StudyProvider, type StudyInput, type StudyStart, type ReviewOutcome } from './research-efforts';
import { litCell, type LitCellState } from './lit-cell';
import { Composer } from './composer';
import { researchIcon } from './research-workspace';

/** A saved Study is never an instruction to launch. Only its explicit Start action dispatches work. */
export interface StudyActions {
  localWorkflows?: boolean;
  control(action: 'pause' | 'continue' | 'stop'): void | Promise<unknown>;
  start?(fields: StudyStart): Promise<unknown>;
  readiness?(): Promise<StudyProvider[]>;
  evidence?(task: string): Promise<StudyEvidence>;
  collect?(task: string, action: 'sync' | 'close'): Promise<unknown>;
  recover?(task: string, providerSession: string): Promise<unknown>;
  review?(task: string, hash: string, outcome: ReviewOutcome, review: string): Promise<unknown>;
  finding?(request: string, statement: string, tasks: string[]): Promise<unknown>;
  /** Open a file the researcher made, kept at its exact revision, in the workspace (the same route as Study job results). */
  openFile?(ref: { id: string; revision: number }, name: string): Promise<unknown>;
  /** Open the Stage bar's Assistant beside the write-up (a Study takes no messages of its own). */
  /** Asks the Stage bar's Assistant these words over the write-up (its panel opens after admission): a Study
   * takes no messages of its own. False when they were not sent (the words then stay in the field). */
  ask?(text: string): boolean | void;
  askBlocked?(): string | null;
  setupAssistant?(): void;
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const el = document.createElement(tag); if (className) el.className = className; if (text) el.textContent = text; return el;
};
const field = (label: string, control: HTMLElement) => { const checkbox = control.tagName === 'INPUT' && (control as HTMLInputElement).type === 'checkbox'; const row = node('label', checkbox ? 'study-new-study-field study-choice' : 'study-new-study-field'); if (checkbox) row.append(control, node('span', '', label)); else row.append(node('span', '', label), control); return row; };
// The core without a research service (effort_api.UNAVAILABLE says the same) lists no researchers: a Study can't start.
export const STUDY_UNAVAILABLE = 'Managed cloud research is unavailable here. Use a connected agent, the configured local researcher, or scientific workflows.';
const details = (title: string, value: unknown) => { const box = node('details', 'study-evidence-details'); box.append(node('summary', '', title), node('pre', '', JSON.stringify(value, null, 2))); return box; };
export function experimentName(request: string, spec: Record<string, unknown> | undefined): string {
  const named = spec && (spec.title ?? spec.name ?? spec.kind);
  return typeof named === 'string' && named.trim() ? named.trim() : request;
}
/** Extract provider text only; all output remains inert text, never HTML or executable links. */
export function evidenceText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(evidenceText).filter(Boolean).join('\n');
  if (!value || typeof value !== 'object') return '';
  const row = value as Record<string, unknown>;
  return evidenceText(row.text ?? row.content ?? row.parts ?? row.message ?? row.output ?? row.response ?? row.candidates);
}

/** The provider's amount is integer USD cents. Use only the displayed snapshot, never a later task observation. */
export function evidenceCost(snapshot: Record<string, unknown>, provider: string | undefined, reserved: number): string {
  if (provider === 'gemini-google') return `Gemini reservation ${dollars(reserved)}; settlement uses this allowance as an estimate. No verified provider cost or invoice is available.`;
  const after = snapshot.after as { usage?: { list_cost?: { currency?: unknown; amount?: unknown } } } | undefined;
  const cost = after?.usage?.list_cost;
  if (cost?.currency === 'USD' && typeof cost.amount === 'string' && /^[0-9]{1,12}$/.test(cost.amount)) return `Provider-reported cost for this snapshot: ${dollars(Number(cost.amount) * 10_000)} (list price, not an invoice).`;
  return 'Provider cost is not retained in this snapshot. A held allowance is not a measured charge.';
}

/** What a researcher can do, in one line on its tile (its route, from the server's readiness). */
function researcherCan(item: StudyProvider): string {
  if (item.inputRoute === 'question-only' || item.id === 'gemini-google') return 'Answers in one reply; can’t open files or run tools.';
  if (item.inputRoute === 'scientific-tools') return 'Works through several steps, reads your inputs and runs your scientific tools.';
  return 'Works through several steps and reads the inputs you chose.';
}
/** What it will actually read: only what is granted, never the whole Project because it knows the Project's name. */
function readsWords(item: StudyProvider | undefined, inputs: number): string {
  if (!item) return '';
  if (item.inputRoute === 'question-only' || item.id === 'gemini-google') return 'It reads the brief only: not this Study’s inputs or anything else in this Project.';
  const these = inputs ? `copies of this Study’s ${inputs} input${inputs === 1 ? '' : 's'}` : 'no saved inputs';
  return `It reads the brief and ${these}, nothing else in this Project.`;
}

/** Who said a transcript line, in words: the person's brief and the researcher's reply never look the same. */
function speaker(role: string): string {
  if (/^user|user\.message|^human/.test(role)) return 'Your brief';
  if (/agent|assistant|model/.test(role)) return 'Researcher';
  if (/tool/.test(role)) return 'Tool';
  return role ? role.replace(/[._]+/g, ' ') : 'Recorded event';
}

/** The Study's allowance, said once, in its Tab's Stage bar (the workspace-feel plan, the Study home): what it has spent
 * and holds of its allowance, or the allowance alone before anything is spent or held. */
export function studyAllowanceWords(study: Pick<Effort, 'capMicrousd' | 'spentMicrousd' | 'heldMicrousd'>): string {
  if (!study.spentMicrousd && !study.heldMicrousd) return `${dollars(study.capMicrousd)} allowance`;
  return `${dollars(study.spentMicrousd)}${study.heldMicrousd ? ` · ${dollars(study.heldMicrousd)} held` : ''} of ${dollars(study.capMicrousd)}`;
}
/** The same in full, for its tooltip: spent, held and left of the allowance. */
export function studyAllowanceTitle(study: Pick<Effort, 'capMicrousd' | 'spentMicrousd' | 'heldMicrousd' | 'availableMicrousd'>): string {
  return `${dollars(study.spentMicrousd)} spent, ${dollars(study.heldMicrousd)} held, ${dollars(study.availableMicrousd)} left of ${dollars(study.capMicrousd)}`;
}

/** An experiment's lit cell by its phase: needs you, at work, done, or how it ended. */
const PHASE_CELL: Record<string, LitCellState> = { review: 'needs', unknown: 'needs', waiting: 'queued', planned: 'queued', running: 'working', launching: 'working',
  cancel_requested: 'still', completed: 'ready', finished: 'ready', failed: 'attention', cancelled: 'stopped' };

export function renderStudy(root: HTMLElement, study: Effort, actions: StudyActions, providers: StudyProvider[] = [], availableInputs: StudyInput[] = []): void {
  root.className = 'study-view study-home'; root.replaceChildren(); delete root.dataset.draft; delete root.dataset.reviewOpen;
  root.addEventListener('input', () => { root.dataset.draft = 'true'; }, { once: true });
  const notice = node('p', 'study-view-notice'); notice.setAttribute('role', 'status');
  let pending = false;
  const perform = async (work: () => void | Promise<unknown>): Promise<void> => {
    if (pending) return;
    pending = true; root.dataset.pending = 'true'; notice.textContent = 'Waiting for confirmation…';
    const controls = [...root.querySelectorAll('button'), ...root.querySelectorAll('input'), ...root.querySelectorAll('textarea'), ...root.querySelectorAll('select')].map(control => [control, control.disabled] as const);
    controls.forEach(([control]) => { control.disabled = true; });
    try { await work(); notice.textContent = ''; }
    catch (error) { notice.textContent = error instanceof StudyRefused ? `${error.message}${error.next ? ` Next: ${error.next}` : ''}` : error instanceof Error ? error.message : String(error); }
    finally { pending = false; delete root.dataset.pending; controls.forEach(([control, disabled]) => { control.disabled = control.dataset.locked === 'true' || disabled; }); }
  };
  const action = (label: string, work: () => void | Promise<unknown>, className = '') => {
    const button = node('button', className, label); button.type = 'button';
    button.addEventListener('click', () => { void perform(work); }); return button;
  };
  // The page (Study target, docs/design/2026-10-23-part1-study/study-tab.html): the question across the top; a reading
  // column (Your move, What we know, Experiments, the composer) beside a rail of facts (allowance, inputs, researchers).
  // Everything below is the same actions and guards as before; only where they sit and how they read changed.
  const tasks = Object.entries(study.tasks ?? {}).filter(([,task])=>task.specification?.provider!=='scientific-gcp-grant');
  const statePill = node('span', `study-state-pill study-state-${study.status}`, studyState(study));
  const controls = node('div', 'study-view-controls'); controls.append(statePill);
  if (study.status !== 'stopped') {
    // Pause matters once experiments can start or run; before the first one it would only add a control (Continue
    // always shows on a paused study). Stop stays reachable, quiet, and asks once more.
    if (tasks.length || study.status === 'paused') controls.append(action(study.status === 'paused' ? 'Continue' : 'Pause', () => actions.control(study.status === 'paused' ? 'continue' : 'pause')));
    const stop = action('Stop…', () => { if (stop.dataset.confirm !== 'true') { stop.dataset.confirm = 'true'; stop.textContent = 'Stop for good? A stopped study is kept, not resumed'; return; } return actions.control('stop'); }, 'study-view-stop'); controls.append(stop);
  }
  controls.title = 'Pause stops new experiments from starting; running ones continue. Stop asks running ones to end, and says Stopping until that is confirmed; their money stays held until then. Closing this view never stops work.';
  // The question opens its conversation as your words, its success criterion under it.
  const criterion = node('small', 'm-cap study-criterion', `Judged by ${study.criteria}`);

  // Bring a part of the conversation into view by scrolling only its own log: scrollIntoView would also scroll the Stage
  // behind it (overflow hidden, but still scrollable by script) and slide the Study under the Tab row.
  const log = node('div', 'rlog study-log'); log.setAttribute('role', 'log'); log.setAttribute('aria-label', 'What the Study has done');
  const reveal = (el: HTMLElement | null | undefined) => {
    if (!el || typeof log.scrollTo !== 'function') return;
    log.scrollTo({ top: el.getBoundingClientRect().top - log.getBoundingClientRect().top + log.scrollTop - 12, behavior: 'smooth' });
  };
  const rail = node('div', 'study-facts-rail');
  const section = (className: string, title: string, count?: number, side?: HTMLElement | null) => {
    const box = node('section', className), head = node('div', 'study-sec-head'), h = node('h3', '', title);
    if (count != null) h.append(node('span', 'study-count', String(count)));
    head.append(h); if (side) head.append(side); box.append(head); return box;
  };

  // The allowance is said once, in the Stage bar (studyAllowanceWords); spent and held beyond it stays said here, since
  // nothing new can start until holds are released.
  const over = study.spentMicrousd + study.heldMicrousd - study.capMicrousd;

  const inputs = node('section', 'study-view-inputs study-rail-section');
  inputs.append(node('h3', '', 'Inputs, frozen at these versions'));
  if (!study.inputs.length) inputs.append(node('p', 'study-view-empty', 'No saved inputs selected for this study.'));
  const list = node('ul', 'study-facts');
  for (const [index, input] of study.inputs.entries()) {
    const named = availableInputs.find(row => row.id === input.id && row.revision === input.revision && row.digest === input.digest);
    const item = node('li', 'study-captured-input'); item.append(node('span', '', named?.title ?? `Saved input ${index + 1}`),
      node('span', 'study-sub', [named ? studyInputDescription(named).split(' · ')[0] : '', `version ${input.revision}`].filter(Boolean).join(' · ')));
    if (named) item.title = studyInputDescription(named);
    list.append(item);
  }
  if (study.inputs.length) inputs.append(list, details('Technical details', study.inputs));
  rail.append(inputs);
  if (providers.length) {
    const who = node('section', 'study-rail-section'); who.append(node('h3', '', 'Researchers'));
    const people = node('ul', 'study-facts');
    for (const item of providers) { const li = node('li'); li.append(node('span', '', item.label), node('span', `study-sub ${item.ready ? item.accessVerified ? 'study-ok' : 'study-unverified' : ''}`, item.ready ? (item.accessVerified ? 'Ready' : 'Set up · live access not yet checked') : 'Not set up')); people.append(li); }
    who.append(people); rail.append(who);
  }

  const preparation = node('div', 'study-view-preparation');

  let composer: HTMLElement | null = null;
  if (actions.start && providers.length && study.status === 'active') {
    const first = !Object.values(study.tasks ?? {}).some(task => task.specification?.provider !== 'scientific-gcp-grant');
    const launch = node('section', 'study-view-start study-composer-wrap');
    const launchHead = node('div', 'study-sec-head'); launchHead.append(node('h3', '', first ? 'Start the first experiment' : 'New experiment'), node('span', 'study-view-empty', 'Nothing runs until you press Start'));
    launch.append(launchHead);
    // Researchers as tiles, not a dropdown: each says what it can do and whether it is ready; one not set up stays visible and
    // says why, but can't be chosen (Study target, "Starting work is a composer").
    const tiles = node('fieldset', 'study-researchers'); tiles.append(node('legend', '', 'Researcher'));
    const radios = new Map<string, HTMLInputElement>(), tileWhy = new Map<string, HTMLElement>();
    let chosenId = providers.find(p => p.id === 'claude-managed-google')?.id ?? providers[0]?.id ?? '';
    const chosenProvider = () => providers.find(p => p.id === chosenId);
    for (const item of providers) {
      const tile = node('label', 'study-researcher-tile'); tile.dataset.provider = item.id;
      const radio = node('input'); radio.type = 'radio'; radio.name = `study-researcher-${study.id}`; radio.value = item.id; radio.checked = item.id === chosenId;
      radio.setAttribute('aria-label', item.label);
      radio.addEventListener('change', () => { if (radio.checked) { chosenId = item.id; refresh(); } });
      const why = node('span', 'study-researcher-ready'); tileWhy.set(item.id, why);
      tile.append(radio, node('span', 'study-researcher-words'), why);
      tile.querySelector('.study-researcher-words')!.append(node('strong', '', item.label), node('span', '', researcherCan(item)));
      radios.set(item.id, radio); tiles.append(tile);
    }
    const readiness = node('p', 'study-view-empty study-start-why'); readiness.id = `study-start-why-${study.id}`;
    const reads = node('p', 'study-view-reads');
    const message = node('textarea'); message.rows = 3; message.value = study.objective; message.setAttribute('aria-label', 'Research instruction');
    const budget = node('input'); budget.type = 'number'; budget.min = '0.01'; budget.step = '0.01'; budget.value = (Math.min(study.availableMicrousd / 1e6, .75)).toFixed(2);
    const sessionBudget = node('input'); sessionBudget.type = 'number'; sessionBudget.min = '0.01'; sessionBudget.step = '0.01'; sessionBudget.value = '0.50';
    const sessionBudgetField = field('Claude session budget ($)', sessionBudget);
    let request = crypto.randomUUID(), captured: StudyStart | null = null;
    const start = action('Start research', async () => {
      if (!captured) {
        const maximumUsd = Number(budget.value), chosen = chosenProvider();
        if (!chosen?.ready || chosen.inputReady !== true || !message.value.trim() || !Number.isFinite(maximumUsd) || maximumUsd <= 0 || maximumUsd * 1e6 > study.availableMicrousd || (chosen.maxReservationUsd != null && maximumUsd > chosen.maxReservationUsd)) throw new Error('Choose a ready researcher, a brief and an amount within the Study’s remaining allowance.');
        const budgetCents = chosen.id === 'gemini-google' ? null : Math.round(Number(sessionBudget.value) * 100);
        if (budgetCents != null && (!Number.isFinite(budgetCents) || budgetCents < 1 || budgetCents > (chosen.maxBudgetCents ?? 0) || maximumUsd <= budgetCents / 100)) throw new Error('The Claude session budget must fit its configured cap, and the most it may spend must be larger, to cover a possible provider overrun (How the hold works).');
        captured = { request, provider: chosen.id, message: message.value.trim(), budgetCents, maximumUsd };
        // An uncertain reply retains exactly the same request and fields. No duplicate dispatch on retry.
        for(const control of [...radios.values(),message,budget,sessionBudget]){control.disabled=true;control.dataset.locked='true';}
      }
      await actions.start!(captured); captured = null; request = crypto.randomUUID();
    }, 'study-primary');
    start.setAttribute('aria-describedby', readiness.id);
    const startWords = () => { const amount = Number(budget.value); start.textContent = Number.isFinite(amount) && amount > 0 ? `Start research · holds up to $${amount.toFixed(2)}` : 'Start research'; };
    budget.addEventListener('input', startWords);
    const refresh = () => {
      const selected = chosenProvider();
      for (const item of providers) {
        const radio = radios.get(item.id)!; radio.checked = item.id === chosenId;
        if (radio.dataset.locked !== 'true') radio.disabled = !item.ready;
        tileWhy.get(item.id)!.textContent = item.ready ? (item.accessVerified ? 'Ready' : 'Set up · live access not yet checked') : `Not set up${item.reason ? `: ${item.reason}` : ''}`;
        tileWhy.get(item.id)!.className = `study-researcher-ready ${item.ready ? item.accessVerified ? 'study-ok' : 'study-unverified' : ''}`;
      }
      const pending = Object.values(study.tasks).some(pendingProviderTask);
      readiness.textContent = !selected ? 'Researcher readiness is unavailable. Check again before starting.'
        : !selected.ready ? `${selected.label} isn’t set up${selected.reason ? `: ${selected.reason}` : ''}.`
        : selected.inputReady !== true ? (selected.inputReason || 'Prepare these inputs for this researcher before starting.')
        : study.availableMicrousd <= 0 ? 'Nothing is left of this Study’s allowance.'
        : pending ? 'Review or reconcile the current experiment before starting another.'
        : [selected.reason, selected.inputReason].filter(Boolean).join(' ') || 'Ready to start.';
      reads.textContent = readsWords(selected, study.inputs.length);
      start.disabled = !selected?.ready || selected.inputReady !== true || study.availableMicrousd <= 0 || pending;
      sessionBudgetField.hidden = selected?.id !== 'claude-managed-google';
      if(selected?.maxBudgetCents != null) sessionBudget.max = (selected.maxBudgetCents / 100).toFixed(2);
      if (selected?.maxReservationUsd != null) budget.max = Math.min(selected.maxReservationUsd, study.availableMicrousd / 1e6).toFixed(2);
      startWords();
    };
    let readinessSequence = 0;
    const checkReadiness = async () => {
      if (!actions.readiness || captured) return;
      const sequence = ++readinessSequence;
      try { const next = await actions.readiness(); if(sequence !== readinessSequence)return; providers = next; refresh(); }
      catch { if(sequence === readinessSequence) { providers = providers.map(provider => ({ ...provider, inputReady: false, inputReason: 'Preparation could not be checked. Retry before starting research.' })); start.disabled = true; readiness.textContent = 'Preparation could not be checked. Retry before starting research.'; } }
    };
    const check = node('button', 'study-check-again', 'Check again'); check.type = 'button'; check.hidden = !actions.readiness;
    check.setAttribute('aria-label', 'Check researcher readiness again');
    check.addEventListener('click', () => { void checkReadiness(); });
    preparation.addEventListener('study-preparation-changed', () => { void checkReadiness(); });

    refresh();
    // The composer card (Study target): who, the brief, what it works from (its copy and tool steps in place), the most
    // it may spend (both amounts in view, as the studio's contract requires), then Start with its reason.
    const spendingHelp = node('details', 'study-hold-help'); spendingHelp.append(node('summary', '', 'How the hold works'), node('p', 'study-view-empty', 'Most it may spend is held from this Study’s allowance until the final cost is known. For Claude, the smaller session budget is sent to the provider; the larger hold covers a possible overrun and is not a guaranteed provider ceiling. Gemini settles its hold as an estimate, not a verified invoice. Scientific tool permissions hold their own allowance.'));
    const whoHead = node('div', 'study-composer-head'); whoHead.append(node('p', 'study-composer-label', 'Researcher'), check);
    const legend = tiles.querySelector('legend'); if (legend) legend.className = 'visually-hidden';
    // What it works from, by name (the inputs frozen with this Study), with the copy and tool steps beneath.
    const uses = node('ul', 'study-uses');
    for (const [index, input] of study.inputs.entries()) {
      const named = availableInputs.find(row => row.id === input.id && row.revision === input.revision && row.digest === input.digest);
      const li = node('li'); li.append(node('span', '', named?.title ?? `Saved input ${index + 1}`), node('span', 'study-tag', `${named ? studyInputDescription(named).split(' · ')[0] : 'Input'} · v${input.revision}`)); uses.append(li);
    }
    const card = node('div', 'study-composer');
    const part = (label: string | null, ...children: (HTMLElement | null)[]) => { const box = node('section', 'study-composer-part'); if (label) box.append(node('p', 'study-composer-label', label)); box.append(...children.filter((c): c is HTMLElement => !!c)); return box; };
    const amounts = node('div', 'study-amounts'); amounts.append(field('Most this experiment may spend ($)', budget), sessionBudgetField);
    const go = node('div', 'study-go'); go.append(node('p', 'study-view-empty study-go-note', 'It works on copies of these inputs. You review its evidence before anything it reports counts.'), start);
    card.append(part(null, whoHead, tiles), part(null, field('Brief', message)), part('It works from', study.inputs.length ? uses : null, reads, first ? preparation : null), part(null, amounts, spendingHelp), part(null, go, readiness));
    launch.append(card);
    // Before the first experiment the composer is the page; afterwards New experiment opens it, and Close puts it away
    // (an unsent brief or amount stays as typed).
    if (!first) {
      launch.hidden = true;
      const close = node('button', 'study-quiet-button', 'Close'); close.type = 'button'; close.addEventListener('click', () => { launch.hidden = true; });
      launchHead.append(close);
    }
    composer = launch;
  }

  // Experiments: in the conversation, one step row each in the order they were made (what was done, its state and money,
  // its mark: a tick once accepted, else the lit cell of its phase), with its actions beneath; by phase on the left.
  const reviewButtons = new Map<string, HTMLButtonElement>(), rows = new Map<string, HTMLElement>();
  // Review is a sheet over the rail (Study target): the experiment's name and facts on top, its evidence in the middle,
  // the decision pinned at the bottom; the study stays in view beside it. Its row says Reviewing while it is open.
  const sheet = node('section', 'study-review-sheet'); sheet.hidden = true; sheet.setAttribute('aria-label', 'Review');
  const sheetHead = node('header', 'study-review-head'), sheetBody = node('div', 'study-review-body'), sheetFoot = node('footer', 'study-review-foot');
  const closeSheet = node('button', 'study-review-close', 'Close'); closeSheet.type = 'button'; closeSheet.setAttribute('aria-label', 'Close review');
  sheet.append(sheetHead, sheetBody, sheetFoot);
  const scrim = node('div', 'study-review-scrim'); scrim.hidden = true; scrim.setAttribute('aria-hidden', 'true');
  let reviewing: { row: HTMLElement; opener: HTMLElement | null } | null = null;
  const hideSheet = () => {
    if (!reviewing) return;
    sheet.hidden = true; scrim.hidden = true; delete reviewing.row.dataset.reviewing; delete root.dataset.reviewOpen;
    const opener = reviewing.opener; reviewing = null; opener?.focus?.({ preventScroll: true });
  };
  closeSheet.addEventListener('click', hideSheet);
  scrim.addEventListener('click', () => hideSheet());
  sheet.addEventListener('keydown', (event) => { if ((event as KeyboardEvent).key === 'Escape') { event.stopPropagation(); hideSheet(); } });
  const showSheet = (row: HTMLElement, opener: HTMLElement | null, title: string, facts: string[], panel: HTMLElement) => {
    if (reviewing && reviewing.row !== row) delete reviewing.row.dataset.reviewing;
    reviewing = { row, opener }; row.dataset.reviewing = 'true';
    const head = node('div', 'study-review-title'); head.append(node('h3', '', title), node('p', 'study-sub', facts.filter(Boolean).join(' · ')));
    sheetHead.replaceChildren(head, closeSheet);
    const decide = panel.querySelector<HTMLElement>('.study-decide');
    sheetBody.replaceChildren(panel); sheetFoot.replaceChildren(...(decide ? [decide] : [])); sheetFoot.hidden = !decide;
    const bar = root.closest?.('.study-research-stage')?.querySelector<HTMLElement>('.study-research-stage-bar');
    if (bar) { sheet.style.top = `${bar.offsetHeight}px`; scrim.style.top = `${bar.offsetHeight}px`; }
    sheet.hidden = false; scrim.hidden = false; closeSheet.focus?.({ preventScroll: true });
  };
  const steps: HTMLElement[] = [];
  for (const [request, task] of tasks) {
    const phase = taskPhase(task), name = experimentName(request, task.specification);
    const row = node('div', 'step study-view-task'); row.dataset.status = phase; row.dataset.step = request;
    rows.set(request, row);
    const money = task.chargedMicrousd == null ? `${dollars(task.reservedMicrousd)} held` : `${dollars(task.chargedMicrousd)}${task.usageEstimated ? ' (estimated)' : ''}`;
    const mark = node('span', 'step-mark');
    mark.append(phase === 'completed' ? researchIcon('tick', 'tick') : litCell(PHASE_CELL[phase] ?? 'idle', { size: 12, label: taskState(task) }));
    // What was done, in the record's words: planned, starting, asked to start (its start reply was lost), or started.
    const verb = task.status === 'planned' ? 'Planned' : task.status === 'launching' ? 'Starting' : task.status === 'unknown' ? 'Asked to start' : 'Started';
    const words = node('span', 'step-words'), sub = node('span', 'step-sub');
    sub.append(node('span', 'study-view-task-state', taskState(task)), ' · ', node('span', 'study-view-task-money mm-num', money));
    words.append(node('b', 'study-view-task-name', `${verb} ${name}`), sub);
    row.title = [name, task.execution?.provider ? providerName(task.execution.provider) : '', taskState(task), money].filter(Boolean).join(' · ');
    row.append(mark, words);
    if (task.problem) words.append(node('p', 'study-view-task-problem', task.problem));
    if (task.collectionProblem) words.append(node('p', 'study-view-task-problem', task.collectionProblem));
    const buttons = node('div', 'study-task-actions'), evidencePanel = node('section', 'study-task-evidence');
    if (task.execution?.provider === 'local-researcher' && /^r[0-9]+$/.test(task.execution.id ?? '')) {
      const open = node('a', 'study-link-button', 'Open researcher');
      open.href = `#research/${encodeURIComponent(task.execution.id!)}${task.execution.day ? `?day=${encodeURIComponent(task.execution.day)}` : ''}`;
      buttons.append(open);
    }
    const scientificGrant = task.specification?.provider === 'scientific-gcp-grant';
    const working = ['running', 'waiting', 'launching'].includes(taskPhase(task));
    if (actions.collect && task.chargedMicrousd == null && task.execution?.provider === 'claude-managed-google' && !!task.execution.session && !scientificGrant && working) {
      buttons.append(action('Check for results', () => actions.collect!(request, 'sync')));
      if (task.status === 'running' && task.observation?.providerStatus === 'idle') buttons.append(action('Finish and collect', () => actions.collect!(request, 'close')));
    }
    if (actions.recover && task.specification?.provider === 'claude-managed-google' && !task.execution
        && task.chargedMicrousd == null && ['launching', 'unknown', 'cancel_requested'].includes(task.status)) {
      // What this means, then the operator's step: settling it needs the original session from the Claude Console,
      // which whoever runs this app's Google Cloud account has. Nothing is guessed.
      const recovery = node('details', 'study-task-recovery'); const means = node('summary', '', 'What this means'); recovery.append(means);
      const session = node('input'); session.type = 'text'; session.autocomplete = 'off';
      session.setAttribute('aria-label', 'Original Claude session ID');
      recovery.append(node('p', 'study-view-empty', 'The reply to its start was lost, so Hooke can’t tell whether it ran. Its money stays held until that is settled. Spending stays held until termination and usage are confirmed.'),
        node('p', 'study-recovery-who', 'For whoever runs this app’s Google Cloud account: find the original session in the Claude Console and reconnect it here. Hooke checks that it belongs to this experiment.'),
        field('Original Claude session ID', session), action('Reconnect original session', async () => {
          const original = session.value.trim();
          if (!/^sesn_[A-Za-z0-9_-]{1,120}$/.test(original)) throw new Error('Enter the original Claude session ID beginning with sesn_.');
          await actions.recover!(request, original);
        }));
      words.append(recovery);
    }
    const evidenceWords = phase === 'review' ? 'Review' : task.status === 'completed' ? 'Open' : ['failed', 'cancelled'].includes(task.status) ? 'See what happened' : 'See its evidence so far';
    let evidenceButton: HTMLButtonElement | null = null;
    if (actions.evidence && task.observation?.evidence && !scientificGrant) buttons.append(evidenceButton = action(evidenceWords, async () => {
      const evidence = await actions.evidence!(request); root.dataset.reviewOpen = 'true'; evidencePanel.replaceChildren();
      evidencePanel.append(node('p', '', evidence.completionVerified ? 'Accepted by you. Scientific correctness remains your judgment.' : 'It reports, unreviewed: a proposed result, not a finding. Review the evidence before accepting it.'));
      const streams = evidence.snapshot.streams;
      if (streams && typeof streams === 'object' && !Array.isArray(streams)) {
        for (const [index, [name, events]] of Object.entries(streams).entries()) {
          evidencePanel.append(node('h4', '', name === 'primary' ? `What ${providerName(task.execution?.provider)} did` : `Helper ${index}`));
          for (const event of Array.isArray(events) ? events : [events]) { const record = event && typeof event === 'object' ? event as Record<string, unknown> : {}; const text = evidenceText(event); if (!text) continue;
            const role = speaker(typeof record.role === 'string' ? record.role : typeof record.type === 'string' ? record.type : '');
            // One turn per row: who said it, then what (inert text), so a brief and a reply never look alike.
            const turn = node('div', 'study-turn'); turn.append(node('span', 'study-who', role), node('pre', 'study-evidence-transcript', text)); evidencePanel.append(turn);
          }
        }
      } else evidencePanel.append(node('pre', 'study-evidence-transcript', evidenceText(evidence.snapshot) || 'No transcript text retained. Inspect the evidence details.'));
      const files = Array.isArray(evidence.snapshot.files) ? evidence.snapshot.files : [];
      const fileList = node('ul', 'study-files');
      if (files.length) evidencePanel.append(node('h4', '', 'What it made'), fileList);
      for (const file of files) {
        if (!file || typeof file !== 'object') continue;
        const row = file as Record<string, unknown>, ref = row.retained as Record<string, unknown> | null;
        if (ref && typeof ref.id === 'string' && Number.isInteger(ref.revision) && Number(ref.revision) > 0) {
          const metadata = row.metadata && typeof row.metadata === 'object' ? row.metadata as Record<string, unknown> : {};
          const name = typeof metadata.name === 'string' ? metadata.name : typeof metadata.path === 'string' ? metadata.path.split('/').pop() : null;
          const download = node('a', '', name ? `Download ${name}` : `Download retained file ${ref.id}`);
          download.href = `/api/scientific-files/${encodeURIComponent(ref.id)}?session=${encodeURIComponent(study.session)}&revision=${ref.revision}`;
          download.download = ''; const li = node('li'); li.append(node('span', '', name ?? `Retained file ${ref.id}`));
          const acts = node('span', 'study-file-acts');
          if (actions.openFile) { const fileRef = { id: ref.id as string, revision: Number(ref.revision) }; acts.append(action('Open', () => actions.openFile!(fileRef, name ?? 'Researcher file'), 'study-link-button')); }
          acts.append(download); li.append(acts); fileList.append(li);
        }
        if (typeof row.limitation === 'string') {
          const provider = row.providerFile && typeof row.providerFile === 'object' ? (row.providerFile as Record<string, unknown>).filename : null;
          const li = node('li', 'study-file-missing'); li.append(node('span', '', typeof provider === 'string' ? provider : 'A file'), node('span', 'study-sub', `Not kept: ${row.limitation.replace(/^./, c => c.toLowerCase())}`)); fileList.append(li);
        }
      }
      evidencePanel.append(node('p', 'study-view-empty', `${evidenceCost(evidence.snapshot, task.execution?.provider, task.reservedMicrousd)} Evidence ${evidence.reference.sha256.slice(0, 8)}.`), details('Technical details', evidence));
      if (actions.review && task.chargedMicrousd == null) {
        const review = node('textarea'); review.rows = 3; review.setAttribute('aria-label', 'Evidence review');
        const reviewButtons = node('div', 'study-task-actions');
        for (const [outcome, label] of [['completed', 'Accept as completed'], ['failed', 'Record as failed'], ['cancelled', 'Record as cancelled']] as const) {
          const save = action(label, async () => {
            if (!review.value.trim()) throw new Error('Describe what the retained evidence demonstrates and its limitations.');
            await actions.review!(request, evidence.reference.sha256, outcome, review.value.trim());
          }, outcome === 'completed' ? 'study-accept' : '');
          // The server validates this exact evidence hash and termination, archive and cost facts again.
          // Incomplete snapshots remain inspectable but never offer acceptance as a default outcome.
          const seen = task.observation, gemini = task.execution?.provider === 'gemini-google';
          const completeSnapshot = seen?.sealedEvidence === true && seen.evidence?.sha256 === evidence.reference.sha256 && (gemini ? seen.providerStatus === 'returned' : seen.providerStatus === 'terminated' && seen.listCostMicrousd != null);
          const archived = task.managedActions?.archive === 'acknowledged' || (['pending', 'unknown'].includes(task.managedActions?.archive ?? '') && !!seen?.archivedAt);
          save.disabled = !completeSnapshot || (outcome === 'completed' && (task.status === 'cancel_requested' || seen?.continuationRequired === true || (!gemini && !archived)));
          reviewButtons.append(save);
        }
        const decide = node('div', 'study-decide');
        evidencePanel.append(decide);
        decide.append(field('Your review and limitations', review), reviewButtons,
          node('p', 'study-view-empty', `Your decision is tied to evidence ${evidence.reference.sha256.slice(0, 8)}. If newer evidence arrives first, you’ll be asked to look again.`),
          node('p', 'study-view-empty', task.execution?.provider === 'gemini-google' ? 'Gemini must have returned its complete bounded turn. Its reserved allowance is recorded as an estimated cost. A requested tool continuation cannot be accepted as completed research.' : 'You can accept once the session has ended, its cost is known and its record is archived. If its evidence changes, review the new version first.'));

      }
      showSheet(row, evidenceButton, experimentName(request, task.specification), [providerName(task.execution?.provider), taskState(task), money], evidencePanel);
    }, phase === 'review' ? 'study-primary' : ''));
    if (phase === 'review' && evidenceButton) reviewButtons.set(request, evidenceButton);
    if (buttons.children.length) words.append(buttons);
    steps.push(row);
  }

  // Your move: the only boxed things on the page, each one decision with one action.
  const moves = section('study-moves', 'Your move', undefined);
  for (const [request, task] of tasks) {
    const phase = taskPhase(task), name = experimentName(request, task.specification);
    if (phase === 'review') {
      const card = node('div', 'study-move'); card.append(node('p', '', `${name} finished. Review its evidence.`), node('p', 'study-move-why', 'Nothing it reported counts until you decide.'));
      const go = node('button', 'study-primary', 'Review'); go.type = 'button'; go.setAttribute('aria-label', `Review ${name}`);
      go.addEventListener('click', () => { reveal(rows.get(request)); reviewButtons.get(request)?.dispatchEvent(new Event('click')); });
      if (reviewButtons.has(request)) card.append(go); moves.append(card);
    } else if (task.status === 'unknown') {
      const card = node('div', 'study-move'); card.append(node('p', '', `${name} may or may not have started.`),
        node('p', 'study-move-why', `The reply to its start was lost. ${dollars(task.reservedMicrousd)} stays held until the original run is checked; nothing here can settle it by guessing.`));
      const go = node('button', '', 'What this means'); go.type = 'button'; go.setAttribute('aria-label', `What this means for ${name}`);
      go.addEventListener('click', () => { const row = rows.get(request); const means = row?.querySelector<HTMLDetailsElement>('details.study-task-recovery'); if (means) means.open = true; reveal(row); (means?.querySelector<HTMLElement>('summary') ?? row?.querySelector<HTMLElement>('button'))?.focus?.({ preventScroll: true }); });
      card.append(go); moves.append(card);
    }
  }
  const needs = studyNeeds(study); if (needs) moves.append(node('p', 'study-view-needs visually-hidden', needs));
  const anyMove = moves.querySelectorAll('.study-move').length > 0;
  const countEl = moves.querySelector('h3'); if (anyMove && countEl) countEl.append(node('span', 'study-count', String(moves.querySelectorAll('.study-move').length)));

  // What we know: findings in the scientist's words, each standing on the experiments it cites.
  const terminal = tasks.filter(([, task]) => ['completed', 'failed', 'cancelled'].includes(task.status));
  const addFinding = node('details', 'study-add-finding');
  const findings = section('study-view-findings', 'What we know', study.findings?.length || undefined, actions.finding && terminal.length ? addFinding : null);
  for (const finding of study.findings ?? []) {
    const item = node('div', 'study-view-finding'); item.append(node('p', '', finding.statement));
    const cites = node('p', 'study-view-cites'); cites.append(document.createTextNode?.('From ') ?? 'From ');
    finding.tasks.forEach((t, i) => { const status = study.tasks?.[t]?.status; const chip = node('span', `study-chip ${status === 'completed' ? 'study-chip-ok' : status === 'failed' ? 'study-chip-bad' : ''}`, experimentName(t, study.tasks?.[t]?.specification)); if (i) cites.append(', '); cites.append(chip); });
    item.append(cites); findings.append(item);
  }
  if (!study.findings?.length) findings.append(node('p', 'study-view-empty', 'None yet.'));
  findings.append(node('p', 'study-view-empty', 'A finding is what you concluded from reviewed experiments. It is not proof, and a researcher’s report is not one until you write it here.'));
  if (actions.finding && terminal.length) {
    const text = node('textarea'); text.rows = 3; text.setAttribute('aria-label', 'Finding'); const citations = node('fieldset'); citations.append(node('legend', '', 'Supporting experiments'));
    const checks = terminal.map(([id, task]) => { const check = node('input'); check.type = 'checkbox'; citations.append(field(experimentName(id, task.specification), check)); return { id, check }; });
    const request = crypto.randomUUID(); let captured: { statement: string; tasks: string[] } | null = null;
    addFinding.append(node('summary', '', 'Add a finding'), field('Finding', text), citations, action('Save finding', async () => {
      captured ??= { statement: text.value.trim(), tasks: checks.filter(item => item.check.checked).map(item => item.id) };
      if (!captured.statement || !captured.tasks.length) { captured = null; throw new Error('Write a finding and select its supporting experiments.'); }
      text.disabled = true;text.dataset.locked='true'; checks.forEach(item => { item.check.disabled = true;item.check.dataset.locked='true'; });
      await actions.finding!(request, captured.statement, captured.tasks);
    }));
  }

  // The Study home in the researcher Tab's shape (the workspace-feel plan; the mock's studyHome()): its experiments by
  // phase on the left (then its inputs, its researchers and Study setup), its conversation in the middle (your question,
  // one step row per experiment, then Your move), and the write-up on paper on the right. Every control keeps its action
  // and guards; only where it sits changed.
  const column = (tag: 'aside' | 'section', className: string, label: string) => { const box = node(tag, className); box.setAttribute('aria-label', label); return box; };
  const grid = node('div', 'rpage'); grid.dataset.files = 'open'; grid.dataset.one = 'conv'; grid.dataset.overlay = 'false';
  const left = column('aside', 'rfiles study-researchers-col', 'Experiments and inputs');
  const leftHead = node('div', 'rhead'); leftHead.append(node('h3', 'rcol-h', 'Experiments'));
  const leftBody = node('div', 'rfiles-body');
  // Below 1240 px of page the left column folds into the conversation header's toggle and opens as a 240 px layer over
  // the conversation (the researcher Tab's own way); Escape, a click outside or choosing a row closes it, and focus goes
  // back to the toggle.
  const filesToggle = node('button', 'rtab-icon-button files-toggle'); filesToggle.type = 'button';
  filesToggle.setAttribute('aria-label', 'Experiments and inputs'); filesToggle.title = 'Experiments and inputs';
  filesToggle.setAttribute('aria-expanded', 'false'); filesToggle.append(researchIcon('sidebar'));
  let outside: ((event: Event) => void) | null = null;
  const setOverlay = (open: boolean, refocus = false) => {
    grid.dataset.overlay = String(open); filesToggle.setAttribute('aria-expanded', String(open));
    if (outside) { document.removeEventListener?.('pointerdown', outside, true); outside = null; }
    if (open) {
      outside = (event: Event) => { if (!left.contains(event.target as Node) && !filesToggle.contains(event.target as Node)) setOverlay(false); };
      document.addEventListener?.('pointerdown', outside, true);
      left.querySelector<HTMLButtonElement>('button')?.focus?.({ preventScroll: true });
    } else if (refocus) filesToggle.focus?.({ preventScroll: true });
  };
  filesToggle.addEventListener('click', () => setOverlay(grid.dataset.overlay !== 'true'));
  left.addEventListener('keydown', (event) => { if ((event as KeyboardEvent).key === 'Escape' && grid.dataset.overlay === 'true') { event.stopPropagation(); setOverlay(false, true); } });
  const PHASES = [['needs', 'Needs you'], ['progress', 'In progress'], ['done', 'Done']] as const;
  const GROUPS: Record<string, 'needs' | 'progress' | 'done'> = { review: 'needs', unknown: 'needs', waiting: 'progress', running: 'progress', launching: 'progress', cancel_requested: 'progress', planned: 'progress', failed: 'done', finished: 'done', completed: 'done', cancelled: 'done' };
  for (const [key, label] of PHASES) {
    const members = tasks.filter(([, task]) => (GROUPS[taskPhase(task)] ?? 'progress') === key);
    if (!members.length) continue;
    const group = node('div', 'sgroup');
    const head = node('div', 'sgroup-h is-static');
    const words = node('span', 'sgroup-words'); words.append(node('span', 'sgroup-n', label), node('span', 'sgroup-c', `${members.length} experiment${members.length === 1 ? '' : 's'}`));
    head.append(words); group.append(head);
    for (const [request, task] of members) {
      const name = experimentName(request, task.specification);
      const child = node('button', 'frow srow'); child.type = 'button'; child.title = `${name}: ${taskState(task)}`;
      const childWords = node('span', 'frow-words');
      childWords.append(node('span', 'frow-name', name), node('span', 'frow-meta mm-num', [task.execution?.provider ? providerName(task.execution.provider) : '', taskState(task)].filter(Boolean).join(' · ')));
      child.append(litCell(PHASE_CELL[taskPhase(task)] ?? 'idle', { size: 12, label: taskState(task) }), childWords);
      // A row brings its experiment's step into view in the conversation, and lets it glow once.
      child.addEventListener('click', () => {
        if (grid.dataset.overlay === 'true') setOverlay(false);
        if (grid.dataset.one === 'art') showOne('conv');
        const row = rows.get(request); reveal(row); row?.classList.add('is-glow'); setTimeout(() => row?.classList.remove('is-glow'), 1700);
        (row?.querySelector<HTMLButtonElement>('button'))?.focus?.({ preventScroll: true });
      });
      group.append(child);
    }
    leftBody.append(group);
  }
  if (!tasks.length) leftBody.append(node('p', 'rfiles-empty', Object.keys(study.experiments ?? {}).length ? 'Workflow experiments are shown in the Study.' : 'None yet'));
  leftBody.append(rail);
  // Study setup: copying large inputs to Google Cloud, the tools it may use and its GPU jobs (workspace-study.ts mounts
  // them here). Before the first experiment they are part of starting it, in its composer card instead.
  if (providers.length && !(composer && !tasks.length)) {
    const setup = node('details', 'study-setup'); setup.append(node('summary', '', 'Study setup'), preparation);
    leftBody.append(setup);
  }
  left.append(leftHead, leftBody);

  // Its conversation: one Tab, "Study" (and in one column the write-up's Tab beside it). Your question first, as your
  // words, judged by its criterion; then each experiment as a step; then what needs you; then a new experiment.
  const centre = column('section', 'rconv study-conversation', 'What the Study has done');
  const centreHead = node('div', 'rhead rconv-head');
  const tabs = node('div', 'ctabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Study');
  const studyTab = node('div', 'ctab is-conv'); studyTab.setAttribute('aria-selected', 'true');
  const tabButton = node('button', 'ctab-btn'); tabButton.type = 'button'; tabButton.setAttribute('role', 'tab'); tabButton.setAttribute('aria-selected', 'true');
  tabButton.append(researchIcon('report'), node('span', '', 'Study')); studyTab.append(tabButton);
  const writeupTab = node('div', 'ctab ctab-art'); writeupTab.setAttribute('aria-selected', 'false');
  const writeupButton = node('button', 'ctab-btn'); writeupButton.type = 'button'; writeupButton.setAttribute('role', 'tab'); writeupButton.setAttribute('aria-selected', 'false'); writeupButton.tabIndex = -1;
  writeupTab.append(writeupButton); tabs.append(studyTab, writeupTab);
  const showOne = (one: 'conv' | 'art') => {
    grid.dataset.one = one;
    for (const [tab, button, selected] of [[studyTab, tabButton, one === 'conv'], [writeupTab, writeupButton, one === 'art']] as const) {
      tab.setAttribute('aria-selected', String(selected)); button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
    }
  };
  tabButton.addEventListener('click', () => showOne('conv')); writeupButton.addEventListener('click', () => showOne('art'));
  centreHead.append(filesToggle, tabs);
  const question = node('div', 'm-you'); question.append(node('div', 'm-user', study.objective), criterion);
  log.append(question, notice);
  for (const problem of study.controlProblems ?? []) log.append(node('p', 'study-warn', `Stop needs checking: ${problem}`));
  if (over > 0) log.append(node('p', 'study-warn', `Spent and held are ${dollars(over)} more than the allowance. Nothing new can start until holds are released.`));
  log.append(...steps);
  if (study.claims?.length) {
    const claims = node('section', 'study-reported-claims'); claims.append(node('h3', '', 'What researchers report'));
    claims.append(node('p', 'study-view-empty', 'Evidence-backed statements for review; agreement alone does not establish correctness.'));
    for (const claim of study.claims) {
      const row = node('details'); row.append(node('summary', '', `${claim.byLabel}: ${claim.statement}`));
      if (claim.limitations) row.append(node('p', '', claim.limitations));
      if (claim.dependsOn.length) row.append(node('p', 'study-sub', `Depends on: ${claim.dependsOn.join(', ')}`));
      for (const evidence of claim.evidence) {
        row.append(actions.openFile ? action(`Open ${evidence.relation} evidence · v${evidence.artifact.revision}`,
          () => actions.openFile!(evidence.artifact, 'Claim evidence'), 'study-link-button')
          : node('p', '', `${evidence.relation}: ${evidence.artifact.id} · v${evidence.artifact.revision}`));
      }
      for (const change of study.claimChanges ?? []) if (change.claim === claim.request)
        row.append(node('p', 'study-warn', `${change.byLabel} challenges this: ${change.reason}`));
      claims.append(row);
    }
    log.append(claims);
  }
  if (!tasks.length && !Object.keys(study.experiments ?? {}).length) log.append(node('p', 'm-note study-view-empty', study.status === 'stopped' ? 'None were run.' : providers.length ? 'None yet. Prepare the inputs, choose a researcher, then Start research.' : 'None yet.'));
  if (anyMove) log.append(moves);
  // Where Start would be, rather than a Study that can't run without a word.
  if (!providers.length && study.status === 'active') log.append(node('p', 'm-note study-view-unavailable', STUDY_UNAVAILABLE));
  if (composer && !tasks.length) log.append(composer);
  else if (composer) { const next = launchSlot(); if (next) log.append(next); log.append(composer); }
  const centreBody = node('div', 'rconv-body'); centreBody.append(log);
  centre.append(centreHead, centreBody);
  // A Study takes no messages of its own: its composer (the shared one, without + or a model chip) asks the Stage bar's
  // Assistant, which opens beside the write-up and can act on this Study.
  if (actions.ask) {
    const dock = node('div', 'rcomp');
    const compose = new Composer(null, { label: 'Ask the Assistant about this study', placeholder: 'Ask the Assistant about this study' });
    compose.root.classList.add('study-ask-composer');
    compose.send.title = 'Ask in Assistant (Enter)';
    compose.send.setAttribute('aria-label', 'Ask in Assistant');
    const status = node('p', 'study-ask-status'); status.setAttribute('role', 'status');
    const setup = actions.setupAssistant ? action('Assistant model settings', actions.setupAssistant, 'study-ask-setup') : null;
    const ready = () => {
      const reason = actions.askBlocked?.() ?? null;
      status.textContent = reason || 'Replies open in the Assistant beside this Study.';
      compose.send.disabled = Boolean(reason);
      if (setup) setup.hidden = !reason;
      return !reason;
    };
    compose.root.addEventListener('assistant-readiness', ready);
    compose.field.addEventListener('input', ready);
    compose.field.addEventListener('focus', ready);
    ready();
    compose.root.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = compose.field.value.trim();
      if (!text || !ready()) return;
      if (actions.ask?.(text) !== false) compose.set('');
      else { ready(); if (!actions.askBlocked?.()) status.textContent = 'Your question was not sent. Keep it here and try again.'; }
    });
    dock.append(status); if (setup) dock.append(setup);
    dock.append(compose.root); centre.append(dock);
  }
  // The write-up on paper: the question as its title, what was concluded from reviewed experiments, and a finding to add.
  const right = column('section', 'rart study-writeup', 'The write-up');
  const rightHead = node('div', 'rhead rart-head');
  const pill = node('span', 'spill is-static'); pill.title = 'The Study’s state';
  const state: LitCellState = studyNeeds(study) ? 'needs' : study.status === 'stopped' ? 'stopped' : study.status === 'paused' ? 'still'
    : tasks.some(([, task]) => ['running', 'launching'].includes(taskPhase(task))) ? 'working' : tasks.length ? 'partial' : 'idle';
  pill.append(litCell(state, { size: 12, label: studyState(study) }), statePill);
  statePill.className = 'spill-w';
  writeupButton.append(litCell(state, { size: 12, decorative: true }), node('span', '', 'The write-up'));
  rightHead.append(pill, node('span', 'rflex'), controls);
  const desk = node('div', 'desk'); desk.dataset.fit = 'width';
  const pages = node('div', 'desk-pages'), paper = node('article', 'page'), inside = node('div', 'page-in');
  paper.dataset.theme = 'paper';
  inside.append(node('h3', 'study-writeup-title', study.objective), node('p', 'byline', `Judged by ${study.criteria}`), findings);
  paper.append(inside); pages.append(paper); desk.append(pages);
  right.append(rightHead, desk);
  grid.append(left, centre, right);
  const wrap = node('div', 'rwrap'); wrap.append(grid);
  root.append(wrap, scrim, sheet);
  if (actions.localWorkflows) {
    mountStudyWorkflows(rail, study, availableInputs, actions.openFile, fetch, log);
    mountLocalStudyResearcher(rail, study);
  }

  /** "New experiment" after the experiments: the composer is on this page, so it goes there and puts you in its brief. */
  function launchSlot(): HTMLElement | null {
    if (!composer || !tasks.length) return null;
    const go = node('button', 'study-new-experiment', 'New experiment'); go.type = 'button';
    go.addEventListener('click', () => { composer.hidden = false; reveal(composer); composer.querySelector<HTMLTextAreaElement>('textarea')?.focus?.({ preventScroll: true }); });
    return go;
  }
}

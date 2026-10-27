import { afterEach, describe, expect, it, vi } from 'vitest';
import { experimentName, renderStudy, evidenceText, evidenceCost, studyAllowanceTitle, studyAllowanceWords } from './study-view';
import type { Effort, StudyEvidence } from './research-efforts';
import { readFileSync } from 'node:fs';
import { TestElement, fire, syntheticDOM } from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());

const study: Effort = { id: 'pabc', session: 'p', revision: 3, digest: 'd', objective: 'Which training data transfers best?', criteria: 'Held-out error',
  status: 'active', capMicrousd: 1_000_000, spentMicrousd: 120_000, heldMicrousd: 250_000, availableMicrousd: 630_000, inputs: [],
  tasks: { a: { status: 'running', specification: { title: 'Linear baseline' }, reservedMicrousd: 250_000, chargedMicrousd: null },
    b: { status: 'completed', specification: { kind: 'with-replogle' }, reservedMicrousd: 200_000, chargedMicrousd: 120_000, usageEstimated: true },
    c: { status: 'unknown', reservedMicrousd: 0, chargedMicrousd: null, problem: 'The launch reply was lost; reconcile it.' } },
  findings: [{ request: 'f1', statement: 'Adding the Replogle screens helped.', tasks: ['b'] }] };

function draw(s: Effort) {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const root = new TestElement('article'), control = vi.fn();
  renderStudy(root as unknown as HTMLElement, s, { control }, [{ id: 'fixture', label: 'Synthetic provider', ready: false }]);
  return { root, control, text: () => root.textContent ?? '' };
}

describe('a study in its Tab', () => {
  it('says what needs you first, then its objective, experiments with state and money, and findings with their experiments', () => {
    const { root, text } = draw(study);
    expect(root.querySelector('.study-view-needs')?.textContent).toContain('1 experiment needs checking');
    expect(text()).toContain('Which training data transfers best?');expect(text()).toContain('Held-out error');
    const rows = root.querySelectorAll('.study-view-task');
    // In the conversation, one step each in the order they were made: what was done in the record's words, then its state
    // and money ("Asked to start": its start reply was lost, so it is never said to have started).
    expect(rows.map((r) => r.textContent)).toEqual(['Started Linear baselineRunning · $0.25 held', 'Started with-replogleAccepted · $0.12 (estimated)',
      'Asked to start cNeeds checking · $0.00 heldThe launch reply was lost; reconcile it.']);
    // Its mark is the lit cell of its phase, a tick once accepted; never a coloured dot or coloured words.
    expect(rows.map((r) => r.querySelector('.step-mark')!.children[0].getAttribute('data-state') ?? r.querySelector('.step-mark')!.children[0].getAttribute('class')))
      .toEqual(['working', 'tick', 'needs']);
    expect(root.querySelector('.study-dot')).toBe(null);
    expect(root.querySelector('.study-move')?.textContent).toContain('c may or may not have started.'); // Your move says it and leads there
    // The allowance is said once, in its Tab's Stage bar (workspace-study.ts), never again on the page.
    expect(studyAllowanceWords(study)).toBe('$0.12 · $0.25 held of $1.00');
    expect(studyAllowanceTitle(study)).toBe('$0.12 spent, $0.25 held, $0.63 left of $1.00');
    expect(studyAllowanceWords({ ...study, spentMicrousd: 0, heldMicrousd: 0 })).toBe('$1.00 allowance');
    expect(root.querySelector('.study-meter')).toBe(null);
    expect(root.querySelector('.study-view-cites')?.textContent).toBe('From with-replogle');
    expect(text()).toContain('No saved inputs selected for this study.');
  });
  it('is laid out as the researcher Tab: its researchers by phase on the left, its conversation in the middle, the write-up on paper on the right', () => {
    const { root } = draw(study);
    const page = root.querySelector('.rpage')!;
    expect(root.className).toBe('study-view study-home');
    expect(page.children.map((n) => n.className)).toEqual(['rfiles study-researchers-col', 'rconv study-conversation', 'rart study-writeup']);
    // Left: Needs you, In progress, Done, each experiment a row with its lit cell, then the frozen inputs, its
    // researchers and Study setup (where copying large inputs, the tools it may use and its GPU jobs are mounted).
    const left = page.children[0];
    expect(left.querySelector('.rcol-h')!.textContent).toBe('Experiments');
    const setup = left.querySelector('.study-setup')!;
    expect(setup.querySelector('summary')!.textContent).toBe('Study setup');
    expect(setup.querySelector('.study-view-preparation')).toBeTruthy();
    expect(left.querySelectorAll('.sgroup-n').map((n) => n.textContent)).toEqual(['Needs you', 'In progress', 'Done']);
    expect(left.querySelectorAll('.srow').map((n) => n.querySelector('.frow-name')!.textContent)).toEqual(['c', 'Linear baseline', 'with-replogle']);
    expect(left.querySelectorAll('.srow').every((n) => !!n.querySelector('[data-kind="lit-cell"]'))).toBe(true);
    expect(left.textContent).toContain('No saved inputs selected for this study.');
    // Middle: your question as your words, judged by its criterion, then what needs you and the experiments.
    const centre = page.children[1];
    expect(centre.querySelector('.m-user')!.textContent).toBe('Which training data transfers best?');
    expect(centre.querySelector('.study-criterion')!.textContent).toBe('Judged by Held-out error');
    expect(centre.querySelectorAll('.study-view-task')).toHaveLength(3);
    // The conversation holds only the conversation: no second list of experiments, no setup, no money panel.
    expect(centre.querySelector('.study-view-experiments')).toBe(null);
    expect(centre.querySelector('.study-view-preparation')).toBe(null);
    expect(centre.querySelector('.study-moves')!.textContent).toContain('c may or may not have started.');
    // Right: the write-up on a paper page, the question as its title and what we know.
    const right = page.children[2];
    expect(right.querySelector('.page')!.dataset.theme).toBe('paper');
    expect(right.querySelector('.study-writeup-title')!.textContent).toBe('Which training data transfers best?');
    expect(right.querySelector('.study-view-cites')!.textContent).toBe('From with-replogle');
    expect(right.querySelector('.spill')!.textContent).toBe('Active');
  });
  it('pauses or continues at once; Stop asks once more, since a stopped study is kept, not resumed', async () => {
    const { root, control } = draw(study);
    const [toggle, stop] = root.querySelector('.study-view-controls')!.querySelectorAll('button');
    fire(toggle, 'click');await Promise.resolve();expect(control).toHaveBeenLastCalledWith('pause');
    fire(stop, 'click');await Promise.resolve();expect(control).toHaveBeenCalledTimes(1);expect(stop.textContent).toContain('Stop for good?');
    fire(stop, 'click');await Promise.resolve();expect(control).toHaveBeenLastCalledWith('stop');
    expect(draw({ ...study, status: 'paused' }).root.querySelector('.study-view-controls')!.querySelector('button')?.textContent).toBe('Continue');
    expect(draw({ ...study, status: 'stopped' }).root.querySelector('.study-view-controls')!.querySelectorAll('button')).toHaveLength(0);
  });
  it('folds its left column below 1240 px into the conversation header’s toggle, a 240 px layer that Escape, a click outside or a row closes', () => {
    const { root } = draw(study);
    const page = root.querySelector('.rpage')!, left = page.children[0];
    const head = page.children[1].querySelector('.rconv-head')!;
    const toggle = head.children[0];
    expect(toggle.className).toBe('rtab-icon-button files-toggle');
    expect(toggle.getAttribute('aria-label')).toBe('Experiments and inputs');
    expect(head.children[1].className).toBe('ctabs');
    let focused: TestElement | null = null;
    Object.assign(toggle, { focus: () => { focused = toggle; } });
    fire(toggle, 'click');
    expect(page.dataset.overlay).toBe('true'); expect(toggle.getAttribute('aria-expanded')).toBe('true');
    fire(left, 'keydown', { key: 'Escape' });
    expect(page.dataset.overlay).toBe('false'); expect(focused).toBe(toggle);
    fire(toggle, 'click');
    fire(left.querySelector('.srow')!, 'click'); // choosing a row brings its step into view and closes the layer
    expect(page.dataset.overlay).toBe('false');
    fire(toggle, 'click');
    fire(document as unknown as TestElement, 'pointerdown', { target: page.children[2] });
    expect(page.dataset.overlay).toBe('false');
    // The CSS shows the toggle and the layer from the same rules as the researcher Tab's (container queries on the page).
    const css = readFileSync(new URL('./workspace-feel.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const two = css.slice(css.indexOf('@container rpage (min-width: 860px) and (max-width: 1239px)'));
    expect(two.slice(0, two.indexOf('\n}'))).toMatch(/\.files-toggle \{ display: inline-grid/);
    expect(two.slice(0, two.indexOf('\n}'))).toMatch(/\[data-overlay='true'\] \.rfiles \{[^}]*width: 240px/);
    // In one column the write-up is the second Tab of the conversation's header.
    const writeup = head.querySelector('.ctab-art')!.querySelector('button')!;
    expect(writeup.textContent).toBe('The write-up');
    fire(writeup, 'click');
    expect(page.dataset.one).toBe('art');
  });
  it('its composer asks the Assistant: a Study takes no messages of its own', () => {
    const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
    const root = new TestElement('article'), ask = vi.fn((_text: string) => true);
    renderStudy(root as unknown as HTMLElement, study, { control: vi.fn(), ask });
    const form = root.querySelector('.rconv')!.querySelector('.rcomp')!.querySelector('.study-ask-composer')!;
    const field = form.querySelector('textarea') as unknown as { value: string; placeholder: string; getAttribute(n: string): string | null };
    expect(field.placeholder).toBe('Ask the Assistant about this study');
    expect(form.querySelector('.mm-composer-send')).not.toBeNull(); // a send arrow; no + and no model chip
    expect(form.querySelector('.mm-composer-attach')).toBeNull(); expect(form.querySelector('.mm-composer-model')).toBeNull();
    field.value = 'Which field has the larger nuclei?';
    fire(form, 'submit');
    expect(ask).toHaveBeenCalledWith('Which field has the larger nuclei?');
    expect(field.value).toBe(''); // sent through the Assistant
    ask.mockReturnValueOnce(false); field.value = 'Again'; fire(form, 'submit');
    expect(field.value).toBe('Again'); // not sent: the words stay
    expect(draw(study).root.querySelector('.study-ask-composer')).toBe(null); // no Assistant here, no composer
  });
  it('says plainly when there is nothing yet', () => {
    const { text } = draw({ ...study, tasks: {}, findings: [] });
    expect(text()).toContain('None yet. Prepare the inputs, choose a researcher, then Start research.');
    expect(text()).toContain('A finding is what you concluded from reviewed experiments. It is not proof');
  });
  it('names an experiment by its specification, else its request', () => {
    expect(experimentName('r9', { name: ' Sweep ' })).toBe('Sweep');expect(experimentName('r9', {})).toBe('r9');
  });
});


const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const byText = (root: TestElement, label: string) => root.querySelectorAll('button').find(button => button.textContent === label)!;
// Start names the most it holds ("Start research · holds up to $0.75").
const startButton = (root: TestElement) => root.querySelectorAll('button').find(button => button.textContent.startsWith('Start research'))!;
describe('ordinary managed Study controls (synthetic DOM)', () => {
  it('does not launch on render, reserves an explicit Claude margin, and retries an uncertain reply with identical admission', async () => {
    vi.stubGlobal('document', syntheticDOM().document);
    const root = new TestElement('article'), start = vi.fn().mockRejectedValueOnce(new Error('Reply lost')).mockResolvedValue(undefined);
    renderStudy(root as unknown as HTMLElement, { ...study, tasks: {}, spentMicrousd: 0, heldMicrousd: 0, availableMicrousd: 1e6 }, { control: vi.fn(), start },
      [{ id: 'claude-managed-google', label: 'Claude', ready: true, inputReady: true, maxBudgetCents: 50, maxReservationUsd: .75 }]);
    expect(start).not.toHaveBeenCalled();
    expect(startButton(root).textContent).toBe('Start research · holds up to $0.75');
    expect(root.textContent).toContain('nothing else in this Project'); // what it reads, said plainly
    fire(startButton(root), 'click'); await settle();
    expect(start.mock.calls[0][0]).toMatchObject({ provider: 'claude-managed-google', budgetCents: 50, maximumUsd: .75 });
    expect(root.textContent).toContain('Reply lost');
    expect(root.querySelector('textarea')!.disabled).toBe(true);
    fire(startButton(root), 'click'); await settle();
    expect(start.mock.calls[1][0]).toEqual(start.mock.calls[0][0]);
  });
  it('keeps unavailable providers and unresolved prior executions from enabling Start', () => {
    vi.stubGlobal('document', syntheticDOM().document);
    const root = new TestElement('article'), start = vi.fn();
    renderStudy(root as unknown as HTMLElement, study, { control: vi.fn(), start }, [{ id: 'claude-managed-google', label: 'Claude', ready: false, reason: 'Activation required' }]);
    expect(startButton(root).disabled).toBe(true); expect(root.textContent).toContain('Activation required'); expect(start).not.toHaveBeenCalled();
    // The researcher that isn't set up stays listed, says why, and can't be chosen.
    const tile = root.querySelector('.study-researcher-tile')!;
    expect(tile.textContent).toContain('Not set up: Activation required'); expect(tile.querySelector('input')!.disabled).toBe(true);
  });
  it('reviews the displayed hash, shows inert transcript text and exposes retained output downloads', async () => {
    vi.stubGlobal('document', syntheticDOM().document);
    const root = new TestElement('article'), review = vi.fn().mockResolvedValue(undefined), openFile = vi.fn().mockResolvedValue(undefined);
    const evidence: StudyEvidence = { reference: { id: 'e', revision: 2, digest: 'd', sha256: 'exact-display-hash' }, completionVerified: false,
      snapshot: { streams: { primary: [{ content: [{ text: '<script>untrusted</script>' }] }] }, files: [{ retained: { id: 'output', revision: 3 } }] } };
    renderStudy(root as unknown as HTMLElement, { ...study, tasks: { a: { ...study.tasks.a, execution: { provider: 'claude-managed-google', session: 'session_1' }, managedActions: {archive:'acknowledged'}, observation: { evidence: { sha256: 'exact-display-hash' }, sealedEvidence: true, providerStatus: 'terminated', listCostMicrousd: 1000 } } } }, { control: vi.fn(), evidence: async () => evidence, review, openFile });
    fire(byText(root, 'Review'), 'click'); await settle();
    expect(root.querySelector('.study-evidence-transcript')?.textContent).toBe('<script>untrusted</script>');
    expect(root.querySelectorAll('script')).toHaveLength(0); expect(root.querySelector('a')?.textContent).toBe('Download retained file output');
    fire(byText(root, 'Open'), 'click'); await settle(); expect(openFile).toHaveBeenCalledWith({ id: 'output', revision: 3 }, 'Researcher file');
    root.querySelector('textarea')!.value = 'Checked these exact outputs';
    expect(root.querySelector('select')).toBe(null); expect(review).not.toHaveBeenCalled();
    expect(root.textContent).toContain('It reports, unreviewed');
    fire(byText(root, 'Accept as completed'), 'click'); await settle();
    expect(review).toHaveBeenCalledWith('a', 'exact-display-hash', 'completed', 'Checked these exact outputs');
    // The review is one sheet: its header names the experiment, its row says Reviewing, and Close puts it away.
    const sheet = root.querySelector('.study-review-sheet')!;
    expect(sheet.hidden).toBe(false); expect(sheet.querySelector('h3')?.textContent).toBe('Linear baseline');
    expect(root.querySelector('.study-view-task')!.dataset.reviewing).toBe('true');
    fire(byText(root, 'Close'), 'click'); expect(sheet.hidden).toBe(true);
  });
  it('extracts Gemini text from the separate persisted provider response', () => {
    expect(evidenceText({ response: { response: { candidates: [{ content: { parts: [{ text: 'Independent Gemini answer' }] } }] } } })).toBe('Independent Gemini answer');
  });
});

it('requires this Study’s input readiness, retains typed instructions while refreshing, and never starts on readiness reads', async () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const root = new TestElement('article'), start = vi.fn(), provider = {id:'claude-managed-google',label:'Claude',ready:true,inputReady:false,inputReason:'Prepare captured scientific inputs',maxBudgetCents:50,maxReservationUsd:.75};
  const readiness = vi.fn().mockResolvedValue([{...provider,inputReady:true,inputReason:'Captured inputs prepared'}]);
  renderStudy(root as unknown as HTMLElement,{...study,tasks:{},availableMicrousd:1e6},{control:vi.fn(),start,readiness},[provider]);
  expect(startButton(root).disabled).toBe(true);
  root.querySelector('textarea')!.value='Keep my edited instruction';
  fire(byText(root,'Check again'),'click');await settle();
  expect(root.querySelector('textarea')!.value).toBe('Keep my edited instruction');
  expect(startButton(root).disabled).toBe(false);expect(start).not.toHaveBeenCalled();
});
it('offers Finish only for an idle Claude session and no review without retained evidence', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const root = new TestElement('article');
  renderStudy(root as unknown as HTMLElement,{...study,tasks:{a:{...study.tasks.a,execution:{provider:'claude-managed-google',session:'session_1'},observation:{providerStatus:'running'}}}}, {control:vi.fn(),collect:vi.fn(),evidence:vi.fn()});
  expect(byText(root,'Finish and collect')).toBeUndefined();expect(byText(root,'See its evidence so far')).toBeUndefined();
  expect(byText(root,'Check for results')).toBeDefined();
});

it('shows the exact evidence snapshot cost without substituting a held allowance or provider estimate', () => {
  expect(evidenceCost({after:{usage:{list_cost:{amount:'12',currency:'USD'}}}},'claude-managed-google',750000)).toContain('$0.12 (list price, not an invoice)');
  expect(evidenceCost({after:{usage:{list_cost:{amount:'0.12',currency:'USD'}}}},'claude-managed-google',750000)).toContain('not retained');
  expect(evidenceCost({},'gemini-google',750000)).toContain('$0.75');
  expect(evidenceCost({},'gemini-google',750000)).toContain('estimate');
});
it('keeps an unconfirmed launch inspectable without offering an impossible provider sync', () => {
  vi.stubGlobal('document', syntheticDOM().document);const root = new TestElement('article');
  renderStudy(root as unknown as HTMLElement,{...study,tasks:{a:{...study.tasks.a,status:'unknown',execution:{provider:'claude-managed-google'}}}},{control:vi.fn(),collect:vi.fn()});
  expect(byText(root,'Check for results')).toBeUndefined();
});

it('does not revive stale input readiness by changing provider after a failed readiness check', async () => {
  vi.stubGlobal('document', syntheticDOM().document);const root = new TestElement('article');
  renderStudy(root as unknown as HTMLElement,{...study,tasks:{},availableMicrousd:1e6},{control:vi.fn(),start:vi.fn(),readiness:vi.fn().mockRejectedValue(new Error('Unavailable'))},[{id:'claude-managed-google',label:'Claude',ready:true,inputReady:true,maxBudgetCents:50,maxReservationUsd:.75}]);
  fire(byText(root,'Check again'),'click');await settle();
  fire(root.querySelector('input')!,'change'); // the researcher tile's radio
  expect(startButton(root).disabled).toBe(true);expect(root.textContent).toContain('Preparation could not be checked');
});

it('offers explicit original-session recovery, preserves an editable ID after refusal, and never launches on render', async () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const root = new TestElement('article'), recover = vi.fn().mockRejectedValueOnce(new Error('Session does not match this experiment')).mockResolvedValue(undefined);
  renderStudy(root as unknown as HTMLElement, {...study, status:'stopped', tasks:{a:{...study.tasks.a,status:'cancel_requested',specification:{provider:'claude-managed-google'},execution:undefined}}}, {control:vi.fn(),recover});
  expect(recover).not.toHaveBeenCalled();
  const submit = byText(root,'Reconnect original session');
  expect(submit).toBeDefined();
  const input = root.querySelector('input')!;
  input.value='sesn_wrong';fire(submit,'click');await settle();
  expect(recover).toHaveBeenCalledWith('a','sesn_wrong');
  expect(root.textContent).toContain('Session does not match');
  expect(input.disabled).toBe(false);expect(input.value).toBe('sesn_wrong');
  input.value='sesn_original';fire(submit,'click');await settle();
  expect(recover).toHaveBeenLastCalledWith('a','sesn_original');
  expect(root.textContent).toContain('Spending stays held');
});

it('does not offer managed recovery for a known session, Gemini or an unlaunched planned task', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  for(const task of [
    {...study.tasks.a,status:'unknown',specification:{provider:'gemini-google'}},
    {...study.tasks.a,status:'planned',specification:{provider:'claude-managed-google'}},
    {...study.tasks.a,status:'unknown',specification:{provider:'claude-managed-google'},execution:{provider:'claude-managed-google',session:'sesn_known'}},
  ]) {
    const root = new TestElement('article');
    renderStudy(root as unknown as HTMLElement,{...study,tasks:{a:task}},{control:vi.fn(),recover:vi.fn()});
    expect(byText(root,'Reconnect original session')).toBeUndefined();
  }
});

it('keeps a standalone local study readable without empty managed setup controls', () => {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const root = new TestElement('article');
  renderStudy(root as unknown as HTMLElement, { ...study, tasks: {} }, { control: vi.fn(), start: vi.fn() }, []);
  expect(root.querySelector('.study-view-start')).toBe(null);
  expect(root.querySelector('.study-setup')).toBe(null);
  expect(root.textContent).toContain(study.objective);
});

// Synthetic DOM and scripted runner evidence, not model-quality evidence.
import { afterEach, expect, test, vi } from 'vitest';
import { ModelComparison, comparisonProblem, type ComparisonResult, type ComparisonView } from './model-comparison';
import type { ActionsDeps } from './actions-panel';
import type { CapabilitiesWire, RoleOption } from './protocol';
import { TestElement, fire } from './workspace-windows.test-support';
import { COMPARE_COLOURS, type TaskModel } from './tools-catalog';
type ActionReply = Awaited<ReturnType<ActionsDeps['run']>>;
afterEach(() => vi.unstubAllGlobals());
const options: RoleOption[] = [
  { id: 'a', label: 'Model A', note: '', available: true, runs: 'mac' },
  { id: 'b', label: 'Model B', note: '', available: true, runs: 'mac' },
  { id: 'c', label: 'Model C', note: '', available: true, runs: 'cloud' },
  { id: 'd', label: 'Model D', note: '', available: false, why: 'Needs a compatible GPU' },
];
const models: TaskModel[] = options.map(o => ({ model: `entry-${o.id}`, name: o.label, role: 'cells', option: o.id, ready: o.available,
  choosable: true, current: o.id === 'a', why: o.why ?? '' }));
function setup(run = vi.fn<ActionsDeps['run']>().mockResolvedValue({ok:true, summary:'Done'})) {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  let session = 'p1';
  const deps: ActionsDeps = { run, stop: vi.fn().mockResolvedValue({ok:true}), session: () => session, selected: () => null,
    openModels: vi.fn(), openActivity: vi.fn(), openTable: vi.fn(), openCamera: vi.fn(), cameraPage: () => '#',
    ask: vi.fn(), showComparison: vi.fn() };
  const wire = { roles: [{ id:'cells', options:[...options] }, {id:'outline', options: [options[0]]}],
    models: [{ id: 'entry-a', usedFor: [{ role: 'cells', option: 'a' }], what: 'Outlines every cell in a view' },
      { id: 'entry-b', usedFor: [{ role: 'cells', option: 'b' }], what: 'Finds bright regions using a threshold' }] } as unknown as CapabilitiesWire;
  const component = new ModelComparison(deps); component.update(wire);
  component.open('cells', 'Outline every cell', models, 'a'); // the model chosen for the task is A
  const root = component.root as unknown as TestElement;
  const choose = (id: string) => { const input = root.querySelectorAll('input').find(input => input.value === id)!;
    Object.assign(input, {checked:!(input as unknown as HTMLInputElement).checked}); fire(input, 'change'); };
  const press = (text: string) => fire(root.querySelectorAll('button').find(button => button.textContent === text)!, 'click');
  return { root, component, deps, wire, choose, press, changeProject() { session = 'p2'; component.update(wire); } };
}
const flush = async () => { await new Promise(resolve => setTimeout(resolve, 0)); };
test('unavailable models and a missing selection block a comparison; an outline needs one selected', () => {
  expect(comparisonProblem(models,['a','d'],'cells',true)).toContain('needs setup');
  expect(comparisonProblem(models,['a','b'],'outline',false)).toContain('Select an outline');
  expect(comparisonProblem(models,['a'],'cells',true)).toContain('two or three');
  expect(comparisonProblem(models,['a','b','c','d'],'cells',true)).toContain('two or three');
});
test('each ticked model takes its letter and its own colour, in the order ticked; a fourth cannot be ticked', () => {
  const t = setup(); t.choose('b');
  const swatches = () => t.root.querySelectorAll('.comparison-swatch');
  expect(swatches().map(s => s.textContent)).toEqual(['A', 'B']);
  t.choose('a'); t.choose('a'); // a untick and tick again: it comes after b now
  expect(swatches().map(s => s.textContent)).toEqual(['B', 'A']); // listed in the task's order, lettered by the order ticked
  expect(swatches().map(s => s.style.background)).toEqual([COMPARE_COLOURS[1], COMPARE_COLOURS[0]]);
  t.choose('a'); t.choose('b'); t.choose('a'); t.choose('b');
  t.choose('c');
  expect(t.root.querySelectorAll('input').find(input => input.value === 'd')?.disabled).toBe(true); // needs setup
  expect(t.root.textContent).toContain('Set up');
  t.press('Set up'); expect(t.deps.openModels).toHaveBeenCalledWith('entry-d');
});
test('the model chosen for the task is ticked as A when Compare opens', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const deps = { session: () => 'p1', selected: () => null } as unknown as ActionsDeps;
  const component = new ModelComparison(deps);
  component.open('cells', 'Outline every cell', models, 'b');
  const root = component.root as unknown as TestElement;
  expect(root.querySelectorAll('input').filter(i => (i as unknown as HTMLInputElement).checked).map(i => i.value)).toEqual(['b']);
});
test('one explicit run compares selected models on current input without a default change or fallback', async () => {
  const t = setup(); t.choose('b'); expect(t.deps.run).not.toHaveBeenCalled();
  t.press('Compare 2 models'); await flush();
  expect(t.deps.run).toHaveBeenCalledExactlyOnceWith('compare-models', {operation:'cells', implementations:['a','b'], policy:'raw', exact:true, input:'view'}, {}, expect.any(String));
});
test('cloud choices require consent and unavailable choices are disabled', () => {
  const t = setup(); t.choose('c');
  expect(t.root.querySelectorAll('button').find(b => b.textContent === 'Compare 2 models')?.disabled).toBe(true);
  expect(t.root.querySelectorAll('input').find(input => input.value === 'd')?.disabled).toBe(true);
  expect(t.root.textContent).toContain('Provider charges apply'); expect(t.deps.run).not.toHaveBeenCalled();
});
test('Keep binds the exact comparison; switching Project disables Keep but retains results', async () => {
  const result: ComparisonResult = {id:'exact-trial', operation:'cells', capture:42, pairs:[], arms:[{impl:'a',label:'Model A',population:'e1',count:4},{impl:'b',label:'Model B',failed:'Failed example'}]};
  const run = vi.fn<ActionsDeps['run']>().mockResolvedValue({ok:true, comparison:result});
  const t = setup(run); t.choose('b'); t.press('Compare 2 models'); await flush();
  t.press('Use this outline'); await flush();
  expect(run).toHaveBeenLastCalledWith('compare-models', {keep:'a', comparison:'exact-trial'}, {}, expect.any(String));
  run.mockResolvedValue({ok:true, comparison:{...result,id:'another-trial'}});
  t.press('Compare 2 models'); await flush(); t.changeProject();
  expect(t.root.querySelectorAll('button').find(b => b.textContent === 'Use this outline')?.disabled).toBe(true);
  expect(t.root.textContent).toContain('Failed example');
});
test('Stop names owned work, waits for settlement, and connection loss never claims completion', async () => {
  let finish!: (reply: ActionReply) => void;
  const run = vi.fn<ActionsDeps['run']>(() => new Promise(resolve => {finish=resolve;}));
  const t = setup(run); t.choose('b'); t.press('Compare 2 models'); t.press('Stop comparison'); await flush();
  const id = run.mock.calls[0][3]; expect(t.deps.stop).toHaveBeenCalledWith(id);
  finish({ok:true,stopping:true}); await flush(); t.component.settled(id, {outcome:'stopped',text:'Stopped safely'});
  expect(t.root.textContent).toContain('Stopped safely');
  t.press('Compare 2 models'); t.component.connectionLost(); expect(t.root.textContent).toContain('Check Activity before retrying');
});
test('retained cell masks open by exact revision without a legacy Keep or rerun', async () => {
  const result: ComparisonResult = {id:'trial',operation:'cells',capture:42,pairs:[],arms:[{impl:'a',label:'Model A',population:'e1',revision:3,count:4}]};
  const run = vi.fn<ActionsDeps['run']>().mockResolvedValue({ok:true,comparison:result});
  const t = setup(run); t.deps.openComparisonMasks = vi.fn().mockResolvedValue(undefined);
  t.choose('b'); t.press('Compare 2 models'); await flush();
  t.press('Continue with this one'); await flush();
  expect(t.deps.openComparisonMasks).toHaveBeenCalledExactlyOnceWith('e1',{session:'p1',revision:3},'Model A');
  expect(run).toHaveBeenCalledTimes(1);
  t.press('Measurements'); expect(t.deps.openTable).toHaveBeenCalledWith('e1',{session:'p1',revision:3});
  t.changeProject(); expect(t.root.querySelectorAll('button').find(b=>b.textContent==='Continue with this one')?.disabled).toBe(true);
});
test('A/B/C keeps failed arms and presentation errors visible beside successful retained output', async () => {
  const result: ComparisonResult = {operation:'cells',capture:42,pairs:[],arms:[
    {impl:'a',label:'Model A',population:'e1',revision:0,count:4},
    {impl:'b',label:'Model B',population:'e2',revision:0,count:2},
    {impl:'c',label:'Model C',failed:'Model ran out of memory'}]};
  const run = vi.fn<ActionsDeps['run']>().mockResolvedValue({ok:false,error:'The comparison picture could not open. Retry from Project Data.',comparison:result});
  const t = setup(run); t.wire.roles[0].options[2] = {...options[2],runs:'mac'}; t.component.update(t.wire);
  t.choose('b'); t.choose('c'); t.press('Compare 3 models'); await flush();
  expect(run.mock.calls[0][1].implementations).toEqual(['a','b','c']);
  expect(t.root.textContent).toContain('2 of 3 models finished');
  expect(t.root.textContent).toContain('Model ran out of memory');
  expect(t.root.textContent).toContain('The comparison picture could not open');
  expect(t.root.querySelectorAll('button').filter(b=>b.textContent==='Measurements')).toHaveLength(2);
});
test('the Stage draws each model in its colour; the line says why the counts differ as facts; the Assistant judges', async () => {
  const result: ComparisonResult = {id:'trial',operation:'cells',capture:42,pairs:['30 found by both Model A and Model B'],arms:[
    {impl:'a',label:'a',population:'e1',revision:0,count:41},{impl:'b',label:'b',population:'e2',revision:2,count:30}]};
  const run = vi.fn<ActionsDeps['run']>().mockResolvedValue({ok:true,comparison:result});
  const t = setup(run); t.choose('b'); t.press('Compare 2 models'); await flush();
  const view = (t.deps.showComparison as ReturnType<typeof vi.fn>).mock.calls[0][0] as ComparisonView;
  expect(view.arms.map(a => [a.letter, a.colour, a.name, a.population, a.revision, a.count])).toEqual([
    ['A', COMPARE_COLOURS[0], 'Model A', 'e1', 0, 41], ['B', COMPARE_COLOURS[1], 'Model B', 'e2', 2, 30]]);
  expect(view.session).toBe('p1'); expect(view.task).toBe('Outline every cell');
  // Facts only: each model's count and how it works, in the catalog's words; never which one is right.
  expect(view.line).toBe('The counts differ because each model finds them its own way: Model A outlines every cell in a view; '
    + 'Model B finds bright regions using a threshold.');
  expect(view.line).not.toMatch(/\b(better|best|more accurate|trust|right|wrong|correct)\b/i);
  expect(t.root.textContent).toContain(view.line);
  t.press('Ask the Assistant which to trust');
  expect(t.deps.ask).toHaveBeenCalledWith('I compared models for “Outline every cell” on the same picture (A. Model A: 41, B. Model B: 30). Which should I trust here, and why?');
  t.press('Show on the Stage'); expect(t.deps.showComparison).toHaveBeenCalledTimes(2);
  t.changeProject(); t.press('Show on the Stage'); expect(t.deps.showComparison).toHaveBeenCalledTimes(2); // its Project only
});
test('Cancel leaves compare mode without running anything', () => {
  const t = setup(); const cancel = vi.fn(); t.component.onCancel = cancel; t.press('Cancel');
  expect(cancel).toHaveBeenCalledTimes(1); expect(t.deps.run).not.toHaveBeenCalled();
});
test('a comparison where no model finished never says its marks are being shown', async () => {
  const result: ComparisonResult = {id:'t',operation:'cells',capture:42,pairs:[],arms:[
    {impl:'a',label:'a',failed:'analyses not yet saved fill their allowance'},{impl:'b',label:'b',failed:'analyses not yet saved fill their allowance'}]};
  const t = setup(vi.fn<ActionsDeps['run']>().mockResolvedValue({ok:true,comparison:result})); t.choose('b'); t.press('Compare 2 models'); await flush();
  expect(t.root.textContent).toContain('0 of 2 models finished. Each one’s reason is below');
  expect(t.root.textContent).not.toContain('Opening each one’s marks');
  expect(t.root.textContent).toContain('analyses not yet saved fill their allowance');
  expect(t.deps.showComparison).not.toHaveBeenCalled();
});

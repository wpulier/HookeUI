import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { syntheticDOM } from './workspace-windows.test-support';
import { showScientificHistory } from './scientific-history';
import { SCRIPT_BYTES } from './scientific-scripts';

beforeEach(() => { vi.stubGlobal('document', syntheticDOM().document); });
afterEach(() => vi.unstubAllGlobals());
const state = (cell: unknown) => ({ format: 'scientific-python-history-v1', input: { id: 'p' + 'a'.repeat(32), revision: 2 }, cells: [cell] });
const transcript = { format: 'scientific-console-v1', sequence: 1, stdout: '<script>never run</script>', stderr: '', result: '2', error: '', truncated: false, privatePreview: true };

test('retained code and confirmed output render as inert text without a kernel or Run action', () => {
  const root = document.createElement('section');
  showScientificHistory(root, state({ source: 'print("<img src=x onerror=run()>")', state: 'completed', transcript }));
  expect(root.textContent).toContain('print("<img src=x onerror=run()>")');
  expect(root.textContent).toContain('<script>never run</script>');
  expect(root.textContent).toContain('Opening this history runs nothing');
  expect(root.querySelectorAll('script').length).toBe(0);
  expect(root.querySelectorAll('button').length).toBe(0);
});

test.each(['submitted', 'interrupted'])('a %s cell is an unconfirmed outcome, never an implied rerun', status => {
  const root = document.createElement('section');
  showScientificHistory(root, state({ source: 'viewer.layers.clear()', state: status }));
  expect(root.textContent).toContain('Outcome unconfirmed');
  expect(root.textContent).toContain('nothing was replayed');
});

test('an error transcript says earlier statements may have changed private data', () => {
  const root = document.createElement('section');
  showScientificHistory(root, state({ source: 'change(); fail()', state: 'completed', transcript: { ...transcript, error: 'ValueError', truncated: true } }));
  expect(root.textContent).toContain('Earlier statements may have changed private data');
  expect(root.textContent).toContain('truncated transcript');
});

test('invalid history does not append a successful-looking partial transcript', () => {
  const root = document.createElement('section');
  expect(() => showScientificHistory(root, state({ source: 'x'.repeat(SCRIPT_BYTES+1), state: 'submitted' }))).toThrow('invalid text');
  expect(root.textContent).toBe('');
});

test('confirmed control refusal is shown as not applied, separate from uncertain execution', () => {
  const root = document.createElement('section');
  showScientificHistory(root, { ...state({ source: 'saved = 83', state: 'completed', transcript }),
    actions: [{ state: 'completed', request: { op: 'change' }, context: {}, outcome: { state: 'not-applied' } }] });
  expect(root.querySelector('summary')?.textContent).toBe('change · Not applied');
  expect(root.textContent).toContain('The worker refused this action before applying it. Private work was kept.');
});


test('large UTF-8 source reopens as exact inert text with unchanged transcript bound', () => {
  const root=document.createElement('section'), source='# calibrated µ input\n'.repeat(2000)+'print(42)';
  showScientificHistory(root,state({source,state:'completed',transcript}));
  expect(root.textContent).toContain(source);expect(root.querySelectorAll('button')).toHaveLength(0);
  expect(()=>showScientificHistory(document.createElement('section'),state({source:'µ'.repeat(SCRIPT_BYTES),state:'submitted'}))).toThrow('UTF-8 allowance');
  expect(()=>showScientificHistory(document.createElement('section'),state({source,state:'completed',transcript:{...transcript,stdout:'x'.repeat((16<<10)+1)}}))).toThrow('bounded transcript');
});

test.each(['submitted','interrupted'])('an uncertain %s action explains private changes without offering replay',status=>{
  const root=document.createElement('section');
  showScientificHistory(root,{...state({source:'kept=83',state:'completed',transcript}),
    actions:[{state:status,request:{op:'change'},context:{},outcome:{state:'unconfirmed'}}]});
  expect(root.querySelector('summary')?.textContent).toBe('change · Outcome unconfirmed');
  expect(root.textContent).toContain('may have changed private work');
  expect(root.textContent).toContain('Nothing was replayed');
  expect(root.querySelectorAll('button')).toHaveLength(0);
});

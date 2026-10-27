import { expect, test } from 'vitest';
import { executionContributions } from './executionflow';
import type { ExecutionDetail, ExecutionStep } from './protocol';
import highlight from './fixtures/execution-highlight.json';
import fourOutlines from './fixtures/execution-four-outlines.json';

const receipt = (): ExecutionDetail => structuredClone(highlight) as ExecutionDetail;
const step = (id: string, fields: Partial<ExecutionStep> = {}): ExecutionStep => ({
  id, parent: 'root', operation: 'vision', outcome: 'completed', model: 'actual-model', ...fields,
});

test('each actual call keeps its own outcome, identity and recorded start order', () => {
  const data = receipt();
  const before = JSON.stringify(data);
  const rows = executionContributions(data);
  expect(rows.map(r => r.stepId)).toEqual([2, 4, 5, 10, 12, 13].map(i => `ed394307d91f3-19.${i}`));
  expect(rows.filter(r => r.label === 'Gemini 3.5 Flash')).toHaveLength(3);
  expect(rows[2].outcome).toBe('failed');
  expect(JSON.stringify(data)).toBe(before);
});

test('a direct click outlines first and names afterward; selected options are not execution evidence', () => {
  const data = receipt();
  const gpu = data.steps[9], vision = data.steps[1];
  data.steps = [
    { ...gpu, parent: 'root', selected: 'sam3' },
    step('name', { operation: 'name', model: undefined }),
    { ...vision, parent: 'name' },
  ];
  expect(executionContributions(data).map(r => [r.capability, r.label])).toEqual([
    ['Draw outline', 'EdgeTAM (on this Mac)'], ['Naming', 'Gemini 3.5 Flash'],
  ]);
});

test('unknown purposes stay unknown, even for a model that also performed a known job', () => {
  const data = receipt();
  data.steps.push({ ...data.steps[1], id: 'unknown', parent: 'missing', role: 'new-role', optionId: 'new-option' });
  const unknown = executionContributions(data).at(-1)!;
  expect(unknown.capability).toBe('Model call (purpose not recorded)');
  expect(unknown.label).toBe('gemini-3.5-flash'); // no matched catalogue identity
  data.steps = [step('selected-only', { model: undefined, selected: 'gemini-3.5-flash' })];
  expect(executionContributions(data)).toEqual([]);
});

test('same-model failures and successes stay separate; a failed tool with a model child stays failed', () => {
  const data = receipt();
  const vision = data.steps[1];
  data.steps = [
    step('tool', { operation: 'tool:highlight', model: undefined, provider: 'app', outcome: 'failed' }),
    { ...vision, parent: 'tool', outcome: 'failed' },
    { ...vision, id: 'retry', parent: 'tool', outcome: 'completed' },
  ];
  expect(executionContributions(data).map(r => [r.capability, r.outcome])).toEqual([
    ['Highlight attempt', 'failed'], ['Highlight', 'failed'], ['Highlight', 'completed'],
  ]);
});

test('reused masks and existing outlines are reuse, without inventing a new model call', () => {
  const data: ExecutionDetail = { status: 'available', steps: [
    step('mask', { operation: 'reuse-mask', model: 'edgetam', provider: 'local', outcome: 'reused', cache: 'previous-automask' }),
    step('kept', { operation: 'target', model: undefined, provider: 'app', outcome: 'reused', cache: 'existing-outline; original producer may be expired' }),
  ] };
  expect(executionContributions(data).map(r => [r.capability, r.label, r.outcome])).toEqual([
    ['Reusing an outline', 'edgetam', 'reused'],
    ['Reusing an outline', 'ObservationUI', 'reused'],
  ]);
});

test('at most 64 steps are inspected and cyclic or missing parents cannot loop', () => {
  const data: ExecutionDetail = { status: 'available', steps: Array.from({ length: 65 }, (_, i) => step(String(i))) };
  data.steps[0].parent = '1'; data.steps[1].parent = '0';
  expect(executionContributions(data)).toHaveLength(64);
  expect(executionContributions({ ...data, status: 'blind' })).toEqual([]);
  expect(executionContributions({ ...data, status: 'expired' })).toEqual([]);
});

test('repeated GPU calls cannot borrow the purpose of a different parent tool', () => {
  const data: ExecutionDetail = { status: 'available', steps: [
    step('highlight', { operation: 'tool:highlight', model: undefined }),
    step('gpu1', { parent: 'highlight', operation: 'gpu' }),
    step('find', { operation: 'tool:find', model: undefined }),
    step('gpu2', { parent: 'find', operation: 'gpu' }),
    step('gpu3', { parent: 'highlight', operation: 'gpu' }),
  ], powers: [
    { stepId: 'gpu1', capability: 'Highlight what it sees', label: 'Actual model', provider: 'local', outcome: 'completed' },
    { stepId: 'gpu2', capability: 'Find', label: 'Actual model', provider: 'local', outcome: 'completed' },
  ] };
  expect(executionContributions(data).map(r => r.capability)).toEqual(['Highlight what it sees', 'Find', 'Highlight what it sees']);
});

test('a worker failure before identity arrives stays visible without asserting the selected model ran', () => {
  const rows = executionContributions({ status: 'available', steps: [
    step('failed', { operation: 'gpu', model: undefined, selected: 'edgetam', outcome: 'failed', errorType: 'TimeoutError' }),
  ] });
  expect(rows).toEqual([{ stepId: 'failed', capability: 'GPU attempt', label: 'Model identity not recorded', provider: '', outcome: 'failed', cache: undefined }]);
});

test('four highlighted objects keep their actual model jobs and repair result distinct', () => {
  const data = fourOutlines as ExecutionDetail;
  const before = JSON.stringify(data);
  const rows = executionContributions(data);
  expect(rows).toHaveLength(16);
  expect(rows.map(r => r.capability)).toEqual([
    'Handling the request', 'Looking at the view',
    'Draw outline', 'Draw outline', 'Checking the outline', 'Appearance fingerprint',
    'Draw outline', 'Checking the outline', 'Appearance fingerprint',
    'Draw outline', 'Checking the outline', 'Appearance fingerprint',
    'Checking the outline', 'Appearance fingerprint', 'Try outline repair', 'Check repair',
  ]);
  expect(rows[1].outcome).toBe('cancelled');
  expect(rows.filter(r => r.objectId === 'o2989').map(r => r.capability)).toEqual([
    'Draw outline', 'Checking the outline', 'Appearance fingerprint', 'Try outline repair', 'Check repair',
  ]);
  expect(rows.at(-1)?.note).toBe('Repair not accepted; original outline kept.');
  expect(rows.filter(r => r.note)).toHaveLength(1);
  expect(JSON.stringify(data)).toBe(before);
});

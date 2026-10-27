import { expect, test } from 'vitest';
import { DISCONNECTED, assistantLines, readReadiness, recordWords, researcherLines, runLines, studyLines, toolSessionLines, unsentLines, type RecordFact } from './restart-readiness';
import type { Effort } from './research-efforts';

test('hand-started runs: each one still going is named with its Project, whatever its pill does', () => {
  expect(runLines([
    { status: 'running', name: 'Cellpose-SAM · Cells', project: 'Blood smear' },
    { status: 'queued', name: 'micro-sam', project: 'Leaf' },
    { status: 'stopping', name: 'Bright objects', project: 'Leaf' },
    { status: 'unknown', name: 'Spot shape', project: 'Leaf' },
    { status: 'completed', name: 'Done one', project: 'Leaf' },
    { status: 'failed', name: 'Failed one', project: 'Leaf' },
  ])).toEqual(['Cellpose-SAM, running in Blood smear', 'micro-sam, waiting to run in Leaf', 'Bright objects, stopping in Leaf',
    'Spot shape in Leaf, which may still be running']);
});

const idle: RecordFact = { recorder: 'idle', scope: 'workspace', on: false, collecting: false, stopping: false, stopUnconfirmed: false };
test('Record says what a restart really does to it: the dataset carries on, the screen recording does not', () => {
  expect(recordWords(idle)).toEqual({ blockers: [], running: [] });
  expect(recordWords({ ...idle, recorder: 'recording', scope: 'camera', on: true, collecting: true }).running)
    .toEqual(['Record (it carries on when Hooke opens again)']);
  // After a reload the page holds no recorder; the dataset still collects.
  expect(recordWords({ ...idle, on: true, collecting: true }).running).toEqual(['Record (it carries on when Hooke opens again)']);
  expect(recordWords({ ...idle, recorder: 'recording', scope: 'workspace', on: true, collecting: true }).running)
    .toEqual(['The screen recording (Record carries on when Hooke opens again, without the screen)']);
  // Without its dataset nothing carries it on: it is named, with no promise.
  expect(recordWords({ ...idle, recorder: 'recording', scope: 'camera', on: true }).running).toEqual(['Record']);
  expect(recordWords({ ...idle, on: true, stopUnconfirmed: true }).running)
    .toEqual(['Record (its Stop isn’t confirmed, so it may carry on when Hooke opens again)']);
  expect(recordWords({ ...idle, recorder: 'starting' }).running).toEqual(['Record, which is starting']);
});

test('a recording still being saved refuses the restart (it is unsaved)', () => {
  for (const fact of [{ ...idle, recorder: 'saving' as const }, { ...idle, on: true, stopping: true }])
    expect(recordWords(fact)).toEqual({ blockers: ['Record is still saving the recording. Wait until it says Saved.'], running: [] });
});

test('the Assistant: an answer being written, its steps still running, and voice', () => {
  expect(assistantLines({ writing: false, steps: [], voice: false })).toEqual([]);
  expect(assistantLines({ writing: true, steps: [{ tool: 'count', ok: null }, { tool: 'outline', ok: true }, { tool: 'dissect', ok: false }], voice: true }))
    .toEqual(['An answer the Assistant is still writing', 'Count, started by the Assistant', 'Your voice conversation']);
});

test('unsent words in any composer are named once, with no promise either way', () => {
  expect(unsentLines([{ value: '' }, { value: '  \n' }])).toEqual([]);
  expect(unsentLines([{ value: 'How many cells?' }, { value: '' }])).toEqual(['Writing a message you haven’t sent (it may not come back)']);
  expect(unsentLines([{ value: 'a' }, { value: 'b' }])).toEqual(['Writing 2 messages you haven’t sent (they may not come back)']);
});

test('researchers still working in any Project: each stops and shows as interrupted when Hooke opens again', () => {
  const wire = { sessions: [
    { purpose: 'research', status: 'running', name: 'Researcher 2', label: 'Counting stomata', sitting: 'p1' },
    { purpose: 'research', status: 'queued', name: 'Researcher 1', brief: 'Describe   the\ncells', sitting: 'p2' },
    { purpose: 'research', status: 'done', name: 'Researcher 3', label: 'Finished', sitting: 'p1', mission: { status: 'watching' } },
    { purpose: 'research', status: 'revising', name: 'Researcher 4', label: 'A lesson', sitting: 'p3' },
    { purpose: 'research', status: 'done', name: 'Researcher 5', label: 'Old', sitting: 'p1' },
    { purpose: 'description', status: 'running', name: 'Description', label: 'x', sitting: 'p1' },
  ] };
  expect(researcherLines(wire, 'p1', sid => sid === 'p2' ? 'Blood smear' : undefined)).toEqual([
    'Researcher 2, “Counting stomata” (it shows as interrupted when Hooke opens again)',
    'Researcher 1, “Describe the cells” in Blood smear (it shows as interrupted when Hooke opens again)',
    'Researcher 3, “Finished” (it shows as interrupted when Hooke opens again)',
    'Researcher 4, “A lesson” in another Project (it shows as interrupted when Hooke opens again)',
  ]);
  expect(() => researcherLines({ error: 'x' }, 'p1', () => undefined)).toThrow();
  expect(() => researcherLines(null, 'p1', () => undefined)).toThrow();
});

const study = (tasks: Effort['tasks'], title = 'Stomata density'): Effort => ({ id: 's1', session: 'p1', revision: 1, digest: 'd', title, objective: 'o',
  criteria: 'c', status: 'active', capMicrousd: 0, spentMicrousd: 0, heldMicrousd: 0, availableMicrousd: 0, inputs: [], tasks, findings: [] });
const task = (status: string, provider: string, execution?: object) => ({ status, specification: { provider }, reservedMicrousd: 0, chargedMicrousd: null, ...(execution ? { execution } : {}) });
test('Studies: work the provider keeps goes on there; a reply still awaited may be lost', () => {
  expect(studyLines(null)).toEqual([]);
  expect(studyLines([study({ a: task('completed', 'claude-managed-google', {}) })])).toEqual([]);
  expect(studyLines([study({ a: task('running', 'claude-managed-google', { session: 'x' }) })]))
    .toEqual(['Study “Stomata density” (its work at the provider goes on, and Hooke collects it when it opens again)']);
  expect(studyLines([study({ a: task('running', 'gemini-google') })])).toEqual(['Study “Stomata density” (a reply it is waiting for may be lost)']);
  expect(studyLines([study({ a: task('launching', 'claude-managed-google') })])).toEqual(['Study “Stomata density” (a reply it is waiting for may be lost)']);
});

test('tool sessions name their saved document: what is not saved in one is lost', () => {
  expect(toolSessionLines(['Cells.tif', 'a saved document'])).toEqual([
    'The tool session for “Cells.tif” (what you haven’t saved in it is lost)', 'The tool session for “a saved document” (what you haven’t saved in it is lost)']);
});

test('one readiness answer from its parts; any part that cannot be read makes the whole answer unreadable', async () => {
  await expect(readReadiness({ blockers: () => ['A note'], running: [() => ['a'], async () => ['b', 'c'], () => []] }))
    .resolves.toEqual({ blockers: ['A note'], running: ['a', 'b', 'c'] });
  await expect(readReadiness({ blockers: () => [], running: [() => ['a'], async () => { throw new Error('/api/research could not be read.'); }] }))
    .rejects.toThrow('could not be read');
  expect(DISCONNECTED).toContain('lost its connection');
});

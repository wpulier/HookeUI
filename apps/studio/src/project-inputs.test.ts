import { describe, expect, it } from 'vitest';
import { projectInputs, materialFacts, projectEntry } from './project-inputs';
import { dataRows, type DataEntry } from './data-roles';

const entry = (id: string, input: boolean, extra = {}): DataEntry => ({
  id, kind: 'project', title: id, day: '2026-10-29', version: 2, summary: '', state: 'saved', actions: [],
  material: {kind: 'file', origin: 'imported', input, section: 'files', category: 'import', record: false, dependencies: [], ...extra},
});

describe('shared Project material', () => {
  it('shows explicit inputs and assignments without promoting generated outputs', () => {
    const imported = entry('controls', true), result = entry('model', false, {origin: 'generated'});
    const assignment = entry('Question', true, {kind: 'assignment', origin: 'authored'});
    expect(projectInputs([imported, result, assignment])).toEqual([imported, assignment]);
    expect(projectInputs([imported, result, assignment], 'question')).toEqual([assignment]);
    expect(materialFacts(imported)).toBe('File · Revision 2');
    expect(materialFacts(assignment)).toBe('Study assignment · Revision 2');
  });
  it('folds receipts beneath the run while retaining each exact revision', () => {
    const run = {...entry('run', false, {kind: 'workflow'}), role: 'history' as const, run: 'run'};
    const attempt = {...entry('attempt', false, {kind: 'workflow', run: 'run'}), role: 'history' as const, run: 'run'};
    const rows = dataRows([attempt, run]);
    expect(rows).toHaveLength(1);
    expect(rows[0].entry?.id).toBe('run');
    expect(rows[0].parts.map(item => item.id)).toEqual(['run', 'attempt']);
  });
});

it('keeps a generated historical revision selected by a Study visible and pinned', () => {
  const model = entry('model', false, {origin: 'generated', uses: [{study: 'study', revision: 1, digest: 'a'.repeat(64), title: 'Original model', available: true}]});
  const [input] = projectInputs([model]);
  expect(input.version).toBe(1);
  expect(input.title).toBe('Original model');
  expect(input.source?.sha256).toBe('a'.repeat(64));
  expect(input.material?.origin).toBe('generated');
  expect(model.version).toBe(2);
});

it('resolves a retained Tab pin without substituting the newest model', () => {
  const model = entry('model', false, {origin: 'generated', uses: [{study: 'study', revision: 1, digest: 'a'.repeat(64), title: 'Original model', available: true}]});
  const pin = {id: 'model', day: '2026-10-29', version: 1};
  expect(projectEntry([model], pin, pin.day)?.title).toBe('Original model');
  expect(projectEntry([model], {...pin, version: 2}, pin.day)).toBe(model);
  expect(projectEntry([model], {...pin, version: 3}, pin.day)).toBeNull();
  expect(projectEntry([model], {...pin, day: 'foreign'}, pin.day)).toBeNull();
});

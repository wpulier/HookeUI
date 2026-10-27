import { expect, test } from 'vitest';
import { readFlow, saveFlow, type CapabilityFlowContext } from './capability-flow';

test('only intent and references survive refresh, never a supplied key or progress', () => {
  const storage = new Map<string, string>();
  const port = { getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => storage.set(k, v), removeItem: (k: string) => storage.delete(k) };
  const context: CapabilityFlowContext = { route: 'find', model: 'one', role: 'cells', option: 'one', session: 's1' };
  saveFlow(Object.assign({}, context, { apiKey: 'secret-fixture', stage: 'ready', percentage: 100 }), port);
  expect(readFlow(port)).toEqual(context);
  expect([...storage.values()].join('')).not.toContain('secret-fixture');
  saveFlow(null, port); expect(readFlow(port)).toBeNull();
});

test('entry drafts retain only bounded nonsecret intent fields', () => {
  const storage = new Map<string, string>();
  const port = { getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => storage.set(k, v), removeItem: (k: string) => storage.delete(k) };
  const context: CapabilityFlowContext = { route: 'create', model: '', role: '', option: '', session: 's1',
    workspace: 'private-id', values: { description: 'Measure spots', method: 'coder', runner: 'fiji' } };
  saveFlow({ ...context, values: Object.assign({}, context.values, { apiKey: 'secret', stage: 'done', job: 'unsupported', query: 'x'.repeat(4097) }) }, port);
  expect(readFlow(port)).toEqual(context);
  expect([...storage.values()].join('')).not.toContain('secret');
});

import { expect, test } from 'vitest';
import { ExecutionStore, Inspection } from './executionlog';
import { ChatModel } from './chatmodel';
import type { ExecutionDetail, ExecutionReference } from './protocol';

const ref = (id: string, thread = 't1', day = '2026-10-06'): ExecutionReference => ({
  id, thread, day, kind: 'click', at: 1, generation: 1, turnId: null, objectIds: ['o1'],
  trialId: null, outcome: 'completed', revision: 1, incomplete: false,
});

test('receipt updates are bounded, separate from chat and stay with their day and conversation', () => {
  const store = new ExecutionStore(2);
  store.add(ref('one'));
  store.add(ref('two', 't2'));
  store.add({ ...ref('one'), revision: 3 });
  expect(store.list('t1', '2026-10-06').map(r => r.revision)).toEqual([3]);
  store.add(ref('three', 't1', '2026-10-07'));
  expect(store.list('t1', '2026-10-06')).toEqual([]);
  expect(store.list('t1', '2026-10-07').map(r => r.id)).toEqual(['three']);
  const chat = new ChatModel(() => false);
  expect(chat.apply({ type: 'execution', entry: ref('one') })).toEqual([]);
  expect(chat.lines).toEqual([]);
});

test('closed details do no fetching; closing aborts and late responses cannot reopen them', async () => {
  let calls = 0;
  let finish!: (data: ExecutionDetail) => void;
  let signal!: AbortSignal;
  const inspection = new Inspection('one', async (_, s) => {
    calls++;
    signal = s;
    return await new Promise<ExecutionDetail>(r => { finish = r; });
  });
  expect(calls).toBe(0);
  const opening = inspection.open();
  expect(calls).toBe(1);
  inspection.close();
  expect(signal.aborted).toBe(true);
  finish({ status: 'available', root: ref('one'), steps: [] });
  await opening;
  expect(inspection.detail).toBeNull();
  expect(inspection.loading).toBe(false);
});

test('reopening reads new late results and blind/expired receipts remain explicit', async () => {
  const replies: ExecutionDetail[] = [
    { status: 'blind', steps: [] }, { status: 'available', root: ref('one'), steps: [{ id: 'one.1', parent: 'one', operation: 'gpu', model: 'actual', outcome: 'completed' }] },
    { status: 'expired', steps: [] },
  ];
  const inspection = new Inspection('one', async () => replies.shift()!);
  await inspection.open();
  expect(inspection.detail?.status).toBe('blind');
  inspection.close();
  await inspection.open();
  expect(inspection.detail?.steps[0].model).toBe('actual');
  inspection.close();
  await inspection.open();
  expect(inspection.detail?.status).toBe('expired');
});

test('inspection failure stays local and is retryable', async () => {
  let fail = true;
  const inspection = new Inspection('one', async () => {
    if (fail) throw new Error('unavailable');
    return { status: 'expired', steps: [] };
  });
  await inspection.open();
  expect(inspection.error).toBe(true);
  fail = false;
  await inspection.open();
  expect(inspection.error).toBe(false);
  expect(inspection.detail?.status).toBe('expired');
});

test('turning execution logging off says what it keeps (the partner §1f)', async () => {
  const { LOGGING_KEEPS } = await import('./executionlog');
  expect(LOGGING_KEEPS).toMatch(/Off stops new details/);
  expect(LOGGING_KEEPS).toMatch(/previously archived evidence, chat, results and essential provenance are kept/);
});

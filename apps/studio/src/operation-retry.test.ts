import { expect, test } from 'vitest';
import { FailedStepRetry } from './operation-retry';

test('retry submits only exact failed action once, never original words or successful steps', async () => {
  const sent: unknown[] = [];
  const state = new FailedStepRetry(async fields => {
    sent.push(fields);
    return 'receiptId' in fields ? { operations: [
      { actionId: 'failed', tool: 'present', available: true, reason: '', status: 'settled' },
      { actionId: 'unsafe', tool: 'reference_photo', available: false, reason: 'already dispatched', status: 'settled' },
    ] } : { ok: true };
  }, () => {});
  await state.load('original');
  await state.retry('unsafe');
  await Promise.all([state.retry('failed'), state.retry('failed')]);
  expect(sent).toEqual([{ receiptId: 'original' }, { actionId: 'failed' }]);
  expect(state.message).toContain('Other steps were not repeated');
});

test('disconnect/error settles working state without inventing success', async () => {
  const state = new FailedStepRetry(async fields => {
    if ('receiptId' in fields) return { operations: [
      { actionId: 'a', tool: 'present', available: true, reason: '', status: 'settled' },
    ] };
    throw new Error('Original browser disconnected');
  }, () => {});
  await state.load('root');
  await state.retry('a');
  expect(state.pending).toBe(false);
  expect(state.message).toBe('Original browser disconnected');
});

test('a failed backend receipt is not shown as completion', async () => {
  const state = new FailedStepRetry(async fields => 'receiptId' in fields ? { operations: [
    { actionId: 'a', tool: 'present', available: true, reason: '', status: 'settled' },
  ] } : { ok: false, error: 'The window did not appear' }, () => {});
  await state.load('root');
  await state.retry('a');
  expect(state.pending).toBe(false);
  expect(state.message).toBe('The window did not appear');
});

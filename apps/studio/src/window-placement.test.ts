import { expect, test } from 'vitest';
import { livePlacementObject } from './window-placement';

const source = { kind: 'live', identity: { object: 'o1', run: 'run1' } };
const sample = { run: 'run1', boot: 'bridge1', seq: 10, epoch: 3 };
const frame = { run: 'run1', boot: 'bridge1', epoch: 3 };

test('only current same-run camera placement can request a verified object connector', () => {
  expect(livePlacementObject(source, sample, frame, true)).toBe('o1');
  expect(livePlacementObject(source, sample, frame, false)).toBeNull();
  expect(livePlacementObject(source, sample, { ...frame, epoch: 4 }, true)).toBeNull();
  expect(livePlacementObject(source, sample, { ...frame, boot: 'restarted' }, true)).toBeNull();
  expect(livePlacementObject(source, sample, { ...frame, run: 'restarted' }, true)).toBeNull();
  expect(livePlacementObject(source, null, frame, true)).toBeNull();
  expect(livePlacementObject({ ...source, kind: 'saved-image' }, sample, frame, true)).toBeNull();
  expect(livePlacementObject({ ...source, identity: { run: 'run1' } }, sample, frame, true)).toBeNull();
});

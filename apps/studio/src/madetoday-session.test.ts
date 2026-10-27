// Made today belongs to the session, not the calendar day (the user's call, 2026-10-11): after "start today afresh"
// it lists what this session made; what came before is kept, one step away under "Earlier today".
import { describe, expect, test } from 'vitest';
import { sessionSplit, type ResourceEntry } from './madetoday';

const entry = (id: string, at: number | undefined) => ({ id, at } as unknown as ResourceEntry);

describe('sessionSplit', () => {
  test('lists what this session made and keeps the rest apart, newest first left to the caller', () => {
    const before = entry('e1', 1000), after = entry('d2', 2000), undated = entry('k1', undefined);
    const { current, earlier } = sessionSplit([before, after, undated], 1500);
    expect(current.map((e) => e.id)).toEqual(['d2', 'k1']); // an entry with no time is not hidden
    expect(earlier.map((e) => e.id)).toEqual(['e1']);
  });

  test('with no session start known, everything is listed', () => {
    const all = [entry('e1', 1000), entry('d2', 2000)];
    expect(sessionSplit(all, null)).toEqual({ current: all, earlier: [] });
  });
});

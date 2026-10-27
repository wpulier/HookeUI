import { describe, expect, it } from 'vitest';
import { workingLine } from './research';

describe("a researcher's working set", () => {
  it('lists each record once, by the index title, with how many pictures it looked at', () => {
    const line = workingLine([
      { id: 'e3', how: 'read', title: 'Cell outlines · 18:04', kind: 'population', at: 1, version: 0 },
      { id: 'e3', how: 'picture 0', title: '', kind: '', at: 2 },
      { id: 'k1', how: 'picture 2', title: '', kind: 'clip', at: 3 },
    ]);
    expect(line).toBe('It looked at: Cell outlines · 18:04 (e3), 1 picture; a held clip (k1), 1 picture.');
    expect(workingLine([])).toBe('');
  });

  it("says what it had the app make (the app's own record) apart from what it looked at", () => {
    const line = workingLine(
      [{ id: 'e3', how: 'read', title: 'Cell outlines · 18:04', kind: 'population', at: 3 }],
      [{ id: 'e7', at: 1, title: 'Cell outlines, for Researcher 2 · 18:10', kind: 'population' }],
    );
    expect(line).toBe('It had the app make: Cell outlines, for Researcher 2 · 18:10 (e7). It looked at: Cell outlines · 18:04 (e3).');
  });

  it('shows the newest 8 in time order, counts the rest and the ones let go, and keeps the first title', () => {
    const records = Array.from({ length: 11 }, (_, i) => ({ id: `e${i}`, how: 'read', title: `T${i}`, kind: 'population', at: 20 - i }));
    records.push({ id: 'e0', how: 'picture 0', title: 'later words', kind: '', at: 30 });
    const line = workingLine(records, [], 5);
    // e0 was looked at last (its picture), so it is the newest; the oldest three (e10, e9, e8) are counted.
    expect(line).toBe('It looked at: T7 (e7); T6 (e6); T5 (e5); T4 (e4); T3 (e3); T2 (e2); T1 (e1); T0 (e0), 1 picture; '
      + 'and 3 more; and 5 earlier.');
  });

  it('orders records by when they were looked at, whatever order they come in', () => {
    const line = workingLine([
      { id: 'e3', how: 'picture 0', title: '', kind: '', at: 5 },
      { id: 'e3', how: 'read', title: 'Cells · 18:04', kind: 'population', at: 1 },
      { id: 'e4', how: 'read', title: 'Cells · 18:10', kind: 'population', at: 3 },
    ]);
    expect(line).toBe('It looked at: Cells · 18:10 (e4); Cells · 18:04 (e3), 1 picture.');
  });

  it('says a record corrected since it was read', () => {
    const line = workingLine([{ id: 'e3', how: 'read', title: 'Cell outlines · 18:04', kind: 'population', at: 1, version: 1, now: 2 }]);
    expect(line).toBe('It looked at: Cell outlines · 18:04 (e3, read at revision 1, corrected since).');
  });

  it('says nothing more of a record read again at the revision it has now', () => {
    const line = workingLine([
      { id: 'e3', how: 'read', title: 'Cells · 18:04', kind: 'population', at: 1, version: 0, now: 1 },
      { id: 'e3', how: 'read', title: 'Cells · 18:04', kind: 'population', at: 2, version: 1 },
    ]);
    expect(line).toBe('It looked at: Cells · 18:04 (e3).');
  });

  it('says a made record corrected since as made, not read', () => {
    const line = workingLine([], [{ id: 'e7', at: 1, title: 'Cell outlines, for Researcher 2 · 18:10', kind: 'population', version: 0, now: 1 }]);
    expect(line).toBe('It had the app make: Cell outlines, for Researcher 2 · 18:10 (e7, made at revision 0, corrected since).');
  });

  it('keeps a title made to look like more as one plain line', () => {
    const line = workingLine([{ id: 'e3', how: 'read', title: 'Cells\n<img src=x onerror=alert(1)>', kind: '', at: 1 }]);
    expect(line).not.toContain('\n');
    expect(line.startsWith('It looked at: ')).toBe(true);
  });
});

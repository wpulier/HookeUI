import { describe, expect, it } from 'vitest';
import { clock, positionAt } from './playhead';

describe('the test footage playhead', () => {
  it('moves on in real time from where the bridge said it was, and loops at the end', () => {
    expect(positionAt({ seconds: 10, duration: 60 }, 2500)).toBeCloseTo(12.5);
    expect(positionAt({ seconds: 59, duration: 60 }, 3000)).toBeCloseTo(2);
    expect(positionAt({ seconds: 5, duration: 0 }, 1000)).toBe(0);
    expect(positionAt({ seconds: 10, duration: 60, paused: true }, 5000)).toBe(10); // paused: it stands still
  });

  it('says the time as a player does', () => {
    expect(clock(42.9)).toBe('0:42');
    expect(clock(223)).toBe('3:43');
    expect(clock(2277)).toBe('37:57');
    expect(clock(3725)).toBe('1:02:05');
  });
});

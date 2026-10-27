// The note above a lesson on a step (quiz.ts): which step, how it is answered, how it is going.
import { describe, expect, test } from 'vitest';
import type { PresenterState } from './protocol';
import { stepNote, tally } from './quiz';

const state = (over: Partial<PresenterState> = {}): PresenterState => ({
  type: 'presenter', open: true, rid: 'r4', index: 0, step: 'q2', ...over,
});

describe('stepNote', () => {
  test('a plain question says only which one, as before', () => {
    expect(stepNote(state())).toBe('On question 2');
    expect(stepNote(state({ stepKind: 'question', outcomes: {} }))).toBe('On question 2');
  });

  test('a find step says it is answered on the slide', () => {
    expect(stepNote(state({ stepKind: 'find' }))).toBe('On question 2 · find it on your slide and click it');
  });

  test("an evidence step asks about the app's guess", () => {
    expect(stepNote(state({ step: 'q3', stepKind: 'evidence' }))).toBe("On question 3 · what would confirm or refute the app's guess?");
  });

  test('how it is going comes last', () => {
    expect(stepNote(state({ stepKind: 'find', outcomes: { q1: 'right', q3: 'skipped' } })))
      .toBe('On question 2 · find it on your slide and click it · 1 right · to go over: question 3');
  });

  test('no step, no note', () => {
    expect(stepNote(state({ step: null, outcomes: { q1: 'right' } }))).toBe('');
  });
});

describe('tally', () => {
  test('wrong and skipped are to go over, in lesson order', () => {
    expect(tally({ q10: 'wrong', q2: 'skipped', q1: 'right' })).toBe('1 right · to go over: question 2, question 10');
    expect(tally({ q1: 'right', q2: 'right' })).toBe('2 right');
    expect(tally(undefined)).toBe('');
  });
});

// The Spending menu's words, from the wire alone (Will's direction of 2026-10-13: a very clear limit, set or not, and
// what was tracked, plainly).
import { expect, test } from 'vitest';
import { parseLimit, spendingView, type Spending } from './spending';

const row = (provider: string, model: string, usd: number, unpriced = 0) =>
  ({ provider, model, area: 'Chat', events: 3, estimatedUsd: usd, unpriced, basis: 'Reported tokens', units: 3 });
const wire = (cap: number | null, spent: number, research: number | null = null): Spending => ({
  day: '2026-10-13', cap: research, estimatedUsd: 0.4, trackingSince: 1760000000, unpriced: 0, recordingErrors: 0,
  entries: [row('Google', 'Gemini 3.8 Flash', 0.4)],
  month: { month: '2026-10', estimatedUsd: spent, unknownCalls: 0, inFlightUsd: 0, cap,
    entries: [row('Google', 'Gemini 3.8 Flash', spent - 1), row('OpenAI', 'GPT-Live', 1, 2)] },
});

test('the optional limit has an explicit state: not set, or the monthly amount', () => {
  expect(spendingView(wire(null, 3), 'month').headline).toBe('Limit not set');
  expect(spendingView(wire(null, 3), 'month').used).toBe('$3.00 + unknown tracked this month');
  const set = spendingView(wire(20, 5), 'month');
  expect(set.headline).toBe('Monthly limit: $20.00');
  expect(set.used).toBe('$5.00 of $20.00 counted toward the limit this month');
  expect(set.share).toBeCloseTo(0.25);
  expect(set.reached).toBeNull();
});

test('a reached limit says what stops, in plain words', () => {
  const reached = spendingView(wire(5, 5), 'month');
  expect(reached.reached).toContain('Limit reached');
  expect(reached.reached).toContain('until next month or until you raise it');
});

test('the daily research allowance is said apart, never as the headline', () => {
  const view = spendingView(wire(20, 1, 2), 'month');
  expect(view.headline).toBe('Monthly limit: $20.00');
  expect(view.research).toBe('Research allowance: $2.00 a day, for researchers only (a separate setting in Settings)');
  expect(spendingView(wire(20, 1, null), 'month').research).toContain('none per day');
  expect(JSON.stringify(view)).not.toContain('No spending limit');
});

test('analytics say their period, and unknown charges are labelled, never zero', () => {
  const month = spendingView(wire(null, 3), 'month').period;
  expect(month.label).toBe('This month (2026-10)');
  expect([month.total, month.qualifier]).toEqual(['$3.00', 'estimated, plus 2 records of unknown cost']);
  expect(month.rows.map(r => [r.provider, r.total])).toEqual([['Google', '$2.00'], ['OpenAI', '$1.00 + unknown']]);
  expect(month.notes.join(' ')).toContain('2 records of unknown cost: not in this total, never counted as free');
  const today = spendingView(wire(null, 3), 'today').period;
  expect(today.label).toBe('Today (2026-10-13)');
  expect([today.total, today.qualifier]).toEqual(['$0.40', 'estimated']);
  const incomplete = spendingView({ ...wire(null, 3), recordingErrors: 1 }, 'today').period;
  expect(incomplete.notes).toContain('Some usage could not be saved: this is incomplete.');
});

test('an unknown call retains its actual limit allowance without presenting that allowance as usage', () => {
  const data = wire(10, 3.10);
  data.month = { ...data.month!, unknownCalls: 1, unknownAllowanceUsd: .10,
    entries: [row('OpenAI', 'GPT example', 3, 1)] };
  const view = spendingView(data, 'month');
  expect(view.counted).toBe('Includes 1 call of unknown cost: $0.10 held for the limit.');
  expect(view.period.total).toBe('$3.00');
  expect(view.period.qualifier).toBe('estimated, plus 1 record of unknown cost');
  expect(view.period.rows[0].total).toBe('$3.00 + unknown');
});

test('a limit is typed as dollars; empty is never removal', () => {
  expect(parseLimit('20')).toEqual({ usd: 20 });
  expect(parseLimit(' $12.50 ')).toEqual({ usd: 12.5 });
  expect(parseLimit('1,000')).toEqual({ usd: 1000 });
  expect(parseLimit('')).toHaveProperty('error');
  expect(parseLimit('-3')).toHaveProperty('error');
  expect(parseLimit('ten')).toHaveProperty('error');
  expect(parseLimit('1e3')).toHaveProperty('error');
});


test('what the limit counts is kept apart from tracked spend, and every total is the sum of its rows (Codex, e38fc57b)', () => {
  // Only a voice session's minutes held ahead: counted toward the limit, nothing tracked yet.
  const held: Spending = { day: '2026-10-13', cap: null, estimatedUsd: 0, trackingSince: 1760000000, unpriced: 0,
    recordingErrors: 0, entries: [], month: { month: '2026-10', estimatedUsd: 0.1, unknownCalls: 0, inFlightUsd: 0.1,
      cap: 20, entries: [], unknownEachUsd: 0.02 } };
  const one = spendingView(held, 'month');
  expect(one.used).toBe('$0.10 of $20.00 counted toward the limit this month');
  expect(one.counted).toBe('Includes $0.10 held for work in progress.');
  expect([one.period.total, one.period.qualifier]).toEqual(['$0.00', 'estimated']);
  expect(one.period.notes).toContain('No cloud usage tracked in this period yet.');
  // Then one call whose cost is unknown: the limit counts it at its allowance; the analytics say it is unknown.
  const unknown = { ...row('OpenAI', 'GPT-Live', 0, 1), events: 1 };
  const two = spendingView({ ...held, entries: [unknown], month: { ...held.month!, estimatedUsd: 0.12, unknownCalls: 1,
    entries: [unknown] } }, 'month');
  expect(two.used).toBe('$0.12 of $20.00 counted toward the limit this month');
  expect(two.counted).toBe('Includes $0.10 held for work in progress and 1 call of unknown cost at $0.02 each.');
  expect([two.period.total, two.period.qualifier]).toEqual(['Unknown', 'estimated, plus 1 record of unknown cost']);
  expect(two.period.rows.map(r => r.total)).toEqual(['unknown cost']);
  const today = spendingView({ ...held, entries: [unknown, row('Google', 'Gemini 3.8 Flash', 0.3)] }, 'today').period;
  expect([today.total, today.rows.map(r => r.total)]).toEqual(['$0.30', ['unknown cost', '$0.30']]);
});

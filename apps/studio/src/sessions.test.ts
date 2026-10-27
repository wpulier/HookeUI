// Sessions are the project, not the day (sessions.ts): the words both views say, and the one source they read. Every
// date said comes from the server's times in the viewer's local time; a session id is never read for a date.
import { afterEach, describe, expect, test, vi } from 'vitest';
import {
  chipSubWords, chipView, countWords, dayLabel, daysAgo, factsLine, groupByDay, lastUsedWords, monthDay, READ_FAILED, rowView,
  sessionName, SessionsModel, sinceWords, statsLine, suggestWords, SWITCH_WAIT_MS,
} from './sessions';
import type { ClientCommand } from './protocol';
import { at, list, NOW, row, state } from './sessions.test-support';

afterEach(() => vi.unstubAllGlobals());

describe('when, in words (local time)', () => {
  test('since: the time today, "yesterday", then the date; the year only when it is not this one', () => {
    expect(sinceWords(at(14, 10), NOW)).toBe('since 2:10 pm');
    expect(sinceWords(at(0, 5), NOW)).toBe('since 12:05 am');
    expect(sinceWords(at(23, 50, 1), NOW)).toBe('since yesterday'); // ten minutes before midnight is yesterday
    expect(sinceWords(at(9, 0, 2), NOW)).toBe('since Oct 10');
    expect(sinceWords(new Date(2025, 9, 9, 12).getTime() / 1000, NOW)).toBe('since Oct 9, 2025');
  });

  test('last used: the time today, "yesterday" with its time, then the date', () => {
    expect(lastUsedWords(at(9, 5), NOW)).toBe('9:05 am');
    expect(lastUsedWords(at(20, 12, 1), NOW)).toBe('yesterday 8:12 pm');
    expect(lastUsedWords(at(20, 12, 3), NOW)).toBe('Oct 9');
  });

  test('day separators: Today, Yesterday, then the date; calendar days, not 24-hour spans', () => {
    expect(dayLabel(at(0, 1), NOW)).toBe('Today');
    expect(dayLabel(at(23, 59, 1), NOW)).toBe('Yesterday');
    expect(dayLabel(at(12, 0, 3), NOW)).toBe('Oct 9');
    expect(daysAgo(at(23, 59, 1), at(0, 1))).toBe(1);
    expect(daysAgo(NOW + 3600, NOW)).toBe(0); // a clock a little ahead is still today
    expect(monthDay(at(12), NOW)).toBe('Oct 12');
  });
});

describe('what a session holds, in words', () => {
  test('zero counts are left out, the conversations always stay; one and many', () => {
    expect(countWords({ made: 5, researchers: 2, conversations: 3 })).toEqual(['5 results', '2 researchers', '3 conversations']);
    expect(countWords({ made: 1, researchers: 1, conversations: 1 })).toEqual(['1 result', '1 researcher', '1 conversation']);
    expect(countWords({ made: 0, researchers: 0, conversations: 2 })).toEqual(['2 conversations']);
    expect(countWords({ made: 0, researchers: 0, conversations: 0 })).toEqual(['0 conversations']);
    expect(countWords(undefined)).toEqual(['0 conversations']);
  });

  test('its assets come first, only when it has some (ADR 0022: the recordings and pictures it opened)', () => {
    expect(countWords({ sources: 3, made: 5, researchers: 2, conversations: 3 })).toEqual(['3 inputs', '5 results', '2 researchers', '3 conversations']);
    expect(countWords({ sources: 1, made: 0, researchers: 0, conversations: 1 })).toEqual(['1 input', '1 conversation']);
    expect(countWords({ sources: 0, made: 2, researchers: 0, conversations: 1 })).toEqual(['2 results', '1 conversation']);
    expect(chipSubWords({ sources: 3, made: 5, researchers: 2, conversations: 3 })).toBe('3 inputs · 5 results · 2 researchers');
    expect(chipSubWords({ sources: 1, made: 0, researchers: 0, conversations: 2 })).toBe('1 input');
    expect(statsLine({ counts: { sources: 2, made: 0, researchers: 0, conversations: 1 }, started: at(14, 10) }, NOW))
      .toBe('2 inputs · 1 conversation · since 2:10 pm');
    expect(factsLine({ started: at(9, 0, 1), days: ['2026-10-12'], counts: { sources: 2, made: 5, researchers: 0, conversations: 3 } }, NOW))
      .toBe('since yesterday · 1 day · 3 conversations · 0 researchers · 2 inputs · 5 results');
    expect(factsLine({ started: at(9, 0, 1), days: ['2026-10-12'], counts: { sources: 0, made: 5, researchers: 0, conversations: 3 } }, NOW))
      .toBe('since yesterday · 1 day · 3 conversations · 0 researchers · 5 results');
  });

  test("the rail's stats line ends with since when; the chip's second line is what was made and who worked", () => {
    expect(statsLine({ counts: { made: 5, researchers: 2, conversations: 3 }, started: at(9, 0, 1) }, NOW))
      .toBe('5 results · 2 researchers · 3 conversations · since yesterday');
    expect(statsLine({ counts: { made: 0, researchers: 1, conversations: 1 }, started: at(14, 10) }, NOW))
      .toBe('1 researcher · 1 conversation · since 2:10 pm');
    expect(chipSubWords({ made: 5, researchers: 2, conversations: 3 })).toBe('5 results · 2 researchers');
    expect(chipSubWords({ made: 0, researchers: 0, conversations: 2 })).toBe('2 conversations');
    expect(factsLine({ started: at(9, 0, 1), days: ['2026-10-11', '2026-10-12'], counts: { made: 5, researchers: 0, conversations: 3 } }, NOW))
      .toBe('since yesterday · 2 days · 3 conversations · 0 researchers · 5 results');
  });

  test("an unnamed session is an Untitled session while it began today; an older day's session reads its date", () => {
    expect(sessionName({ name: '', started: at(14, 10) }, NOW)).toBe('Untitled project');
    expect(sessionName({ name: '  ', started: at(9, 0, 1) }, NOW)).toBe('Oct 11');
    expect(sessionName({ name: 'Leaf epidermis', started: at(9, 0, 4) }, NOW)).toBe('Leaf epidermis');
  });

  test('the suggestion says the fact plainly', () => {
    expect(suggestWords({ specimen: 'kidney section', hours: 5.2 })).toBe('This looks like a new specimen (kidney section), 5 hours after this project was last used.');
    expect(suggestWords({ specimen: '', hours: 0.4 })).toBe('This looks like a new specimen, 1 hour after this project was last used.');
  });
});

describe('a session id is opaque', () => {
  test('"2026-10-12.2" is never read for a date: its name and when come from the times the server sends', () => {
    // A second session of Oct 12 that began on Oct 5 by its own times, and one whose id is no date at all.
    const second = row('2026-10-12.2', { started: at(9, 0, 7), lastActive: at(11) });
    const odd = row('lab-bench', { started: at(9, 0, 2), lastActive: at(10, 0, 2) });
    expect(rowView(second, '', NOW)).toMatchObject({ id: '2026-10-12.2', name: 'Oct 5', stats: '1 conversation · since Oct 5', lastUsed: '11:00 am' });
    expect(rowView(odd, '', NOW)).toMatchObject({ name: 'Oct 10', lastUsed: 'Oct 10' });
    expect(chipView(state('2026-10-12.2', { started: at(9, 0, 7), name: '' }), NOW)).toMatchObject({ name: 'Oct 5', since: 'since Oct 5' });
    const sent: ClientCommand[] = [];
    const model = new SessionsModel((c) => { sent.push(c); return true; }, () => NOW * 1000);
    model.applySession(state('2026-10-12'));
    model.switchTo('2026-10-12.2');
    expect(sent).toEqual([{ type: 'session', action: 'switch', id: '2026-10-12.2' }]);
  });
});

describe('rows and groups', () => {
  test('newest first by when last used, under Today, Yesterday and the date', () => {
    const rows = [row('a', { lastActive: at(9) }), row('b', { lastActive: at(20, 0, 1) }), row('c', { lastActive: at(14) }),
      row('d', { lastActive: at(8, 0, 3) })];
    expect(groupByDay(rows, NOW).map((g) => [g.label, g.rows.map((r) => r.id)])).toEqual([
      ['Today', ['c', 'a']], ['Yesterday', ['b']], ['Oct 9', ['d']],
    ]);
    // By when each began, for "All sessions".
    expect(groupByDay([row('x', { started: at(9, 0, 1), lastActive: at(15) })], NOW, 'started')[0].label).toBe('Yesterday');
  });

  test("a row's view: its name, stats, last used, working, open now", () => {
    const view = rowView(row('s1', { name: 'Blood smear', working: 2, counts: { made: 3, researchers: 2, conversations: 2 } }), 's1', NOW);
    expect(view).toMatchObject({ name: 'Blood smear', unnamed: false, current: true, working: 2, lastUsed: '10:00 am',
      stats: '3 results · 2 researchers · 2 conversations · since 9:00 am', counts: '3 results · 2 researchers · 2 conversations' });
    expect(view.title).toBe('Blood smear · 3 results · 2 researchers · 2 conversations · since 9:00 am · 2 researchers working');
    expect(rowView(row('s2'), 's1', NOW)).toMatchObject({ name: 'Untitled project', unnamed: true, current: false, working: 0 });
  });

  test('the chip: name · since over what it holds; an older server without names reads "Today · Oct 12" as before', () => {
    expect(chipView(state('s1', { name: 'Leaf epidermis', started: at(9, 0, 1), counts: { made: 5, researchers: 2, conversations: 3 } }), NOW))
      .toEqual({ name: 'Leaf epidermis', since: 'since yesterday', sub: '5 results · 2 researchers', line: 'Leaf epidermis · since yesterday · 5 results · 2 researchers', suggest: false });
    expect(chipView(state('s1', { name: '', started: at(14, 10) }), NOW).line).toBe('Untitled project · since 2:10 pm · 1 conversation');
    const legacy = { type: 'session' as const, id: '2026-10-12', label: 'Session: Oct 12', started: at(8), specimen: '' };
    expect(chipView(legacy, NOW)).toEqual({ name: 'Today · Oct 12', since: '', sub: '', line: 'Today · Oct 12', suggest: false });
    expect(chipView(state('s1', { suggest: { specimen: 'kidney', hours: 5 } }), NOW).suggest).toBe(true);
  });
});

describe('the one source both views read', () => {
  function model(online = true) {
    const sent: ClientCommand[] = [];
    let clock = NOW * 1000;
    const m = new SessionsModel((c) => { if (online) sent.push(c); return online; }, () => clock);
    let changes = 0;
    m.onChange(() => changes++);
    return { m, sent, tick: (ms: number) => { clock += ms; }, changes: () => changes };
  }

  test('a switch is sent and waits for the echo ("Opening…"); the echo of that session ends it', () => {
    const { m, sent } = model();
    m.applySession(state('a'));
    m.applyList(list('a', [row('a'), row('b')]));
    expect(m.switchTo('a')).toBe(false); // already open
    expect(m.switchTo('b')).toBe(true);
    expect(sent).toEqual([{ type: 'session', action: 'switch', id: 'b' }]);
    expect(m.opening('b')).toBe(true);
    m.applySession(state('a')); // a stale echo of the one left changes nothing
    expect(m.opening('b')).toBe(true);
    m.applySession(state('b'));
    expect(m.opening()).toBe(false);
    expect(m.current).toBe('b');
  });

  test('a new session: any other id echoed ends it, and the new one is given once to a view to name', () => {
    const { m, sent } = model();
    m.applySession(state('2026-10-12'));
    expect(m.create()).toBe(true);
    expect(sent).toEqual([{ type: 'session', action: 'new' }]);
    expect(m.creating()).toBe(true);
    m.applySession(state('2026-10-12.2', { started: NOW }));
    expect(m.creating()).toBe(false);
    expect(m.takeCreated()).toBe('2026-10-12.2');
    expect(m.takeCreated()).toBeNull();
    // Before the list names it, it is there as the open session says it.
    expect(m.rows().map((r) => r.id)).toEqual(['2026-10-12.2']);
  });

  test('a rename shows at once and is kept until the server says it; blank or unchanged sends nothing', () => {
    const { m, sent } = model();
    m.applySession(state('a', { name: '' }));
    m.applyList(list('a', [row('a'), row('b', { name: 'Blood smear' })]));
    expect(m.rename('b', '  ')).toBe(false);
    expect(m.rename('b', 'Blood smear')).toBe(false);
    expect(m.rename('b', ' Onion skin ')).toBe(true);
    expect(sent).toEqual([{ type: 'session', action: 'rename', id: 'b', name: 'Onion skin' }]);
    expect(m.rows().find((r) => r.id === 'b')?.name).toBe('Onion skin');
    m.applyList(list('a', [row('a'), row('b', { name: 'Blood smear' })])); // an older list: still the new name
    expect(m.rows().find((r) => r.id === 'b')?.name).toBe('Onion skin');
    m.applyList(list('a', [row('a'), row('b', { name: 'Onion skin' })]));
    m.applyList(list('a', [row('a'), row('b', { name: 'Renamed elsewhere' })])); // said by the server since: its word
    expect(m.rows().find((r) => r.id === 'b')?.name).toBe('Renamed elsewhere');
  });

  test('the open session as the server says it now: its name and counts over the list\'s', () => {
    const { m } = model();
    m.applyList(list('a', [row('a', { name: 'Old', lastActive: at(9) }), row('b', { lastActive: at(12) })]));
    m.applySession(state('a', { name: 'Leaf', lastActive: at(15), counts: { made: 4, researchers: 1, conversations: 2 } }));
    const rows = m.rows();
    expect(rows.map((r) => r.id)).toEqual(['a', 'b']); // used last, so first
    expect(rows[0]).toMatchObject({ name: 'Leaf', counts: { made: 4, researchers: 1, conversations: 2 } });
  });

  test('offline: nothing waits, and it says to reconnect', () => {
    const { m } = model(false);
    m.applySession(state('a'));
    expect(m.switchTo('b')).toBe(false);
    expect(m.opening()).toBe(false);
    expect(m.error).toBe('Reconnect to the studio, then try again.');
  });

  test('a switch the server never echoes is given up, and said', () => {
    const { m, tick } = model();
    m.applySession(state('a'));
    m.switchTo('b');
    tick(SWITCH_WAIT_MS - 1);
    expect(m.expire()).toBe(false);
    tick(2);
    expect(m.expire()).toBe(true);
    expect(m.opening()).toBe(false);
    expect(m.error).toBe('That project did not open. Try again.');
  });

  test('the list is read once when no sessions message came; a failure says so and the next opening tries again', async () => {
    const { m } = model();
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue({ ok: true, json: async () => ({ current: 'a', sessions: [row('a')] }) });
    await m.load(fetcher as unknown as typeof fetch);
    expect(m.error).toBe(READ_FAILED);
    await m.load(fetcher as unknown as typeof fetch);
    await m.load(fetcher as unknown as typeof fetch);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(m.error).toBe('');
    expect(m.list?.sessions.map((r) => r.id)).toEqual(['a']);
    const pushed = model().m;
    pushed.applyList(list('a', [])); // a sessions message came: nothing is fetched
    const never = vi.fn();
    await pushed.load(never as unknown as typeof fetch);
    expect(never).not.toHaveBeenCalled();
  });
});

// Compatibility with an older running backend without renaming user-owned titles.
test('project wording adapts the generated legacy placeholder but preserves an explicit name', () => {
  expect(sessionName({name:'',label:'Untitled session',started:at(14)},at(15))).toBe('Untitled project');
  expect(sessionName({name:'Untitled session',label:'Untitled session',started:at(14)},at(15))).toBe('Untitled session');
});

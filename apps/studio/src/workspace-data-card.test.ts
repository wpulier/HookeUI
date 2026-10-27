import { describe, expect, it } from 'vitest';
import { cardActions, dataCounts, dataKind, kindWords, madeByWords, makerName, matchesFilter, matchesSearch, menuActions, resultRowWords, shownFilters } from './workspace-data-card';
import type { CapabilitiesWire } from './protocol';

const entry = (kind: string, extra: { table?: boolean; product?: boolean } = {}) => ({ kind, ...extra }) as Parameters<typeof dataKind>[0];

describe('the Data panel’s one filter', () => {
  it('puts every entry in exactly one kind, with Other for the rest', () => {
    expect(dataKind(entry('population'))).toBe('masks');
    expect(dataKind(entry('result-table'))).toBe('tables');
    expect(dataKind(entry('result', { table: true }))).toBe('tables');
    expect(dataKind(entry('tracks'))).toBe('tracks');
    expect(dataKind(entry('result'))).toBe('answers');
    expect(dataKind(entry('annotation'))).toBe('notes');
    expect(dataKind(entry('research'))).toBe('research');
    for (const kind of ['recording', 'snapshot', 'dataset', 'bookmark', 'clip', 'job']) expect(dataKind(entry(kind))).toBe('other');
    expect(dataKind(entry('result', { product: true }))).toBe('other');
  });
  it('counts each kind, shows only kinds present (and the chosen one), and filters by them', () => {
    const items = [entry('population'), entry('population'), entry('annotation')];
    const counts = dataCounts(items);
    expect(counts).toMatchObject({ all: 3, masks: 2, notes: 1, tables: 0 });
    expect(shownFilters(counts, 'all').map(([id, , n]) => `${id}:${n}`)).toEqual(['all:3', 'masks:2', 'notes:1']);
    expect(shownFilters(counts, 'tables').map(([id]) => id)).toEqual(['all', 'masks', 'tables', 'notes']);
    expect(items.filter((item) => matchesFilter(item, 'masks'))).toHaveLength(2);
    expect(items.filter((item) => matchesFilter(item, 'all'))).toHaveLength(3);
  });
});

describe('a Data card’s actions', () => {
  it('keeps every action it had, at most three in view and the rest under Details', () => {
    const masks = cardActions(entry('population'), { placed: true, source: true });
    expect(masks.shown).toEqual(['reveal', 'masks', 'open-in']);
    expect(masks.details).toEqual(['measurements', 'provenance', 'capture', 'use-source']);
    const table = cardActions(entry('result-table'), { placed: false, source: false });
    expect(table).toEqual({ shown: ['measurements', 'open-in'], details: ['provenance'] });
    const note = cardActions(entry('annotation'), { placed: false, source: false });
    expect(note).toEqual({ shown: ['open-in'], details: ['provenance'] });
  });
});

describe('a Data row’s ⋯', () => {
  it('holds every action the card had, once each, the card’s first ones first and How it was made last', () => {
    for (const [kind, facts] of [['population', { placed: true, source: true }], ['result-table', { placed: false, source: false }], ['annotation', { placed: true, source: false }]] as const) {
      const { shown, details } = cardActions(entry(kind), facts), menu = menuActions(entry(kind), facts);
      expect([...menu].sort()).toEqual([...shown, ...details].sort());
      expect(new Set(menu).size).toBe(menu.length);
      expect(menu.at(-1)).toBe('provenance');
      expect(menu.slice(0, shown.length)).toEqual(shown);
    }
    expect(menuActions(entry('population'), { placed: true, source: true })).toEqual(['reveal', 'masks', 'open-in', 'measurements', 'capture', 'use-source', 'provenance']);
  });
});

describe('a Data card’s kind line', () => {
  it('says the kind in a word, and a revision only once there is a later one', () => {
    expect(kindWords({ kind: 'population', version: 0 } as Parameters<typeof kindWords>[0])).toBe('Masks');
    expect(kindWords({ kind: 'population', version: 2 } as Parameters<typeof kindWords>[0])).toBe('Masks · revision 2');
    expect(kindWords({ kind: 'result', table: true, version: 0 } as Parameters<typeof kindWords>[0])).toBe('Table');
    expect(kindWords({ kind: 'named-outline', version: null } as Parameters<typeof kindWords>[0])).toBe('Masks');
    expect(kindWords({ kind: 'dataset', version: 0 } as Parameters<typeof kindWords>[0])).toBe('Dataset');
  });
});

describe('a Data row names its maker by its display name, never its key', () => {
  const wire = { roles: [{ id: 'cells', options: [{ id: 'basic-bright-objects', label: 'Bright objects' }, { id: 'micro-sam-lm', label: 'micro-sam (light microscopy)' }] }],
    models: [{ id: 'swin2sr-enlarge', name: 'Swin2SR enlarge', usedFor: [] }] } as unknown as CapabilitiesWire;
  const named = { model: (made: { implementation?: string; operation?: string }) => makerName(wire, made), researcher: (rid: string) => rid === 'r602534' ? 'Researcher 3' : undefined };
  it('by the model\'s one name, from its role or its catalog entry; a researcher by name', () => {
    expect(madeByWords({ kind: 'population', madeBy: { implementation: 'basic-bright-objects', operation: 'cells' } }, null, named)).toBe('by Bright objects');
    expect(madeByWords({ kind: 'population', madeBy: { implementation: 'micro-sam-lm' } }, null, named)).toBe('by micro-sam (light microscopy)');
    expect(madeByWords({ kind: 'result', madeBy: { implementation: 'swin2sr-enlarge' } }, null, named)).toBe('by Swin2SR enlarge');
    expect(madeByWords({ kind: 'result', madeBy: { for: 'r602534', implementation: 'basic-bright-objects' } }, null, named)).toBe('by Researcher 3');
    expect(madeByWords({ kind: 'result', madeBy: { for: 'Researcher 4' } }, null, named)).toBe('by Researcher 4');
  });
  it('no row meta ends in "by <hyphenated-id>": an unknown key says nothing (the row then says when)', () => {
    const rows = [{ kind: 'population', madeBy: { implementation: 'unknown-model-key' } }, { kind: 'result', madeBy: { for: 'r999999' } },
      { kind: 'population', madeBy: { implementation: 'basic-bright-objects' } }];
    for (const row of rows) expect(madeByWords(row, null, named)).not.toMatch(/by [a-z0-9]+(?:[-_.][a-z0-9]+)+$|by r\d+$/);
    expect(madeByWords(rows[0], null, named)).toBe('');
    expect(madeByWords(rows[0])).toBe(''); // with no names at all, nothing raw either
  });
});

describe('Data search', () => {
  it('matches the kind word each row shows: "mask" finds a Masks result whose title never says it', () => {
    const masks = { kind: 'population', title: 'Bright regions · Bright objects · 16:20', summary: '', plain: '', id: 'e12', version: 0 } as Parameters<typeof matchesSearch>[0];
    expect(matchesSearch(masks, 'mask')).toBe(true);
    expect(matchesSearch(masks, 'MASKS')).toBe(true);
    expect(matchesSearch(masks, 'bright')).toBe(true);
    expect(matchesSearch(masks, 'table')).toBe(false);
  });
});

it('a Result row is a title line and one quiet line: the dot-joined title splits; its time comes first, the maker said once', () => {
  // "Bright regions · Bright objects · B…" and "· 19:54" lost to the ellipsis, on seven of ten rows (2026-10-25 review).
  const maker = 'by Bright objects · Basic local method'; // madeByWords' display name
  expect(resultRowWords('Bright regions · Bright objects · Basic local method · 19:54', { kind: 'Masks', maker, where: 'on Tab 4' }))
    .toEqual({ title: 'Bright regions', sub: 'Masks · 19:54 · on Tab 4 · Bright objects (Basic local method)' });
  // The maker the title line already names is not said again.
  expect(resultRowWords('Bright objects · Basic local method · 19:54', { kind: 'Answer', maker }))
    .toEqual({ title: 'Bright objects', sub: 'Answer · 19:54 · Basic local method' });
  expect(resultRowWords('Bright objects · 19:54', { kind: 'Answer', maker, where: 'on Tab 4' }))
    .toEqual({ title: 'Bright objects', sub: 'Answer · 19:54 · on Tab 4 · Basic local method' });
  // A maker the title does not name (a researcher it was made for) is added; another day says its day.
  expect(resultRowWords('Cell outlines · micro-sam (light microscopy) · 16:20', { kind: 'Masks', maker: 'by Researcher 3', day: 'Oct 22' }))
    .toEqual({ title: 'Cell outlines', sub: 'Masks · Oct 22 · micro-sam (light microscopy) · by Researcher 3' });
  expect(resultRowWords('Spot measure · 19:32', { kind: 'Table', maker: 'by Researcher 3' })).toEqual({ title: 'Spot measure', sub: 'Table · 19:32 · Researcher 3' });
  // A title without the server's time (a note's) stays whole.
  expect(resultRowWords('Your note · Untitled note', { kind: 'Note', where: 'on Main' })).toEqual({ title: 'Your note · Untitled note', sub: 'Note · on Main' });
});

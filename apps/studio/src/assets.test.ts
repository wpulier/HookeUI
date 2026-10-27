import { describe, expect, it } from 'vitest';
import { cardNote, filterGroups } from './assets';
import { footageGroups, type FootageItem } from './camera';

const item = (name: string, title: string, group: string, extra: Partial<FootageItem> = {}): FootageItem =>
  ({ name, title, group, seconds: 223, truth: false, ...extra });

const library = [
  item('real/leaf.mp4', 'Leaf epidermis, 2026-06-14', 'Real microscope', { specimen: 'leaf epidermis' }),
  item('added/onion.mp4', 'Onion skin, 2026-10-07', 'Added here', { backedUp: false }),
  item('corpus/corpus-bccd.mp4', 'Blood smear', 'Corpus: blood smear', { truth: true }),
  item('corpus/corpus-sperm.mp4', 'Human sperm', 'Corpus: human sperm', { truth: true }),
];

describe('the Assets view', () => {
  it('finds recordings by every word of the search, across title, group and specimen', () => {
    const groups = footageGroups(library);
    expect(filterGroups(groups, '').length).toBe(4);
    expect(filterGroups(groups, 'blood').map((g) => g.items.map((i) => i.name))).toEqual([['corpus/corpus-bccd.mp4']]);
    expect(filterGroups(groups, 'leaf epidermis').map((g) => g.name)).toEqual(['Real microscope']);
    expect(filterGroups(groups, 'leaf blood')).toEqual([]);
  });

  it("says a card's length, its objective when known, and when it is not backed up yet", () => {
    expect(cardNote(library[0])).toBe('3:43');
    expect(cardNote(library[1])).toBe('3:43 · not backed up yet');
    expect(cardNote({ ...library[0], optics: { objective: 40, eyepiece: 10 } })).toBe('3:43 · 40x objective');
    expect(cardNote({ ...library[0], seconds: null })).toBe('');
  });
});

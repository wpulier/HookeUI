// The library's wire and words (asset-library.ts; ADR 0022's amendments): the wire read safely, the words of a card,
// and the user's own footage found as the asset it is. All assets itself is library-page.test.ts.
import { describe, expect, test, vi } from 'vitest';
import type { AssetMeta } from './protocol';
import {
  assetFor, clipCount, clipName, isOwnFootage, libraryMeta, libraryUrl, readLibrary, specimenTitle, type LibrarySpecimen,
} from './asset-library';
import { playingName } from './assets';
import { at, NOW } from './sessions.test-support';

function asset(id: string, fields: Partial<AssetMeta> = {}): AssetMeta {
  return { id, kind: 'recording', title: `Asset ${id}`, from: 'added', capturedAt: at(1, 44), captured: 'Oct 12, 1:44 am', seconds: 192,
    capturedOn: null, microscope: true, optics: {}, specimen: null, specimenBy: null, notes: null, origin: null, file: `${id}.mp4`,
    thumb: `/api/library/${id}/thumbnail`, ...fields };
}
const example = (id: string, specimen: string, n = 0) =>
  asset(id, { from: 'example', title: `${specimenTitle(specimen)}${n ? ` (${n + 1})` : ''}`, specimen, seconds: null, optics: { objective: 40 } });
const kind = (s: LibrarySpecimen) => [s.name, s.count, s.first.id];

describe('the wire and the words', () => {
  test("the library's address says only what is asked", () => {
    expect(libraryUrl({})).toBe('/api/library?q=');
    expect(libraryUrl({ q: ' sperm ', kind: 'still', scope: 'mine', fresh: true })).toBe('/api/library?q=sperm&kind=still&scope=mine&fresh=1');
    expect(libraryUrl({ scope: 'example', specimen: 'Gram-stained bacteria', offset: 36, limit: 36 }))
      .toBe('/api/library?q=&scope=example&specimen=Gram-stained+bacteria&offset=36&limit=36');
    expect(libraryUrl({ scope: 'mine', category: 'Protists & algae' })).toBe('/api/library?q=&scope=mine&category=Protists+%26+algae');
  });

  test('read safely: rows that are not an asset are left out, and so are specimens without one', () => {
    expect(readLibrary(null)).toEqual({ assets: [], count: 0, specimens: null });
    const read = readLibrary({ assets: [asset('a1'), { id: 'o3' }], count: 1503,
      specimens: [{ name: 'pond water', count: 2, first: asset('a2') }, { name: 'x', count: 1, first: null }, 'nonsense'] });
    expect(read.assets.map((a) => a.id)).toEqual(['a1']);
    expect(read.count).toBe(1503);
    expect(read.specimens!.map(kind)).toEqual([['pond water', 2, 'a2']]);
  });

  test("a row's second line: example data its kind and magnification; the user's own when, length, magnification, specimen", () => {
    expect(libraryMeta(example('a5', 'blood smear'), NOW)).toBe('Recording · 40x');
    expect(libraryMeta(asset('a1', { specimen: 'sperm', optics: { objective: 40 } }), NOW)).toBe('Recording · 1:44 am · 3:12 · 40x · sperm');
    expect(specimenTitle('gram-stained  bacteria ')).toBe('Gram-stained bacteria');
    expect(specimenTitle('')).toBe('Not named yet');
    expect([clipCount(1), clipCount(1503)]).toEqual(['1 clip', '1,503 clips']);
    expect(clipName('Gram-stained bacteria (37)', 'Gram-stained bacteria')).toBe('Clip 37');
    expect(clipName('Pond water', 'pond water')).toBe('Clip 1');
    expect(clipName('My own name for it', 'pond water')).toBe('My own name for it');
  });
});

describe("the user's own footage is opened as the asset it is", () => {
  test('real microscope recordings and files added here are the user\'s own; the rest is test footage', () => {
    expect(isOwnFootage({ group: 'Real microscope' })).toBe(true);
    expect(isOwnFootage({ group: 'Added here' })).toBe(true);
    for (const group of ['Camera, not on the microscope', 'Corpus: blood smear', 'Other']) expect(isOwnFootage({ group })).toBe(false);
  });

  test('found by its file among the user\'s own, read fresh; none when the library does not have it or can\'t be read', async () => {
    const sperm = asset('a12', { file: '2026-10-12-sperm-11m11s.mov', title: 'Sperm, 2026-10-12' });
    const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ assets: [asset('a3', { file: '2026-10-12-sperm-11m11s.mov.json' }), sperm] }) }));
    expect(await assetFor({ name: 'added/2026-10-12-sperm-11m11s.mov' }, fetcher as unknown as typeof fetch)).toEqual(sperm);
    expect(fetcher).toHaveBeenCalledWith('/api/library?q=2026-10-12-sperm-11m11s&scope=mine&fresh=1', { cache: 'no-store' });
    expect(await assetFor({ name: 'real/leaf.mp4' }, fetcher as unknown as typeof fetch)).toBeNull();
    expect(await assetFor({ name: 'real/leaf.mp4' }, vi.fn(async () => { throw new Error('offline'); }) as unknown as typeof fetch)).toBeNull();
  });

  test('the Footage page knows which of its cards is on stage, test footage or one of the user\'s own', () => {
    const library = [{ name: 'added/sperm.mov' }, { name: 'corpus/corpus-bccd.mp4' }];
    expect(playingName('corpus/corpus-bccd.mp4', library)).toBe('corpus/corpus-bccd.mp4');
    expect(playingName('asset:/Users/x/MicroscopeMind/footage/added/sperm.mov', library)).toBe('added/sperm.mov');
    expect(playingName('asset:/Users/x/datasets/ds-1/video/part-1.mp4', library)).toBeNull();
    expect(playingName(null, library)).toBeNull();
  });
});

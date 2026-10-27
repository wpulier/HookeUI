// The library's wire and words, the studio's half (ADR 0022 and its amendments): the route's address and body read
// safely, the words a card says, and the user's own footage found as the asset it is. All assets (library-page.ts)
// draws them. The user's own recordings also show in the camera bridge's footage library (the Test footage page,
// assets.ts): those are found here as the assets they are (`assetFor`) and opened as such, never played as test footage.
// Everything here is pure (asset-library.test.ts).
import type { AssetMeta } from './protocol';
import type { FootageItem } from './camera';
import { isAsset, magnificationWords, sourceMeta, uploadedFile } from './sources';

/** Whose: the user's own recordings and pictures, or example data (the public corpus). */
export type LibraryScope = 'mine' | 'example';

/** A specimen of example data: its name as said, how many, and its newest (a cover, or the one to open when alone). */
export interface LibrarySpecimen {
  name: string;
  count: number;
  first: AssetMeta;
}

/** What GET /api/library gives: a page of assets, how many match, and for example data its specimens. */
export interface LibraryPage {
  assets: AssetMeta[];
  count: number;
  specimens: LibrarySpecimen[] | null;
}

export interface LibraryQuery {
  q?: string;
  kind?: 'recording' | 'still' | null;
  scope?: LibraryScope | null;
  specimen?: string | null;
  category?: string | null;
  offset?: number;
  limit?: number;
  fresh?: boolean;
}

/** The library's address for a query (only what is asked is said). */
export function libraryUrl(query: LibraryQuery): string {
  const params = new URLSearchParams({ q: (query.q ?? '').trim() });
  if (query.kind) params.set('kind', query.kind);
  if (query.scope) params.set('scope', query.scope);
  if (query.specimen) params.set('specimen', query.specimen);
  if (query.category) params.set('category', query.category);
  if (query.offset) params.set('offset', String(query.offset));
  if (query.limit) params.set('limit', String(query.limit));
  if (query.fresh) params.set('fresh', '1');
  return `/api/library?${params}`;
}

/** The route's body, read safely: rows that are not an asset's are left out, and so are specimens without a cover. */
export function readLibrary(body: unknown): LibraryPage {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const assets = Array.isArray(b.assets) ? b.assets.filter(isAsset) : [];
  const specimens = Array.isArray(b.specimens)
    ? b.specimens.filter((s): s is LibrarySpecimen => !!s && typeof s === 'object' && typeof (s as LibrarySpecimen).name === 'string'
      && typeof (s as LibrarySpecimen).count === 'number' && isAsset((s as LibrarySpecimen).first))
    : null;
  return { assets, count: typeof b.count === 'number' ? b.count : assets.length, specimens };
}

/** "Gram-stained bacteria" for its heading (the record's word, capitalised); "Not named yet" when it has none. */
export function specimenTitle(name: string): string {
  const said = name.replace(/\s+/g, ' ').trim();
  return said ? said.charAt(0).toUpperCase() + said.slice(1) : 'Not named yet';
}

/** A clip's name inside its specimen: "Clip 37" for "Gram-stained bacteria (37)", "Clip 1" for the specimen's own name;
 * its title when it is named otherwise. A narrow column then tells the clips apart. */
export function clipName(title: string, specimen: string): string {
  const name = specimenTitle(specimen).toLowerCase();
  if (!specimen.trim() || !title.toLowerCase().startsWith(name)) return title;
  const rest = title.slice(name.length).trim();
  const n = /^\((\d+)\)$/.exec(rest);
  return !rest ? 'Clip 1' : n ? `Clip ${n[1]}` : title;
}

/** "1 clip", "1,503 clips". */
export function clipCount(n: number): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? 'clip' : 'clips'}`;
}

/** A row's second line: example data says its kind and magnification (its file's date says nothing about it); the
 * user's own say when, a recording's length and the magnification, then the specimen. */
export function libraryMeta(asset: AssetMeta, now: number): string {
  if (asset.from === 'example') return [asset.kind === 'still' ? 'Picture' : 'Recording', magnificationWords(asset.optics)].filter(Boolean).join(' · ');
  return [sourceMeta(asset, now), asset.specimen ?? ''].filter(Boolean).join(' · ');
}

// --- the user's own recordings in the camera bridge's footage library -------------------------------------------

/** The bridge's groups (footage_media.py) that hold the user's own: real microscope recordings and files added here.
 * They are assets, opened as a session's sources; the rest (the corpus, bench clips, the camera off the microscope) is
 * test footage, which stands for the live camera. */
export const OWN_FOOTAGE_GROUPS: readonly string[] = ['Real microscope', 'Added here'];

export function isOwnFootage(item: Pick<FootageItem, 'group'>): boolean {
  return OWN_FOOTAGE_GROUPS.includes(item.group);
}

/** The asset one of the user's own footage items is (its file, `real/x.mp4` or `added/x.mov`, matched by name among the
 * user's own, read fresh); null when the library does not have it, or can't be read. */
export async function assetFor(item: Pick<FootageItem, 'name'>, fetcher: typeof fetch = fetch): Promise<AssetMeta | null> {
  const { file, stem } = uploadedFile(item.name);
  try {
    const response = await fetcher(libraryUrl({ q: stem, scope: 'mine', fresh: true }), { cache: 'no-store' });
    if (!response.ok) return null;
    return readLibrary(await response.json()).assets.find((a) => a.file === file) ?? null;
  } catch {
    return null;
  }
}

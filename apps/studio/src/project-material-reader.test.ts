import {afterEach, expect, it, vi} from 'vitest';
import {readMaterialRevision} from './project-material-reader';
const ref = {id: 'p1', day: '2026-10-29', version: 2};
const digest = 'a'.repeat(64);
const project = {id: ref.id, session: ref.day, revision: 2, digest, document: {kind: 'source-file', title: 'Controls'}};
afterEach(() => vi.unstubAllGlobals());
it('reads only the exact Project revision and never loads the source bytes', async () => {
  const fetcher = vi.fn(async (_url: string, _options: RequestInit) => ({ok: true, json: async () => ({project})}));
  vi.stubGlobal('fetch', fetcher);
  expect(await readMaterialRevision(ref, digest, new AbortController().signal)).toEqual(project.document);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/scientific-projects/p1?session=2026-10-29&revision=2');
});
it.each([{revision: 3}, {session: 'foreign'}, {digest: 'b'.repeat(64)}, {id: 'different'}])('refuses a mismatched retained identity: %j', async change => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok: true, json: async () => ({project: {...project, ...change}})})));
  await expect(readMaterialRevision(ref, digest, new AbortController().signal)).rejects.toThrow('not the requested saved revision');
});

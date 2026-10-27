import { describe, expect, it, vi } from 'vitest';
import { jobActions, listVolumes, needs, orientationProblem, type VolumeAdded, type VolumeEntry } from './volumespanel';

const volume = (facts: Record<string, unknown>, channels = 1): VolumeEntry => ({
  id: 'v1', kind: 'volume', title: 'Volume · brain', state: 'saved', summary: '',
  values: { facts: Object.fromEntries(Object.entries(facts).map(([k, v]) => [k, { value: v, source: v == null ? null : 'file' }])),
    series: [{ axes: channels > 1 ? 'ZCYX' : 'ZYX', shape: [3, 4, 5], channels }] },
});

describe('brain volumes', () => {
  it('asks only for what the file does not state, and always the orientation', () => {
    expect(needs(volume({ voxel_um: [50, 40, 40], orientation: null, series: 0 }))).toEqual({ series: 0, voxel: false, orientation: true, channels: 0, times: 0 });
    expect(needs(volume({ voxel_um: null, orientation: 'psl', series: 0, channel: null }, 2))).toEqual({ series: 0, voxel: true, orientation: false, channels: 2, times: 0 });
    expect(needs(volume({ voxel_um: [1, 1, 1], orientation: 'psl', series: 0, channel: 1 }, 2))).toEqual({ series: 0, voxel: false, orientation: false, channels: 0, times: 0 });
  });
  it('asks a file of several series which one first: its scale and channels are its own', () => {
    const two: VolumeEntry = { ...volume({ voxel_um: null, orientation: null, series: null }),
      values: { facts: { series: { value: null, source: null } }, series: [
        { axes: 'ZYX', shape: [4, 6, 7] }, { axes: 'ZCYX', shape: [3, 8, 9], channels: 2 }] } };
    expect(needs(two)).toEqual({ series: 2, voxel: false, orientation: false, channels: 0, times: 0 });
  });
  it('takes BrainGlobe orientations only: one of each pair', () => {
    expect(orientationProblem('PSL')).toBe('');
    expect(orientationProblem('asr')).toBe('');
    expect(orientationProblem('pps')).toContain('each direction once');
    expect(orientationProblem('xyz')).toContain('three letters');
  });
  it('offers Cancel while it runs, Run again when it ended without a result, Open once done', () => {
    const job = (state: string): VolumeEntry => ({ id: 'j1', kind: 'job', title: '', state, summary: '' });
    expect(jobActions(job('running'))).toEqual(['cancel']);
    expect(jobActions(job('done'))).toEqual(['open']);
    expect(['interrupted', 'failed', 'cancelled'].map((s) => jobActions(job(s))[0])).toEqual(['again', 'again', 'again']);
  });
});


it('reads the volume list, jobs and every selected volume detail from the same explicit session', async () => {
  const read = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url);
    if (path === '/api/resources?kind=volume&day=session-a') return Response.json({ entries: [
      { id: 'v1', day: 'session-a', kind: 'volume', title: 'Chosen stack' },
      { id: 'v1', day: 'other-session', kind: 'volume', title: 'Other stack' }] });
    if (path === '/api/resources?kind=job&day=session-a') return Response.json({ entries: [] });
    if (path === '/api/resources/v1?day=session-a') return Response.json({ detail: { values: { facts: { voxel_um: { value: [1, 2, 3], source: 'file' } } } } });
    throw new Error(`unbound request: ${path}`);
  });
  const found = await listVolumes('session-a', read);
  expect(found).toHaveLength(1);
  expect(found[0].title).toBe('Chosen stack');
  expect(found[0].values?.facts?.voxel_um.value).toEqual([1, 2, 3]);
  expect(read).toHaveBeenCalledTimes(3);
});

describe('adding a volume by its path', () => {
  // Drawn the way the Models page draws it: its words are the page's, and every setWords draws the section again.
  const page = async (add: (path: string) => Promise<VolumeAdded | undefined>, words: string, owner?: () => () => boolean) => {
    const { volumesSection } = await import('./volumespanel');
    const { syntheticDOM } = await import('./workspace-windows.test-support');
    const dom = syntheticDOM();
    vi.stubGlobal('document', dom.document);
    const commands = { upload: vi.fn(), add, describe: vi.fn(), start: vi.fn(), cancel: vi.fn(), again: vi.fn(), open: vi.fn(), owner };
    let said = words;
    const draw = () => dom.host.replaceChildren(volumesSection([], commands, said,
      (next) => { said = next; draw(); }) as unknown as Parameters<typeof dom.host.replaceChildren>[0]);
    draw();
    return dom.host;
  };
  const submit = async (host: Awaited<ReturnType<typeof page>>, value: string) => {
    const { fire, settleMicrotasks } = await import('./workspace-windows.test-support');
    const form = host.querySelector('[data-model-editor="volume-add"]')!;
    form.querySelector('input')!.value = value;
    fire(form, 'submit');
    await settleMicrotasks(); await settleMicrotasks();
  };
  const refusal = "brainreg isn't installed here yet: install it on its card";

  it('says what the add said, in place of an earlier refusal (Window C, 2026-10-14)', async () => {
    try {
      const add = vi.fn(async () => ({ ok: true, says: 'brain.tif: a volume, 3 × 4 × 5 (ZYX)' }));
      const host = await page(add, refusal);
      expect(host.querySelector('.models-own-said')!.textContent).toBe(refusal);
      await submit(host, ' /Users/me/brain.tif ');
      expect(add).toHaveBeenCalledWith('/Users/me/brain.tif');
      const words = host.querySelector('.models-own-said')!.textContent;
      expect(words).toBe('brain.tif: a volume, 3 × 4 × 5 (ZYX)');
      expect(words).not.toContain("isn't installed");
      expect(host.querySelector('[data-model-editor="volume-add"]')!.querySelector('input')!.value).toBe('');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('leaves a path typed in the next session when an add of the session before answers after the switch', async () => {
    try {
      let session = 'a';
      let answer: (reply: VolumeAdded) => void = () => undefined;
      const add = vi.fn(() => new Promise<VolumeAdded>((resolve) => { answer = resolve; }));
      const owner = () => { const claimed = session; return () => claimed === session; };
      const host = await page(add, '', owner);
      await submit(host, '/Users/me/brain-a.tif');
      session = 'b';  // the form is kept across the switch, and the user types the next session's path in it
      const input = host.querySelector('[data-model-editor="volume-add"]')!.querySelector('input')!;
      input.value = '/Users/me/brain-b.tif';
      answer({ says: 'brain-a.tif: a volume, 3 × 4 × 5 (ZYX)' });
      const { settleMicrotasks } = await import('./workspace-windows.test-support');
      await settleMicrotasks(); await settleMicrotasks();
      expect(input.value).toBe('/Users/me/brain-b.tif');
      expect(host.querySelector('.models-own-said')!.textContent).toBe('');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('still says why when the add is refused', async () => {
    try {
      const add = vi.fn(async () => { throw new Error('No file at /Users/me/missing.tif'); });
      const host = await page(add, '');
      await submit(host, '/Users/me/missing.tif');
      expect(host.querySelector('.models-own-said')!.textContent).toBe('No file at /Users/me/missing.tif');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

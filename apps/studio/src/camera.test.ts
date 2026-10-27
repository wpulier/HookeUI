import { afterEach, describe, expect, it, vi } from 'vitest';
import { CameraFeed, clips, duration, feedQuery, footageGroups, opticsLine, pickKey, specimenOf, type FootageItem } from './camera';

const item = (name: string, group: string, seconds: number | null = 10): FootageItem => ({ name, title: name, group, seconds, truth: false });

describe('the camera menu groups the footage library', () => {
  it('keeps the bridge order, one group per run of the same group, corpus groups folded', () => {
    const groups = footageGroups([
      item('real/a.mp4', 'Real microscope'),
      item('real/b.mp4', 'Real microscope'),
      item('real/room.mp4', 'Camera, not on the microscope'),
      item('corpus/corpus-bccd.mp4', 'Corpus: blood smear'),
      item('corpus/corpus-sperm.mp4', 'Corpus: sperm'),
      item('mine.mp4', 'Other'),
    ]);
    expect(groups.map((g) => [g.name, g.items.length, g.folded])).toEqual([
      ['Real microscope', 2, false],
      ['Camera, not on the microscope', 1, false],
      ['Corpus: blood smear', 1, true],
      ['Corpus: sperm', 1, true],
      ['Other', 1, false],
    ]);
  });

  it('shows no length until the bridge has measured it', () => {
    expect(duration(null)).toBe('');
    expect(duration(223)).toBe('3:43');
  });
});


describe('what a recording was filmed with, and the public library', () => {
  it('says only the optics that are known', () => {
    expect(opticsLine(null)).toBe('');
    expect(opticsLine({ camera: 'iPhone', objective: null })).toBe('');
    expect(opticsLine({ objective: 40, eyepiece: 10, fieldNumber: 18, umPerPx: 0.29 })).toBe('40x objective · 10x eyepiece · FN 18 · 0.29 µm/px');
    expect(opticsLine({ objective: 2.5 })).toBe('2.5x objective');
  });

  it('names a public-library group by its specimen and counts clips, not tours', () => {
    expect(specimenOf('Corpus: blood smear')).toBe('Blood smear');
    expect(clips(1)).toBe('1 clip');
    expect(clips(1503)).toBe('1,503 clips');
  });
});


// Test footage plays from the studio itself (where, and held at once); the user's own recordings and pictures are the
// server's to play (ADR 0022), and the studio only shows the switch while it waits.
describe('playing into the feed: test footage where and paused, and a switch the server makes', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('says each pick as the bridge\'s /feed query', () => {
    expect(feedQuery({ kind: 'camera' })).toBe('source=camera');
    expect(feedQuery({ kind: 'footage', name: 'real/leaf epidermis.mp4' })).toBe('source=footage&footage=real%2Fleaf+epidermis.mp4');
    expect(feedQuery({ kind: 'footage', name: 'real/leaf.mp4', at: 42.53, paused: true })).toBe('source=footage&footage=real%2Fleaf.mp4&paused=1&at=42.5');
    expect(feedQuery({ kind: 'footage', name: 'real/leaf.mp4', at: 0, paused: false })).toBe('source=footage&footage=real%2Fleaf.mp4');
    expect(pickKey({ kind: 'footage', name: 'real/leaf.mp4' })).toBe('real/leaf.mp4');
    expect(pickKey({ kind: 'camera' })).toBeNull();
  });

  it('switches with one POST, shows what was picked at once, and the last pick asked for meanwhile goes next', async () => {
    const calls: string[] = [];
    let release: () => void = () => {};
    vi.stubGlobal('performance', { now: () => 0 });
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      calls.push(url);
      const state = url.includes('source=footage') ? { source: 'footage', footage: 'real/leaf.mp4' } : { source: 'camera', footage: null };
      return new Promise((resolve) => { release = () => resolve({ ok: true, json: async () => state }); });
    }));
    const feed = new CameraFeed(() => {});
    feed.bridgeUrl = 'http://bridge';
    feed.library = [item('real/leaf.mp4', 'Real microscope')];
    const first = feed.use('real/leaf.mp4', { at: 12, paused: true });
    expect(feed.choosing).toMatchObject({ name: 'real/leaf.mp4' });
    expect(calls).toEqual(['http://bridge/feed?source=footage&footage=real%2Fleaf.mp4&paused=1&at=12.0']);
    // Two more while it switches: only the last goes, after it.
    void feed.use('real/other.mp4');
    void feed.use(null);
    expect(calls).toHaveLength(1);
    release();
    expect(await first).toBeNull();
    await Promise.resolve();
    expect(calls).toEqual(['http://bridge/feed?source=footage&footage=real%2Fleaf.mp4&paused=1&at=12.0', 'http://bridge/feed?source=camera']);
    release();
  });

  it('a switch the server makes is shown by its title at once, then what plays is read again; a failure is said', async () => {
    const reads: string[] = [];
    vi.stubGlobal('performance', { now: () => 0 });
    vi.stubGlobal('AbortSignal', { timeout: () => undefined });
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      reads.push(url);
      return { ok: true, json: async () => ({ source: 'footage', footage: 'asset:/x/smear.png', footageTitle: 'Smear', playback: { seconds: 0, duration: 0, still: true } }) };
    }));
    const seen: (string | null)[] = [];
    const feed = new CameraFeed(() => seen.push(feed.busy));
    feed.bridgeUrl = 'http://bridge';
    let finish: () => void = () => {};
    const done = feed.viaServer({ title: 'Smear', seconds: null }, () => new Promise<void>((resolve) => { finish = resolve; }));
    expect(feed.busy).toBe('switching');
    expect(feed.choosing).toMatchObject({ title: 'Smear' });
    expect(feed.choosing?.name.startsWith('asset:')).toBe(true);
    finish();
    expect(await done).toBeNull();
    expect(feed.busy).toBeNull();
    expect(feed.choosing).toBeNull();
    expect(reads).toEqual(['http://bridge/feed']); // read again once it was there
    expect(feed.state?.playback?.still).toBe(true);
    expect(await feed.viaServer(null, async () => { throw new Error('there is no asset a999'); })).toBe('there is no asset a999');
    expect(feed.busy).toBeNull();
  });
});

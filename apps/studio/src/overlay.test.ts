// The drawing contract the server and the simulation harness share (AGENTS.md "Freshness and
// overlay contract", contract/overlay.json): outlines fade while the slide moves fast and are
// drawn only inside the eyepiece; a picture dims when frames stop arriving; the magnifier's fitted window and how
// close it goes (services/tests/test_contract.py checks the server against the same numbers);
// the result viewer shows the server's items.
import { describe, expect, test } from 'vitest';
import contract from '../../../contract/overlay.json';
import { fitWindow, fitWords, hitObject, isFresh, isVisible, maxLevel, overlayOpacity, scaleBar, sizeWords, statusWords, taughtWords } from './overlay';
import type { FrameHeader, ResearchSession, SceneObject } from './protocol';
import { outputOf } from './viewer';

const header = (fov: FrameHeader['fov'], dx = 0, dy = 0): FrameHeader => ({
  type: 'frame', id: 1, w: 1280, h: 720, dx, dy, epoch: 0, tracked: true, steady: true, speed: 0, t: 0, sent: 0, fov,
});

const outline = (x: number, y: number, track = 'tracking'): SceneObject => ({
  id: 'o1', kind: 'highlight', polygons: [[[x - 5, y - 5], [x + 5, y - 5], [x, y + 5]]], anchorFrame: 1, anchorEpoch: 0,
  anchorOffset: [0, 0], source: 'ai', label: '', labelKey: '', status: 'likely', confidence: null, note: '', seen: '',
  group: null, trial: null, parent: null, arm: null, track, revisedBy: null, pinned: null, createdAt: 0,
});

describe('overlay', () => {
  test('the contract holds the agreed numbers', () => {
    expect(contract.frameDimMs).toBe(1500);
    expect(contract.feedStaleS).toBe(3);
    expect([contract.fadeStartPx, contract.fadeEndPx, contract.speedWindow]).toEqual([4, 12, 10]);
    expect([contract.inViewRadius, contract.clickRadius]).toEqual([0.95, 0.97]);
    expect(contract.kindOrder).toEqual({ html: 0, video: 1, image: 2 });
  });

  test('outlines are solid when still, fade between 4 and 12 px/frame, and are gone above', () => {
    expect(overlayOpacity(0)).toBe(1);
    expect(overlayOpacity(4)).toBe(1);
    expect(overlayOpacity(8)).toBeCloseTo(0.5);
    expect(overlayOpacity(12)).toBe(0);
    expect(overlayOpacity(40)).toBe(0);
  });

  test('an outline is drawn only while its centre is inside 95% of the eyepiece', () => {
    const fov: FrameHeader['fov'] = [640, 360, 300];
    expect(isVisible(outline(640, 360), header(fov))).toBe(true);
    expect(isVisible(outline(640 + 280, 360), header(fov))).toBe(true);
    expect(isVisible(outline(640 + 290, 360), header(fov))).toBe(false);
    expect(isVisible(outline(640, 360), header(fov, 300, 0))).toBe(false); // the slide moved it out
    // A frame the motion tracker could not register (the first frames of a cut, before the epoch changes):
    // nothing on it is placed, so nothing is drawn (going live, G2: four such frames lasted ~2 s when frames slowed).
    expect(isVisible(outline(640, 360), { ...header(fov), tracked: false })).toBe(false);
    expect(isVisible(outline(640, 360, 'lost'), header(fov))).toBe(false);
    expect(isVisible(outline(5000, 5000), header(null))).toBe(true); // no eyepiece found: drawn
  });

  test('nothing unverified is drawn: an outline being re-found or from another epoch is placed nowhere', () => {
    const fov: FrameHeader['fov'] = [640, 360, 300];
    expect(isVisible(outline(640, 360, 'refinding'), header(fov))).toBe(false);
    expect(isVisible({ ...outline(640, 360), anchorEpoch: 3 }, header(fov))).toBe(false); // another epoch
    expect(isVisible({ ...outline(640, 360), kind: 'cell', anchorEpoch: 3 }, header(fov))).toBe(false); // cells too
  });

  test('a picture older than 1.5 s is dimmed (the server calls the feed stale only after 3 s)', () => {
    expect(isFresh(0)).toBe(true);
    expect(isFresh(1499)).toBe(true);
    expect(isFresh(1500)).toBe(false);
  });
});

describe('magnifier', () => {
  // The same cases as services/tests/test_contract.py (box in camera px -> window, closest level).
  const cases: [number, number, [number, number], number][] = [
    [60, 60, [104, 78], 19.5], // 78 x 78 padded, widened to 4:3; 78 / 4 px
    [100, 50, [130, 97.5], 24.375], // wide: made taller
    [10, 10, [128 / 3, 32], 8], // small: at least 32 px
    [1000, 800, [4160 / 3, 1040], 64], // large: at most 64x
  ];

  test.each(cases)('a %d x %d px object fits a window and goes so close', (w, h, fit, level) => {
    const [fw, fh] = fitWindow(w, h);
    expect(fw).toBeCloseTo(fit[0], 9);
    expect(fh).toBeCloseTo(fit[1], 9);
    expect(maxLevel([fw, fh])).toBeCloseTo(level, 9);
  });

  test('the fitted window takes the panel shape it is drawn in; with no object it goes 64x', () => {
    expect(fitWindow(60, 60, 1)).toEqual([78, 78]);
    expect(maxLevel(null)).toBe(64);
  });
});

describe('result viewer', () => {
  test("shows the server's items, each with its own output", () => {
    const session = {
      outputs: [
        { id: 'o1', kind: 'image', path: 'a.png', caption: '', url: '', version: 1, current: true },
        { id: 'o2', kind: 'html', path: 'l1.html', caption: '', url: '', version: 1, current: false },
        { id: 'o3', kind: 'html', path: 'l2.html', caption: '', url: '', version: 2, replaces: 'o2', current: true },
      ],
      items: [
        { kind: 'html', label: 'Lesson v2', id: 'o3' }, { kind: 'image', label: 'Picture', id: 'o1' },
        { kind: 'html', label: 'Lesson v1', id: 'o2' }, { kind: 'summary', label: 'What it found', id: null },
      ],
      result: { title: 'x' },
    } as unknown as ResearchSession;
    expect(session.items.map((it) => outputOf(session, it)?.path)).toEqual(['l2.html', 'a.png', 'l1.html', undefined]);
  });
});

describe('fitWords', () => {
  test('says the check doubts an outline, in its own reason or in plain words, and nothing otherwise', () => {
    expect(fitWords({ fit: { fits: 'too_small', why: 'covers only the cap' } })).toBe('may not fit: covers only the cap');
    expect(fitWords({ fit: { fits: 'too_big', why: '' } })).toBe('may not fit: it covers more than the thing');
    expect(fitWords({})).toBe('');
  });
});

describe('statusWords', () => {
  test('says how sure a name is in words, never a percentage', () => {
    expect(statusWords('unsure')).toBe('Not sure');
    expect(statusWords('unsure', ['neutrophil', 'band cell'])).toBe('Not sure: maybe neutrophil or band cell');
    expect(statusWords('likely')).toBe('Probably');
    expect(statusWords('confirmed')).toBe('Confirmed');
    expect(statusWords('outline')).toBe('');
  });
});

describe('scale', () => {
  test('a scale bar is a round length close to the wanted width', () => {
    expect(scaleBar(null, 1)).toBeNull();
    expect(scaleBar(0.69, 0.5)?.um).toBe(100); // 40x on a 650 px disc shown at half size: 1.38 µm a screen px
    const bar = scaleBar(1.73, 1)!; // 10x
    expect([100, 200]).toContain(bar.um);
  });

  test('sizes read as a person would say them, ~ for an estimate', () => {
    expect(sizeWords({ lengthUm: 12.4, widthUm: 11.2, measured: false })).toBe('~12 µm');
    expect(sizeWords({ lengthUm: 152, widthUm: 31, measured: true })).toBe('150 × 31 µm');
    expect(sizeWords(null)).toBe('');
  });
});

describe('the labels the user taught, apart from the model name', () => {
  const base = outline(0, 0);
  test('shows the closest taught label and how alike, to two places', () => {
    expect(taughtWords({ ...base, taught: { label: 'guard cell', similarity: 0.8234, examples: 4 } })).toBe('your label: guard cell (0.82)');
  });
  test('nothing without a match, or when the user named it themselves', () => {
    expect(taughtWords(base)).toBe('');
    expect(taughtWords({ ...base, taught: null })).toBe('');
    expect(taughtWords({ ...base, status: 'confirmed', taught: { label: 'guard cell', similarity: 0.9, examples: 3 } })).toBe('');
  });

  test('a click lands only on an outline that is drawn', () => {
    // Refinding and other-epoch outlines are hidden (isVisible), so they must not catch the click:
    // it would open a hidden outline's card instead of outlining what was clicked (scaffolding review, M1.1 item 2).
    const h = header([640, 360, 300]);
    const shown = outline(640, 360);
    expect(hitObject([shown], h, [640, 360], 1)?.id).toBe('o1');
    expect(hitObject([outline(640, 360, 'refinding')], h, [640, 360], 1)).toBeNull();
    expect(hitObject([{ ...shown, anchorEpoch: 1 }], h, [640, 360], 1)).toBeNull();
    expect(hitObject([{ ...shown, kind: 'cell' }], h, [640, 360], 1)).toBeNull();
    // Drawn where the slide's move puts it, and hit there.
    expect(hitObject([shown], header([640, 360, 300], 30, 0), [670, 360], 1)?.id).toBe('o1');
  });

  test('a click inside a dissected object is the object, never one of its parts (live 2026-10-11)', () => {
    const h = header([640, 360, 300]);
    const stoma = outline(640, 360);
    const pore = { ...outline(640, 360), id: 'p2', kind: 'part', parent: 'o1' };
    expect(hitObject([stoma, pore], h, [640, 360], 1)?.id).toBe('o1'); // the part is newer, and never caught
  });
});

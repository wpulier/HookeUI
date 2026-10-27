// Kinds of media (kind (f) of the end of the effort; orchestrator/media.py): each picture says what it is, from
// the server's record, never guessed; and a picture beside the live close-up (live case 7) makes the magnifier
// two pictures wide, each its usual shape, still never over its object.
import { describe, expect, test } from 'vitest';
import { besideOf, kindLine, keepsPictures } from './magnifier';
import { panelWidth, PICTURE_ASPECT, placeAway, type Box, type Placing } from './panels';
import type { MagnifierSource, MagnifierState, ResultItem } from './protocol';
import { kindNote } from './viewer';

const photo: MagnifierSource = {
  kind: 'image', space: 'picture', label: 'Reference photo: Stoma in a leaf (Wikimedia Commons)', url: '/api/picture/abc',
  credit: 'A. Botanist, CC BY-SA 4.0', media: 'reference', line: 'A reference photo from the web, not this slide · A. Botanist, CC BY-SA 4.0',
};
const state = (index: number, beside: MagnifierSource | null = photo): MagnifierState => ({
  type: 'magnifier', space: 'frame', open: true, objectId: 'o1', index, beside,
  sources: [{ kind: 'live', space: 'frame', label: 'Live', media: 'capture' }, { kind: 'held', space: 'capture', label: 'Sharpest frame', url: '/api/crop/o1' }, photo],
});

describe('what kind of picture it is', () => {
  test('a picture says the server\'s line; one with none is never called real', () => {
    expect(kindLine(photo)).toBe(photo.line);
    expect(kindLine({ kind: 'image', space: 'picture', label: 'A real close-up', url: '/x.png' })).toBe('A picture; how it was made is not recorded');
    expect(kindLine({ kind: 'live', space: 'frame', label: 'Live' })).toBe(''); // the live close-up says "Live" itself
  });

  test('the result viewer says it above pictures, videos and reference photos', () => {
    const item = (kind: string, line?: string): ResultItem => ({ kind, label: '', id: null, line });
    expect(kindNote(item('image', 'AI illustration, not microscope footage'))).toBe('AI illustration, not microscope footage');
    expect(kindNote(item('reference', 'A reference photo from the web, not this slide'))).toContain('not this slide');
    expect(kindNote(item('image'))).toBe('A picture; how it was made is not recorded');
    expect(kindNote(item('summary', 'What the researcher found, in its words'))).toBe('');
    expect(kindNote(undefined)).toBe('');
  });
});

describe('a picture beside the close-up', () => {
  test('also renders a reference beside a different saved specimen picture', () => {
    const saved: MagnifierSource = { kind: 'image', space: 'picture', label: 'saved c4', url: '/api/picture/specimen', media: 'capture' };
    const pair = { ...state(3), sources: [...state(0).sources, saved] };
    expect(besideOf(pair)).toBe(photo);
    expect(besideOf({ ...pair, beside: saved })).toBeNull();
  });
  test('is shown only while the close-up is what it shows', () => {
    expect(besideOf(state(0))).toBe(photo);
    expect(besideOf(state(1))).toBe(photo); // the sharpest frame, beside it too
    expect(besideOf(state(2))).toBeNull(); // the photo itself, alone
    expect(besideOf(state(0, null))).toBeNull();
    expect(besideOf({ ...state(0), open: false })).toBeNull();
  });

  test('two pictures wide, as tall as one, never over its object', () => {
    const area = { width: 1600, height: 900 };
    const object: Box = [760, 430, 800, 470];
    const base: Placing = { area, disc: [800, 450, 400], object, obstacles: [], size: { width: panelWidth(area), chrome: 28 } };
    const one = placeAway(base);
    const two = placeAway({ ...base, size: { ...base.size, width: base.size.width * 2, aspect: PICTURE_ASPECT * 2 } });
    const tall = (w: number, aspect: number) => w / aspect + 28;
    // Each pane keeps the usual shape: the pair is as tall as one picture of half its width.
    expect(tall(two.w, PICTURE_ASPECT * 2)).toBeCloseTo(tall(two.w / 2, PICTURE_ASPECT), 6);
    expect(two.w).toBeGreaterThan(one.w);
    const box: Box = [two.x, two.y, two.x + two.w, two.y + tall(two.w, PICTURE_ASPECT * 2)];
    expect(box[0] < object[2] && object[0] < box[2] && box[1] < object[3] && object[1] < box[3]).toBe(false);
    // Without an aspect nothing changes: the same spot as before the parameter existed.
    expect(placeAway({ ...base, size: { ...base.size } })).toEqual(one);
  });
});


describe('a lost live view with retained evidence', () => {
  test('keeps the comparison and hidden pictures available across tracking loss', () => {
    expect(keepsPictures(state(0))).toBe(true);
    expect(keepsPictures(state(0, null))).toBe(true);
    expect(keepsPictures({ ...state(0), sources: state(0).sources.slice(0, 2), beside: null })).toBe(false);
    expect(keepsPictures({ ...state(0), open: false })).toBe(false);
  });
});

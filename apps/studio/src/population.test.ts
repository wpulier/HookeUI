// The population layer's rules (population.ts): only verified members of the shown population, only
// in its epoch, moved with the slide; colours from ids; a member's words in px or µm by its scale.
import { describe, expect, test } from 'vitest';
import contract from '../../../contract/overlay.json';
import { categoryColour, categoryWords, colourChoice, colourOf, drawable, heat, keepPopulation, memberAt, memberLook, memberWords, PALETTE, type LayerWire, type PopulationWire } from './population';
import type { FrameHeader } from './protocol';

const header = (dx = 0, epoch = 0): FrameHeader => ({
  type: 'frame', id: 9, w: 1280, h: 720, dx, dy: 0, epoch, tracked: true, steady: true, speed: 0, t: 0, sent: 0, fov: null,
});

const square = (x: number): [number, number][][] => [[[x, 100], [x + 20, 100], [x + 20, 120], [x, 120]]];

const pop: PopulationWire = {
  type: 'population', space: 'capture', id: 'e3', operation: 'cells', madeBy: {}, capture: 5, epoch: 0, offset: [10, 0], at: 0, revision: 0,
  status: 'saved', count: 2, coverage: 0.1, basis: { unit: 'px', umPerPx: null, scale: null, objective: null },
  members: [
    { id: 'c1', polygons: square(100), colour: 3, placement: 'verified', measure: { areaPx: 400, elongation: 1 } },
    { id: 'c2', polygons: square(200), colour: 14, placement: 'unverified', measure: { areaPx: 400 } },
  ],
};
const layer: LayerWire = { type: 'layer', space: 'frame', population: 'e3', style: { opacity: 1, colour: 'id' }, verified: ['c1'] };

describe('population layer', () => {
  test('only verified members of the shown population are drawn, moved with the slide', () => {
    const { members, shift } = drawable(pop, layer, header(30));
    expect(members.map((m) => m.id)).toEqual(['c1']);
    expect(shift).toEqual([20, 0]);
  });

  test('the members\' own correction moves them with the slide', () => {
    expect(drawable(pop, { ...layer, correction: [2, -3] }, header(30)).shift).toEqual([22, -3]);
  });

  test('a rejoin carries the population to the epoch rejoined', () => {
    const carried = { ...layer, view: { epoch: 2, offset: [40, 0] as [number, number] } };
    expect(drawable(pop, carried, header(50, 2)).shift).toEqual([10, 0]);
    expect(drawable(pop, carried, header(50, 0)).members).toEqual([]);
  });

  test('nothing is drawn in another epoch or when another population is shown', () => {
    expect(drawable(pop, layer, header(30, 1)).members).toEqual([]);
    // A frame the tracker could not register (the first frames of a cut): no member is drawn or clicked (going live, G2).
    expect(drawable(pop, layer, { ...header(30), tracked: false }).members).toEqual([]);
    expect(drawable(pop, { ...layer, population: 'e4' }, header()).members).toEqual([]);
    expect(drawable(pop, { ...layer, population: null }, header()).members).toEqual([]);
  });

  test('a newly shown population is kept whether its wire comes before or after the layer', () => {
    let kept = keepPopulation(new Map(), pop);
    kept = keepPopulation(kept, { ...pop, id: 'e4' }); // e4's wire while e3 is still the layer's
    expect(drawable(kept.get('e4') ?? null, { ...layer, population: 'e4' }, header(30)).members.map((m) => m.id)).toEqual(['c1']);
    for (const id of ['e5', 'e6', 'e7']) kept = keepPopulation(kept, { ...pop, id });
    expect([...kept.keys()]).toEqual(['e4', 'e5', 'e6', 'e7']); // the newest few only
  });

  test('a member is drawn by the layer\'s style: its own colour, one colour, or by a measurement', () => {
    expect(memberLook(pop.members[0], layer.style).colour).toBe(PALETTE[3]);
    expect(memberLook(pop.members[0], { opacity: 1, colour: 'uniform', value: '#ffdc64' }).colour).toBe('#ffdc64');
    const bySize = { opacity: 1, colour: 'column', column: 'areaPx', values: { c1: 1, c2: 0 } };
    expect(memberLook(pop.members[0], bySize).colour).toBe(heat(1));
    expect(memberLook(pop.members[1], bySize).colour).toBe(heat(0));
    expect(heat(0)).toBe('rgb(59, 130, 246)');
  });

  test('by look group, each group has its palette colour, as its legend shows it (K1)', () => {
    const byGroup = { opacity: 1, colour: 'category', column: 'group', values: { c1: 0, c2: 1 },
      legend: { label: 'look groups', categories: [{ id: 'g1', n: 1 }, { id: 'g2', n: 1 }] } };
    expect(memberLook(pop.members[0], byGroup).colour).toBe(categoryColour(0));
    expect(memberLook(pop.members[1], byGroup).colour).toBe(PALETTE[1]);
    expect(memberLook({ ...pop.members[0], id: 'c9' }, byGroup).colour).toBe('#8a93a6'); // not grouped: grey
  });

  test('the colour menu names a look group and your nearest named group, and nothing it lacks (K1 re-check)', () => {
    expect(colourChoice({ opacity: 1, colour: 'category', column: 'group' })).toBe('look groups');
    expect(colourChoice({ opacity: 1, colour: 'category', column: 'yourLabel' })).toBe('your label');
    expect(colourChoice({ opacity: 1, colour: 'column', column: 'meanRed', legend: { label: 'meanRed' } })).toBe('meanRed');
    expect(colourChoice({ opacity: 1, colour: 'column', column: 'areaPx', legend: { label: 'size' } })).toBe('size');
    expect(colourChoice({ opacity: 1, colour: 'id' })).toBe('id');
  });

  test('a legend says a look group by its number, and your label by its own words (K1 review SF3)', () => {
    expect(categoryWords({ id: 'g2', n: 14 })).toBe('group 2 (14)');
    expect(categoryWords({ id: 'platelets', n: 9, label: 'platelets' })).toBe('platelets (9)');
  });

  test('an emphasised selection keeps the rest faint, or hidden', () => {
    const fade = { opacity: 1, colour: 'id', emphasis: { members: ['c1'], rest: 'fade' as const } };
    expect(memberLook(pop.members[0], fade)).toMatchObject({ alpha: 1, hidden: false });
    expect(memberLook(pop.members[1], fade)).toMatchObject({ alpha: 0.2, hidden: false });
    expect(memberLook(pop.members[1], { ...fade, emphasis: { members: ['c1'], rest: 'hide' as const } }).hidden).toBe(true);
  });

  test('a click lands on a drawn member only', () => {
    expect(memberAt(pop, layer, header(30), [130, 110])?.id).toBe('c1'); // 110 + the 20 px move
    expect(memberAt(pop, layer, header(30), [230, 110])).toBeNull(); // c2 is not verified: not drawn
  });

  // The same cases as services/tests/test_going_live.py (orchestrator/colours.py): the server names the colour each
  // member is drawn in, so the two must draw alike (going live, G3).
  test('the palette, the ramp and the grey are the contract\'s, and the server draws the same cases', () => {
    expect(PALETTE).toEqual(contract.memberColours.palette);
    const ramp: [number, string][] = [[0, 'rgb(59, 130, 246)'], [0.25, 'rgb(30, 171, 186)'], [0.5, 'rgb(135, 194, 94)'],
      [2.5 / 3, 'rgb(245, 136, 45)'], [1, 'rgb(239, 68, 68)'], [1.5, 'rgb(239, 68, 68)'], [-1, 'rgb(59, 130, 246)']];
    for (const [t, rgb] of ramp) expect(heat(t)).toBe(rgb);
    const c6 = { id: 'c6', polygons: square(100), colour: 3, placement: 'verified', measure: null };
    expect(memberLook(c6, { opacity: 1, colour: 'id' }).colour).toBe('#e74c3c');
    expect(memberLook(c6, { opacity: 1, colour: 'uniform', value: '#ffdc64' }).colour).toBe('#ffdc64');
    expect(memberLook(c6, { opacity: 1, colour: 'column', column: 'areaPx', values: { c6: 0.25 } }).colour).toBe('rgb(30, 171, 186)');
    expect(memberLook(c6, { opacity: 1, colour: 'column', column: 'areaPx', values: {} }).colour).toBe('#8a93a6');
    expect(memberLook(c6, { opacity: 1, colour: 'category', column: 'group', values: { c6: 2 } }).colour).toBe('#38c3f7');
    expect(memberLook(c6, { opacity: 1, colour: 'category', column: 'group', values: { c6: 14 } }).colour).toBe('#38c3f7');
    expect(memberLook(c6, { opacity: 1, colour: 'category', column: 'group', values: {} }).colour).toBe('#8a93a6');
    expect(memberLook(c6, { opacity: 1, colour: 'id', emphasis: { members: [], rest: 'fade' } })).toMatchObject({ colour: '#e74c3c', hidden: false });
    expect(memberLook(c6, { opacity: 1, colour: 'id', emphasis: { members: [], rest: 'hide' } }).hidden).toBe(true);
  });

  test('colours come from the id, from one palette', () => {
    expect(colourOf(pop.members[0])).toBe(PALETTE[3]);
    expect(colourOf(pop.members[1])).toBe(PALETTE[2]);
  });

  test('sizes are in px without a scale and in µm with one', () => {
    expect(memberWords(pop, pop.members[0])).toBe('c1 of e3: 400 px², elongation 1.00');
    const scaled = { ...pop, basis: { unit: 'px', umPerPx: 0.5, scale: 'estimated', objective: '40' } };
    expect(memberWords(scaled, pop.members[0])).toBe('c1 of e3: 100.0 µm², elongation 1.00 (estimated scale)');
  });
});

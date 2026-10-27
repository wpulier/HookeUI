import { describe, expect, test } from 'vitest';
import { cornerMode, cornerWidth } from './layout-rules';
import { creator, matchesFilter, resourceTarget, type ResourceEntry } from './madetoday';
import { ChatModel } from './chatmodel';
import { latestRequestActions, retainedResults } from './chatlog';

describe("the Stage's corners", () => {
  // The researchers' chips once stacked here beside the eyepiece, as many as stayed clear of the disc; they are the
  // Working strip's pills now, under the Stage (working-strip.ts), so work coming and going never touches this layout.
  test('flow on a narrow window, bands only when controls have too little room, corners otherwise', () => {
    expect(cornerMode(960, 400, true)).toBe('flow');
    expect(cornerMode(1440, 120, true)).toBe('bands');
    expect(cornerMode(1440, 120, false)).toBe('corners');
    expect(cornerMode(1440, 300, true)).toBe('corners');
  });
  test("a corner's width stays between a pill's least and most", () => {
    expect(cornerWidth(40)).toBe(150);
    expect(cornerWidth(260.4)).toBe(260);
    expect(cornerWidth(900)).toBe(352);
  });
});

test('Yours never infers authorship from a title, model, or an absent producer', () => {
  const entry: ResourceEntry = { id: 'd1', kind: 'result', title: 'Your photo', summary: '', state: 'done', actions: [] };
  expect(matchesFilter(entry, 'Yours')).toBe(false);
  expect(creator(entry)).toBe(null);
  expect(matchesFilter({ ...entry, madeBy: { implementation: 'Model' } }, 'Yours')).toBe(false);
  expect(matchesFilter({ ...entry, madeBy: { for: 'Researcher 1' } }, 'Yours')).toBe(false);
  expect(matchesFilter({ ...entry, madeBy: { requestedBy: 'user' } }, 'Yours')).toBe(true);
});

test('replay uses the recorded timestamp and leaves old times unknown', () => {
  const model = new ChatModel(() => false, () => 999999);
  model.apply({ type: 'chat', role: 'assistant', text: 'Saved', at: 123.456 });
  model.apply({ type: 'chat', role: 'assistant', text: 'Historical' });
  expect(model.lines.map(line => line.at)).toEqual([123.456, undefined]);
});

test('written research with pictures reopens the exact report, not its first picture', () => {
  const entry: ResourceEntry = { day: '2026-10-10', id: 'r1', version: 3, kind: 'research', title: 'Study', summary: '', state: 'done', actions: ['open'], pictures: ['/api/example'] };
  expect(resourceTarget(entry)).toEqual({day: '2026-10-10', id: 'r1', version: 3, picture: null});
  expect(resourceTarget({...entry, kind: 'capture'}).picture).toBe(0);
});

test('standalone terminal receipts settle the same request without a published answer', () => {
  const model = new ChatModel(() => false);
  model.apply({type:'chat', role:'user', text:'Find a photo', turnId:7});
  model.apply({type:'chat-action', turnId:7, standalone:true, tool:'reference_photo', ok:false, summary:'Source unavailable', ms:10});
  expect(latestRequestActions(model.lines).map(a => a.ok)).toEqual([false]);
  model.apply({type:'chat', role:'user', text:'A new question', turnId:8});
  expect(latestRequestActions(model.lines)).toEqual([]);
});
test('cards require exact retained addresses and never promote pending or failed results', () => {
  const resource = {day:'2026-10-10',id:'d2',version:null,picture:0,title:'Guard cells'};
  const action = {tool:'reference_photo',ok:true,summary:'Photo displayed',ms:1,resource};
  expect(retainedResults([action,action])).toEqual([resource]);
  expect(retainedResults([{...action,ok:false},{...action,ok:null}])).toEqual([]);
});

test("the Stage's corners are decided once when the side columns land, never on each in-between size (b041)", async () => {
  const { vi } = await import('vitest');
  const { StageLayout } = await import('./stage-layout');
  const { expectSideMotion, playSideMotion } = await import('./side-motion');
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal('ResizeObserver', class { observe() {} });
  vi.stubGlobal('MutationObserver', class { observe() {} });
  vi.stubGlobal('window', { addEventListener() {} });
  const part = {};
  const stage = { querySelector: () => part } as unknown as HTMLElement;
  try {
    const layout = new StageLayout(stage, {} as never);
    expectSideMotion(); // a press's motion is starting: the Stage resizes on every frame of it
    for (let i = 0; i < 12; i++) layout.schedule();
    expect(frames).toHaveLength(0);
    playSideMotion([{ node: {} as HTMLElement, props: { width: [1008, 668] }, closing: false }], () => true); // lands (no Web Animations here: at once)
    expect(frames).toHaveLength(1);
  } finally { vi.unstubAllGlobals(); }
});

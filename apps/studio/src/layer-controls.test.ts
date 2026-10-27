// The cells' control over synthetic DOM: its × and closing itself once its population is gone (the user's notes of
// 2026-10-11: the control stayed after the cells it was for had gone, with no way to close it).
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { bindLayerControls } from './layer-controls';
import { fire, TestElement } from './workspace-windows.test-support';

let nodes: Map<string, TestElement>;
beforeEach(() => {
  nodes = new Map(['layer-controls', 'layer-visible', 'layer-opacity', 'layer-colour', 'layer-fill'].map(id => [id, new TestElement(id === 'layer-colour' ? 'select' : 'div')]));
  (nodes.get('layer-colour') as unknown as { options: unknown[] }).options = [];
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag), getElementById: (id: string) => nodes.get(id), activeElement: null });
});
afterEach(() => vi.unstubAllGlobals());

function bound(correct?: Parameters<typeof bindLayerControls>[2], openTable?: Parameters<typeof bindLayerControls>[3],
  openPopulation?: (id: string, pin: {session:string;revision:number}) => void) {
  const sent: unknown[] = [];
  const view = { layer: { population: 'e3', verified: [], style: { opacity: 1 } }, population: { count: 12 }, tracksLayer: null,
    shownTracks: null, externalLegends: false } as never as Parameters<typeof bindLayerControls>[0];
  const controls = bindLayerControls(view, (command) => sent.push(command), correct, openTable, undefined, openPopulation);
  return { view: view as unknown as { layer: unknown; population: unknown }, sent, controls };
}

test('its × hides the cells and closes it, until cells are shown again', () => {
  const { view, sent, controls } = bound();
  controls.syncCells();
  const panel = nodes.get('layer-controls')!;
  expect(panel.hidden).toBe(false);
  fire(panel.querySelector('.layer-close') as TestElement, 'click');
  expect(sent).toEqual([{ type: 'present', what: 'layer:e3', do: 'hide' }]);
  expect(panel.hidden).toBe(true);
  view.layer = { population: null, verified: [], style: {} };
  controls.syncCells();
  expect(panel.hidden).toBe(true); // stays closed while the cells are hidden
  view.layer = { population: 'e3', verified: [], style: { opacity: 1 } };
  controls.syncCells();
  expect(panel.hidden).toBe(false); // "show the cells": back with them
});

test('shown outlines that none of are drawn on the Stage say so in the card, and why', () => {
  const { view, controls } = bound();
  const card = () => nodes.get('layer-controls')!.querySelector('.layer-name')!;
  view.layer = { population: 'e3', verified: [], style: { opacity: 1 },
    why: 'kept on the original image; open the saved result to inspect its particles' };
  controls.syncCells();
  expect(card().querySelector('.layer-sub')!.textContent).toBe('12'); // the count stays
  expect(card().querySelector('.layer-why')!.textContent).toBe('Kept on the original image; open the saved result to inspect its particles.');
  expect(card().title).toBe('Kept on the original image; open the saved result to inspect its particles.');
  view.layer = { population: 'e3', verified: [], style: { opacity: 1 }, why: 'the picture on the Stage is changing' };
  controls.syncCells();
  expect(card().querySelector('.layer-why')!.textContent).toBe('Not drawn on the Stage: the picture on the Stage is changing.');
  view.layer = { population: 'e3', verified: ['c1'], style: { opacity: 1 }, why: '' };
  controls.syncCells();
  expect(card().querySelector('.layer-why')).toBeNull(); // drawn: nothing to explain
  expect(card().title).toBe('1 of 12 verified in place');
  view.layer = { population: 'e3', verified: [], style: { opacity: 1 } };
  controls.syncCells();
  expect(card().querySelector('.layer-why')).toBeNull(); // no reason given (its check not yet made): no claim
});

test('it closes itself once its population is gone', () => {
  const { view, controls } = bound();
  controls.syncCells();
  view.layer = null; view.population = null;
  controls.syncCells();
  expect(nodes.get('layer-controls')!.hidden).toBe(true);
});

test('retained members can be removed and undone with their displayed session and revision', async () => {
  const corrections: unknown[] = [];
  const tables: unknown[] = [];
  const { view, sent, controls } = bound(async command => { corrections.push(command); return { ok: true }; }, (id, pin) => tables.push({id, pin}));
  view.population = { id: 'e8', session: '2026-10-13', revision: 0, status: 'saved', count: 3, originalCount: 3,
    canUndo: false, members: [{ id: 'c7', measure: { areaPx: 317 } }, { id: 'c8', measure: { areaPx: 149 } },
      { id: 'c9', measure: { areaPx: 208 } }] };
  view.layer = { population: 'e8', verified: [], style: { opacity: 1 } };
  controls.syncCells();
  const panel = nodes.get('layer-controls')!;
  fire(panel.querySelector('.layer-table')!, 'click');
  expect(tables).toEqual([{id:'e8', pin:{session:'2026-10-13',revision:0}}]);
  expect(corrections).toEqual([]);
  const selector = panel.querySelector('.member-select')!;
  expect(selector).not.toBeNull();
  selector.value = 'c9'; fire(selector, 'change');
  const remove = panel.querySelector('.member-remove')!;
  expect(remove.disabled).toBe(false);
  fire(remove, 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(corrections).toEqual([{ type: 'population-correct', session: '2026-10-13', population: 'e8', revision: 0,
    change: 'remove', member: 'c9' }]);
  view.population = { id: 'e8', session: '2026-10-13', revision: 1, status: 'saved', count: 2, originalCount: 3,
    canUndo: true, members: [{ id: 'c7', measure: {} }, { id: 'c8', measure: {} }] };
  controls.syncCells();
  expect(selector.value).toBe('');
  fire(panel.querySelector('.member-undo')!, 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(corrections.at(-1)).toEqual({ type: 'population-correct', session: '2026-10-13', population: 'e8', revision: 1, change: 'undo' });
  fire(panel.querySelector('.population-original')!, 'click');
  expect(sent.at(-1)).toEqual({ type: 'present', what: 'e8', do: 'open', revision: 0 });
});

test.each([
  {name: 'a newer revision', replacement: {revision: 4}},
  {name: 'another Project with the same local ID', replacement: {session: '2026-10-14'}},
])('Table refuses $name arriving before the displayed controls refresh', ({replacement}) => {
  const tables: unknown[] = [];
  const {view, controls} = bound(undefined, (id, pin) => tables.push({id, pin}));
  const original = {id:'e8',session:'2026-10-13',revision:3,status:'saved',count:0,members:[]};
  view.population=original;
  view.layer={population:'e8',verified:[],style:{opacity:1}};
  controls.syncCells();
  view.population={...original,...replacement};
  fire(nodes.get('layer-controls')!.querySelector('.layer-table')!, 'click');
  expect(tables).toEqual([]);
  controls.syncCells();
  fire(nodes.get('layer-controls')!.querySelector('.layer-table')!, 'click');
  expect(tables).toEqual([{id:'e8',pin:{session:replacement.session??original.session,revision:replacement.revision??original.revision}}]);
});

test('Table keeps an explicitly shown historical revision and refuses a different selected layer', () => {
  const tables: unknown[]=[];
  const {view,controls}=bound(undefined,(id,pin)=>tables.push({id,pin}));
  view.population={id:'e8',session:'2026-10-13',revision:0,status:'saved',count:0,members:[]};
  view.layer={population:'e8',verified:[],style:{opacity:1}};
  controls.syncCells();
  const table=nodes.get('layer-controls')!.querySelector('.layer-table')!;
  fire(table,'click');
  expect(tables).toEqual([{id:'e8',pin:{session:'2026-10-13',revision:0}}]);
  view.layer={population:'e9',verified:[],style:{opacity:1}};
  fire(table,'click');
  controls.syncCells();
  expect(table.disabled).toBe(true);
  fire(table,'click');
  expect(tables).toHaveLength(1);
});

test.each([
  {status:'unavailable',session:'2026-10-13',revision:0},
  {status:'saved',session:undefined,revision:0},
  {status:'saved',session:'2026-10-13',revision:-1},
  {status:'saved',session:'2026-10-13',revision:0.5},
])('Table refuses an incomplete or unavailable retained identity: %j', facts=>{
  const opened=vi.fn(),{view,controls}=bound(undefined,opened);
  view.population={id:'e8',count:0,members:[],...facts};
  view.layer={population:'e8',verified:[],style:{opacity:1}};
  controls.syncCells();
  const table=nodes.get('layer-controls')!.querySelector('.layer-table')!;
  expect(table.disabled).toBe(true);
  fire(table,'click');
  expect(opened).not.toHaveBeenCalled();
});


test('Original and Current open their exact saved population through the workspace owner', () => {
  const opened=vi.fn(),{view,controls,sent}=bound(undefined,undefined,opened);
  view.population={id:'e8',session:'2026-10-13',revision:3,status:'saved',count:2,originalCount:3,members:[]};
  view.layer={population:'e8',verified:[],style:{opacity:1}};
  controls.syncCells();
  const panel=nodes.get('layer-controls')!;
  fire(panel.querySelector('.population-original')!,'click');
  fire(panel.querySelector('.population-current')!,'click');
  expect(opened.mock.calls).toEqual([['e8',{session:'2026-10-13',revision:0}],['e8',{session:'2026-10-13',revision:3}]]);
  expect(sent).toEqual([]);
});

test.each(['revision','session','layer'])('Population history refuses a changed %s before controls refresh', change => {
  const opened=vi.fn(),{view,controls,sent}=bound(undefined,undefined,opened);
  const original={id:'e8',session:'2026-10-13',revision:3,status:'saved',count:2,originalCount:3,members:[]};
  view.population=original;view.layer={population:'e8',verified:[],style:{opacity:1}};controls.syncCells();
  if(change==='revision')view.population={...original,revision:4};
  if(change==='session')view.population={...original,session:'2026-10-14'};
  if(change==='layer')view.layer={population:'e9',verified:[],style:{opacity:1}};
  fire(nodes.get('layer-controls')!.querySelector('.population-original')!,'click');
  fire(nodes.get('layer-controls')!.querySelector('.population-current')!,'click');
  expect(opened).not.toHaveBeenCalled();expect(sent).toEqual([]);
});

// Run again adds a layer (Will's call, 2026-10-25): the earlier outlines stay listed under the card, each shown again
// with one click; the layer shows one population at a time.
test('Run again adds a layer under the card: the earlier ones stay listed, newest first, each with Show', () => {
  const sent: unknown[] = [];
  const pop = (id: string, count: number, model: string, session = 'p1') =>
    ({ id, session, title: `Cell outlines · ${id}`, count, revision: 0, status: 'saved', members: [], madeBy: { model } });
  const view = { layer: { population: 'e5', verified: [], style: { opacity: 1 } }, population: pop('e5', 15, 'Cellpose-SAM'),
    tracksLayer: null, shownTracks: null, externalLegends: false } as never as Parameters<typeof bindLayerControls>[0];
  const controls = bindLayerControls(view, (command) => sent.push(command), undefined, undefined,
    (madeBy) => String((madeBy as { model?: string } | undefined)?.model ?? ''));
  const v = view as unknown as { layer: { population: string | null }; population: unknown };
  const panel = nodes.get('layer-controls')!;
  const earlier = () => panel.querySelector('.layer-earlier')!;
  const rows = () => earlier().querySelectorAll('.layer-earlier-row').map((row) => row.querySelector('.layer-sub')!.textContent);
  controls.syncCells();
  expect(earlier().hidden).toBe(true); // one layer: nothing earlier
  v.layer = { ...v.layer, population: 'e6' }; v.population = pop('e6', 12, 'micro-sam');
  controls.syncCells();
  expect(earlier().hidden).toBe(false);
  expect(rows()).toEqual(['Outlines · 15 by Cellpose-SAM']);
  v.layer = { ...v.layer, population: 'e7' }; v.population = pop('e7', 9, 'Bright objects');
  controls.syncCells();
  expect(rows()).toEqual(['Outlines · 12 by micro-sam', 'Outlines · 15 by Cellpose-SAM']);
  const show = earlier().querySelectorAll('.layer-earlier-show')[1];
  expect(show.getAttribute('aria-label')).toBe('Show Outlines · 15 by Cellpose-SAM');
  fire(show, 'click');
  expect(sent.at(-1)).toEqual({ type: 'present', what: 'e5', do: 'show' });
  // Shown again, it is the card's own; the one shown before it joins the list. Hidden, the list stays.
  v.layer = { ...v.layer, population: 'e5' }; v.population = pop('e5', 15, 'Cellpose-SAM');
  controls.syncCells();
  expect(rows()).toEqual(['Outlines · 9 by Bright objects', 'Outlines · 12 by micro-sam']);
  v.layer = { ...v.layer, population: null };
  controls.syncCells();
  expect(rows()).toEqual(['Outlines · 9 by Bright objects', 'Outlines · 12 by micro-sam']);
  // Another Project's layer: only its own earlier layers are listed (none yet).
  v.layer = { ...v.layer, population: 'e1' }; v.population = pop('e1', 4, 'Cellpose-SAM', 'p2');
  controls.syncCells();
  expect(earlier().hidden).toBe(true);
});

test('the earlier layers are each one row, newest first, the newest few', async () => {
  const { rememberLayer, earlierWords, EARLIER_MOST } = await import('./layer-controls');
  let list = rememberLayer([], { id: 'e1', session: 'p', count: 3 }, 'A');
  list = rememberLayer(list, { id: 'e2', session: 'p', count: 4 }, 'B');
  expect(rememberLayer(list, { id: 'e2', session: 'p', count: 4 }, 'B')).toBe(list); // unchanged: the same list
  list = rememberLayer(list, { id: 'e1', session: 'p', count: 2 }, 'A'); // corrected and shown again: moved, renewed
  expect(list.map((r) => [r.id, r.count])).toEqual([['e1', 2], ['e2', 4]]);
  for (const id of ['e3', 'e4', 'e5', 'e6']) list = rememberLayer(list, { id, session: 'p', count: 1 }, '');
  expect(list.map((r) => r.id)).toEqual(['e6', 'e5', 'e4', 'e3']);
  expect(list).toHaveLength(EARLIER_MOST + 1); // the shown one and the few under it
  expect(earlierWords(list[0])).toBe('Outlines · 1');
});

test('the earlier layers are kept per Project in this browser: a reload lists them again; storage is optional', () => {
  const kept = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => { kept.set(k, v); } });
  const pop = (id: string, count: number, model: string, session = 'p1') =>
    ({ id, session, title: `Cell outlines · ${id}`, count, revision: 0, status: 'saved', members: [], madeBy: { model } });
  const bind = (population: ReturnType<typeof pop>) => {
    const view = { layer: { population: population.id, verified: [], style: { opacity: 1 } }, population, tracksLayer: null,
      shownTracks: null, externalLegends: false } as never as Parameters<typeof bindLayerControls>[0];
    const controls = bindLayerControls(view, () => {}, undefined, undefined, (madeBy) => String((madeBy as { model?: string } | undefined)?.model ?? ''));
    return { controls, v: view as unknown as { layer: { population: string | null }; population: unknown } };
  };
  const rows = () => nodes.get('layer-controls')!.querySelector('.layer-earlier')!.querySelectorAll('.layer-earlier-row')
    .map((row) => row.querySelector('.layer-sub')!.textContent);
  const { controls, v } = bind(pop('e5', 15, 'Cellpose-SAM'));
  controls.syncCells();
  v.layer = { ...v.layer, population: 'e6' }; v.population = pop('e6', 12, 'micro-sam');
  controls.syncCells();
  expect(JSON.parse(kept.get('micromind-earlier-layers:p1')!).map((r: { id: string }) => r.id)).toEqual(['e6', 'e5']);
  // A reload (the card bound afresh), then Run again in the same Project: the earlier ones are listed again.
  nodes.set('layer-controls', new TestElement('div'));
  const again = bind(pop('e7', 9, 'Bright objects'));
  again.controls.syncCells();
  expect(rows()).toEqual(['Outlines · 12 by micro-sam', 'Outlines · 15 by Cellpose-SAM']);
  // Another Project's are its own.
  again.v.layer = { ...again.v.layer, population: 'e1' }; again.v.population = pop('e1', 4, 'Cellpose-SAM', 'p2');
  again.controls.syncCells();
  expect(rows()).toEqual([]);
  // Storage holding anything else, or none at all: nothing listed from it, and nothing breaks.
  kept.set('micromind-earlier-layers:p3', '{"not":"a list"}');
  nodes.set('layer-controls', new TestElement('div'));
  bind(pop('e9', 2, 'Cellpose-SAM', 'p3')).controls.syncCells();
  expect(rows()).toEqual([]);
  vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } });
  nodes.set('layer-controls', new TestElement('div'));
  const blocked = bind(pop('f1', 3, 'Cellpose-SAM', 'p4'));
  blocked.controls.syncCells();
  blocked.v.layer = { ...blocked.v.layer, population: 'f2' }; blocked.v.population = pop('f2', 5, 'micro-sam', 'p4');
  blocked.controls.syncCells();
  expect(rows()).toEqual(['Outlines · 3 by Cellpose-SAM']); // kept in memory for this page
});

// Production MadeToday over synthetic DOM/HTTP; no browser rendering or model evidence.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { MadeToday, type ResourceEntry } from './madetoday';
import { TestElement } from './workspace-windows.test-support';
class Node extends TestElement {
  before(node: TestElement) { this.parentElement?.insertBefore(node, this); }
}
let nodes: Map<string, Node>;
let unavailable = false;
let entries: ResourceEntry[] = [];
beforeEach(() => {
  unavailable = false; entries = [];
  nodes = new Map(['made-today', 'made-today-list', 'made-today-note', 'made-today-close'].map(id => [id, new Node('div')]));
  nodes.get('made-today')!.append(nodes.get('made-today-list')!, nodes.get('made-today-note')!, nodes.get('made-today-close')!);
  vi.stubGlobal('document', { createElement: (tag: string) => new Node(tag), getElementById: (id: string) => nodes.get(id), activeElement: null });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ok: !unavailable, json: async () => url.endsWith('/summary')
    ? {kinds: [], couldMake: [], running: []} : {entries}})));
});
afterEach(() => vi.unstubAllGlobals());
test('successful unchanged refresh clears the preceding read failure without replacing retained rows', async () => {
  const view = new MadeToday(() => {});
  await view.refresh();
  const original = nodes.get('made-today-list')!.children[0];
  unavailable = true; await view.refresh();
  expect(nodes.get('made-today-note')!.textContent).toContain('could not be read');
  unavailable = false; await view.refresh();
  expect(nodes.get('made-today-note')!.textContent).toBe('');
  expect(nodes.get('made-today-list')!.children[0]).toBe(original);
});

test('a verified immutable read is labelled without inventing an integer revision', async () => {
  entries = [{id:'image:one',kind:'result',title:'Retained photo',summary:'',state:'done',actions:[],
    readBy:[{researcher:'r1',name:'Researcher',at:1,version:null,how:'resource-read'}]}];
  await new MadeToday(() => {}).refresh();
  const title = nodes.get('made-today-list')!.querySelector('.made-readers')!.title;
  expect(title).toContain('verified retained content'); expect(title).not.toContain('revision null');
});

test('only products are listed and counted: named outlines, outcomes and the sources stay out (2026-10-11, 10-12)', async () => {
  // A clearer picture is one of the session's sources (ADR 0022, the user's Q11): in Sources, not here.
  entries = [
    {id:'e1',kind:'population',title:'Cell outlines · 01:07',summary:'',state:'saved',actions:['open'],product:true},
    {id:'named-s8',kind:'named-outline',title:'blue tissue · 02:31',summary:'',state:'done',actions:['read'],product:false},
    {id:'u73490e',kind:'outcome',title:'Outcome · EdgeTAM, outline',summary:'',state:'done',actions:['read'],product:false},
    {id:'r3',kind:'result',title:'Clearer picture · 02:40',summary:'',state:'done',actions:['open'],product:false,madeBy:{tool:'enhance'}},
    {id:'r4',kind:'result',title:'Reference photo · 02:41',summary:'',state:'done',actions:['open'],product:true,madeBy:{tool:'reference_photo'}},
  ];
  const view = new MadeToday(() => {});
  let counted = -1; view.onCount = (n) => { counted = n; };
  await view.refresh();
  const cards = nodes.get('made-today-list')!.querySelectorAll('[data-entry]').map((card) => (card as Node).dataset.entry);
  expect(cards.sort()).toEqual(['e1', 'r4']);
  expect(counted).toBe(2);
});

test('the Overlays and Measurements filters use the index kinds that exist', async () => {
  const { matchesFilter } = await import('./madetoday');
  const tracks = {id:'m1',kind:'tracks',title:'Motion · 18:10',summary:'',state:'saved',actions:['open']};
  const cells = {id:'e1',kind:'population',title:'Cell outlines',summary:'',state:'saved',actions:['open']};
  const followed = {id:'i4',kind:'individual',title:'Followed cell',summary:'',state:'done',actions:['read']};
  expect(matchesFilter(tracks, 'Overlays')).toBe(true);
  expect(matchesFilter(cells, 'Overlays')).toBe(true);
  expect(matchesFilter(followed, 'Measurements')).toBe(true);
  expect(matchesFilter(tracks, 'Measurements')).toBe(true);
});

test('every product card shows its thumbnail, made by the server, and its kind icon while there is none', async () => {
  const { thumbnailVersion } = await import('./madetoday');
  entries = [
    {id:'e1',kind:'population',title:'Cell outlines · 01:07',summary:'312 cells',state:'saved',actions:['open'],product:true,day:'2026-10-11',version:0},
    {id:'MicroscopeMind-20261011-031500',kind:'recording',title:'Screen recording · 03:15',summary:'',state:'recording',actions:['read'],product:true,day:'2026-10-11'},
    {id:'r2',kind:'research',title:"Researcher 2's lesson",summary:'',state:'done',actions:['open'],product:true,day:'2026-10-11'},
  ];
  await new MadeToday(() => {}).refresh();
  const cards = nodes.get('made-today-list')!.querySelectorAll('[data-entry]') as Node[];
  const thumb = (id: string) => cards.find((card) => card.dataset.entry === id)!.querySelector('.made-thumb') as Node;
  expect(cards.map((card) => thumb(card.dataset.entry).dataset.icon).sort()).toEqual(['cells', 'recording', 'research']);
  for (const card of cards) {
    const entry = entries.find((e) => e.id === card.dataset.entry)!;
    const src = (thumb(entry.id).querySelector('img') as unknown as { src: string }).src;
    expect(src).toBe(`/api/resources/${encodeURIComponent(entry.id)}/thumbnail?day=2026-10-11&v=${thumbnailVersion(entry)}`);
  }
  // A new revision is a new thumbnail; words alone are not (a screen recording's size changes while it records).
  expect(thumbnailVersion({...entries[0], version: 1})).not.toBe(thumbnailVersion(entries[0]));
  expect(thumbnailVersion({...entries[1], summary: '9 MB'})).toBe(thumbnailVersion(entries[1]));
  // No picture to make one from (the server says 404): the kind's icon stays.
  thumb('r2').querySelector('img')!.dispatchEvent(new Event('error'));
  expect(thumb('r2').querySelector('img')).toBeNull();
  expect(thumb('r2').dataset.icon).toBe('research');
  thumb('e1').querySelector('img')!.dispatchEvent(new Event('load'));
  expect('picture' in thumb('e1').dataset).toBe(true);
});

test('a click or Enter on the whole row opens the item over the live workspace, once (live 2026-10-11)', async () => {
  const { fire } = await import('./workspace-windows.test-support');
  entries = [
    {id:'e1',kind:'population',title:'Cell outlines · 01:07',summary:'',state:'saved',actions:['open'],product:true},
    {id:'r9',kind:'research',title:'Notes',summary:'',state:'done',actions:['read'],product:true},
  ];
  const sent: unknown[] = [];
  await new MadeToday((message) => sent.push(message)).refresh();
  const cards = nodes.get('made-today-list')!.querySelectorAll('[data-entry]') as Node[];
  const row = cards.find((card) => card.dataset.entry === 'e1')!;
  expect(row.dataset.openable).toBe('true');
  fire(row, 'click', { target: row });
  expect(sent).toEqual([{ type: 'present', what: 'e1', do: 'show' }]);
  fire(row, 'keydown', { target: row, key: 'Enter', preventDefault: () => {} });
  expect(sent).toHaveLength(2);
  fire(row.querySelector('.made-eye') as Node, 'click', { stopPropagation: () => {} });
  expect(sent).toHaveLength(3); // its own button opens it once, not twice through the row
  expect(cards.find((card) => card.dataset.entry === 'r9')!.dataset.openable).toBe('false');
});

// Made in this session (once "Made today"): a session may span days, so its work sits under quiet day dividers; another
// session opened (session-cleared) is read again at once, never shown with the last one's rows.
test('a session that spans days: its work under day dividers, newest first; one day needs none', async () => {
  const now = Date.now() / 1000;
  const day = 86_400;
  entries = [
    {id:'e1',kind:'population',title:'Cell outlines · 10:02',summary:'',state:'saved',actions:['open'],product:true,at:now - 2 * day},
    {id:'m1',kind:'tracks',title:'Motion · 09:15',summary:'',state:'saved',actions:['open'],product:true,at:now - 1},
    {id:'r2',kind:'result',title:'Reference photo',summary:'',state:'done',actions:['open'],product:true,at:now - 2},
  ];
  await new MadeToday(() => {}).refresh();
  const cards = nodes.get('made-today-list')!.querySelector('.made-cards')!;
  const order = cards.children.map((child) => child.classList.contains('made-day') ? `— ${child.textContent}` : (child as Node).dataset.entry);
  expect(order[0]).toBe('— Today');
  expect(order.slice(1, 3)).toEqual(['m1', 'r2']);
  expect(order[3]).toMatch(/^— [A-Z][a-z]{2} \d{1,2}(, \d{4})?$|^— Yesterday$/); // two days ago: its date
  expect(order[4]).toBe('e1');
  entries = entries.slice(1);
  await new MadeToday(() => {}).refresh();
  expect(nodes.get('made-today-list')!.querySelectorAll('.made-day')).toEqual([]);
});

test('another session opened: what was listed goes at once, and the index is read again', async () => {
  entries = [{id:'e1',kind:'population',title:'Cell outlines · 01:07',summary:'',state:'saved',actions:['open'],product:true}];
  const view = new MadeToday(() => {});
  let counted = -1; view.onCount = (n) => { counted = n; };
  await view.refresh();
  expect(counted).toBe(1);
  const fetches = (fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
  entries = [];
  view.sessionChanged();
  expect(counted).toBe(0); // at once, before the read comes back
  expect(nodes.get('made-today-list')!.querySelectorAll('[data-entry]')).toEqual([]);
  expect((fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(fetches + 2); // the list and its summary
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  expect(nodes.get('made-today-list')!.textContent).toContain('Nothing made yet.');
});

// The side bar's column (made-column.ts) reads what this read: told once per change of the data, never on a filter or
// a window's change, at once when it starts listening; and it opens an entry as the card does.
test('others listening are told what was read, once per change, and open an entry as its card does', async () => {
  entries = [{id:'e1',kind:'population',title:'Cell outlines · 01:07',summary:'',state:'saved',actions:['open'],product:true,at:1000},
    {id:'r3',kind:'result',title:'Reference photo',summary:'',state:'done',actions:['open'],product:true,day:'s1',version:1,pictures:[{}],at:2000},
    {id:'named-s8',kind:'named-outline',title:'blue tissue',summary:'',state:'done',actions:['read'],product:false}];
  const sent: unknown[] = [];
  const opened: unknown[] = [];
  const view = new MadeToday((c) => sent.push(c), (source, title) => opened.push([source, title]));
  const told: string[][] = [];
  view.onData((list) => told.push(list.map((e) => e.id)));
  expect(told).toEqual([]); // nothing read yet
  await view.refresh();
  expect(told).toEqual([['e1', 'r3']]); // products only
  await view.refresh(); // the same data: not told again
  view.visibilityChanged();
  expect(told).toHaveLength(1);
  const late: string[][] = [];
  view.onData((list) => late.push(list.map((e) => e.id)));
  expect(late).toEqual([['e1', 'r3']]); // at once
  view.openEntry(entries[0]);
  view.openEntry(entries[1]);
  expect(sent).toEqual([{ type: 'present', what: 'e1', do: 'show' }]);
  expect(opened).toEqual([[{ day: 's1', id: 'r3', version: 1, picture: 0 }, 'Reference photo']]);
  // A window already showing it comes back instead.
  const restored: string[] = [];
  view.views = () => [{ id: 'w1', title: 'Reference photo', visible: false, resource: { day: 's1', id: 'r3', version: 1, picture: 0 } }];
  view.restore = (id) => restored.push(id);
  view.openEntry(entries[1]);
  expect(restored).toEqual(['w1']);
  expect(opened).toHaveLength(1);
});


test('an unavailable retained result explains recovery directly on its card', async () => {
  entries = [{id:'e1',kind:'population',title:'Unavailable result e1',summary:'Masks changed; restore the evidence folder from backup',state:'unavailable',actions:['open'],product:true}];
  await new MadeToday(() => {}).refresh();
  expect(nodes.get('made-today-list')!.querySelector('.made-meta')!.textContent).toContain('restore the evidence folder');
});

test('a result card offers the capability download only when the result says which capabilities made it', async () => {
  // Spec review of 64f49d1b, P2-1: a result with no madeWith (a reference photo) has nothing to carry, so the action
  // always failed there; one that says what made it (resources.Entry.portable) offers it.
  entries = [{id:'d1',kind:'result',title:'Reference photo',summary:'',state:'done',actions:['open'],
    product:true,day:'session-one',pictures:[{}]}];
  await new MadeToday(() => {}).refresh();
  expect(nodes.get('made-today-list')!.querySelector('.portable-picture')).toBeNull();
  entries = [{...entries[0], id:'d2', title:'Your model', portable:true}];
  await new MadeToday(() => {}).refresh();
  expect(nodes.get('made-today-list')!.querySelector('.portable-picture')).not.toBeNull();
});

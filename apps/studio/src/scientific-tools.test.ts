// Synthetic DOM/HTTP contracts; actual native integration is evaluated separately.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
import { scientificTools } from './scientific-tools';
const source = { id: 'p' + 'a'.repeat(32), day: 'original', version: 2 };
const digest = 'c'.repeat(64);
const item = { key: 'mine/tool#viewer', name: 'Native animation', owner: 'mine/tool', generation: 'b'.repeat(16), canReview: true, reason: '', scope: 'Declared scene integration' };
const workspace = () => ({ id: source.id, session: source.day, revision: 3, document: { title: 'Accepted scene', state: { scientificToolChoice: { format: 1, capability: item.key, generation: item.generation, source: { id: source.id, session: source.day, revision: 2, digest } } } } });
const response = (data: object, status = 200) => new Response(JSON.stringify(data), { status });
const discovery = (row = item) => response({ ok: true, source: { id: source.id, session: source.day, revision: 2, digest }, tools: [row], comparison:{available:true,reason:''} });
let root: TestElement;
const fetcher = vi.fn<typeof fetch>(), open = vi.fn(async (_target: { id: string; version: number; title: string }) => {});
const button = (text: string) => root.querySelectorAll('button').find(el => el.textContent === text)!;
async function settle() { for (let i = 0; i < 12; i++) await settleMicrotasks(); }
beforeEach(() => {
  vi.stubGlobal('document', syntheticDOM().document); vi.stubGlobal('fetch', fetcher); fetcher.mockReset(); open.mockClear();
  const host = document.createElement('section'); root = host as unknown as TestElement; scientificTools(host, source, open);
});
afterEach(() => vi.unstubAllGlobals());

test('explicit choice pins saved values and generation; open and execution are separate', async () => {
  expect(fetcher).not.toHaveBeenCalled();
  expect(root.textContent).toContain('Unsaved edits and other windows are not inputs');
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ ok: true, project: workspace() }));
  fire(button('Find interactive tools'), 'click'); await settle();
  expect(root.textContent).toContain('Compare alternatives on this input');
  fire(button('Continue with Native animation'), 'click'); await settle();
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ session: source.day, revision: 2, capability: item.key, generation: item.generation });
  expect(open).not.toHaveBeenCalled();
  expect(root.textContent).toContain('This window remains on revision 2');
  expect(root.textContent).toContain('Native animation is selected. Open it to inspect settings; running is a separate action.');
  fire(button('Open Native animation'), 'click'); await settle();
  expect(open).toHaveBeenCalledWith({ id: source.id, version: 3, title: 'Accepted scene' });
});

test('a second tool chosen from the same window follows the first choice and opens', async () => {
  // final052: Cellpose was chosen from the retained image and labels (revision 2 → 3); Continue with napari-animation in
  // that same window then saved nothing. The server now records it after the earlier choice, on the same inputs.
  const project = workspace(); project.revision = 4;
  project.document.state.scientificToolChoice.source = { id: source.id, session: source.day, revision: 3, digest: 'e'.repeat(64) };
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ ok: true, project }));
  fire(button('Find interactive tools'), 'click'); await settle();
  fire(button('Continue with Native animation'), 'click'); await settle();
  expect(root.textContent).toContain('Native animation is selected');
  expect(root.textContent).toContain('Saved revision 4; input revision 2, after the choice in revision 3.');
  fire(button('Open Native animation'), 'click'); await settle();
  expect(open).toHaveBeenCalledWith({ id: source.id, version: 4, title: 'Accepted scene' });
});

test.each([[1, 2], [3, 5]])('refuses a choice from revision %i saved as revision %i', async (from, saved) => {
  const project = workspace(); project.revision = saved;
  project.document.state.scientificToolChoice.source = { id: source.id, session: source.day, revision: from, digest: 'e'.repeat(64) };
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ ok: true, project }));
  fire(button('Find interactive tools'), 'click'); await settle();
  fire(button('Continue with Native animation'), 'click'); await settle();
  expect(root.textContent).toContain('differs from your selected input');
  expect(button('Open Native animation')).toBeUndefined(); expect(open).not.toHaveBeenCalled();
});

test('missing preparation links to setup while keeping selected input fixed', async () => {
  fetcher.mockResolvedValueOnce(discovery({ ...item, canReview: false, reason: 'Prepare this integration in Models' }));
  fire(button('Find interactive tools'), 'click'); await settle();
  expect(button('Continue with Native animation').disabled).toBe(true);
  expect(root.querySelector('a')).toHaveProperty('href', '#models/mine%2Ftool');
  expect(fetcher).toHaveBeenCalledTimes(1); expect(open).not.toHaveBeenCalled();
});

test('conflicting revision remains explicit and retry uses the same captured request', async () => {
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ ok: false, says: 'This project changed.', next: 'Reopen the accepted revision.' }, 409));
  fire(button('Find interactive tools'), 'click'); await settle();
  fire(button('Continue with Native animation'), 'click'); await settle();
  expect(root.textContent).toContain('This project changed');
  expect(open).not.toHaveBeenCalled();
  fetcher.mockResolvedValueOnce(response({ ok: true, project: workspace() }));
  fire(button('Continue with Native animation'), 'click'); await settle();
  expect(fetcher.mock.calls[2][1]?.body).toBe(fetcher.mock.calls[1][1]?.body);
});

test.each(['input', 'generation', 'revision', 'digest'])('refuses a mismatched %s receipt without opening it', async field => {
  const project = workspace();
  if (field === 'input') project.id = 'p' + 'd'.repeat(32);
  if (field === 'generation') project.document.state.scientificToolChoice.generation = 'd'.repeat(16);
  if (field === 'revision') project.revision = 4;
  if (field === 'digest') project.document.state.scientificToolChoice.source.digest = 'd'.repeat(64);
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ ok: true, project }));
  fire(button('Find interactive tools'), 'click'); await settle();
  fire(button('Continue with Native animation'), 'click'); await settle();
  expect(root.textContent).toContain('differs from your selected input');
  expect(button('Open Native animation')).toBeUndefined(); expect(open).not.toHaveBeenCalled();
});

test('scientific task aliases find tools without hiding preparation requirements', async()=>{
 fetcher.mockResolvedValueOnce(discovery({...item,name:'Unfamiliar package',canReview:false,reason:'Prepare this integration in Models',category:'Present/Animation',searchAliases:['camera movie']} as typeof item));
 fire(button('Find interactive tools'),'click');await settle();
 const search=root.querySelector('input')!;search.value='camera movie';fire(search,'input');
 const row=root.querySelector('article')!;expect(row.hidden).toBe(false);expect(row.textContent).toContain('Prepare this integration');
 search.value='segmentation';fire(search,'input');expect(row.hidden).toBe(true);expect(open).not.toHaveBeenCalled();
});

test('runtime-free native data keeps direct choice but explains unavailable comparisons', async()=>{
 fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:2,digest},tools:[item],comparison:{available:false,reason:'Choose an interactive tool first; compare alternatives from its saved scene.'}}));
 fire(button('Find interactive tools'),'click');await settle();
 expect(button('Continue with Native animation').disabled).toBe(false);
 expect(root.textContent).not.toContain('Compare alternatives on this input');
 expect(root.textContent).toContain('Choose an interactive tool first');
 expect(open).not.toHaveBeenCalled();
});


test('update trial is explicit and carries the exact prepared generation without changing ordinary choice', async () => {
  fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:2,digest},
    tools:[{...item,updateTrial:true}],comparison:{available:true,reason:''}}))
    .mockResolvedValueOnce(response({ok:true,project:workspace()}));
  fire(button('Find interactive tools'),'click');await settle();
  expect(button('Continue with Native animation')).toBeUndefined();
  fire(button('Try update: Native animation'),'click');await settle();
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({session:source.day,revision:2,
    capability:item.key,generation:item.generation,updateTrial:true});
  expect(open).not.toHaveBeenCalled();
});

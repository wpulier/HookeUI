// All assets (library-page.ts) over a synthetic DOM: categories first with their covers and counts, a category's assets
// under the names their looks gave them, the examples by specimen, the search; a click previews and never touches the
// session, Add to this session does; the trash asks first, naming the sessions that use it, and offers Undo; files
// added join your assets only.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { AssetMeta } from './protocol';
import { byName, categoryLine, CATEGORIES, itemCount, LibraryPage, LOOKS_OFF, NOT_SURE, readCategories, type AssetDetails, type LibraryActions } from './library-page';
import { fire } from './workspace-windows.test-support';
import { at, NOW, sessionDOM, SessionNode, type SessionDOM } from './sessions.test-support';

function asset(id: string, fields: Partial<AssetDetails> = {}): AssetDetails {
  return { id, kind: 'recording', title: `Asset ${id}`, from: 'added', capturedAt: at(1, 44), captured: 'Oct 12, 1:44 am', seconds: 192,
    capturedOn: null, microscope: true, optics: {}, specimen: null, specimenBy: null, notes: null, origin: null, file: `${id}.mp4`,
    thumb: `/api/library/${id}/thumbnail`, category: null, ...fields };
}
const filed = (group: string, name: string) => ({ group, name, by: 'app' as const, sure: true });

/** Yours: two rotifers, a smear and one not looked at yet; the examples: 3 clips of one rotifer specimen, 1 of a diatom.
 * Served as the route does: categories with covers, one category's assets, by specimen, a page at a time. */
function library() {
  const mine = [asset('a1', { title: 'Pond rotifer', category: filed('Invertebrates', 'Rotifers') }),
    asset('a2', { title: 'Second rotifer', category: filed('Invertebrates', 'Rotifers') }),
    asset('a3', { kind: 'still', title: 'Smear', category: filed('Blood', 'Red blood cells') }),
    asset('a4', { title: 'New one' })];
  const examples = [...[0, 1, 2].map((i) => asset(`a${10 + i}`, { from: 'example', title: `Rotifer${i ? ` (${i + 1})` : ''}`, specimen: 'rotifer',
    category: filed('Invertebrates', 'Rotifers') })), asset('a20', { from: 'example', title: 'Diatom', specimen: 'diatom', category: filed('Protists & algae', 'Diatoms') })];
  const fetcher = vi.fn(async (url: string) => {
    if (/^\/api\/library\/a\d+$/.test(url)) {
      const id = url.split('/').pop()!;
      const one = [...mine, ...examples].find((a) => a.id === id)!;
      return { ok: true, json: async () => ({ ...one, sessions: id === 'a1' ? [{ id: 's1', label: 'Pond water' }, { id: 's2', label: 'Oct 11' }] : [] }) };
    }
    const query = new URL(url, 'http://studio').searchParams;
    const words = (query.get('q') ?? '').toLowerCase().split(/\s+/).filter(Boolean);
    let found = (query.get('scope') === 'example' ? examples : mine)
      .filter((a) => !query.get('kind') || a.kind === query.get('kind'))
      .filter((a) => words.every((w) => `${a.title} ${a.specimen ?? ''} ${categoryLine(a.category)}`.toLowerCase().includes(w)));
    const group = (a: AssetDetails) => a.category?.group ?? NOT_SURE;
    const categories = [...CATEGORIES, NOT_SURE].filter((g) => found.some((a) => group(a) === g))
      .map((g) => ({ group: g, count: found.filter((a) => group(a) === g).length, cover: found.find((a) => group(a) === g) }));
    if (query.get('category')) found = found.filter((a) => group(a) === query.get('category'));
    const specimens: { name: string; count: number; first: AssetMeta }[] = [];
    for (const a of found) {
      const known = specimens.find((s) => s.name === a.specimen);
      if (known) known.count += 1;
      else specimens.push({ name: a.specimen ?? '', count: 1, first: a });
    }
    if (query.get('specimen')) found = found.filter((a) => a.specimen === query.get('specimen'));
    const offset = Number(query.get('offset') ?? 0), limit = Number(query.get('limit') ?? 500);
    return { ok: true, json: async () => ({ assets: found.slice(offset, offset + limit), count: found.length, categories,
      ...(query.get('scope') === 'example' ? { specimens } : {}) }) };
  });
  return fetcher;
}

let dom: SessionDOM;
beforeEach(() => { dom = sessionDOM(); vi.useFakeTimers(); vi.setSystemTime(NOW * 1000); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function page(inSession: string[] = [], catalogLayout = false, organization = false) {
  const fetcher = library();
  const asked: unknown[][] = [];
  const actions: LibraryActions = {
    ...(organization ? {organize:()=>{asked.push(['organize']);}} : {}),
    add: async (a) => { asked.push(['add', a.id]); return true; },
    setMeta: async (id, set) => { asked.push(['meta', id, set]); },
    trash: async (a) => { asked.push(['trash', a.id]); return true; },
    restore: async (id) => { asked.push(['restore', id]); return true; },
    upload: async (files) => { asked.push(['upload', files.length]); },
    openSession: (id) => asked.push(['session', id]),
  };
  const root = dom.node('assets', 'section');
  const view = new LibraryPage(root as unknown as HTMLElement, actions, { fetcher: fetcher as unknown as typeof fetch,
    catalogLayout, clock: () => NOW * 1000, inSession: (id) => inSession.includes(id) });
  return { fetcher, asked, root, view, actions };
}

test('global catalog exposes organization and datasets without adding an asset to a Project', async () => {
  const {root,view,asked}=page([],true,true);
  view.show();await settle();
  expect(root.textContent).toContain('across all Projects');
  expect(root.querySelector('.lib-package-note')?.textContent).toContain('Project metadata');
  expect(root.querySelector('.lib-organization-status')?.textContent).toContain('status unavailable');
  fire(root.querySelector('.lib-organize') as SessionNode,'click');
  expect(asked).toEqual([['organize']]);
});
async function settle(): Promise<void> {
  await vi.runOnlyPendingTimersAsync();
  for (let i = 0; i < 6; i++) await Promise.resolve();
}
const pv = (view: LibraryPage) => view.preview as unknown as SessionNode;
const cards = (root: SessionNode, kind: string) => root.querySelectorAll(`.lib-${kind}`) as SessionNode[];
const titles = (root: SessionNode, kind: string) => cards(root, kind).map((c) => (c.querySelector('.lib-card-title') as SessionNode).textContent);

describe('the words', () => {
  test('categories read safely, counts, a category line, and a category\'s assets under their names', () => {
    expect(readCategories({ categories: [{ group: 'Blood', count: 2, cover: asset('a1') }, { group: 'x' }, 'nonsense'] }).map((c) => c.group)).toEqual(['Blood']);
    expect(readCategories(null)).toEqual([]);
    expect([itemCount(1), itemCount(57)]).toEqual(['1 item', '57 items']);
    expect(categoryLine(filed('Invertebrates', 'Rotifers'))).toBe('Invertebrates › Rotifers');
    expect(categoryLine(null)).toBe(NOT_SURE);
    const groups = byName([asset('a1', { category: filed('Invertebrates', 'Worms') }), asset('a2'), asset('a3', { category: filed('Invertebrates', 'Rotifers') })]);
    expect(groups.map((g) => [g.name, g.assets.map((a) => a.id)])).toEqual([['Rotifers', ['a3']], ['Worms', ['a1']], ['Not named yet', ['a2']]]);
  });
});

describe('All assets', () => {
  test('categories first: each with its cover and how many, "Not sure yet" last; one opens its assets under their names', async () => {
    const { root, view } = page();
    view.show();
    await settle();
    expect(titles(root, 'category')).toEqual(['Blood', 'Invertebrates', NOT_SURE]);
    expect(cards(root, 'category').map((c) => (c.querySelector('.lib-card-meta') as SessionNode).textContent)).toEqual(['1 item', '2 items', '1 item']);
    expect((cards(root, 'category')[1].querySelector('img') as SessionNode).src).toBe('/api/library/a1/thumbnail'); // its newest is its cover
    fire(cards(root, 'category')[1], 'click');
    await settle();
    expect((root.querySelectorAll('.lib-name') as SessionNode[]).map((n) => n.textContent)).toEqual(['Rotifers']);
    expect(titles(root, 'asset')).toEqual(['Pond rotifer', 'Second rotifer']);
    expect((root.querySelector('.lib-crumb-here') as SessionNode).textContent).toBe('Invertebrates');
    fire(root.querySelector('.lib-crumb-link') as SessionNode, 'click'); // All categories
    await settle();
    expect(titles(root, 'category')).toEqual(['Blood', 'Invertebrates', NOT_SURE]);
  });

  test('before any look, "Not sure yet" is all there is: it opens at once (one card is no choice), with no way back to it', async () => {
    const { fetcher, root, view } = page();
    fetcher.mockImplementation(async (url: string) => {
      const query = new URL(url, 'http://studio').searchParams;
      const all = [asset('a7', { title: 'First' }), asset('a8', { title: 'Second' })];
      return { ok: true, json: async () => ({ assets: all, count: all.length, categories: query.get('category') ? []
        : [{ group: NOT_SURE, count: 2, cover: all[0] }] }) };
    });
    view.show();
    await settle();
    expect(cards(root, 'category')).toEqual([]);
    expect(titles(root, 'asset')).toEqual(['First', 'Second']);
    expect((root.querySelector('.lib-crumb') as SessionNode).hidden).toBe(true);
    expect(fetcher).toHaveBeenLastCalledWith(expect.stringContaining('category=Not+sure+yet'), { cache: 'no-store' });
  });

  test('with Library looks off, "Not sure yet" says no look is coming and how to file them; on, nothing is said', async () => {
    for (const looks of ['off', 'on']) {
      const { fetcher, root, view } = page();
      fetcher.mockImplementation(async (url: string) => {
        const query = new URL(url, 'http://studio').searchParams;
        const all = [asset('a7', { title: 'First' })];
        return { ok: true, json: async () => ({ assets: all, count: all.length, looks, categories: query.get('category') ? []
          : [{ group: NOT_SURE, count: 1, cover: all[0] }] }) };
      });
      view.show();
      await settle();
      const notes = (root.querySelectorAll('.lib-note') as SessionNode[]).map((n) => n.textContent);
      expect(notes).toEqual(looks === 'off' ? [LOOKS_OFF] : []);
      view.hide();
    }
  });

  test('the search finds across categories, by what the looks named too', async () => {
    const { root, view } = page();
    view.show();
    await settle();
    view.search.value = 'rotifers';
    fire(view.search as unknown as SessionNode, 'input');
    await settle();
    expect(titles(root, 'asset')).toEqual(['Pond rotifer', 'Second rotifer']);
    expect(cards(root, 'category')).toEqual([]);
  });

  test('the examples: a category opens its specimens, a specimen its clips, one alone previews at once', async () => {
    const { root, view } = page();
    view.show();
    await settle();
    fire(root.querySelector('[data-group="example"]') as SessionNode, 'click');
    await settle();
    expect(titles(root, 'category')).toEqual(['Invertebrates', 'Protists & algae']);
    fire(cards(root, 'category')[0], 'click');
    await settle();
    expect(titles(root, 'specimen')).toEqual(['Rotifer']);
    expect((cards(root, 'specimen')[0].querySelector('.lib-card-meta') as SessionNode).textContent).toBe('3 clips');
    fire(cards(root, 'specimen')[0], 'click');
    await settle();
    expect(titles(root, 'asset')).toEqual(['Clip 1', 'Clip 2', 'Clip 3']);
    expect((root.querySelectorAll('.lib-crumb-here') as SessionNode[]).map((n) => n.textContent)).toEqual(['Rotifer']);
    expect(view.kind).toBeNull();
    expect((root.querySelector('.lib-add-files') as SessionNode).hidden).toBe(true); // files are added to yours
  });

  test('a click previews and never touches the session; Add to this session does, and says when it is in it already', async () => {
    const { asked, root, view } = page(['a3']);
    view.show();
    await settle();
    fire(cards(root, 'category')[1], 'click');
    await settle();
    fire(cards(root, 'asset')[0], 'click');
    await settle();
    expect(asked).toEqual([]); // a preview: nothing asked of the session
    expect(view.preview.hidden).toBe(false);
    expect((pv(view).querySelector('video') as SessionNode).src).toBe('/api/library/a1/file');
    expect((pv(view).querySelector('.lib-preview-category') as SessionNode).textContent).toBe('Invertebrates › Rotifers');
    expect((pv(view).querySelector('.lib-sessions') as SessionNode).textContent).toContain('In Projects: ');
    expect((pv(view).querySelectorAll('.lib-session-link') as SessionNode[]).map((l) => l.textContent)).toEqual(['Pond water', 'Oct 11']);
    fire((pv(view).querySelectorAll('.lib-session-link') as SessionNode[])[0], 'click');
    expect(asked).toEqual([['session', 's1']]);
    const add = pv(view).querySelector('.lib-add') as SessionNode;
    expect(add.textContent).toBe('Open in this Project');
    fire(add, 'click');
    await settle();
    expect(asked.at(-1)).toEqual(['add', 'a1']);
    expect(view.preview.hidden).toBe(true);
    // One already in this session: Put on stage.
    view.openCategory('Blood');
    await settle();
    fire(cards(root, 'asset')[0], 'click');
    await settle();
    expect((pv(view).querySelector('.lib-add') as SessionNode).textContent).toBe('Open in this Project');
    expect((root.querySelector('.library-tag') as SessionNode).textContent).toBe('In this project');
  });

  test('its own fields are saved as they change; its category is the user\'s word', async () => {
    const { asked, root, view } = page();
    view.show();
    await settle();
    fire(cards(root, 'category')[0], 'click');
    await settle();
    fire(cards(root, 'asset')[0], 'click');
    await settle();
    const select = pv(view).querySelector('select') as SessionNode;
    select.value = 'Human & animal tissue';
    fire(select, 'change');
    await settle();
    expect(asked[0]).toEqual(['meta', 'a3', { category: 'Human & animal tissue' }]);
  });

  test('a field says whether it saved: Saving…, then Saved, or Not saved and why, with Try again keeping the text', async () => {
    const { actions, root, view } = page();
    let refuse = true;
    const sent: unknown[] = [];
    actions.setMeta = async (_id, set) => { sent.push(set); if (refuse) throw new Error('Reconnect to save'); };
    view.show(); await settle();
    fire(cards(root, 'category')[0], 'click'); await settle();
    fire(cards(root, 'asset')[0], 'click'); await settle();
    const title = (pv(view).querySelectorAll('input') as SessionNode[])[2];
    title.value = 'Rotifer, second slide';
    fire(title, 'change');
    const status = () => pv(view).querySelector('.lib-field-status') as SessionNode;
    expect(status().textContent).toBe('Saving…');
    await settle();
    expect(status().dataset.state).toBe('failed');
    expect(status().textContent).toContain('Not saved: Reconnect to save. What you typed is kept here.');
    expect(title.value).toBe('Rotifer, second slide');
    refuse = false;
    fire(status().querySelector('.lib-field-retry') as SessionNode, 'click'); await settle();
    expect(status().textContent).toBe('Saved');
    expect(sent).toEqual([{ title: 'Rotifer, second slide' }, { title: 'Rotifer, second slide' }]);
  });

  test('a field serializes changed drafts and never lets a stale save or Retry certify older text', async () => {
    const {actions, root, view} = page();
    const sent: unknown[] = [], pending: {resolve:()=>void; reject:(error:Error)=>void}[] = [];
    actions.setMeta = async (_id, set) => { sent.push(set); await new Promise<void>((resolve,reject)=>pending.push({resolve,reject})); };
    view.show(); await settle(); fire(cards(root, 'category')[0], 'click'); await settle();
    fire(cards(root, 'asset')[0], 'click'); await settle();
    const title = (pv(view).querySelectorAll('input') as SessionNode[])[2];
    const status = () => pv(view).querySelector('.lib-field-status') as SessionNode;
    title.value='Draft A'; fire(title,'input'); fire(title,'change');
    title.value='Draft B'; fire(title,'input'); fire(title,'change');
    expect(sent).toEqual([{title:'Draft A'}]); // B waits; two writes cannot race on the server.
    pending[0].reject(new Error('A reply was lost')); await settle();
    expect(sent).toEqual([{title:'Draft A'},{title:'Draft B'}]);
    expect(status().textContent).toBe('Saving…'); expect(status().querySelector('.lib-field-retry')).toBeNull();
    pending[1].resolve(); await settle(); expect(status().textContent).toBe('Saved');
    title.value='Draft C'; fire(title,'input'); expect(status().textContent).toBe('Not saved yet');
    fire(title,'change'); pending[2].reject(new Error('Reconnect')); await settle();
    const staleRetry=status().querySelector('.lib-field-retry') as SessionNode;
    title.value='Draft D'; fire(title,'input'); fire(staleRetry,'click');
    expect(sent.at(-1)).toEqual({title:'Draft D'});
    pending[3].resolve(); await settle(); expect(status().textContent).toBe('Saved');
    title.value='Draft E'; fire(title,'input'); fire(title,'change');
    title.value='Draft F'; fire(title,'input');
    pending[4].resolve(); for (let i = 0; i < 6; i++) await Promise.resolve(); // before the typing pause elapses
    expect(status().textContent).toBe('Not saved yet'); expect(title.value).toBe('Draft F');
  });

  test('a removed preview cannot save an older draft after the reopened field saves its latest draft', async () => {
    const { actions, view } = page();
    const sent: unknown[] = [];
    actions.setMeta = async (_id, set) => { sent.push(set); };
    await view.openPreview(asset('a1'));
    const title = () => (pv(view).querySelectorAll('input') as SessionNode[])[2];
    title().value = 'Draft A'; fire(title(), 'input');
    view.closePreview();
    await view.openPreview(asset('a1'));
    title().value = 'Draft B'; fire(title(), 'input'); fire(title(), 'change');
    for (let i = 0; i < 6; i++) await Promise.resolve();
    expect(pv(view).querySelector('.lib-field-status')?.textContent).toBe('Saved');
    await vi.advanceTimersByTimeAsync(900);
    expect(sent).toEqual([{ title: 'Draft B' }]);
    expect(title().value).toBe('Draft B');
    expect(pv(view).querySelector('.lib-field-status')?.textContent).toBe('Saved');
    title().value = 'Draft C'; fire(title(), 'input'); view.closePreview();
    await vi.advanceTimersByTimeAsync(900);
    expect(sent.at(-1)).toEqual({ title: 'Draft C' }); // closing still keeps the draft
    let finish!: () => void;
    actions.setMeta = async (_id, set) => {
      sent.push(set);
      if (set.title === 'Draft D') await new Promise<void>(resolve => { finish = resolve; });
    };
    await view.openPreview(asset('a1'));
    title().value = 'Draft D'; fire(title(), 'change'); view.closePreview();
    await view.openPreview(asset('a1'));
    expect(title().value).toBe('Draft D');
    title().value = 'Draft E'; fire(title(), 'change');
    expect(sent.at(-1)).toEqual({ title: 'Draft D' }); // new mount shares the in-flight queue
    expect(pv(view).querySelector('.lib-field-status')?.textContent).not.toBe('Saved');
    finish(); await settle();
    expect(sent.slice(-2)).toEqual([{ title: 'Draft D' }, { title: 'Draft E' }]);
    expect(pv(view).querySelector('.lib-field-status')?.textContent).toBe('Saved');
  });

  test('typing says Not saved yet at once, then saves itself after a pause; the same text is not sent twice', async () => {
    const { actions, root, view } = page();
    const sent: unknown[] = [];
    actions.setMeta = async (_id, set) => { sent.push(set); };
    view.show(); await settle();
    fire(cards(root, 'category')[0], 'click'); await settle();
    fire(cards(root, 'asset')[0], 'click'); await settle();
    const title = (pv(view).querySelectorAll('input') as SessionNode[])[2];
    title.value = 'Rotifer, third slide';
    fire(title, 'input');
    const status = () => pv(view).querySelector('.lib-field-status') as SessionNode;
    expect(status().textContent).toBe('Not saved yet');
    expect(sent).toEqual([]);
    await vi.advanceTimersByTimeAsync(900); await settle();
    expect(status().textContent).toBe('Saved');
    fire(title, 'change'); await settle(); // leaving the field: nothing new to send
    expect(sent).toEqual([{ title: 'Rotifer, third slide' }]);
  });

  test('Move to trash asks first, naming the sessions that use it; Undo puts it back', async () => {
    const { asked, root, view } = page();
    view.show();
    await settle();
    fire(cards(root, 'category')[1], 'click');
    await settle();
    fire(cards(root, 'asset')[0], 'click');
    await settle();
    fire(pv(view).querySelector('.lib-trash') as SessionNode, 'click');
    const ask = pv(view).querySelector('.lib-confirm') as SessionNode;
    expect(ask.textContent).toContain('the 2 projects that use it (Pond water, Oct 11)');
    expect(asked).toEqual([]); // nothing until it is confirmed
    fire(ask.querySelector('.lib-trash-no') as SessionNode, 'click');
    expect(pv(view).querySelector('.lib-confirm')).toBeNull();
    fire(pv(view).querySelector('.lib-trash') as SessionNode, 'click');
    fire(pv(view).querySelector('.lib-trash-yes') as SessionNode, 'click');
    await settle();
    expect(asked).toEqual([['trash', 'a1']]);
    expect(view.preview.hidden).toBe(true);
    expect(view.status.textContent).toContain('Moved “Pond rotifer” to the trash.');
    fire((view.status as unknown as SessionNode).querySelector('.lib-undo') as SessionNode, 'click');
    await settle();
    expect(asked.at(-1)).toEqual(['restore', 'a1']);
  });

  test('files added join your assets only (never the session): said, and yours shown', async () => {
    const { asked, view, fetcher } = page();
    view.show();
    await settle();
    view.setScope('example');
    await settle();
    Object.defineProperty(view.fileInput, 'files', { value: [{ name: 'pond.mov' }], configurable: true });
    fire(view.fileInput as unknown as SessionNode, 'change');
    await settle();
    expect(asked).toEqual([['upload', 1]]);
    expect(view.scope).toBe('mine');
    expect(view.status.textContent).toBe('Added pond.mov to your Library.');
    expect(fetcher.mock.calls.some(([url]) => new URL(url, 'http://studio').searchParams.get('fresh') === '1')).toBe(true);
    fetcher.mockClear();
    fire(view.fileInput as unknown as SessionNode, 'change');
    await settle();
    expect(fetcher.mock.calls.some(([url]) => new URL(url, 'http://studio').searchParams.get('fresh') === '1')).toBe(true);
  });

  test.each(['hidden', 'superseded'] as const)('upload stays visible after a %s refresh', async (scenario) => {
    const { actions } = page();
    let finishUpload!: () => void;
    actions.upload = () => new Promise<void>((resolve) => { finishUpload = resolve; });
    let finishFresh!: () => void;
    let freshReads = 0;
    const fetcher: typeof fetch = async (input) => {
      const fresh = new URL(String(input), 'http://studio').searchParams.get('fresh') === '1';
      if (fresh && ++freshReads === 1 && scenario === 'superseded') {
        await new Promise<void>((resolve) => { finishFresh = resolve; });
      }
      return new Response(JSON.stringify({ assets: fresh ? [asset('a99', { title: 'Uploaded grid', kind: 'still' })] : [],
        count: fresh ? 1 : 0, categories: [] }));
    };
    const root = dom.node('upload-assets', 'section');
    const view = new LibraryPage(root as unknown as HTMLElement, actions, { fetcher });
    view.search.value = 'Uploaded grid';
    view.show();
    await settle();
    Object.defineProperty(view.fileInput, 'files', { value: [{ name: 'grid.png' }] });
    fire(view.fileInput as unknown as SessionNode, 'change');
    if (scenario === 'hidden') view.hide();
    finishUpload();
    await settle();
    if (scenario === 'hidden') view.show();
    else view.refresh();
    await settle();
    if (scenario === 'superseded') { finishFresh(); await settle(); }
    expect(titles(root, 'asset')).toEqual(['Uploaded grid']);
  });

});


describe('workspace library catalog', () => {
  test('opens actual media directly and filters via the persistent category sidebar without adding to the session', async () => {
    const {root, view, asked, fetcher} = page([], true);
    view.show(); await settle();
    expect(titles(root,'asset')).toEqual(['Pond rotifer','Second rotifer','Smear','New one']);
    expect(cards(root,'category')).toHaveLength(0);
    const choices = root.querySelectorAll('.lib-category-choice') as SessionNode[];
    const blood = choices.find(b=>b.getAttribute('aria-label')==='Blood')!;
    fire(blood,'click'); await settle();
    expect(titles(root,'asset')).toEqual(['Smear']);
    expect(fetcher).toHaveBeenLastCalledWith(expect.stringContaining('category=Blood'),{cache:'no-store'});
    expect(asked).toEqual([]);
    view.openCategory('Bacteria & fungi'); await settle();
    expect(titles(root,'asset')).toEqual([]);
    expect(root.querySelector('.lib-empty')?.textContent).toBe('Nothing here.');
  });
});

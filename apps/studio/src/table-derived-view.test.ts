// Synthetic DOM/transport. Numerical acceptance and the installed workspace have separate evidence.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { TestElement, fire } from './workspace-windows.test-support';
import { TableDerivedView, checkedDerived, graphPNG, type DerivedRecord } from './table-derived-view';
import type { DataTableSnapshot } from './datatable-view';
const owner = { session: 'project-one', generation: 7 }, pin = { session: owner.session, id: 'tb1', version: 3 };
const table = (): DataTableSnapshot => ({ id: pin.id, version: pin.version, title: 'Cells', mode: 'follow',
  columns: [{ key: 'area', label: 'Area', type: 'number', unit: 'µm²', origin: 'source' }, { key: 'condition', label: 'Condition', type: 'text', unit: '', origin: 'added' }],
  rows: [{ row: 'e1:a', source: 'e1', member: 'a', area: 1, condition: 'control', excluded: null }, { row: 'e1:b', source: 'e1', member: 'b', area: 5, condition: 'treated', excluded: null }],
  sources: [{ day: owner.session, id: 'e1', revision: 2, kind: 'population', label: 'Cells' }], added: [], counts: { rows: 2, included: 2, excluded: 0 }, versions: [3], freshness: { state: 'current', words: 'Current' } });
const saved = (version = 1): DerivedRecord => ({ id: 'an1', version, kind: 'analysis', title: 'Area comparison', mode: 'follow',
  source: { ...pin, sha256: 'a'.repeat(64) }, spec: { method: 'welch', value: 'area', group: 'condition', groups: ['treated', 'control'] },
  result: { method: 'welch', statistic: 2.12345678901234, df: 4, p: .127, effect: { name: 'mean difference', value: 4, ci95: [-1, 9] }, groups: [], pairs: [], assumptions: {}, notes: ['No threshold selected.'] },
  freshness: { state: 'current', words: 'Current · table revision 3' } });
const response = (value: unknown, ok = true) => ({ ok, json: async () => structuredClone(value) }) as Response;
function rig() {
  const root = new TestElement('section'), request = vi.fn<typeof fetch>().mockResolvedValue(response(saved())), registered = vi.fn();
  let generation = owner.generation;
  const view = new TableDerivedView(root as unknown as HTMLElement, { current: o => o.session === owner.session && o.generation === generation, request, saved: registered });
  return { root, view, request, registered, changeProject: () => { generation++; } };
}
function field(root: TestElement, name: string) { return root.querySelectorAll('select').find(n => n.getAttribute('aria-label') === name)!; }
function choose(root: TestElement, name: string, value: string) { const node = field(root, name); node.value = value; fire(node, 'change'); }
function configure(r: ReturnType<typeof rig>) {
  expect(r.view.setup('analysis', pin, table(), owner)).toBe(true);
  choose(r.root, 'Method', 'welch'); choose(r.root, 'Value', 'area'); choose(r.root, 'Group', 'condition');
  const boxes = r.root.querySelector('.derived-groups')!.querySelectorAll('input');
  for (const box of [boxes[1], boxes[0]]) { Object.assign(box, { checked: true }); fire(box, 'change'); }
  fire(r.root.querySelector('.derived-setup')!, 'change');
}
beforeEach(() => { vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) }); vi.stubGlobal('window', { open: vi.fn() }); });
afterEach(() => vi.unstubAllGlobals());
test('explicit test, ordered groups and pinned table are captured once; failure retains draft, accepted Save publishes exact result', async () => {
  const r = rig(); r.view.setup('analysis', pin, table(), owner);
  expect(await r.view.calculate()).toBe(false); expect(r.request).not.toHaveBeenCalled();
  configure(r); expect(r.view.canClose()).toBe(false);
  r.request.mockResolvedValueOnce(response({ error: 'Source changed', next: 'Review table' }, false));
  expect(await r.view.calculate()).toBe(false); expect(r.view.dirty).toBe(true); expect(r.registered).not.toHaveBeenCalled();
  expect(await r.view.calculate()).toBe(true); expect(r.view.dirty).toBe(false); expect(r.view.canClose()).toBe(true);
  const [url, init] = r.request.mock.calls[1]; expect(url).toBe('/api/table-derivatives');
  expect(JSON.parse(String(init!.body))).toEqual({ session: owner.session, kind: 'analysis', table: { id: 'tb1', version: 3 }, spec: saved().spec });
  expect(r.registered).toHaveBeenCalledWith(saved()); expect(r.root.textContent).toContain('2.12345678901234'); expect(r.root.textContent).toContain('Saved');
});
test('away-and-back Project generation cannot send or publish a late result', async () => {
  const r = rig(); configure(r); let release!: (response: Response) => void;
  r.request.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const pending = r.view.calculate(); expect(r.view.busy).toBe(true); r.changeProject(); release(response(saved()));
  expect(await pending).toBe(false); expect(r.registered).not.toHaveBeenCalled(); expect(r.view.dirty).toBe(true);
  expect(await r.view.calculate()).toBe(false); expect(r.request).toHaveBeenCalledTimes(1);
});
test('reopen is exact GET without recompute; update is explicit CAS and old freshness cannot replace a newer result', async () => {
  const r = rig(); expect(await r.view.open({ session: owner.session, id: 'an1', version: 1 }, owner)).toBe(true);
  expect(r.request.mock.calls[0][0]).toBe('/api/table-derivatives/an1?session=project-one&version=1');
  expect(r.request.mock.calls[0][1]?.method).toBeUndefined(); expect(r.registered).not.toHaveBeenCalled();
  let release!: (response: Response) => void; r.request.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const refresh = r.view.refresh(); r.request.mockResolvedValueOnce(response(saved(2)));
  expect(await r.view.update()).toBe(true);
  expect(JSON.parse(String(r.request.mock.calls[2][1]!.body))).toEqual({ session: owner.session, version: 1, update: true });
  const stale = saved(); stale.freshness = { state: 'stale', words: 'OLD freshness' }; release(response(stale)); await refresh;
  expect(r.root.textContent).not.toContain('OLD freshness'); expect(r.registered).toHaveBeenCalledWith(saved(2));
});
test('foreign source and mismatched output identity refuse publication without claiming Saved', async () => {
  const foreign = saved(); foreign.source.session = 'other'; expect(() => checkedDerived(foreign, owner.session)).toThrow('Project');
  const r = rig(); configure(r); const changed = saved(); changed.source.version = 4; r.request.mockResolvedValueOnce(response(changed));
  expect(await r.view.calculate()).toBe(false); expect(r.registered).not.toHaveBeenCalled(); expect(r.view.dirty).toBe(true); expect(r.root.textContent).toContain('captured source');
});
test('bar uncertainty is an explicit choice; valid graph setup uses the same pinned endpoint', async () => {
  const r = rig(); expect(r.view.setup('graph', pin, table(), owner)).toBe(true); choose(r.root, 'Graph type', 'bar'); choose(r.root, 'Value', 'area');
  expect(await r.view.calculate()).toBe(false); expect(r.request).not.toHaveBeenCalled();
  choose(r.root, 'Uncertainty', 'ci95'); r.request.mockResolvedValueOnce(response({ error: 'Synthetic refusal' }, false)); await r.view.calculate();
  expect(JSON.parse(String(r.request.mock.calls[0][1]!.body)).spec).toEqual({ kind: 'bar', value: 'area', error: 'ci95' });
});
test('annotation selector uses only accepted matching table revisions and sends an exact analysis pin', async () => {
  const root = new TestElement('section'), wrong = saved(); wrong.source.version = 2;
  const request = vi.fn<typeof fetch>().mockResolvedValue(response({ error: 'Synthetic refusal' }, false));
  const view = new TableDerivedView(root as unknown as HTMLElement, { current: () => true, request, analyses: async () => [wrong, saved()] });
  view.setup('graph', pin, table(), owner); await Promise.resolve(); await Promise.resolve();
  choose(root, 'Graph type', 'box'); choose(root, 'Value', 'area'); choose(root, 'Saved analysis annotations', '0');
  expect(field(root, 'Saved analysis annotations').children).toHaveLength(2);
  await view.calculate(); expect(JSON.parse(String(request.mock.calls[0][1]!.body)).spec.analysis).toEqual({ id: 'an1', version: 1 });
});
test('PNG rasterizes the saved SVG at its exact dimensions and releases its owned URL on success or failure', async () => {
  const decode = vi.fn().mockResolvedValue(undefined), draw = vi.fn(), png = new Blob(['synthetic pixels'], { type: 'image/png' });
  vi.stubGlobal('Image', class { src = ''; decode = decode; });
  const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: draw }), toBlob: (callback: BlobCallback, type: string) => { expect(type).toBe('image/png'); callback(png); } };
  vi.stubGlobal('document', { createElement: () => canvas });
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:owned'), revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  try {
    expect(await graphPNG('<svg width="960" height="580"></svg>')).toBe(png); expect(canvas.width).toBe(960); expect(canvas.height).toBe(580); expect(draw).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith('blob:owned'); decode.mockRejectedValueOnce(new Error('decoder failed'));
    await expect(graphPNG('<svg width="960" height="580"></svg>')).rejects.toThrow('decoder failed'); expect(revoke).toHaveBeenCalledTimes(2);
  } finally { create.mockRestore(); revoke.mockRestore(); }
});

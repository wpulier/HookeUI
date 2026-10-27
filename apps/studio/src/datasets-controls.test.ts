// Actual production controls over synthetic DOM/HTTP failures; not browser or storage evidence.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { DatasetsView } from './datasets';
import { TestElement, fire } from './workspace-windows.test-support';
import type { DatasetRow } from './protocol';

let active: ElementNode | null = null;
class ElementNode extends TestElement {
  get childElementCount() { return this.children.length; }
  focus() { active = this; }
  blur() { if (active === this) active = null; this.dispatchEvent(new Event('blur')); }
  select() {}
  replaceWith(node: ElementNode) {
    const parent = this.parentElement; if (!parent) return;
    const i = parent.children.indexOf(this); this.remove(); parent.children.splice(i, 0, node); node.parentElement = parent;
  }
}
class InputNode extends ElementNode { constructor() { super('input'); } }
const row: DatasetRow = { id:'ds-20261010-000000', name:'Original dataset', status:'complete', startedAt:null,
  stoppedAt:null, startedAtEpoch:1, stoppedAtEpoch:2, endedBy:'user', reconstructed:false, runs:1,
  gaps:[], parts:[], counts:{events:1}, rig:{}, pictures:0, files:{}, bytes:0, path:'/private/test-dataset' };
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
beforeEach(() => {
  active = null;
  vi.stubGlobal('document', { createElement:(tag:string) => tag === 'input' ? new InputNode() : new ElementNode(tag), get activeElement() { return active; } });
  vi.stubGlobal('HTMLInputElement', InputNode);
  vi.stubGlobal('CSS', { escape:(text:string) => text });
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok:true, json:async () => ({ datasets:[row], root:'/private/datasets' }) })));
});
afterEach(() => vi.unstubAllGlobals());
function fixture() {
  const root = new ElementNode('main');
  const rename = vi.fn(async () => { throw new Error('disk unavailable'); });
  const trash = vi.fn(async () => { throw new Error('dataset is collecting'); });
  const reveal = vi.fn(async () => { throw new Error('Finder unavailable'); });
  const view = new DatasetsView(root as unknown as HTMLElement, rename, reveal, async () => {}, trash);
  const control = (label:string) => { const button = root.querySelectorAll('button').find(n => n.textContent === label); if (!button) throw new Error(`Missing ${label}`); return button; };
  return {root, view, rename, trash, reveal, control};
}

test('a failed listing is visible instead of crashing on an HTTP error body', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:false, status:503, json:async()=>({error:'offline'})})));
  const {root, view} = fixture(); await view.load();
  expect(root.textContent).toContain('Dataset service returned 503');
});

test('failed rename retains original dataset and reports why it was not saved', async () => {
  const {root, view, rename, control} = fixture(); await view.load();
  fire(control('Rename'), 'click');
  const input = root.querySelector('.ds-name-input')!;
  input.value = 'New name'; fire(input, 'keydown', {key:'Enter'}); await settle();
  expect(rename).toHaveBeenCalledWith(row.id, 'New name');
  expect(root.textContent).toContain('Name was not saved: disk unavailable');
  expect(root.textContent).toContain('Original dataset');
  expect(root.querySelector('.ds-name-input')).toBeNull();
});

test('failed trash preserves its row, prevents duplicate submission and reports why', async () => {
  const {root, view, trash, control} = fixture(); await view.load();
  fire(control('Move to trash'), 'click');
  const confirm = control('Move to trash'); fire(confirm, 'click'); fire(confirm, 'click'); await settle();
  expect(trash).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('Dataset was not moved to trash: dataset is collecting');
  expect(root.textContent).toContain('Original dataset');
  expect(root.querySelector('.ds-leaving')).toBeNull();
});

test('Finder failure is visible and leaves the retained dataset intact', async () => {
  const {root, view, control} = fixture(); await view.load();
  fire(control('Show in Finder'), 'click'); await settle();
  expect(root.textContent).toContain('Could not show this dataset: Finder unavailable');
  expect(root.textContent).toContain('Original dataset');
});

test.each(['button', 'Escape'])('cancelling trash with %s returns focus to the retained dataset action', async (method) => {
  const {root, view, trash, control} = fixture(); await view.load();
  fire(control('Move to trash'), 'click');
  if (method === 'button') fire(control('Keep it'), 'click');
  else fire(root.querySelector('.ds-confirm')!, 'keydown', {key:'Escape'});
  expect(root.querySelector('.ds-confirm')).toBeNull();
  expect(active).toBe(control('Move to trash'));
  expect(trash).not.toHaveBeenCalled();
});

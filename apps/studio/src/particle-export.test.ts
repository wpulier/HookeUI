import { afterEach, expect, test, vi } from 'vitest';
import { bindParticleExport } from './particle-export';
import { TestElement, fire } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());
function prepare() {
  const host = new TestElement('div');
  vi.stubGlobal('document', {createElement: (tag: string) => new TestElement(tag)});
  const create = vi.fn(() => 'blob:verified');
  vi.stubGlobal('URL', {createObjectURL: create, revokeObjectURL: vi.fn()});
  let selected = {id: 'e2', session: '2026-10-13', revision: 3, status: 'saved'};
  const control = bindParticleExport(host as unknown as HTMLElement, () => selected);
  control.sync();
  return {host, create, control, change: () => {selected = {...selected, revision: 4}; control.sync();}};
}
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test('exports the displayed saved revision and offers a validated whole ZIP', async () => {
  const {host, create} = prepare();
  const fetch = vi.fn(async (_url: string, _options: unknown) => ({ok: true, blob: async () => new Blob(['zip'])}));
  vi.stubGlobal('fetch', fetch);
  fire(host.querySelector('.particle-export')!, 'click'); await settle();
  expect(fetch.mock.calls[0][0]).toBe('/api/resources/e2/exchange?session=2026-10-13&revision=3');
  expect(create).toHaveBeenCalledOnce();
  expect(host.querySelector('a')!.hidden).toBe(false);
});

test('superseding or cancelling an export never offers a stale download', async () => {
  const {host, create, change} = prepare();
  let complete!: (value: unknown) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => {complete = resolve;})));
  fire(host.querySelector('.particle-export')!, 'click'); change();
  complete({ok: true, blob: async () => new Blob(['stale'])}); await settle();
  expect(create).not.toHaveBeenCalled();
  expect(host.querySelector('a')!.hidden).toBe(true);
  fire(host.querySelector('.particle-export')!, 'click');
  fire(host.querySelector('.particle-export-cancel')!, 'click');
  complete({ok: true, blob: async () => new Blob(['cancelled'])}); await settle();
  expect(create).not.toHaveBeenCalled();
});

test('an unavailable retained result shows the refusal and remains retryable', async () => {
  const {host} = prepare();
  vi.stubGlobal('fetch', vi.fn(async () => ({ok: false, json: async () => ({error: 'masks.npz is missing or changed'})})));
  fire(host.querySelector('.particle-export')!, 'click'); await settle();
  expect(host.textContent).toContain('masks.npz is missing or changed');
  expect(host.querySelector('.particle-export')!.disabled).toBe(false);
});

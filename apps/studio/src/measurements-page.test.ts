// Synthetic renderer/network ordering evidence, not a browser or scientific-data oracle.
import { afterEach, expect, test, vi } from 'vitest';
import { MeasurementsPage } from './measurements';
import { syntheticDOM } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());
const table = (id: string, session = '2026-10-13', revision = 0) => ({ id, session, revision, kind: 'population', title: id,
  memberLabel: 'particle', count: 1, note: 'Original image pixels', columns: [{ key: 'cell', label: 'Particle', unit: '', type: 'text' }],
  rows: [{ cell: `${id}-particle` }], stats: [] });
const response = (data: unknown) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
function setup() {
  const dom = syntheticDOM(); let session = '2026-10-13';
  vi.stubGlobal('document', dom.document);
  const root = dom.document.createElement('section');
  const page = new MeasurementsPage(root as unknown as HTMLElement, { showOnView: vi.fn(), session: () => session });
  const pending = new Map<string, (r: Response) => void>();
  vi.stubGlobal('fetch', (url: string) => {
    if (url.includes('/table')) return new Promise<Response>(resolve => pending.set(url.split('/')[3], resolve));
    return Promise.resolve(response({ day: session, entries: url.includes('kind=tracks') ? [] :
      ['e1', 'e2'].map(id => ({ id, day: session, version: 0, kind: 'population', title: id })) }));
  });
  return { root, page, pending, session: (s: string) => { session = s; } };
}
async function tick() { for (let n = 0; n < 15; n++) await Promise.resolve(); }

test('a superseded read cannot overwrite the newer selected table', async () => {
  const w = setup(); const first = w.page.show('e1'); await tick();
  const second = w.page.show('e2'); await tick();
  w.pending.get('e2')!(response(table('e2'))); await second;
  w.pending.get('e1')!(response(table('e1'))); await first;
  expect(w.root.textContent).toContain('e2-particle');
  expect(w.root.textContent).not.toContain('e1-particle');
});

test('changing session during a read exposes no rows or download from the previous session', async () => {
  const w = setup(); const read = w.page.show('e1'); await tick();
  w.session('2026-10-13.2');
  w.pending.get('e1')!(response(table('e1'))); await read;
  expect(w.root.textContent).not.toContain('e1-particle');
  expect(w.root.textContent).not.toContain('Download CSV');
});

test('a correction invalidating a CSV request offers reopening instead of downloading other rows', async () => {
  const w = setup(); const read = w.page.show('e1'); await tick();
  w.pending.get('e1')!(response(table('e1'))); await read;
  const urls: string[] = [];
  vi.stubGlobal('fetch', async (url: string) => { urls.push(url); return new Response(JSON.stringify({ error: 'The measurements revision changed. Reopen Measurements before downloading.' }), { status: 409 }); });
  w.root.querySelector('.meas-primary')!.dispatchEvent(new Event('click'));
  await tick();
  expect(urls[0]).toBe('/api/resources/e1/table.csv?session=2026-10-13&revision=0');
  expect(w.root.textContent).toContain('revision changed');
  expect(w.root.textContent).toContain('Reopen measurements');
  expect(w.root.textContent).not.toContain('Download CSV');
});

test('an unavailable requested result never substitutes the newest result', async () => {
  const w = setup(); await w.page.show('e999');
  expect(w.root.textContent).toContain('unavailable');
  expect(w.pending.size).toBe(0);
});

test('leaving Measurements cancels the read and its late response cannot restore a table', async () => {
  const w = setup(); const read = w.page.show('e1'); await tick();
  w.page.hide(); w.pending.get('e1')!(response(table('e1'))); await read;
  expect(w.root.textContent).not.toContain('e1-particle');
});

test('the returned session and revision must match the selected table', async () => {
  const w = setup(); const read = w.page.show('e1'); await tick();
  w.pending.get('e1')!(response(table('e1', '2026-10-13', 1))); await read;
  expect(w.root.textContent).toContain('measurements changed');
  expect(w.root.textContent).not.toContain('e1-particle');
});

test('an Actions link from another session cannot resolve a same-named result here', async () => {
  const w = setup(); await w.page.show('e1', { session: '2026-10-12', revision: 0 });
  expect(w.root.textContent).toContain('Open the result’s project');
  expect(w.pending.size).toBe(0);
});

test('a correction notification withdraws the old table and download until it is reopened', async () => {
  const w = setup(); const read = w.page.show('e1'); await tick();
  w.pending.get('e1')!(response(table('e1'))); await read;
  w.page.evidenceChanged('e1', { session: '2026-10-13', revision: 1 });
  expect(w.root.textContent).toContain('revision changed');
  expect(w.root.textContent).not.toContain('e1-particle');
  expect(w.root.textContent).not.toContain('Download CSV');
});

test('a deep link waits for the session handshake and then reads its selected result', async () => {
  const w = setup(); w.session('');
  await w.page.show('e1');
  expect(w.pending.size).toBe(0);
  w.session('2026-10-13');
  const ready = w.page.sessionAvailable(); await tick();
  w.pending.get('e1')!(response(table('e1'))); await ready;
  expect(w.root.textContent).toContain('e1-particle');
});

test('the loaded callback follows successful rendered exact measurements, never a failed or stale table', async () => {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const root = dom.document.createElement('section');
  const rendered: string[] = [];
  const loaded = vi.fn(() => rendered.push(root.textContent));
  const page = new MeasurementsPage(root as unknown as HTMLElement, { session: () => '2026-10-13', showOnView: vi.fn(), loaded });
  vi.stubGlobal('fetch', async (url: string) => response(url.includes('/table') ? table('e1') : { day: '2026-10-13', entries: url.includes('kind=tracks') ? [] : [{ id: 'e1', day: '2026-10-13', version: 0 }] }));
  await page.show('e1', { session: '2026-10-13', revision: 0 });
  expect(loaded).toHaveBeenCalledTimes(1);
  expect(rendered[0]).toContain('e1-particle');
  await page.show('e1', { session: '2026-10-13', revision: 1 });
  expect(loaded).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('measurements changed');
});

test('a capability’s kept table is listed and shown, with nothing to show on the view', async () => {
  // Live 2026-10-15: Spot measure's answer said "shown in the measurements table" and the page called d6 unavailable,
  // since it listed only populations and tracks.
  const dom = syntheticDOM(); const session = '2026-10-13';
  vi.stubGlobal('document', dom.document);
  const root = dom.document.createElement('section');
  const page = new MeasurementsPage(root as unknown as HTMLElement, { showOnView: vi.fn(), session: () => session });
  const urls: string[] = [];
  vi.stubGlobal('fetch', async (url: string) => {
    urls.push(url);
    if (url.includes('/table')) return response({ ...table('d6'), kind: 'result-table', memberLabel: 'row' });
    return response({ day: session, entries: !url.includes('kind=result') ? [] : [
      { id: 'd6', day: session, kind: 'result', title: 'Spot measure · 19:32', table: true },
      { id: 'd7', day: session, kind: 'result', title: 'Clearer picture · 19:33' }] });
  });
  await page.show('d6');
  expect(urls).toContain('/api/resources/d6/table?session=2026-10-13&revision=0');
  expect(root.textContent).toContain('d6-particle');
  expect(root.textContent).toContain('Download CSV');
  expect(root.textContent).not.toContain('Show on the view');
  expect(root.textContent).not.toContain('unavailable');
});

test('each summary card shows the mean ± SD under the median, and no mean line when the server sent none', async () => {
  const w = setup(); const read = w.page.show('e1'); await tick();
  const area = { key: 'areaPx', label: 'Area', unit: 'µm²', type: 'number' };
  const width = { key: 'feretMinPx', label: 'Width', unit: 'µm', type: 'number' };
  w.pending.get('e1')!(response({ ...table('e1'), columns: [...table('e1').columns, area, width],
    stats: [{ key: 'areaPx', n: 5, median: 412.5, q1: 388, q3: 455, min: 301.75, max: 520.25, mean: 415.5, sd: 81.25, sem: 36.34, ci95: [314.6, 516.4] },
      { key: 'feretMinPx', n: 5, median: 9.1, q1: 8, q3: 10, min: 7, max: 12 }] }));
  await read;
  const cards = [...w.root.querySelectorAll('.meas-stat')].map((c) => c.textContent);
  expect(cards[0]).toContain('413 µm² (middle half 388–455)');
  expect(cards[0]).toContain('mean 416 ± 81.3 µm² (SD)');
  expect(cards[1]).not.toContain('mean');
  expect(w.root.querySelectorAll('.meas-stat-mean').length).toBe(1);
});

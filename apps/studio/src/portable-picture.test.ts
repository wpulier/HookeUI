// Real download control over synthetic DOM/HTTP, with separate original and portable URLs.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { TestElement, fire } from './workspace-windows.test-support';
import { portablePictureControls } from './portable-picture';

beforeEach(() => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:portable');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test('the adjacent download control requests a portable copy and retains a separate original link', async () => {
  const fetcher = vi.fn(async () => new Response(new Blob(['portable bytes'], { type: 'image/png' }),
    { headers: { 'X-Carried-Entries': '1' } }));
  vi.stubGlobal('fetch', fetcher);
  const host = portablePictureControls('/api/resources/d1/share?day=session', '/api/resources/d1/picture?day=session');
  const node = host as unknown as TestElement;
  const button = node.querySelector('button')!;
  fire(button, 'click');
  await vi.waitFor(() => expect(button.disabled).toBe(false));
  expect(fetcher).toHaveBeenCalledWith('/api/resources/d1/share?day=session');
  const links = node.querySelectorAll('a') as unknown as HTMLAnchorElement[];
  expect(links[0].href).toBe('/api/resources/d1/picture?day=session');
  expect(links[1].href).toBe('blob:portable');
  expect(links[1].download).toBe('portable-result.png');
  expect(node.textContent).toContain('a separate copy carrying the capability that made it');
});

test('a copy that carries no entry says it only names what made it (Standards review of 64f49d1b, P3-1)', async () => {
  vi.stubGlobal('fetch', async () => new Response(new Blob(['portable bytes'], { type: 'image/png' }),
    { headers: { 'X-Carried-Entries': '0' } }));
  const node = portablePictureControls('/api/library/a1/share', '/api/library/a1/file') as unknown as TestElement;
  fire(node.querySelector('button')!, 'click');
  await vi.waitFor(() => expect(node.textContent).toContain('naming what made it'));
  expect(node.textContent).not.toContain('carrying');
});

test('a failure that is not the app\u2019s JSON still says the original is kept', async () => {
  vi.stubGlobal('fetch', async () => new Response('<html>Bad gateway</html>', { status: 502 }));
  const node = portablePictureControls('/api/library/a1/share', '/api/library/a1/file') as unknown as TestElement;
  fire(node.querySelector('button')!, 'click');
  await tick();
  expect(node.textContent).toContain('Your original is kept.');
});

test('a precise preparation failure leaves the original download available', async () => {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ error: 'Original kept. Entry cache is missing.' }), { status: 409 }));
  const node = portablePictureControls('/api/library/a1/share', '/api/library/a1/file') as unknown as TestElement;
  fire(node.querySelector('button')!, 'click');
  await tick();
  expect(node.textContent).toContain('Original kept. Entry cache is missing.');
  expect((node.querySelectorAll('a')[0] as unknown as HTMLAnchorElement).href).toBe('/api/library/a1/file');
  expect(node.querySelectorAll('a')[1].hidden).toBe(true);
});

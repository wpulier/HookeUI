// Synthetic rendering and delayed responses; real browser verification is separate.
import { afterEach, expect, test, vi } from 'vitest';
import { openProjectFolder, type ProjectStorage } from './project-folder';
import { syntheticDOM } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());
function dom() {
  const fixture = syntheticDOM(), create = fixture.document.createElement;
  fixture.document.createElement = (tag: string) => {
    const result = create(tag);
    return Object.assign(result, { showModal: vi.fn(), close: () => result.dispatchEvent(new Event('close')) });
  };
  vi.stubGlobal('document', fixture.document); return fixture;
}
const status: ProjectStorage = {session: 'p', origin: 'a'.repeat(32), location: '/Projects/Research',
  layout: 'project-folder', availability: 'available', reason: null,
  collection: {state: 'not-assessed', reason: 'Complete dependencies have not been verified.'}};

test('uses retained location and reports collection separately from availability', async () => {
  dom(); const reveal = vi.fn(async () => ({ok: true, path: status.location!}));
  const dialog = openProjectFolder({title: 'Research', session: 'p', current: () => true,
    read: async () => ({storage: status}), reveal});
  await vi.waitFor(() => expect(dialog.textContent).toContain('/Projects/Research'));
  expect(dialog.textContent).toContain('complete portable copy has not yet been verified');
  const button = Array.from(dialog.querySelectorAll('button')).find(row => row.textContent === 'Reveal in Finder')!;
  button.dispatchEvent(new Event('click'));
  await vi.waitFor(() => expect(dialog.textContent).toContain('Location revealed'));
  expect(reveal).toHaveBeenCalledOnce();
});

test('late Project responses cannot populate a different opening or reveal files', async () => {
  dom(); let current = true;
  let resolve!: (value: {storage: ProjectStorage}) => void;
  const reveal = vi.fn();
  const dialog = openProjectFolder({title: 'Research', session: 'p', current: () => current,
    read: () => new Promise(done => { resolve = done; }), reveal});
  current = false; resolve({storage: status});
  await vi.waitFor(() => expect(dialog.textContent).toContain('Project changed'));
  expect(dialog.textContent).not.toContain('/Projects/Research');
  dialog.querySelector('footer')!.querySelector('button')!.dispatchEvent(new Event('click'));
  expect(reveal).not.toHaveBeenCalled();
});

test.each(['project-folder', 'legacy', 'unavailable'] as const)('unavailable %s does not claim accessible work and offers retry', async layout => {
  dom(); const reveal = vi.fn();
  const dialog = openProjectFolder({title: 'Research', session: 'p', current: () => true,
    read: async () => ({storage: {...status, layout, availability: 'unavailable', reason: 'Folder missing'}}), reveal});
  await vi.waitFor(() => expect(dialog.textContent).toContain('Folder missing'));
  expect((dialog.querySelector('footer')!.querySelector('button') as HTMLButtonElement).disabled).toBe(true);
  expect(dialog.textContent).toContain('not been replaced or recreated');
  expect(dialog.textContent).not.toContain('work remains available');
  expect(dialog.textContent).not.toContain('Saved in Hooke');
  expect(dialog.textContent).toContain('Project location unavailable');
  expect(dialog.textContent).toContain('Retry');
});

test('failed reads retry without losing the original Project identity', async () => {
  dom(); const read = vi.fn().mockRejectedValueOnce(new Error('Disconnected')).mockResolvedValue({storage: status});
  const dialog = openProjectFolder({title: 'Research', session: 'p', current: () => true, read, reveal: vi.fn()});
  await vi.waitFor(() => expect(dialog.textContent).toContain('Disconnected'));
  Array.from(dialog.querySelectorAll('button')).find(row => row.textContent === 'Retry')!.dispatchEvent(new Event('click'));
  await vi.waitFor(() => expect(dialog.textContent).toContain('/Projects/Research'));
  expect(read).toHaveBeenCalledTimes(2);
});

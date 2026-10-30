// Synthetic dialog lifecycle; folder integrity and browser use are checked separately.
import {afterEach, expect, test, vi} from 'vitest';
import {showOpenProject, type OpenedProject} from './project-open';
import {syntheticDOM} from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());
function dom() {
  const fixture = syntheticDOM(), create = fixture.document.createElement;
  fixture.document.createElement = (tag: string) => {
    const result = create(tag);
    return Object.assign(result, {showModal: vi.fn(), focus: vi.fn(), close: () => result.dispatchEvent(new Event('close'))});
  };
  vi.stubGlobal('document', fixture.document);
}
const project: OpenedProject = {session: 'p1', origin: 'a'.repeat(32), name: 'Research', location: '/Projects/Research'};
function submit(dialog: HTMLDialogElement) {
  const input = dialog.querySelector('input')!; input.value = '/Projects/Research';
  dialog.querySelector('form')!.dispatchEvent(new Event('submit', {cancelable: true}));
}
test('verified registration navigates through the normal unsaved-work gate only once', async () => {
  dom(); const open = vi.fn(async () => project), navigate = vi.fn(async () => false);
  const dialog = showOpenProject({current: () => true, open, navigate});
  submit(dialog); submit(dialog);
  await vi.waitFor(() => expect(navigate).toHaveBeenCalledExactlyOnceWith('p1'));
  expect(open).toHaveBeenCalledExactlyOnceWith('/Projects/Research');
  expect(document.body.contains(dialog)).toBe(false);
});
test.each(['closed', 'navigated'])('late registration after %s never moves the workspace', async kind => {
  dom(); let current = true, done!: (value: OpenedProject) => void;
  const navigate = vi.fn();
  const dialog = showOpenProject({current: () => current, open: () => new Promise(resolve => {done = resolve;}), navigate});
  submit(dialog);
  if (kind === 'closed') dialog.close(); else current = false;
  done(project); await Promise.resolve(); await Promise.resolve();
  expect(navigate).not.toHaveBeenCalled();
  if (kind === 'navigated') expect(dialog.textContent).toContain('now in All Projects');
});
test('refusal preserves the path for recovery and a subsequent retry', async () => {
  dom(); const open = vi.fn().mockRejectedValueOnce(new Error('Another host is writing')).mockResolvedValue(project);
  const navigate = vi.fn(async () => true);
  const dialog = showOpenProject({current: () => true, open, navigate});
  submit(dialog); await vi.waitFor(() => expect(dialog.textContent).toContain('Another host is writing'));
  expect(dialog.querySelector('input')!.value).toBe('/Projects/Research');
  submit(dialog); await vi.waitFor(() => expect(navigate).toHaveBeenCalledOnce());
});

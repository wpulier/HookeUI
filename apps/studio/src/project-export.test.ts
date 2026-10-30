// Synthetic presentation/transport checks. Real folder and browser journeys are separate.
import {afterEach, expect, test, vi} from 'vitest';
import {projectExport, type ExportActions, type ExportOperation} from './project-export';
import {syntheticDOM} from './workspace-windows.test-support';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
function setup() {
  vi.stubGlobal('document', syntheticDOM().document);
  const saved = new Map<string, string>();
  vi.stubGlobal('localStorage', {getItem: (key: string) => saved.get(key), setItem: (key: string, value: string) => saved.set(key, value), removeItem: (key: string) => saved.delete(key)});
  const actions: ExportActions = {
    prepare: vi.fn(async (): Promise<Awaited<ReturnType<ExportActions['prepare']>>> => ({files: 12, bytes: 4096, requiredFreeBytes: 100000,
      collection: {state: 'unverified', reason: 'Keep the original Project.'}})),
    start: vi.fn(async () => ({id: 'export-1', state: 'running'})),
    read: vi.fn(async () => ({id: 'export-1', state: 'running'})),
    stop: vi.fn(async () => ({id: 'export-1', state: 'cancelRequested'})), reveal: vi.fn(async () => {}),
  };
  return {actions, saved};
}
function click(element: HTMLElement, text: string) {
  const button = Array.from(element.querySelectorAll('button')).find(button => button.textContent === text);
  expect(button, text).toBeDefined(); button!.dispatchEvent(new Event('click'));
}

test('requires scope review, retries a lost admission with the same key, and recovers without restarting', async () => {
  const {actions} = setup();
  vi.mocked(actions.start).mockRejectedValueOnce(new Error('Reply lost'));
  const first = projectExport(actions, () => true, 'origin');
  click(first.element, 'Review export');
  await vi.waitFor(() => expect(first.element.textContent).toContain('not a fully collected Project'));
  expect(actions.start).not.toHaveBeenCalled();
  click(first.element, 'Export this partial copy');
  await vi.waitFor(() => expect(first.element.textContent).toContain('Reply lost'));
  click(first.element, 'Retry this export');
  await vi.waitFor(() => expect(first.element.textContent).toContain('Export admitted'));
  expect(vi.mocked(actions.start).mock.calls[0][0]).toBe(vi.mocked(actions.start).mock.calls[1][0]);
  first.dispose();
  const reopened = projectExport(actions, () => true, 'origin');
  await vi.waitFor(() => expect(actions.read).toHaveBeenCalledWith('export-1'));
  expect(actions.start).toHaveBeenCalledTimes(2);
  reopened.dispose();
});

test('retains verified output and its incompleteness rather than offering collection as complete', async () => {
  const {actions, saved} = setup(); saved.set('hooke.project-export.origin', 'export-1');
  vi.mocked(actions.read).mockResolvedValue({id: 'export-1', state: 'succeeded', outcome: {export: {
    location: '/Projects/Exports/copy', files: 12, bytes: 4096, manifestSha256: 'a'.repeat(64),
    collection: {state: 'unverified', reason: 'Keep the original Project.'},
  }}});
  const view = projectExport(actions, () => true, 'origin');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Folder copy saved and verified'));
  expect(view.element.textContent).toContain('Keep the original Project');
  click(view.element, 'Reveal copy');
  await vi.waitFor(() => expect(actions.reveal).toHaveBeenCalledWith('export-1'));
  view.dispose();
});

test('lost admission reply survives closing the panel and takes precedence over an older export', async () => {
  const {actions, saved} = setup();
  vi.mocked(actions.start).mockRejectedValueOnce(new Error('Reply lost'));
  const view = projectExport(actions, () => true, 'origin'); click(view.element, 'Review export');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Export this partial copy'));
  click(view.element, 'Export this partial copy');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Reply lost'));
  view.dispose(); saved.set('hooke.project-export.origin', 'old-export');
  const reopened = projectExport(actions, () => true, 'origin');
  expect(actions.read).not.toHaveBeenCalled();
  click(reopened.element, 'Recover export');
  await vi.waitFor(() => expect(reopened.element.textContent).toContain('Export admitted'));
  expect(vi.mocked(actions.start).mock.calls[0][0]).toBe(vi.mocked(actions.start).mock.calls[1][0]);
  expect(saved.has('hooke.project-export.origin.pending')).toBe(false);
  reopened.dispose();
});

test('an old panel acknowledgement cannot erase a newer export recovery key', async () => {
  const {actions, saved} = setup();
  let oldReply!: (value: ExportOperation) => void;
  let newReply!: (value: ExportOperation) => void;
  vi.mocked(actions.start)
    .mockImplementationOnce(() => new Promise(done => { oldReply = done; }))
    .mockResolvedValueOnce({id: 'export-1', state: 'failed', outcome: {says: 'First copy stopped'}})
    .mockImplementationOnce(() => new Promise(done => { newReply = done; }));
  const first = projectExport(actions, () => true, 'origin'); click(first.element, 'Review export');
  await vi.waitFor(() => expect(first.element.textContent).toContain('Export this partial copy'));
  click(first.element, 'Export this partial copy'); first.dispose();
  const second = projectExport(actions, () => true, 'origin'); click(second.element, 'Recover export');
  await vi.waitFor(() => expect(second.element.textContent).toContain('First copy stopped'));
  click(second.element, 'Prepare another copy');
  await vi.waitFor(() => expect(second.element.textContent).toContain('Export this partial copy'));
  click(second.element, 'Export this partial copy');
  const latestKey = saved.get('hooke.project-export.origin.pending');
  expect(latestKey).toBeTruthy();
  oldReply({id: 'export-1', state: 'running'});
  await Promise.resolve(); await Promise.resolve();
  expect(saved.get('hooke.project-export.origin.pending')).toBe(latestKey);
  newReply({id: 'export-2', state: 'running'});
  await vi.waitFor(() => expect(saved.get('hooke.project-export.origin')).toBe('export-2'));
  second.dispose();
});

test('late polling cannot undo an observed stop and disposal never cancels admitted work', async () => {
  const {actions, saved} = setup(); saved.set('hooke.project-export.origin', 'export-1');
  vi.useFakeTimers();
  const view = projectExport(actions, () => true, 'origin');
  await Promise.resolve(); await Promise.resolve();
  let resolve!: (value: ExportOperation) => void;
  vi.mocked(actions.read).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await vi.advanceTimersByTimeAsync(1000);
  click(view.element, 'Stop export');
  await Promise.resolve(); await Promise.resolve();
  expect(view.element.textContent).toContain('Stopping');
  resolve({id: 'export-1', state: 'running'});
  await Promise.resolve(); await Promise.resolve();
  expect(view.element.textContent).toContain('Stopping');
  view.dispose(); await vi.advanceTimersByTimeAsync(3000);
  expect(actions.stop).toHaveBeenCalledTimes(1);
  expect(actions.read).toHaveBeenCalledTimes(2);
});

test('changing Project during preparation cannot expose an export action for stale material', async () => {
  const {actions} = setup(); let current = true;
  let finish!: (value: Awaited<ReturnType<ExportActions['prepare']>>) => void;
  vi.mocked(actions.prepare).mockImplementationOnce(() => new Promise(done => { finish = done; }));
  const view = projectExport(actions, () => current, 'origin'); click(view.element, 'Review export'); current = false;
  finish({files: 12, bytes: 4096, requiredFreeBytes: 100000, collection: {state: 'unverified', reason: 'Original'}});
  await Promise.resolve(); await Promise.resolve();
  expect(view.element.textContent).not.toContain('Export this partial copy');
  expect(actions.start).not.toHaveBeenCalled(); view.dispose();
});

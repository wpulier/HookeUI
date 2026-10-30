import {afterEach, expect, test, vi} from 'vitest';
import {recordInclusion, type RecordActions, type RecordOperation, type RecordReview} from './project-record-inclusion';
import {syntheticDOM} from './workspace-windows.test-support';

afterEach(() => { vi.unstubAllGlobals(); });
function setup() {
  vi.stubGlobal('document', syntheticDOM().document);
  const memory = new Map<string, string>();
  vi.stubGlobal('localStorage', {getItem: (key: string) => memory.get(key), setItem: (key: string, value: string) => memory.set(key, value)});
  const done: RecordOperation = {id: 'op-1', project: 'project-1', state: 'succeeded', outcome: {inclusion: {reference: {session: 'project-1', id: 'retained-1', revision: 1, digest: 'digest'}}}};
  const actions: RecordActions = {
    list: vi.fn(async () => [{id: 'ds-1', status: 'complete', name: 'Shared capture'}]),
    review: vi.fn(async (id: string): Promise<RecordReview> => ({session: 'project-1', origin: 'origin-1', dataset: id, title: 'Shared capture', scope: 'whole-record-folder', reviewDigest: 'hash', files: 6, bytes: 2000, requiredFreeBytes: 6000, captureCheck: {ok: false, problems: ['Missing interval']}, reason: 'Includes work from other Projects.'})),
    start: vi.fn(async () => done), read: vi.fn(async () => done), stop: vi.fn(async () => done),
  };
  return {actions, memory, done};
}
function click(element: HTMLElement, title: string) {
  const button = Array.from(element.querySelectorAll('button')).find(row => row.textContent === title);
  expect(button, title).toBeDefined(); button!.dispatchEvent(new Event('click'));
}
async function review(element: HTMLElement) {
  click(element, 'Choose recording');
  await vi.waitFor(() => expect(element.querySelector('select')).toBeTruthy());
  element.querySelector('select')!.value = 'ds-1';
  click(element, 'Review inclusion');
  await vi.waitFor(() => expect(element.textContent).toContain('Missing interval'));
}

test('requires whole-dataset review and explicit inclusion; retains original errors as context', async () => {
  const {actions} = setup();
  const view = recordInclusion(actions, 'project-1', 'origin-1', () => true);
  await review(view.element);
  expect(actions.start).not.toHaveBeenCalled();
  expect(view.element.textContent).toContain('other Projects');
  click(view.element, 'Include whole dataset');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Recorded dataset copied'));
  expect(actions.start).toHaveBeenCalledWith('ds-1', 'hash', expect.any(String));
  expect(view.element.textContent).toContain('retained-1'); view.dispose();
});

test('lost reply keeps the same confirmed identity across closing and reopening', async () => {
  const {actions} = setup(); vi.mocked(actions.start).mockRejectedValueOnce(new Error('Reply lost'));
  const first = recordInclusion(actions, 'project-1', 'origin-1', () => true);
  await review(first.element); click(first.element, 'Include whole dataset');
  await vi.waitFor(() => expect(first.element.textContent).toContain('Reply lost'));
  const original = vi.mocked(actions.start).mock.calls[0]; first.dispose();
  const second = recordInclusion(actions, 'project-1', 'origin-1', () => true);
  expect(actions.start).toHaveBeenCalledTimes(1);
  click(second.element, 'Retry this inclusion');
  await vi.waitFor(() => expect(second.element.textContent).toContain('Recorded dataset copied'));
  expect(vi.mocked(actions.start).mock.calls[1]).toEqual(original); second.dispose();
  const third = recordInclusion(actions, 'project-1', 'origin-1', () => true);
  await vi.waitFor(() => expect(actions.read).toHaveBeenCalledWith('op-1'));
  expect(actions.start).toHaveBeenCalledTimes(2); third.dispose();
});

test('navigation during review cannot offer inclusion in a different Project', async () => {
  const {actions} = setup(); let current = true;
  let finish!: (value: Awaited<ReturnType<RecordActions['review']>>) => void;
  const actual = await actions.review('ds-1');
  vi.mocked(actions.review).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = recordInclusion(actions, 'project-1', 'origin-1', () => current);
  click(view.element, 'Choose recording');
  await vi.waitFor(() => expect(view.element.querySelector('select')).toBeTruthy());
  view.element.querySelector('select')!.value = 'ds-1'; click(view.element, 'Review inclusion');
  current = false; finish(actual);
  await Promise.resolve();
  expect(view.element.textContent).not.toContain('Include whole dataset');
  expect(actions.start).not.toHaveBeenCalled(); view.dispose();
});

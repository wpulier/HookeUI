// Synthetic rendering of Publish and Start from; the server's review and sealing are tested in Python.
import { afterEach, expect, test, vi } from 'vitest';
import { openStartFrom, projectPublish, type PublicationReview, type PublishActions } from './project-publish';
import { syntheticDOM } from './workspace-windows.test-support';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
function dom() {
  const fixture = syntheticDOM(), create = fixture.document.createElement;
  fixture.document.createElement = (tag: string) => {
    const result = create(tag);
    return Object.assign(result, { showModal: vi.fn(), close: () => result.dispatchEvent(new Event('close')) });
  };
  vi.stubGlobal('document', fixture.document); return fixture;
}
const review: PublicationReview = {
  origin: 'a'.repeat(32), name: 'Research', licence: 'CC-BY-4.0', licenceName: 'Creative Commons Attribution 4.0',
  snapshotSha256: 'b'.repeat(64), reviewDigest: 'c'.repeat(64), staging: 'publication-0123456789ab', bytes: 2_000_000,
  recordFiles: 12, largeFiles: 2, memberCount: 14, members: [{path: 'project.json', bytes: 120, sha256: 'd'.repeat(64)}],
  leftOut: {conversations: '1 journal(s) reduced to their identity line and 3 line(s) that list results', transcripts: 3, otherFiles: []},
  publishedText: [{session: '2026-11-02', id: 'a1', version: 1, title: 'Division', text: '<b>Looks like</b> a dividing cell'}],
  includeTranscripts: false, findings: [{path: 'artifacts/results/r.json', line: 4, kind: 'home-path', blocks: false}], blocked: false,
};
function actions(overrides: Partial<PublishActions> = {}): PublishActions {
  return {
    prepare: vi.fn(async () => review),
    publish: vi.fn(async () => ({published: {commit: 'e'.repeat(40), snapshotSha256: review.snapshotSha256, recordFiles: 12, bulkObjects: 2, uploadedBytes: 1, bytes: 2}, location: 'https://github.com/ada/research'})),
    status: vi.fn(async () => ({published: [], startedFrom: null})),
    update: vi.fn(async () => ({added: [], extended: ['p1'], unchanged: [], refused: [], filesAdded: 1, fileConflicts: []})),
    github: {status: vi.fn(async () => ({setUp: true, signedIn: true, says: 'Signed in to GitHub'})), start: vi.fn(), poll: vi.fn()},
    ...overrides,
  };
}
const click = (root: Element, label: string) => {
  const b = Array.from(root.querySelectorAll('button')).find(row => row.textContent === label);
  if (!b) throw new Error(`no button ${label}: ${Array.from(root.querySelectorAll('button')).map(x => x.textContent).join(', ')}`);
  b.dispatchEvent(new Event('click')); return b as HTMLButtonElement;
};
const check = (root: Element, text: string) => {
  const row = Array.from(root.querySelectorAll('label')).find(l => l.textContent?.includes(text))!;
  const box = row.querySelector('input') as HTMLInputElement; box.checked = true; box.dispatchEvent(new Event('change'));
};

test('shows what is left out, waits for every sign-off, then sends the exact review', async () => {
  dom(); const a = actions();
  const view = projectPublish(a, () => true, 'Cells under stress');
  document.body.append(view.element);
  click(view.element, 'Review what will be published');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Conversations: 1 journal(s) reduced'));
  expect(view.element.textContent).toContain('Research transcripts left out: 3');
  expect(view.element.textContent).toContain('Your notes, published as written');
  const note = Array.from(view.element.querySelectorAll('li')).find(li => li.textContent?.startsWith('Division'))!;
  expect(note.textContent).toBe('Division: <b>Looks like</b> a dividing cell'); // as text, never markup
  expect(note.querySelectorAll('b').length).toBe(0);
  expect(view.element.textContent).toContain('names a folder in your home folder (check it)');
  await vi.waitFor(() => expect(a.github.status).toHaveBeenCalled());
  const send = Array.from(view.element.querySelectorAll('button')).find(b => b.textContent === 'Publish') as HTMLButtonElement;
  expect(send.disabled).toBe(true);
  check(view.element, 'right to share'); check(view.element, 'identifiable human data');
  expect(send.disabled).toBe(true);
  check(view.element, 'not erased');
  await vi.waitFor(() => expect(send.disabled).toBe(false));
  click(view.element, 'Publish');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Published and checked.'));
  expect(a.publish).toHaveBeenCalledWith(review, {rights: true, noIdentifiableHumanData: true, permanent: true},
    {kind: 'github', create: true, name: 'cells-under-stress'});
  view.dispose();
});

test('a blocked review offers no way to publish', async () => {
  dom();
  const blocked = {...review, blocked: true, findings: [{path: 'artifacts/results/r.json', line: 2, kind: 'secret', blocks: true}]};
  const view = projectPublish(actions({prepare: vi.fn(async () => blocked)}), () => true, 'Research');
  click(view.element, 'Review what will be published');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Must be resolved'));
  expect(view.element.textContent).toContain('looks like a key or password');
  expect(Array.from(view.element.querySelectorAll('button')).some(b => b.textContent === 'Publish')).toBe(false);
});

test('GitHub sign-in shows the code and keeps waiting until it is approved', async () => {
  dom();
  let signed = false;
  const poll = vi.fn(async () => (poll.mock.calls.length > 1 ? (signed = true, {state: 'done', says: 'Signed in to GitHub'}) : {state: 'waiting', every: 0.3, says: 'Waiting for you to approve it on GitHub'}));
  const a = actions({github: {
    status: vi.fn(async () => ({setUp: true, signedIn: signed, says: signed ? 'Signed in to GitHub' : 'Sign in to GitHub to share with everyone'})),
    start: vi.fn(async () => ({id: 'h1', code: 'WDJB-MJHT', open: 'https://github.com/login/device', every: 0.3, until: 0})),
    poll,
  }});
  const view = projectPublish(a, () => true, 'Research');
  click(view.element, 'Review what will be published');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Sign in to GitHub to share with everyone'));
  click(view.element, 'Sign in to GitHub');
  await vi.waitFor(() => expect(view.element.textContent).toContain('WDJB-MJHT'));
  await vi.waitFor(() => expect(poll).toHaveBeenCalledTimes(2), {timeout: 3000});
  await vi.waitFor(() => expect(view.element.textContent).toContain('Signed in to GitHub'));
  view.dispose();
});

test('a started-from copy can update, and says plainly what it kept', async () => {
  dom();
  const a = actions({status: vi.fn(async () => ({published: [], startedFrom: {type: 'project-started-from'}})),
    update: vi.fn(async () => ({added: [], extended: ['p1'], unchanged: [], refused: [{id: 'p2', reason: 'diverged'}], filesAdded: 2, fileConflicts: [], resultsAdded: 3}))});
  const view = projectPublish(a, () => true, 'Research');
  await vi.waitFor(() => expect(Array.from(view.element.querySelectorAll('button')).some(b => b.textContent === 'Update from the published version')).toBe(true));
  click(view.element, 'Update from the published version');
  await vi.waitFor(() => expect(view.element.textContent).toContain('1 document updated, 3 results and 2 files added.'));
  expect(view.element.textContent).toContain('no longer follows the version here, so it was left as it was');
});

test('Start from takes a GitHub address or a folder and offers to open the copy', async () => {
  dom();
  const start = vi.fn(async () => ({project: {session: '2026-11-02', name: 'Copy', location: '/Projects/Copy'}}));
  const opened = vi.fn();
  const dialog = openStartFrom({start}, opened);
  const [source, name] = Array.from(dialog.querySelectorAll('input')) as HTMLInputElement[];
  source.value = 'github.com/ada/research'; name.value = 'Copy';
  click(dialog, 'Start from it');
  await vi.waitFor(() => expect(dialog.textContent).toContain('Started “Copy”'));
  expect(start).toHaveBeenCalledWith('github.com/ada/research', 'Copy');
  click(dialog, 'Open it');
  expect(opened).toHaveBeenCalledWith('2026-11-02');
  const again = openStartFrom({start});
  (again.querySelector('input') as HTMLInputElement).value = '/Volumes/Drive/Research';
  click(again, 'Start from it');
  await vi.waitFor(() => expect(start).toHaveBeenLastCalledWith({kind: 'folder', path: '/Volumes/Drive/Research'}, 'Started project'));
});

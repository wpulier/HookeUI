// Synthetic rendering of Back up and Restore; the server's sealing, sending and restoring are tested in Python.
import { afterEach, expect, test, vi } from 'vitest';
import { again, openRestore, projectBackup, type BackupActions, type BackupEntry } from './project-backup';
import { syntheticDOM } from './workspace-windows.test-support';

afterEach(() => { vi.unstubAllGlobals(); });
function dom() {
  const fixture = syntheticDOM(), create = fixture.document.createElement;
  fixture.document.createElement = (tag: string) => {
    const result = create(tag);
    return Object.assign(result, { showModal: vi.fn(), close: () => result.dispatchEvent(new Event('close')) });
  };
  vi.stubGlobal('document', fixture.document); return fixture;
}
const sent = {backup: {commit: 'a'.repeat(64), snapshotSha256: 'b'.repeat(64), recordFiles: 10, bulkFiles: 2, bulkObjects: 1, uploadedBytes: 2_000_000, bytes: 3_000_000}, location: '/Volumes/Backup/Hooke'};
const folderEntry: BackupEntry = {type: 'project-backed-up', commit: 'c'.repeat(64), snapshotSha256: 'd'.repeat(64), at: 1_793_700_000,
  destination: {records: {kind: 'folder', base: '/Volumes/Backup/Hooke'}}};
function actions(backups: BackupEntry[] = [], overrides: Partial<BackupActions> = {}): BackupActions {
  return {
    status: vi.fn(async () => ({backups})),
    backUp: vi.fn(async () => sent),
    remove: vi.fn(async () => ({purgeAfter: 1_796_300_000})),
    keep: vi.fn(async () => undefined),
    github: {status: vi.fn(async () => ({setUp: true, signedIn: true, says: 'Signed in to GitHub'})), start: vi.fn(), poll: vi.fn()},
    ...overrides,
  };
}
const field = (root: Element, label: string) =>
  Array.from(root.querySelectorAll('input')).find(i => i.getAttribute('aria-label') === label) as HTMLInputElement;
const radios = (root: Element) => Array.from(root.querySelectorAll('input')).filter(i => (i as HTMLInputElement).type === 'radio') as HTMLInputElement[];
const click = (root: Element, label: string) => {
  const b = Array.from(root.querySelectorAll('button')).find(row => row.textContent === label);
  if (!b) throw new Error(`no button ${label}: ${Array.from(root.querySelectorAll('button')).map(x => x.textContent).join(', ')}`);
  b.dispatchEvent(new Event('click')); return b as HTMLButtonElement;
};

test('a first backup goes to the folder chosen and says what was sent', async () => {
  dom(); const a = actions();
  const view = projectBackup(a, () => true, 'Cells under stress');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Not backed up yet.'));
  click(view.element, 'Back up…');
  const path = field(view.element, 'Backup folder location');
  const go = Array.from(view.element.querySelectorAll('button')).find(b => b.textContent === 'Back up') as HTMLButtonElement;
  expect(go.disabled).toBe(true);
  path.value = '/Volumes/Backup/Hooke'; path.dispatchEvent(new Event('input'));
  expect(go.disabled).toBe(false);
  click(view.element, 'Back up');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Backed up and checked: 12 files, 2.9 MB; 1.9 MB sent.'));
  expect(a.backUp).toHaveBeenCalledWith({kind: 'folder', path: '/Volumes/Backup/Hooke', create: true});
  view.dispose();
});

test('a project backed up before is backed up again to the same place in one click', async () => {
  dom(); const a = actions([folderEntry]);
  const view = projectBackup(a, () => true, 'Research');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Backed up to /Volumes/Backup/Hooke'));
  click(view.element, 'Back up again');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Backed up and checked'));
  expect(a.backUp).toHaveBeenCalledWith({kind: 'folder', path: '/Volumes/Backup/Hooke'});
  await vi.waitFor(() => expect(Array.from(view.element.querySelectorAll('button')).some(b => b.textContent === 'Back up somewhere else')).toBe(true));
  view.dispose();
});

test('a new private GitHub repository needs a sign-in and is named after the project', async () => {
  dom(); const a = actions();
  const view = projectBackup(a, () => true, 'Cells under stress');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Not backed up yet.'));
  click(view.element, 'Back up…');
  const [folder, github] = radios(view.element);
  github.checked = true; folder.checked = false;
  github.dispatchEvent(new Event('change'));
  const repo = field(view.element, 'New private GitHub repository name');
  expect(repo.value).toBe('cells-under-stress-backup');
  await vi.waitFor(() => expect(a.github.status).toHaveBeenCalled());
  await vi.waitFor(() => expect((Array.from(view.element.querySelectorAll('button')).find(b => b.textContent === 'Back up') as HTMLButtonElement).disabled).toBe(false));
  click(view.element, 'Back up');
  await vi.waitFor(() => expect(a.backUp).toHaveBeenCalledWith({kind: 'github', create: true, name: 'cells-under-stress-backup'}));
  view.dispose();
});

test('a refusal is said as the server says it, and nothing claims a backup', async () => {
  dom(); const a = actions([folderEntry], {backUp: vi.fn(async () => { throw new Error('This backup has a newer version, sent from another Mac. Nothing was sent.'); })});
  const view = projectBackup(a, () => true, 'Research');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Back up again'));
  click(view.element, 'Back up again');
  await vi.waitFor(() => expect(view.element.textContent).toContain('newer version, sent from another Mac'));
  expect(view.element.textContent).not.toContain('Backed up and checked');
  view.dispose();
});

test('earlier backups name where they went', () => {
  expect(again(folderEntry)?.destination).toEqual({kind: 'folder', path: '/Volumes/Backup/Hooke'});
  expect(again({...folderEntry, destination: {records: {kind: 'github', location: 'https://github.com/ada/research-backup'}}}))
    .toEqual({label: 'github.com/ada/research-backup (private)', destination: {kind: 'github', repo: 'ada/research-backup'}});
  expect(again({...folderEntry, destination: {records: {kind: 'github', location: 'https://evil.example/x'}}})).toBeNull();
});

test('restore takes a folder or a GitHub address and offers to open the project', async () => {
  dom();
  const restore = vi.fn(async () => ({project: {session: '2026-11-03', name: 'Research', location: '/Users/ada/Projects/Research'}}));
  const opened = vi.fn();
  const dialog = openRestore({restore}, opened);
  const source = dialog.querySelector('input') as HTMLInputElement;
  source.value = 'https://github.com/ada/research-backup.git';
  click(dialog, 'Restore');
  await vi.waitFor(() => expect(dialog.textContent).toContain('Restored “Research”, with its history and conversations'));
  expect(restore).toHaveBeenCalledWith({kind: 'github', repo: 'ada/research-backup'}, false);
  click(dialog, 'Open it');
  expect(opened).toHaveBeenCalledWith('2026-11-03');
  const second = openRestore({restore});
  (second.querySelector('input') as HTMLInputElement).value = '/Volumes/Backup/Hooke';
  click(second, 'Restore');
  await vi.waitFor(() => expect(restore).toHaveBeenLastCalledWith({kind: 'folder', path: '/Volumes/Backup/Hooke'}, false));
});

test('a project whose folder is not there is restored in its place only when asked', async () => {
  dom();
  const restore = vi.fn(async (_source: unknown, replaceMissing?: boolean) => replaceMissing
    ? {project: {session: '2026-11-03', name: 'Research', location: '/Users/ada/Projects/Research'}}
    : {missing: 'This Mac knew this Project at /Volumes/Lab/Research, which is not there now.'});
  const dialog = openRestore({restore});
  (dialog.querySelector('input') as HTMLInputElement).value = '/Volumes/Backup/Hooke';
  click(dialog, 'Restore');
  await vi.waitFor(() => expect(dialog.textContent).toContain('which is not there now'));
  expect(restore).toHaveBeenLastCalledWith({kind: 'folder', path: '/Volumes/Backup/Hooke'}, false);
  click(dialog, 'Restore in its place');
  await vi.waitFor(() => expect(dialog.textContent).toContain('Restored “Research”'));
  expect(restore).toHaveBeenLastCalledWith({kind: 'folder', path: '/Volumes/Backup/Hooke'}, true);
});

test('deleting a backup asks first, says it stays restorable for 30 days, and can be undone', async () => {
  dom();
  let listed: BackupEntry[] = [folderEntry];
  const a = actions([], {status: vi.fn(async () => ({backups: listed}))});
  (a.remove as ReturnType<typeof vi.fn>).mockImplementation(async () => { listed = [{...folderEntry, state: 'deleting', purgeAfter: 1_796_300_000}]; return {purgeAfter: 1_796_300_000}; });
  (a.keep as ReturnType<typeof vi.fn>).mockImplementation(async () => { listed = [{...folderEntry, state: 'active'}]; });
  const view = projectBackup(a, () => true, 'Research');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Delete backup'));
  click(view.element, 'Delete backup');
  expect(view.element.textContent).toContain('It can still be restored, or kept, for 30 days; then Hooke removes it.');
  expect(a.remove).not.toHaveBeenCalled();
  click(view.element, 'Delete it');
  await vi.waitFor(() => expect(view.element.textContent).toContain('being deleted on'));
  expect(a.remove).toHaveBeenCalledWith({kind: 'folder', path: '/Volumes/Backup/Hooke'});
  expect(Array.from(view.element.querySelectorAll('button')).some(b => b.textContent === 'Back up again')).toBe(false);
  click(view.element, 'Keep it');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Back up again'));
  expect(a.keep).toHaveBeenCalledWith({kind: 'folder', path: '/Volumes/Backup/Hooke'});
  view.dispose();
});

test('a backup that was removed is no longer offered', async () => {
  dom(); const view = projectBackup(actions([{...folderEntry, state: 'deleted'}]), () => true, 'Research');
  await vi.waitFor(() => expect(view.element.textContent).toContain('Not backed up yet.'));
  view.dispose();
});

/** Back up and Restore (ADR 0028; hooke-projects ticket 08). A backup is the whole Project, conversations included,
 * where only its owner reads it: a folder or a private GitHub repository. The server seals, sends and checks. */
import {githubSignIn, type PublishActions} from './project-publish';

export type BackupDestination = {kind: 'github'; create: true; name: string} | {kind: 'github'; repo: string} | {kind: 'folder'; path: string; create?: boolean};
export interface BackupEntry {
  type: string; commit: string; snapshotSha256: string; bytes?: number | null; at?: number | null;
  destination: {records: {kind: string; base?: string; location?: string}; bulk?: unknown};
  /** `deleting`: marked for deletion, still restorable until `purgeAfter`; `deleted`: removed. */
  state?: 'active' | 'deleting' | 'deleted'; purgeAfter?: number | null;
}
export interface BackedUp {commit: string; snapshotSha256: string; recordFiles: number; bulkFiles: number; bulkObjects: number; uploadedBytes: number; bytes: number}
export interface BackupActions {
  status(): Promise<{backups: BackupEntry[]}>;
  backUp(destination: BackupDestination): Promise<{backup: BackedUp; location: string}>;
  /** Mark a backup for deletion (removed after 30 days), or keep one that was marked. */
  remove(destination: BackupDestination): Promise<{purgeAfter: number}>;
  keep(destination: BackupDestination): Promise<void>;
  github: PublishActions['github'];
}
export type RestoreSource = {kind: 'folder'; path: string} | {kind: 'github'; repo: string};
export interface RestoreActions {
  /** `missing` when this Mac knew the Project at a folder that is not there now; restoring again with
   * `replaceMissing` puts the backup in its place. */
  restore(source: RestoreSource, replaceMissing?: boolean): Promise<{project: {session: string; name: string; location: string}} | {missing: string}>;
}

const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const e = document.createElement(tag); e.textContent = text; e.className = cls; return e;
};
const size = (n: number) => n < 1024 ** 2 ? `${Math.max(1, Math.ceil(n / 1024))} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const slug = (title: string) => `${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'hooke-project'}-backup`;
const when = (at?: number | null) => at ? new Date(at * 1000).toLocaleString() : '';

/** Where an earlier backup went, as the destination that backs up there again. */
export function again(entry: BackupEntry): {label: string; destination: BackupDestination} | null {
  const records = entry.destination?.records ?? {kind: ''};
  if (records.kind === 'folder' && records.base) return {label: records.base, destination: {kind: 'folder', path: records.base}};
  const repo = records.kind === 'github' ? (records.location ?? '').replace(/^https:\/\/github\.com\//, '') : '';
  if (/^[\w.-]+\/[\w.-]+$/.test(repo)) return {label: `github.com/${repo} (private)`, destination: {kind: 'github', repo}};
  return null;
}

export function projectBackup(actions: BackupActions, current: () => boolean, title: string): {element: HTMLElement; dispose(): void} {
  const element = node('section', '', 'project-publish project-backup');
  const status = node('p', '', 'project-folder-status'); status.setAttribute('role', 'status');
  const content = node('div'), controls = node('div', '', 'project-publish-controls');
  element.append(node('h3', 'Private backup'), node('p', 'Keep a versioned copy of your Project, including its conversations and research history, in a private GitHub repository or another folder. Back up again to send later changes; this is not automatic sync.'), content, status, controls);
  let disposed = false, busy = false;
  const active = () => !disposed && current();
  const button = (label: string, fn: () => Promise<void> | void, primary = false) => {
    const b = node('button', label, primary ? 'study-primary' : 'study-quiet'); b.type = 'button';
    b.addEventListener('click', () => { if (active() && !busy) void fn(); }); controls.append(b); return b;
  };
  const send = async (destination: BackupDestination) => {
    busy = true; controls.replaceChildren(); status.textContent = 'Sealing the project and sending what the backup lacks…';
    try {
      const result = await actions.backUp(destination);
      if (!active()) return;
      status.textContent = `Backed up and checked: ${result.backup.recordFiles + result.backup.bulkFiles} files, ${size(result.backup.bytes)}; ${size(result.backup.uploadedBytes)} sent.`;
      void intro(false);
    } catch (error) { if (active()) { status.textContent = message(error); button('Back', () => intro()); } }
    finally { busy = false; }
  };
  const choose = (destination: 'folder' | 'github' = 'folder') => {
    content.replaceChildren(); controls.replaceChildren(); status.textContent = '';
    const where = node('fieldset', '', 'project-publish-where'); where.append(node('legend', 'Back up to'));
    const folder = node('input'), github = node('input');
    folder.type = 'radio'; folder.name = 'backup-where'; folder.checked = destination === 'folder';
    github.type = 'radio'; github.name = 'backup-where'; github.checked = destination === 'github';
    const path = node('input'); path.placeholder = '/Volumes/Backup/Hooke'; path.setAttribute('aria-label', 'Backup folder location');
    const repo = node('input'); repo.value = slug(title); repo.setAttribute('aria-label', 'New private GitHub repository name');
    const folderRow = node('label'); folderRow.append(folder, node('span', ' A folder (an external drive or shared folder) at '), path);
    const githubRow = node('label'); githubRow.append(github, node('span', ' A new private GitHub repository named '), repo);
    where.append(folderRow, githubRow); content.append(where);
    const signIn = node('div'); content.append(signIn);
    let signedIn = false;
    const go = button('Back up', () => send(folder.checked ? {kind: 'folder', path: path.value.trim(), create: true}
      : {kind: 'github', create: true, name: repo.value.trim()}), true);
    const ready = () => { go.disabled = folder.checked ? !path.value.trim().startsWith('/') : !(signedIn && repo.value.trim()); };
    const refresh = () => {
      signIn.replaceChildren();
      if (github.checked) signIn.append(githubSignIn(actions.github, active, () => { signedIn = true; ready(); }));
      ready();
    };
    for (const input of [folder, github]) input.addEventListener('change', refresh);
    for (const input of [path, repo]) input.addEventListener('input', ready);
    button('Cancel', () => intro());
    refresh();
  };
  const intro = async (clear = true) => {
    content.replaceChildren(); controls.replaceChildren(); if (clear) status.textContent = '';
    let listed: BackupEntry[] = [];
    try { listed = (await actions.status()).backups; } catch { /* the choices below still work */ }
    if (!active()) return;
    const places = listed.filter(entry => entry.state !== 'deleted').map(entry => ({entry, to: again(entry)})).filter(p => p.to);
    const change = async (work: () => Promise<unknown>, doing: string) => {
      busy = true; controls.replaceChildren(); status.textContent = doing;
      try { await work(); if (active()) void intro(false); }
      catch (error) { if (active()) { status.textContent = message(error); button('Back', () => intro()); } }
      finally { busy = false; }
    };
    for (const {entry, to} of places) {
      const many = places.length > 1;
      if (entry.state === 'deleting') {
        content.append(node('p', `${to!.label} · being deleted${entry.purgeAfter ? ` on ${new Date(entry.purgeAfter * 1000).toLocaleDateString()}` : ''}. It can still be restored until then.`, 'project-folder-path'));
        button(many ? `Keep ${to!.label}` : 'Keep it', () => change(async () => { await actions.keep(to!.destination); status.textContent = 'Kept.'; }, 'Keeping the backup…'));
        continue;
      }
      const row = node('p', `${entry.type === 'project-restored-from-backup' ? 'Restored from' : 'Backed up to'} ${to!.label}${entry.at ? ` · ${when(entry.at)}` : ''}`, 'project-folder-path');
      content.append(row);
      button(many ? `Back up again to ${to!.label}` : 'Back up again', () => send(to!.destination), true);
      button(many ? `Delete ${to!.label}` : 'Delete backup', () => {
        controls.replaceChildren();
        status.textContent = `Delete the backup at ${to!.label}? It can still be restored, or kept, for 30 days; then Hooke removes it.`;
        button('Delete it', () => change(async () => { await actions.remove(to!.destination); status.textContent = 'Marked for deletion.'; }, 'Marking the backup for deletion…'));
        button('Cancel', () => intro());
      });
    }
    if (!places.length) content.append(node('p', 'Not backed up yet.'));
    if (!places.some(({to}) => to?.destination.kind === 'github')) button('Back up to GitHub…', () => choose('github'), !places.length);
    button(places.length ? 'Back up somewhere else' : 'Back up…', () => choose());
  };
  void intro();
  return {element, dispose: () => { disposed = true; }};
}

export function openRestore(actions: RestoreActions, opened?: (session: string) => void): HTMLDialogElement {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = node('dialog', '', 'project-folder project-start-from');
  dialog.setAttribute('aria-label', 'Restore a backup');
  const header = node('header'), close = node('button', 'Close', 'study-quiet'); close.type = 'button';
  close.addEventListener('click', () => dialog.close());
  header.append(node('h2', 'Restore a backup'), close);
  const source = node('input'); source.placeholder = '/Volumes/Backup/Hooke or github.com/you/project-backup';
  source.setAttribute('aria-label', 'Backup folder or private GitHub repository');
  const status = node('p', 'Hooke checks every file before the project opens. A project this Mac still has is never restored over: open it where it is.', 'project-folder-status');
  status.setAttribute('role', 'status');
  const footer = node('footer'), go = node('button', 'Restore', 'study-primary'); go.type = 'button';
  footer.append(go);
  dialog.append(header, source, status, footer);
  let closed = false;
  const run = async (replaceMissing: boolean) => {
    const value = source.value.trim();
    if (!value) { status.textContent = 'Give the backup’s folder or GitHub repository.'; return; }
    go.disabled = true; status.textContent = 'Fetching and checking every file…';
    try {
      const repo = value.replace(/^https?:\/\//, '').replace(/^github\.com\//, '').replace(/\.git$/, '').replace(/\/$/, '');
      const result = await actions.restore(value.startsWith('/') ? {kind: 'folder', path: value} : {kind: 'github', repo}, replaceMissing);
      if (closed) return;
      if ('missing' in result) {
        status.textContent = result.missing;
        const instead = node('button', 'Restore in its place', 'study-quiet'); instead.type = 'button';
        instead.addEventListener('click', () => { instead.remove(); void run(true); });
        footer.replaceChildren(instead, go); go.disabled = false;
        return;
      }
      status.textContent = `Restored “${result.project.name}”, with its history and conversations, at ${result.project.location}.`;
      footer.replaceChildren();
      if (opened) {
        const open = node('button', 'Open it', 'study-primary'); open.type = 'button';
        open.addEventListener('click', () => { dialog.close(); opened(result.project.session); });
        footer.append(open);
      }
    } catch (error) { if (!closed) { status.textContent = message(error); go.disabled = false; } }
  };
  go.addEventListener('click', () => void run(false));
  dialog.addEventListener('close', () => { closed = true; dialog.remove(); if (opener?.isConnected) opener.focus({preventScroll: true}); });
  document.body.append(dialog); dialog.showModal(); source.focus?.(); return dialog;
}

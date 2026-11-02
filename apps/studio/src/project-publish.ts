/** Publish, Update and Start from (ADR 0028). The server builds, seals and checks; this view shows the exact review
 * and sends only with the owner's sign-off on its digest. Nothing here decides what is safe to share. */

export interface Finding {path: string; line: number; kind: string; blocks: boolean}
export interface PublicationReview {
  origin: string; name: string; licence: string; licenceName: string; snapshotSha256: string; reviewDigest: string;
  staging: string; bytes: number; recordFiles: number; largeFiles: number; memberCount: number;
  members: {path: string; bytes: number; sha256: string}[];
  leftOut: {conversations: string; transcripts: number; otherFiles: string[]};
  includeTranscripts: boolean; findings: Finding[]; blocked: boolean;
  /** The person's own words that are published: their notes on results (the secret scan reads them too). */
  publishedText?: {session: string; id: string; version: number | null; title: string; text: string}[];
}
export interface Published {commit: string; snapshotSha256: string; recordFiles: number; bulkFiles: number; bulkObjects: number; uploadedBytes: number; bytes: number}
export interface GitHubStatus {setUp: boolean; signedIn: boolean; says: string}
export interface GitHubCode {id: string; code: string; open: string; every: number; until: number}
export interface Signoff {rights: boolean; noIdentifiableHumanData: boolean; permanent: boolean; author?: string}
export type Destination = {kind: 'github'; create: true; name: string} | {kind: 'github'; repo: string} | {kind: 'folder'; path: string; create?: boolean};
export interface UpdateResult {added: string[]; extended: string[]; unchanged: string[]; refused: {id: string; reason: string}[]; filesAdded: number; fileConflicts: string[]; resultsAdded?: number}
export interface RemoteStatus {published: {destination: unknown; commit: string; snapshotSha256: string; licence: string}[]; startedFrom: unknown | null}
export interface PublishActions {
  prepare(options: {includeTranscripts: boolean; licence: string}): Promise<PublicationReview>;
  publish(review: PublicationReview, signoff: Signoff, destination: Destination): Promise<{published: Published; location: string}>;
  status(): Promise<RemoteStatus>;
  update(): Promise<UpdateResult>;
  github: {status(): Promise<GitHubStatus>; start(): Promise<GitHubCode>; poll(id: string): Promise<{state: string; every?: number; says: string}>};
}

const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const e = document.createElement(tag); e.textContent = text; e.className = cls; return e;
};
const size = (n: number) => n < 1024 ** 2 ? `${Math.max(1, Math.ceil(n / 1024))} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
const words: Record<string, string> = {
  'secret': 'looks like a key or password', 'configured-key': 'contains one of this Mac’s keys',
  'home-path': 'names a folder in your home folder', 'unreadable': 'could not be read', 'more-not-listed': 'and more',
  'metadata-location': 'carries a location (GPS)', 'metadata-person': 'carries a person’s name', 'metadata-home-path': 'carries a path from your home folder',
};
const slug = (title: string) => title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'hooke-project';
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

function checkbox(label: string): {row: HTMLLabelElement; box: HTMLInputElement} {
  const row = node('label', '', 'project-publish-check'), box = node('input');
  box.type = 'checkbox'; row.append(box, node('span', ` ${label}`));
  return {row, box};
}

/** GitHub's device sign-in: show the code, open GitHub's page, poll until approved. */
export function githubSignIn(github: PublishActions['github'], active: () => boolean, done: () => void): HTMLElement {
  const element = node('div', '', 'project-publish-github');
  const status = node('p', 'Checking GitHub sign-in…'); status.setAttribute('role', 'status');
  element.append(status);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const show = async () => {
    try {
      const state = await github.status();
      if (!active()) return;
      element.replaceChildren(status);
      status.textContent = state.says;
      if (state.signedIn) { done(); return; }
      if (!state.setUp) return;
      const start = node('button', 'Sign in to GitHub', 'study-primary'); start.type = 'button';
      start.addEventListener('click', async () => {
        start.disabled = true;
        try {
          const code = await github.start();
          if (!active()) return;
          const shown = node('p', '', 'project-publish-code');
          shown.append(node('span', 'Open '), Object.assign(node('a', code.open), {href: code.open, target: '_blank', rel: 'noopener'}),
            node('span', ' and type '), node('strong', code.code));
          element.replaceChildren(status, shown);
          status.textContent = 'Waiting for you to approve it on GitHub';
          const poll = async () => {
            if (!active()) return;
            try {
              const result = await github.poll(code.id);
              if (!active()) return;
              status.textContent = result.says;
              if (result.state === 'done') { element.replaceChildren(status); done(); return; }
              if (result.state === 'waiting') timer = setTimeout(poll, (result.every ?? code.every) * 1000);
              else { start.disabled = false; element.append(start); }
            } catch (error) { if (active()) { status.textContent = message(error); start.disabled = false; element.append(start); } }
          };
          timer = setTimeout(poll, code.every * 1000);
        } catch (error) { if (active()) { status.textContent = message(error); start.disabled = false; } }
      });
      element.append(start);
    } catch (error) { if (active()) status.textContent = message(error); }
  };
  void show();
  element.addEventListener('dispose', () => { if (timer) clearTimeout(timer); });
  return element;
}

export function projectPublish(actions: PublishActions, current: () => boolean, title: string): {element: HTMLElement; dispose(): void} {
  const element = node('section', '', 'project-publish');
  const status = node('p', '', 'project-folder-status'); status.setAttribute('role', 'status');
  const content = node('div'), controls = node('div', '', 'project-export-actions');
  element.append(node('h3', 'Publish'), content, status, controls);
  let disposed = false, busy = false;
  const active = () => !disposed && current();
  const button = (label: string, fn: () => Promise<void> | void, primary = false) => {
    const b = node('button', label, primary ? 'study-primary' : 'study-quiet'); b.type = 'button';
    b.addEventListener('click', () => { if (active() && !busy) void fn(); }); controls.append(b); return b;
  };

  const intro = () => {
    content.replaceChildren(node('p', 'Share this project so anyone can start from it. You review every file first. '
      + 'Conversations stay on this Mac. What lists your results goes with it: tables, evidence, tracks and your notes.'));
    controls.replaceChildren(); status.textContent = '';
    button('Review what will be published', () => review(false, 'CC-BY-4.0'), true);
    void showStatus();
  };

  const showStatus = async () => {
    try {
      const remote = await actions.status();
      if (!active()) return;
      if (remote.published.length) content.append(node('p', `Published ${remote.published.length} time${remote.published.length > 1 ? 's' : ''}. A new publication adds the latest version.`));
      if (remote.startedFrom) {
        const update = button('Update from the published version', async () => {
          busy = true; update.disabled = true; status.textContent = 'Fetching and checking the published version…';
          try {
            const result = await actions.update();
            if (!active()) return;
            const changed = result.added.length + result.extended.length;
            const results = result.resultsAdded ?? 0;
            status.textContent = changed || results || result.filesAdded
              ? `${changed} document${changed === 1 ? '' : 's'} updated, ${results} result${results === 1 ? '' : 's'} and ${result.filesAdded} file${result.filesAdded === 1 ? '' : 's'} added.`
              : 'Already up to date.';
            if (result.refused.length) content.append(node('p', `${result.refused.length} document${result.refused.length > 1 ? 's' : ''} in the publication no longer follow${result.refused.length > 1 ? '' : 's'} the version here, so ${result.refused.length > 1 ? 'they were' : 'it was'} left as ${result.refused.length > 1 ? 'they were' : 'it was'}.`));
          } catch (error) { if (active()) status.textContent = message(error); }
          finally { busy = false; update.disabled = false; }
        });
      }
    } catch { /* status is informative only */ }
  };

  const review = async (includeTranscripts: boolean, licence: string) => {
    busy = true; controls.replaceChildren(); status.textContent = 'Sealing and checking what would be published…';
    try {
      const r = await actions.prepare({includeTranscripts, licence});
      if (!active()) return;
      show(r);
    } catch (error) { if (active()) { status.textContent = message(error); button('Try again', () => review(includeTranscripts, licence)); } }
    finally { busy = false; }
  };

  const show = (r: PublicationReview) => {
    content.replaceChildren(); controls.replaceChildren();
    status.textContent = r.blocked ? 'Some files need attention before this can be published.' : 'Review, then sign off.';
    content.append(node('p', `${r.memberCount.toLocaleString()} files · ${size(r.bytes)} · ${r.largeFiles} large files`));
    const left = node('ul', '', 'project-publish-left');
    left.append(node('li', `Conversations: ${r.leftOut.conversations}`));
    left.append(node('li', r.includeTranscripts ? 'Research transcripts are included' : `Research transcripts left out: ${r.leftOut.transcripts}`));
    if (r.leftOut.otherFiles.length) left.append(node('li', `Also left out: ${r.leftOut.otherFiles.length} connector and restoration records`));
    content.append(node('strong', 'Not published'), left);
    if (r.publishedText?.length) {
      const notes = node('ul', '', 'project-publish-notes');
      for (const n of r.publishedText.slice(0, 20)) notes.append(node('li', n.title ? `${n.title}: ${n.text}` : n.text));
      if (r.publishedText.length > 20) notes.append(node('li', `and ${r.publishedText.length - 20} more`));
      content.append(node('strong', 'Your notes, published as written'), notes);
    }
    if (r.findings.length) {
      const list = node('ul', '', 'project-publish-findings');
      for (const f of r.findings.slice(0, 20)) list.append(node('li', `${f.path}${f.line ? ` line ${f.line}` : ''} ${words[f.kind] ?? f.kind}${f.blocks ? '' : ' (check it)'}`));
      content.append(node('strong', r.blocked ? 'Must be resolved' : 'Please check'), list);
    }
    const files = node('details'), summary = node('summary', 'Every file');
    const listing = node('ul', '', 'project-publish-members');
    for (const m of r.members) listing.append(node('li', `${m.path} · ${size(m.bytes)}`));
    if (r.memberCount > r.members.length) listing.append(node('li', `and ${r.memberCount - r.members.length} more`));
    files.append(summary, listing); content.append(files);
    const transcripts = checkbox('Include research transcripts'); transcripts.box.checked = r.includeTranscripts;
    const licence = node('select'); licence.setAttribute('aria-label', 'Licence');
    for (const [id, label] of [['CC-BY-4.0', 'CC BY 4.0: anyone may reuse with credit'], ['CC0-1.0', 'CC0: no rights reserved']]) {
      const option = node('option', label); option.value = id; option.selected = id === r.licence; licence.append(option);
    }
    const again = node('button', 'Review again with these choices', 'study-quiet'); again.type = 'button';
    again.addEventListener('click', () => { if (active() && !busy) void review(transcripts.box.checked, licence.value); });
    content.append(transcripts.row, node('label', 'Licence ', 'project-publish-licence'), licence, again);
    if (r.blocked) { button('Back', intro); return; }
    // Where it goes.
    const where = node('fieldset', '', 'project-publish-where'); where.append(node('legend', 'Publish to'));
    const github = node('input'), folder = node('input');
    github.type = 'radio'; github.name = `where-${r.reviewDigest}`; github.checked = true;
    folder.type = 'radio'; folder.name = `where-${r.reviewDigest}`;
    const repo = node('input'); repo.value = slug(title); repo.setAttribute('aria-label', 'New GitHub repository name');
    const path = node('input'); path.placeholder = '/Volumes/Shared/Hooke'; path.setAttribute('aria-label', 'Folder location');
    const githubRow = node('label'); githubRow.append(github, node('span', ' A new public GitHub repository named '), repo);
    const folderRow = node('label'); folderRow.append(folder, node('span', ' A folder (a drive or shared folder) at '), path);
    where.append(githubRow, folderRow); content.append(where);
    const signIn = node('div'); content.append(signIn);
    let signedIn = false;
    const refreshSignIn = () => {
      signIn.replaceChildren();
      if (github.checked) signIn.append(githubSignIn(actions.github, active, () => { signedIn = true; ready(); }));
      ready();
    };
    github.addEventListener('change', refreshSignIn); folder.addEventListener('change', refreshSignIn);
    // Sign-off.
    const rights = checkbox('I have the right to share every file listed');
    const human = checkbox('These files hold no identifiable human data, or release is permitted');
    const permanent = checkbox('I understand a publication can be withdrawn from listings but not erased');
    const author = node('input'); author.placeholder = 'Your name for the citation (optional)'; author.setAttribute('aria-label', 'Author');
    content.append(rights.row, human.row, permanent.row, author);
    const send = button('Publish', async () => {
      const destination: Destination = github.checked ? {kind: 'github', create: true, name: repo.value.trim()}
        : {kind: 'folder', path: path.value.trim(), create: true};
      busy = true; send.disabled = true; status.textContent = 'Sending and checking every file…';
      try {
        const result = await actions.publish(r, {rights: rights.box.checked, noIdentifiableHumanData: human.box.checked,
          permanent: permanent.box.checked, ...(author.value.trim() ? {author: author.value.trim()} : {})}, destination);
        if (!active()) return;
        content.replaceChildren(node('p', 'Published and checked.'), node('p', result.location, 'project-folder-path'),
          node('p', `${result.published.recordFiles} record files and ${result.published.bulkFiles} large files. Anyone can now start from it.`));
        controls.replaceChildren(); status.textContent = '';
      } catch (error) { if (active()) { status.textContent = message(error); send.disabled = false; } }
      finally { busy = false; }
    }, true);
    const ready = () => {
      const where = github.checked ? signedIn && /^[A-Za-z0-9_.-]{1,100}$/.test(repo.value.trim()) : path.value.trim().startsWith('/');
      send.disabled = !(rights.box.checked && human.box.checked && permanent.box.checked && where);
    };
    for (const box of [rights.box, human.box, permanent.box]) box.addEventListener('change', ready);
    repo.addEventListener('input', ready); path.addEventListener('input', ready);
    button('Cancel', intro);
    refreshSignIn();
  };

  intro();
  return {element, dispose() { disposed = true; element.querySelectorAll('.project-publish-github').forEach(e => e.dispatchEvent(new Event('dispose'))); }};
}

export interface StartFromActions {start(source: string | {kind: 'folder'; path: string}, name: string): Promise<{project: {session: string; name: string; location: string}}>}

/** Start from a published project: a GitHub address (no sign-in needed) or a folder. */
export function openStartFrom(actions: StartFromActions, opened?: (session: string) => void): HTMLDialogElement {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = node('dialog', '', 'project-folder project-start-from');
  dialog.setAttribute('aria-label', 'Start from a project');
  const header = node('header'), close = node('button', 'Close', 'study-quiet'); close.type = 'button';
  close.addEventListener('click', () => dialog.close());
  header.append(node('h2', 'Start from a project'), close);
  const source = node('input'); source.placeholder = 'github.com/owner/project or /Volumes/Drive/Project';
  source.setAttribute('aria-label', 'Published project address or folder');
  const name = node('input'); name.placeholder = 'Name for your copy'; name.setAttribute('aria-label', 'Name for your copy');
  const status = node('p', 'Hooke checks every file against the project’s manifest before showing it. Nothing in it runs until you choose to.', 'project-folder-status');
  status.setAttribute('role', 'status');
  const footer = node('footer'), start = node('button', 'Start from it', 'study-primary'); start.type = 'button';
  footer.append(start);
  dialog.append(header, source, name, status, footer);
  let closed = false;
  start.addEventListener('click', async () => {
    const value = source.value.trim();
    if (!value) { status.textContent = 'Give a GitHub address or a folder.'; return; }
    start.disabled = true; status.textContent = 'Fetching and checking every file…';
    try {
      const result = await actions.start(value.startsWith('/') ? {kind: 'folder', path: value} : value, name.value.trim() || 'Started project');
      if (closed) return;
      status.textContent = `Started “${result.project.name}”, a project of your own, in your Projects. What came from the publication is read-only there; copy a document to change it.`;
      footer.replaceChildren();
      if (opened) {
        const open = node('button', 'Open it', 'study-primary'); open.type = 'button';
        open.addEventListener('click', () => { dialog.close(); opened(result.project.session); });
        footer.append(open);
      }
    } catch (error) { if (!closed) { status.textContent = message(error); start.disabled = false; } }
  });
  dialog.addEventListener('close', () => { closed = true; dialog.remove(); if (opener?.isConnected) opener.focus({preventScroll: true}); });
  document.body.append(dialog); dialog.showModal(); source.focus?.(); return dialog;
}

import {projectExport, type ExportActions} from './project-export';
import './project-folder.css';

/** Same projection as contract/agent/project-storage; this inspector never moves evidence. */
export interface ProjectStorage {
  session: string; origin: string | null; location: string | null;
  layout: 'legacy' | 'project-folder' | 'unavailable';
  availability: 'available' | 'unavailable'; reason: string | null;
  collection: {state: 'not-assessed'; reason: string};
}
interface Options {
  title: string; session: string; current: () => boolean;
  exportActions?: ExportActions;
  restorationActions?: (operation: string) => ExportActions;
  read: () => Promise<{storage: ProjectStorage}>;
  reveal: () => Promise<{ok: boolean; path: string; error?: string}>;
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const element = document.createElement(tag); element.textContent = text; element.className = cls; return element;
};

export function openProjectFolder(opts: Options): HTMLDialogElement {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = node('dialog', '', 'project-folder');
  dialog.setAttribute('aria-label', `Project folder: ${opts.title}`);
  const header = node('header'), close = node('button', 'Close', 'study-quiet'); close.type = 'button';
  close.addEventListener('click', () => dialog.close());
  header.append(node('h2', 'Project folder'), close);
  const body = node('div', '', 'project-folder-body');
  const actions = node('footer'), reveal = node('button', 'Reveal in Finder', 'study-primary'); reveal.type = 'button'; reveal.disabled = true;
  const status = node('p', 'Reading saved location…', 'project-folder-status'); status.setAttribute('role', 'status');
  actions.append(reveal); dialog.append(header, node('p', opts.title, 'project-folder-name'), body, status, actions);
  let closed = false;
  let exportView: ReturnType<typeof projectExport> | undefined;
  const current = () => !closed && opts.current();
  const changed = () => { reveal.disabled = true; status.textContent = 'The Project changed. Close this panel and reopen its folder details.'; };
  reveal.addEventListener('click', async () => {
    if (!current()) { changed(); return; }
    reveal.disabled = true;
    try {
      const result = await opts.reveal();
      if (!current()) { if (!closed) changed(); return; }
      status.textContent = result.ok ? 'Location revealed in Finder.' : `${result.error ?? 'Open this location in your file manager.'} ${result.path}`;
    } catch (error) { if (current()) status.textContent = error instanceof Error ? error.message : String(error); }
    finally { if (current()) reveal.disabled = false; }
  });
  const load = async () => {
    exportView?.dispose(); exportView = undefined;
    status.textContent = 'Reading saved location…'; body.replaceChildren(); reveal.disabled = true;
    try {
      const {storage} = await opts.read();
      if (!current() || storage.session !== opts.session) { if (!closed) changed(); return; }
      const portable = storage.layout === 'project-folder';
      const available = storage.availability === 'available';
      body.append(node('strong', !available ? 'Project location unavailable' : portable ? 'Saved in a Project folder' : 'Saved in Hooke’s existing storage'));
      if (storage.location) body.append(node('p', storage.location, 'project-folder-path'));
      if (storage.reason) body.append(node('p', storage.reason));
      if (available) body.append(node('p', portable
        ? 'This folder keeps the Project’s accepted records. After closing its Hooke host, use Open Project to reopen this folder or update its moved location. A complete portable copy has not yet been verified.'
        : 'Your work remains available here. A portable Project copy has not been collected.'));
      const details = node('details'), summary = node('summary', 'Identity and collection');
      details.append(summary, node('p', `Project identity: ${storage.origin ?? 'Unavailable'}`, 'project-folder-path'), node('p', storage.collection.reason));
      body.append(details);
      if (available && portable && storage.origin && opts.exportActions) {
        exportView = projectExport(opts.exportActions, current, storage.origin, 'export', opts.restorationActions);
        body.append(exportView.element);
      }
      reveal.disabled = !available;
      status.textContent = available ? '' : 'Your research has not been replaced or recreated.';
      if (!available) addRetry();
    } catch (error) {
      if (!current()) { if (!closed) changed(); return; }
      status.textContent = error instanceof Error ? error.message : String(error);
      addRetry();
    }
  };
  const addRetry = () => {
    const retry = node('button', 'Retry', 'study-quiet'); retry.type = 'button';
    retry.addEventListener('click', () => { if (current()) void load(); else changed(); }); body.append(retry);
  };
  dialog.addEventListener('close', () => { closed = true; exportView?.dispose(); dialog.remove(); if (opener?.isConnected) opener.focus({preventScroll: true}); });
  document.body.append(dialog); dialog.showModal(); void load(); return dialog;
}

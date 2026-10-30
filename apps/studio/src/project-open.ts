import './project-folder.css';
import {nativeFolderBridge} from './scientific-folder-native';

export interface OpenedProject {session: string; origin: string; name: string; location: string}
interface Actions {
  current: () => boolean;
  open: (path: string) => Promise<OpenedProject>;
  navigate: (session: string) => Promise<boolean>;
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const el = document.createElement(tag); el.textContent = text; el.className = cls; return el;
};

/** Owner path entry also works in the browser; no agent or saved document chooses it. */
export function showOpenProject(actions: Actions): HTMLDialogElement {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = node('dialog', '', 'project-folder project-open');
  dialog.setAttribute('aria-label', 'Open Project folder');
  const header = node('header'), close = node('button', 'Cancel'); close.type = 'button';
  close.addEventListener('click', () => dialog.close());
  header.append(node('h2', 'Open Project'), close);
  const native = nativeFolderBridge();
  const choose = native ? node('button', 'Choose folder…') : null;
  const form = node('form'), label = node('label', 'Project folder on this Mac');
  const input = node('input'); input.type = 'text'; input.required = true; input.autocomplete = 'off';
  input.placeholder = '/Users/you/Hooke Projects/Your project'; input.setAttribute('aria-label', 'Project folder on this Mac'); label.append(input);
  const status = node('p', '', 'project-folder-status'); status.setAttribute('role', 'status');
  const submit = node('button', 'Open Project', 'study-primary'); submit.type = 'submit';
  const footer = node('footer'); footer.append(submit);
  form.append(label, node('p', 'Choose the folder containing project.json. Existing evidence and authorship stay with the Project; agent access is set up separately.', 'project-folder-name'), status, footer);
  dialog.append(header, form); document.body.append(dialog);
  let closed = false, busy = false, choosing = false;
  if (choose && native) {
    choose.type = 'button'; label.append(choose);
    choose.addEventListener('click', async () => {
      if (busy || choosing || closed || !actions.current()) return;
      choosing = true; choose.disabled = true; submit.disabled = true;
      try {
        const selected = await native.postMessage({op: 'choose-project'});
        if (closed || !actions.current() || selected === null) return;
        if (typeof selected !== 'string' || !selected.startsWith('/')) throw new Error('Choose the Project folder again, or enter its location.');
        input.value = selected; status.textContent = ''; input.focus();
      } catch (error) { if (!closed) status.textContent = error instanceof Error ? error.message : 'Enter the Project folder location to continue.'; }
      finally { choosing = false; choose.disabled = false; submit.disabled = false; }
    });
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || choosing || closed) return;
    if (!actions.current()) { status.textContent = 'Your workspace changed. Close this dialog and open the folder again.'; return; }
    const path = input.value.trim(); if (!path) { input.focus(); return; }
    busy = true; submit.disabled = true; input.disabled = true;
    status.textContent = 'Verifying retained evidence…';
    try {
      const project = await actions.open(path);
      if (closed) return;
      if (!actions.current()) { status.textContent = `${project.name} is now in All Projects. Your current workspace was left as it is.`; return; }
      // Dismiss before existing unsaved-work/recovery dialogs need the foreground.
      dialog.close();
      await actions.navigate(project.session);
    } catch (error) {
      if (!closed) status.textContent = error instanceof Error ? error.message : 'The Project could not be opened. Keep the original folder and try again.';
    } finally { busy = false; submit.disabled = false; input.disabled = false; }
  });
  dialog.addEventListener('close', () => { closed = true; if (choosing && native) void native.postMessage({op: 'cancel'}).catch(() => undefined); dialog.remove(); if (opener?.isConnected) opener.focus(); }, {once: true});
  dialog.showModal(); input.focus(); return dialog;
}

import { StudioSheet } from './sheet';
import type { DataEntry } from './data-roles';
import './material-organization.css';

const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const item = document.createElement(tag); item.textContent = text; return item; };
const drafts = new Map<string, {role: string; input: boolean; request: string; revision: number}>();

/** Drafts belong to the exact Project/revision; closing a sheet never drops typed organization. */
export function organizeMaterial(entry: DataEntry, session: string, current: () => boolean, saved: () => void): void {
  const version = entry.version, digest = entry.source?.sha256;
  if (!version || typeof digest !== 'string' || entry.day !== session) throw new Error('Reopen the exact saved material before organizing it.');
  const key = `${session}/${entry.id}/${version}/${digest}`;
  let draft = drafts.get(key) ?? {role: entry.material?.organization?.role ?? '', input: entry.material?.input ?? false, request: crypto.randomUUID(), revision: entry.material?.organization?.revision ?? 0};
  const changedElsewhere = (entry.material?.organization?.revision ?? 0) > draft.revision;
  if (changedElsewhere) draft = {...draft, revision: entry.material!.organization!.revision, request: crypto.randomUUID()};
  drafts.set(key, draft);
  const sheet = new StudioSheet('Organize material');
  const form = node('form'); form.className = 'material-organization';
  const title = node('strong', entry.title);
  const role = node('input'); role.maxLength = 80; role.value = draft.role; role.placeholder = 'e.g. Training data or Reference papers';
  const label = node('label', 'Group'); label.append(role);
  const selected = node('input'); selected.type = 'checkbox'; selected.checked = draft.input;
  const selectedLabel = node('label', 'Include in Inputs'); selectedLabel.className = 'material-organization-choice'; selectedLabel.prepend(selected);
  const status = node('p', changedElsewhere ? `Organization changed elsewhere (current group: ${entry.material?.organization?.role || 'ungrouped'}). Your draft is kept; review it before saving.` : ''); status.setAttribute('role', 'status');
  const save = node('button', changedElsewhere ? 'Review and save organization' : 'Save organization'); save.type = 'submit'; save.className = 'study-primary';
  const remember = () => { draft = {...(drafts.get(key) ?? draft), role: role.value, input: selected.checked, request: crypto.randomUUID()}; drafts.set(key, draft); };
  role.addEventListener('input', remember); selected.addEventListener('change', remember);
  form.append(title, label, selectedLabel, node('p', 'Groups describe how you use material. Study dependencies stay in Inputs, and earlier evidence stays kept.'), status, save);
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (save.disabled) return;
    if (!current()) { status.textContent = 'Return to this Project before saving. Your draft is kept.'; return; }
    draft = drafts.get(key) ?? draft; const submitted = {...draft}; const submittedDraft = draft;
    save.disabled = true; role.disabled = true; selected.disabled = true; status.textContent = 'Saving…';
    try {
      const response = await fetch(`/api/scientific-projects/${encodeURIComponent(entry.id)}/organization`, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-MicroscopeMind': 'save-project'},
        body: JSON.stringify({reference: {id: entry.id, session, revision: version, digest}, role: submitted.role, input: submitted.input, request: submitted.request, expectedRevision: submitted.revision})});
      const result = await response.json();
      if (!response.ok || result.ok !== true) throw new Error([result.says ?? 'Organization could not be saved.', result.next ?? ''].filter(Boolean).join(' '));
      const membership = result.project?.document?.state?.materialMembership;
      if (result.project?.session !== session || result.project?.revision !== submitted.revision + 1 ||
          membership?.reference?.id !== entry.id || membership?.reference?.revision !== version || membership?.reference?.digest !== digest)
        throw new Error('The saved organization did not match this material. Reopen its current organization.');
      const latest = drafts.get(key);
      if (latest === submittedDraft) drafts.delete(key);
      else if (latest?.revision === submitted.revision) drafts.set(key, {...latest, revision: result.project.revision});
      status.textContent = 'Saved'; saved(); sheet.close();
    } catch (error) { status.textContent = error instanceof Error ? error.message : String(error); }
    finally { save.disabled = false; role.disabled = false; selected.disabled = false; }
  });
  sheet.open(form); role.focus();
}

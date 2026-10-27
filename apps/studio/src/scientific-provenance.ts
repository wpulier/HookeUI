import { recordedScientificReader } from './scientific-reader-context';
/** Accepted revision provenance is inert data; opening it never starts a worker. */
import { showScientificHistory } from './scientific-history';

type Reference = { id: string; day: string; version: number; picture: null };
type Source = { id: string; day: string; version: number };
const RECORDS = ['hookeEdit', 'hookeOutput', 'hookeCopy'];
const object = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === 'object' && !Array.isArray(x);
function reference(x: unknown): Reference {
  if (!object(x) || typeof x.id !== 'string' || !/^p[0-9a-f]{32}$/.test(x.id) || typeof x.session !== 'string' ||
      !Number.isSafeInteger(x.revision) || Number(x.revision) < 1 || typeof x.digest !== 'string' || !/^[0-9a-f]{64}$/.test(x.digest))
    throw new Error('This edit has an invalid saved history reference. Keep the original scientific revision.');
  return { id: x.id, day: x.session, version: Number(x.revision), picture: null };
}
/**
 * Shows what the saved document itself records, then, given its exact saved reference, what the server could
 * confirm about how it was made. The returned promise settles once that answer is shown; it never rejects.
 */
export function showScientificProvenance(root: HTMLElement, state: unknown, open: (ref: Reference, title: string) => void,
                                         source?: Source, request: typeof fetch = fetch, savedTool?: (value: unknown) => void): Promise<void> | undefined {
  if (!object(state)) return;
  recorded(root, state, open);
  if (source === undefined || !RECORDS.some(key => state[key] !== undefined)) return;
  const section = document.createElement('section'), heading = document.createElement('h3'), status = document.createElement('p');
  section.className = 'scientific-made';
  heading.textContent = 'How this was made';
  status.textContent = 'Checking saved records…';
  section.append(heading, status);
  root.append(section);
  return confirmed(section, status, source, open, request, savedTool);
}

function recorded(root: HTMLElement, state: Record<string, unknown>, open: (ref: Reference, title: string) => void): void {
  if (state.sourceReader !== undefined) {
    const origin=state.sourceReader;
    if(!object(origin)||origin.format!==1||typeof origin.capability!=='string'||typeof origin.generation!=='string'||!/^[a-f0-9]{16}$/.test(origin.generation))
      throw new Error('This reader identity is invalid. Reopen the original source before choosing a reader.');
    const original=reference(origin.source),section=document.createElement('section'),heading=document.createElement('h3'),description=document.createElement('p'),button=document.createElement('button');
    const reader=recordedScientificReader(state);
    heading.textContent=reader?`Reader: ${reader.name}`:'Selected reader';
    description.textContent='This is a private candidate. Starting runs the captured integration; preparation does not certify its scientific results.';
    const identity=document.createElement('details'),summary=document.createElement('summary'),exact=document.createElement('p');
    summary.textContent='Reader identity';exact.textContent=`${origin.capability} · generation ${origin.generation}`;identity.append(summary,exact);
    button.textContent=`Open original source · revision ${original.version}`;
    button.addEventListener('click',()=>open(original,'Original scientific source'));
    section.append(heading,description,button,identity);root.append(section);
  }
  if (state.hookeOutput !== undefined) {
    const output = state.hookeOutput;
    if (!object(output) || (output.format!==1 && output.format!==2) || !object(output.producer) || !Array.isArray(output.inputs) ||
        (output.format===1 ? !object(output.output) : !Array.isArray(output.outputs) || output.outputs.length<2 || output.outputs.length>8 ||
          output.outputs.some(row=>!object(row)||!object(row.output)||!object(row.meaning)||!object(row.components)||!object(row.calibration))))
      throw new Error('This output provenance is unsupported. Keep the saved scientific document.');
    const base = reference(output.base);
    const history = output.producer.history == null ? null : reference(output.producer.history);
    const details = document.createElement('details'), summary = document.createElement('summary'), note = document.createElement('p');
    summary.textContent = output.format===2 ? 'How these outputs were saved together' : 'How this new output was made';
    note.textContent = 'Native values, declared meaning and layer coordinates are retained. Original code is not rerun on reopening. Alignment to input coordinates was not inferred.';
    details.append(summary, note);
    for (const [ref, label] of [[base, 'Open originating scientific document'], ...(history ? [[history, 'Open producing history']] : [])] as [Reference, string][]) {
      const button = document.createElement('button'); button.textContent = label;
      button.addEventListener('click', () => open(ref, label)); details.append(button);
    }
    root.append(details);
  }
  if (state.hookeEdit === undefined) return;
  const retained = state.hookeEdit;
  const changes = object(retained) && retained.format === 2 ? retained.changes : [retained];
  if (!Array.isArray(changes) || changes.length < 1 || changes.length > 64)
    throw new Error('This edit provenance is unsupported. Keep the original scientific revision.');
  const fragments: HTMLElement[] = [];
  for (const [index, edit] of changes.entries()) {
  if (!object(edit) || edit.format !== 1 || !object(edit.producer) || !object(edit.input) || !object(edit.output))
    throw new Error('This edit provenance is unsupported. Keep the original scientific revision.');
  const base = reference(edit.base);
  const history = edit.producer.history === null || edit.producer.history === undefined ? null : reference(edit.producer.history);
  const details = document.createElement('details'), summary = document.createElement('summary'), description = document.createElement('p');
  summary.textContent = 'How these changes were made';
  description.textContent = 'Saved values and their exact input are retained. Recorded code and actions describe this session; replay is not guaranteed.';
  details.append(summary, description);
  const add = (label: string, ref: Reference, title: string) => {
    const button = document.createElement('button'); button.textContent = label;
    button.addEventListener('click', () => open(ref, title)); details.append(button);
  };
  add(`Open original · revision ${base.version}`, base, 'Original scientific input');
  if (history) add(`Open producing history · revision ${history.version}`, history, 'Producing scientific history');
  else { const note = document.createElement('p'); note.textContent = 'No producing code or manual-action history was recorded for this snapshot.'; details.append(note); }
  if (changes.length > 1) {
    const name = typeof edit.input.component === 'string' && edit.input.component.startsWith('array.')
      ? edit.input.component.slice(6) : `layer ${index + 1}`;
    summary.textContent = `How changes to ${name} were made`;
  }
  fragments.push(details);
  }
  root.append(...fragments);
}

const element = <K extends keyof HTMLElementTagNameMap>(tag: K, value: string) => {
  const result = document.createElement(tag); result.textContent = value; return result;
};
const count = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;

async function confirmed(section: HTMLElement, status: HTMLElement, source: Source,
                         open: (ref: Reference, title: string) => void, request: typeof fetch, savedTool?: (value: unknown) => void): Promise<void> {
  try {
    const query = new URLSearchParams({ session: source.day, revision: String(source.version) });
    const response = await request(`/api/scientific-projects/${encodeURIComponent(source.id)}/provenance?${query}`);
    const value: unknown = await response.json();
    if (!object(value) || value.ok !== true || !object(value.provenance))
      throw new Error(object(value) && typeof value.says === 'string' ? value.says : 'The server gave no answer.');
    madeFrom(section, status, value.provenance, source, open);
    if (Array.isArray(value.provenance.problems) && !value.provenance.problems.length) savedTool?.(value.provenance.savedTool);
  } catch (error) {
    status.textContent = `How this was made could not be checked: ${error instanceof Error ? error.message : String(error)} ` +
      'The saved values are unchanged.';
  }
}

function madeFrom(section: HTMLElement, status: HTMLElement, found: Record<string, unknown>, source: Source,
                  open: (ref: Reference, title: string) => void): void {
  const record = found.record;
  if (!object(record) || record.id !== source.id || record.session !== source.day || record.revision !== source.version
      || !Array.isArray(found.problems) || found.problems.some(p => typeof p !== 'string') || !Array.isArray(found.edits))
    throw new Error('The answer does not describe this saved revision.');
  const problems = found.problems as string[];
  status.textContent = problems.length ? 'Some saved records could not be confirmed. Nothing else was put in their place:'
    : 'Checked against the saved records. Nothing was rerun to show this.';
  if (problems.length) {
    const list = document.createElement('ul');
    for (const problem of problems) list.append(element('li', problem));
    section.append(list);
  }
  if (found.recorded !== true) { section.append(element('p', 'No record of how this revision was made was saved.')); return; }
  if (!problems.length) {
    // Only a record that checked out is offered for export; a problem stays visible here instead.
    const query = new URLSearchParams({ session: source.day, revision: String(source.version) });
    const download = element('a', 'Export with how it was made');
    download.setAttribute('href', `/api/scientific-projects/${encodeURIComponent(source.id)}/bundle?${query}`);
    download.setAttribute('download', '');
    section.append(download, element('p', 'The export carries this revision, its original, the producing histories and their saved values as exact bytes. Use Inputs or Data → Import native bundle in another Hooke to check and retain these bytes. Import requires a verified original Project owner. Legacy bundles without one are refused; keep the archive for offline recovery.'));
  }
  if (object(found.copy)) section.append(element('p', 'Saved as a separate copy because its original changed first. The original was not altered.'));
  if (found.edits.length) {
    const accepted = found.acceptedAt;
    section.append(element('p', accepted === source.version ? `These values were accepted in this revision (${source.version}).`
      : count(accepted) ? `These values were accepted in revision ${accepted}; this revision kept them unchanged.`
      : 'The revision that accepted these values could not be confirmed.'));
  }
  for (const edit of found.edits) {
    if (!object(edit)) throw new Error('The answer describes a change it cannot show.');
    const part = document.createElement('section');
    const component = object(edit.output) && typeof edit.output.component === 'string' ? edit.output.component.replace(/^array\./, '') : 'values';
    part.append(element('h4', `Change to ${component}`));
    const base = edit.base;
    if (object(base) && typeof base.id === 'string' && typeof base.session === 'string' && count(base.revision)) {
      const button = element('button', `Open original · revision ${base.revision}`);
      button.addEventListener('click', () => open({ id: base.id as string, day: base.session as string, version: Number(base.revision), picture: null }, 'Original scientific input'));
      part.append(button);
    }
    part.append(element('p', changed(edit.changed)));
    runtimes(part, edit.runtimes);
    producer(part, edit.producer, base, open);
    if (typeof edit.unrecorded === 'string') part.append(element('p', edit.unrecorded));
    section.append(part);
  }
  const outputs = found.outputs;
  if (object(outputs)) {
    const part = document.createElement('section'), made = Array.isArray(outputs.made) ? outputs.made.length : 0;
    part.append(element('h4', made > 1 ? `${made} outputs saved together` : 'A new output'));
    runtimes(part, outputs.runtimes);
    producer(part, outputs.producer, outputs.base, open);
    if (typeof outputs.unrecorded === 'string') part.append(element('p', outputs.unrecorded));
    section.append(part);
  }
}

function runtimes(part: HTMLElement, raw: unknown): void {
  if (!Array.isArray(raw)) return;
  for (const runtime of raw.slice(0, 8)) {
    if (!object(runtime) || typeof runtime.bundle !== 'string') continue;
    const facts = [`Integration ${runtime.bundle}`, ...(typeof runtime.operation === 'string' ? [`operation ${runtime.operation}`] : []),
      ...(typeof runtime.generation === 'string' ? [`generation ${runtime.generation}`] : [])];
    part.append(element('p', facts.join(' · ')));
  }
}

function changed(record: unknown): string {
  if (!object(record)) return 'No change record exists for these values.';
  if (record.layout === 'changed') return 'The array layout changed, so every value is treated as changed.';
  const chunks = count(record.chunks), of = count(record.of);
  if (chunks === null || of === null) return 'The change record could not be read.';
  if (chunks === 0) return 'No stored values changed.';
  const axes = Array.isArray(record.axes) ? record.axes : [];
  const bounds = Array.isArray(record.bounds) ? record.bounds : [];
  const where = bounds.map((pair, i) => Array.isArray(pair) && count(pair[0]) !== null && count(pair[1])
    ? `${typeof axes[i] === 'string' ? axes[i] : `axis ${i}`} ${pair[0]}–${Number(pair[1]) - 1}` : '').filter(Boolean).join(', ');
  return `${chunks} of ${of} stored blocks changed${where ? `, within ${where}` : ''}. ` +
    'Values inside a changed block may be partly unchanged.';
}

function producer(part: HTMLElement, raw: unknown, base: unknown, open: (ref: Reference, title: string) => void): void {
  if (!object(raw) || raw.recorded !== true) {
    part.append(element('p', object(raw) && typeof raw.details === 'string' ? raw.details : 'How these values were produced was not recorded.'));
    return;
  }
  if (raw.verified === false) {
    part.append(element('p', typeof raw.details === 'string' ? raw.details : 'Its producer could not be checked.'));
    if (object(raw.record)) part.append(element('pre', JSON.stringify(raw.record, null, 2).slice(0, 32768)));
    return;
  }
  const kernel = typeof raw.kernel === 'string' ? raw.kernel : 'an unnamed session';
  part.append(element('p', `Made in scientific session “${kernel}”. Only this session's own code and actions are shown.`));
  const history = raw.history;
  if (object(history) && typeof history.id === 'string' && typeof history.session === 'string' && count(history.revision)) {
    const button = element('button', `Open producing history · revision ${history.revision}`);
    button.addEventListener('click', () => open({ id: history.id as string, day: history.session as string, version: Number(history.revision), picture: null }, 'Producing scientific history'));
    part.append(button);
  }
  if (!Array.isArray(raw.cells) || !Array.isArray(raw.actions)) {
    part.append(element('p', history == null ? 'No code or manual action had been saved in this session when the values were accepted.'
      : 'Its code and actions are not shown because the producing history could not be confirmed.'));
    return;
  }
  if (!raw.cells.length && !raw.actions.length) { part.append(element('p', 'This session had no saved code or manual actions.')); return; }
  showScientificHistory(part, { format: 'scientific-python-history-v1', input: base, cells: raw.cells, actions: raw.actions });
}

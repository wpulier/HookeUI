/** Retained numerical outputs: inspect the producer's record, never recompute on open. */
import { showScientificSource } from './scientific-file-import';
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const n = document.createElement(tag); n.textContent = text; return n;
};

/** Keep large arrays/objects out of the reading path; exact metadata is expanded on demand. */
function summaryValue(value: unknown): HTMLElement {
  const simple = value === null || ['string', 'number', 'boolean'].includes(typeof value);
  const shortArray = Array.isArray(value) && value.length <= 8 && value.every(v => v === null || ['number', 'boolean'].includes(typeof v));
  const text = typeof value === 'string' ? value : simple || shortArray ? JSON.stringify(value) : '';
  if ((simple || shortArray) && text.length <= 180) return node('span', text);
  const details = node('details'); details.className = 'scientific-result-value';
  const description = Array.isArray(value) ? `${value.length.toLocaleString()} items`
    : object(value) ? `${Object.keys(value).length.toLocaleString()} ${Object.keys(value).length === 1 ? 'field' : 'fields'}`
    : `${String(value).length.toLocaleString()} characters`;
  details.append(node('summary', `View ${description}`));
  let rendered = false;
  details.addEventListener('toggle', () => {
    if (!details.open || rendered) return;
    rendered = true;
    details.append(node('pre', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
  });
  return details;
}

export function showMatrixResult(root: HTMLElement, state: unknown, ref: { id: string; day: string; version: number }, resources: unknown,
  open: (target: { id: string; version: number; title: string }) => Promise<unknown>): void {
  const op = object(state) ? state.matrixOperation : null;
  if (!object(op) || op.format !== 1 || typeof op.capability !== 'string' || !object(op.output) || !object(op.output.summary) || !object(op.inputs))
    throw new Error('This saved result has no supported computation record. Keep its exact revision.');
  const result = node('section'); result.className = 'scientific-matrix-result';
  const file = node('div'); file.className = 'scientific-result-file';
  showScientificSource(file, state, ref, undefined, resources, true);
  result.append(file, node('h3', 'Result summary'));
  const note = node('p', `Produced by ${op.capability} · Recorded output, not an independent validation.`);
  note.className = 'scientific-result-note'; result.append(note);
  const facts = node('dl'); facts.className = 'scientific-result-summary';
  for (const [key, value] of Object.entries(op.output.summary)) {
    const row = node('div'); row.className = 'scientific-result-fact';
    const label = key.replace(/([a-z])([A-Z])/g, (_, a: string, b: string) => `${a} ${b.toLowerCase()}`).replace(/_/g, ' ');
    const term = node('dt', label.charAt(0).toUpperCase() + label.slice(1));
    const definition = node('dd'); definition.append(summaryValue(value));
    row.append(term, definition); facts.append(row);
  }
  result.append(facts);
  const inputs = node('details'); inputs.className = 'scientific-result-method';
  inputs.append(node('summary', 'Exact inputs and computation'));
  inputs.append(node('pre', JSON.stringify({ capability: op.capability, generation: op.generation, parameters: op.params, runtime: op.runtime }, null, 2)));
  const notice = node('p'); notice.setAttribute('role', 'status');
  for (const [port, input] of Object.entries(op.inputs)) {
    if (!object(input) || input.session !== ref.day || typeof input.id !== 'string' || !Number.isSafeInteger(input.revision) || Number(input.revision) < 1) continue;
    const b = node('button', `Open ${port} input · v${input.revision}`); b.type = 'button';
    b.addEventListener('click', () => { void open({ id: input.id as string, version: Number(input.revision), title: port }).catch(e => { notice.textContent = String(e); }); });
    inputs.append(b);
  }
  result.append(inputs, notice); root.append(result);
}

import { pythonTranscript } from './scientific-console';
import { SCRIPT_BYTES } from './scientific-scripts';

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown, max: number) => {
  if (typeof value !== 'string' || value.length > max) throw new Error('Saved Python history contains invalid text. Keep the original and inspect its saved revision.');
  return value;
};
const element = <K extends keyof HTMLElementTagNameMap>(tag: K, value: string) => {
  const result = document.createElement(tag); result.textContent = value; return result;
};

export function showScientificHistory(root: HTMLElement, raw: unknown): void {
  if (!object(raw) || raw.format !== 'scientific-python-history-v1' || !object(raw.input) || !Array.isArray(raw.cells) || raw.cells.length > 128)
    throw new Error('This saved Python history is not supported. Keep its original revision.');
  const source = text(raw.input.id, 128), revision = raw.input.revision;
  if (!Number.isSafeInteger(revision) || Number(revision) < 1) throw new Error('Python history has no exact saved input revision.');
  const content = document.createElement('section');
  content.append(element('p', 'Saved code, controls and edit outcomes. Opening this history runs nothing. Accepted arrays are saved separately; this history does not restore kernel variables.'),
    element('p', `Input ${source} · revision ${revision}`));
  for (const rawCell of raw.cells) {
    if (!object(rawCell) || !['submitted', 'completed', 'interrupted', 'not-dispatched'].includes(String(rawCell.state)))
      throw new Error('A saved Python cell has an unknown outcome. Keep this history and inspect its original revision.');
    const cell = document.createElement('section');
    const code = text(rawCell.source, SCRIPT_BYTES);
    if (new TextEncoder().encode(code).length > SCRIPT_BYTES) throw new Error('Saved Python source exceeds its UTF-8 allowance. Keep its original revision.');
    cell.append(element('h3', `Cell ${content.querySelectorAll('h3').length + 1}`), element('pre', code));
    if (rawCell.state === 'completed') {
      const output = pythonTranscript(rawCell.transcript);
      cell.append(element('p', output.error ? 'Execution returned an error. Earlier statements may have changed private data.' : 'Execution completed. Transcript saved.'));
      for (const key of ['stdout', 'stderr', 'result', 'error'] as const) {
        if (output[key]) cell.append(element('h4', key), element('pre', output[key]));
      }
      if (output.truncated) cell.append(element('p', 'The output exceeded its allowance. This is the retained, truncated transcript.'));
    } else if (rawCell.state === 'not-dispatched') cell.append(element('p', 'This code was never sent to the worker and changed nothing. Nothing was replayed.'));
    else cell.append(element('p', 'Outcome unconfirmed. The code may have run; nothing was replayed. Its private state was not saved.'));
    content.append(cell);
  }
  if (raw.actions !== undefined) {
    if (!Array.isArray(raw.actions) || raw.actions.length > 128) throw new Error('Saved action history is invalid. Keep the original revision.');
    for (const action of raw.actions) {
      if (!object(action) || !object(action.request) || !object(action.context) ||
          !['submitted', 'completed', 'interrupted', 'not-dispatched'].includes(String(action.state)))
        throw new Error('Saved action history is invalid. Keep the original revision.');
      const body = JSON.stringify({ request: action.request, context: action.context, outcome: action.outcome }, null, 2);
      if (body.length > (1 << 20)) throw new Error('Saved action history exceeds its display allowance.');
      const detail = document.createElement('details');
      const refused = action.state === 'completed' && object(action.outcome) && action.outcome.state === 'not-applied';
      const uncertain = action.state === 'submitted' || action.state === 'interrupted';
      detail.append(element('summary', `${text(action.request.op, 64)} · ${refused ? 'Not applied' : uncertain ? 'Outcome unconfirmed' : String(action.state)}`));
      if (refused) detail.append(element('p', 'The worker refused this action before applying it. Private work was kept.'));
      if (uncertain) detail.append(element('p', 'This action may have changed private work. Nothing was replayed. Inspect the current session before another action; this history does not restore unsaved data.'));
      detail.append(element('pre', body));
      content.append(detail);
    }
  }
  root.append(content);
}

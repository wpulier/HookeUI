import type { ScientificAskInput } from './protocol';

/** A typed-Ask attachment, independent of DOM focus and never a saved-work mutation. */
export class ScientificAskSelection {
  private value: { input: ScientificAskInput; title: string } | null = null;
  select(input: ScientificAskInput, title: string): void { this.value = { input: structuredClone(input), title }; }
  clear(): void { this.value = null; }
  current(session: string, tab: string): { input: ScientificAskInput; title: string } | null {
    if (this.value && (this.value.input.reference.session !== session || this.value.input.tab !== tab)) this.clear();
    return this.value ? structuredClone(this.value) : null;
  }
}
const selection = new ScientificAskSelection();
export function scientificAskSelection() {
  return selection.current(document.body.dataset.workspaceProject ?? '', document.body.dataset.activeWorkspaceView ?? '');
}
export function clearScientificAsk(): void { selection.clear(); window.dispatchEvent(new Event('scientific-ask-input')); }
export function attachScientificAsk(input: ScientificAskInput, title: string): void {
  if (input.reference.session !== document.body.dataset.workspaceProject || input.tab !== document.body.dataset.activeWorkspaceView)
    throw new Error('Open the saved input in the current Project and Tab before attaching it.');
  selection.select(input, title); window.dispatchEvent(new Event('scientific-ask-input'));
}

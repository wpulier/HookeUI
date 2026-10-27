/** Explicit, revision-bound assistance. Never evaluates code or replaces a newer draft. */
export type PythonHelpMode = 'complete' | 'help';
type Answer = { format: 'scientific-python-assist-v1'; mode: PythonHelpMode; expression: string; matches: string[]; text: string; truncated: boolean };
const bytes = (text: string) => new TextEncoder().encode(text).length;
const identifier = /^[\p{ID_Start}_][\p{ID_Continue}]*$/u;

export function pythonAssistance(raw: unknown, mode: PythonHelpMode, expression: string): Answer {
  const fail = (): never => { throw new Error('Python returned invalid assistance. Retry the lookup.'); };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fail();
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).sort().join(',') !== 'expression,format,matches,mode,text,truncated' ||
      value.format !== 'scientific-python-assist-v1' || value.mode !== mode || value.expression !== expression ||
      typeof value.text !== 'string' || bytes(value.text) > 8192 || typeof value.truncated !== 'boolean' ||
      !Array.isArray(value.matches) || value.matches.length > 64 ||
      value.matches.some(name => typeof name !== 'string' || !identifier.test(name) || bytes(name) > 256) ||
      (mode === 'help' && value.matches.length)) return fail();
  return value as Answer;
}

export class ScientificPythonHelp {
  readonly element = document.createElement('div');
  private readonly status = document.createElement('p');
  private readonly output = document.createElement('div');
  private readonly buttons: HTMLButtonElement[] = [];
  private available = false;
  private pending = false;
  private epoch = 0;
  private closed = false;
  private readonly editor: HTMLTextAreaElement;
  private readonly ask: (mode: PythonHelpMode, expression: string) => Promise<unknown>;
  private readonly changed = () => this.reset();
  private readonly key = (event: KeyboardEvent) => {
    if (event.ctrlKey && event.code === 'Space') {
      event.preventDefault(); void this.lookup('complete');
    }
  };

  constructor(editor: HTMLTextAreaElement, ask: (mode: PythonHelpMode, expression: string) => Promise<unknown>) {
    this.editor = editor; this.ask = ask;
    this.element.className = 'scientific-python-help';
    for (const [mode, label] of [['complete', 'Complete name'], ['help', 'Help for name']] as const) {
      const button = document.createElement('button'); button.textContent = label;
      button.addEventListener('click', () => { void this.lookup(mode); });
      this.buttons.push(button); this.element.append(button);
    }
    const hint = document.createElement('p');
    hint.textContent = 'Place the cursor after a name, or select a dotted name. Ctrl+Space completes it. Properties and calls are never evaluated by lookup.';
    this.status.setAttribute('role', 'status');
    this.output.setAttribute('aria-label', 'Python assistance');
    this.element.append(hint, this.status, this.output);
    editor.addEventListener('input', this.changed); editor.addEventListener('select', this.changed);
    editor.addEventListener('keydown', this.key); this.paint();
  }

  setAvailable(value: boolean): void { this.available = value; this.paint(); }
  reset(): void { ++this.epoch; this.pending = false; this.status.textContent = ''; this.output.replaceChildren(); this.paint(); }
  close(): void {
    this.closed = true; ++this.epoch;
    this.editor.removeEventListener('input', this.changed); this.editor.removeEventListener('select', this.changed);
    this.editor.removeEventListener('keydown', this.key); this.element.remove();
  }
  private paint(): void {
    const disabled = this.closed || !this.available || this.pending;
    for (const button of this.buttons) button.disabled = disabled;
    this.output.querySelectorAll('button').forEach(button => { button.disabled = disabled; });
  }
  private async lookup(mode: PythonHelpMode): Promise<void> {
    if (this.closed || !this.available || this.pending) return;
    const source = this.editor.value, end = this.editor.selectionEnd, selectedStart = this.editor.selectionStart;
    const start = selectedStart === end ? end - (source.slice(0, end).match(/[\p{ID_Continue}._]*$/u)?.[0].length ?? 0) : selectedStart;
    const replacementEnd = selectedStart === end ? end + (source.slice(end).match(/^[\p{ID_Continue}]*/u)?.[0].length ?? 0) : end;
    const expression = source.slice(start, end);
    if (bytes(expression) > 256 || !expression || expression.split('.').some((part, i, all) =>
        !identifier.test(part) && !(mode === 'complete' && i === all.length - 1 && !part))) {
      this.status.textContent = 'Choose a name such as viewer.add_image. Calls and indexing require Run code.'; return;
    }
    const epoch = ++this.epoch;
    const current = () => !this.closed && this.epoch === epoch && this.editor.value === source &&
      this.editor.selectionStart === selectedStart && this.editor.selectionEnd === end;
    this.pending = true; this.status.textContent = 'Looking in this Python session…'; this.output.replaceChildren(); this.paint();
    try {
      const answer = pythonAssistance(await this.ask(mode, expression), mode, expression);
      if (!current()) return;
      this.status.textContent = answer.truncated ? 'Results were limited. Type a more specific name.' : '';
      const text = document.createElement('pre'); text.textContent = answer.text; this.output.append(text);
      for (const name of answer.matches) {
        const button = document.createElement('button'); button.textContent = name;
        button.addEventListener('click', () => {
          if (!current() || !this.available || this.pending) return;
          const replacement = expression.slice(0, expression.lastIndexOf('.') + 1) + name;
          this.editor.value = source.slice(0, start) + replacement + source.slice(replacementEnd);
          this.editor.focus(); this.editor.setSelectionRange(start + replacement.length, start + replacement.length);
          this.editor.dispatchEvent(new Event('input', { bubbles: true }));
        });
        this.output.append(button);
      }
    } catch (error) {
      if (current()) this.status.textContent = error instanceof Error ? error.message : 'Lookup failed. Retry; your code is kept.';
    } finally {
      if (!this.closed && this.epoch === epoch) {
        if (!current()) this.status.textContent = 'Code or selection changed. Look up the current name again.';
        this.pending = false; this.paint();
      }
    }
  }
}

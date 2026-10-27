/** Local script files become editor drafts only. Execution remains the console's explicit Run action. */
export const SCRIPT_BYTES = 256 * 1024;
export async function readPythonScript(file: File): Promise<string> {
  if (!/\.py$/i.test(file.name) || file.size > SCRIPT_BYTES || file.size < 1)
    throw new Error('Choose a UTF-8 .py file no larger than 256 KiB. Your current draft is kept.');
  const data = await file.arrayBuffer();
  if (data.byteLength !== file.size || data.byteLength > SCRIPT_BYTES)
    throw new Error('The script size changed while reading. Choose the file again; your draft is kept.');
  let source: string;
  try { source = new TextDecoder('utf-8', { fatal: true }).decode(data); }
  catch { throw new Error('This script is not UTF-8 text. Save a UTF-8 copy and choose it again; your draft is kept.'); }
  if (source.includes('\0') || !source.trim()) throw new Error('Choose a nonempty Python text file without null bytes. Your draft is kept.');
  return source;
}

export class ScientificScripts {
  private readonly input: HTMLTextAreaElement;
  private readonly changed: () => void;
  readonly element = document.createElement('section');
  private readonly file = document.createElement('input');
  private readonly status = document.createElement('p');
  private readonly replace = document.createElement('button');
  private readonly cancel = document.createElement('button');
  private readonly download = document.createElement('a');
  private candidate: string | null = null;
  private request = 0;
  private revision = 0;
  private closed = false;
  private url = '';

  constructor(input: HTMLTextAreaElement, changed: () => void) {
    this.input = input; this.changed = changed;
    const label = document.createElement('label'); label.textContent = 'Open Python script';
    this.file.type = 'file'; this.file.accept = '.py'; label.append(this.file);
    this.file.addEventListener('change', () => { const file = this.file.files?.[0]; this.file.value = ''; if (file) void this.load(file); });
    this.status.setAttribute('role', 'status');
    this.replace.type = this.cancel.type = 'button';
    this.replace.textContent = 'Replace draft with this script';
    this.cancel.textContent = 'Keep current draft';
    this.replace.addEventListener('click', () => { if (!this.closed && this.candidate !== null) this.use(this.candidate); });
    this.cancel.addEventListener('click', () => {
      this.candidate = null; this.status.textContent = 'Current draft kept. Nothing ran.'; this.paint();
    });
    this.download.textContent = 'Download draft as .py'; this.download.download = 'hooke-script.py';
    this.input.addEventListener('input', this.edited);
    const note = document.createElement('p');
    note.textContent = 'Open a UTF-8 .py file (up to 256 KiB) to inspect or edit it. Nothing runs until Run code. Download saves source text only, without arrays or Python variables.';
    this.element.append(label, this.download, note, this.status, this.replace, this.cancel);
    this.paint();
  }

  private readonly edited = () => { ++this.revision; this.paint(); };

  private async load(file: File): Promise<void> {
    const request = ++this.request, revision = this.revision;
    this.candidate = null; this.status.textContent = 'Reading this script…'; this.paint();
    try {
      const source = await readPythonScript(file);
      if (this.closed || request !== this.request) return;
      if (!this.input.value && this.revision === revision) { this.use(source); return; }
      this.candidate = source;
      this.status.textContent = `${file.name.slice(0, 240)} is ready. Your existing draft is kept. Download it first if needed, then explicitly replace it or keep it. Nothing ran.`;
      this.paint();
    } catch (error) {
      if (!this.closed && request === this.request) this.status.textContent = error instanceof Error ? error.message : 'The script could not be read. Your draft is kept; choose the file again.';
    }
  }

  private use(source: string): void {
    this.input.value = source; this.candidate = null; ++this.revision;
    this.status.textContent = 'Script loaded into the editor. Inspect it, then press Run code when ready.';
    this.changed(); this.paint(); this.input.focus();
  }

  private paint(): void {
    if (this.closed) return;
    this.replace.hidden = this.cancel.hidden = this.candidate === null;
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = '';
    this.download.hidden = !this.input.value || new TextEncoder().encode(this.input.value).length > SCRIPT_BYTES;
    this.download.href = '';
    if (!this.download.hidden) {
      this.url = URL.createObjectURL(new Blob([this.input.value], { type: 'text/x-python;charset=utf-8' }));
      this.download.href = this.url;
    }
  }

  close(): void {
    this.closed = true; ++this.request; this.candidate = null;
    this.input.removeEventListener('input', this.edited);
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = ''; this.file.value = ''; this.element.replaceChildren();
  }
}

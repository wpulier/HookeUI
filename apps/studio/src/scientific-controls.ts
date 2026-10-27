import { parameterControl, type Field } from './parameter-fields';

type Common = { id: string; label: string; help: string; enabled: boolean; visible: boolean };
export type ScientificControl = Common & (
  { kind: 'button' } | { kind: 'label'; value: string } |
  { kind: 'text'; value: string; multiline: boolean } | { kind: 'boolean'; value: boolean } |
  { kind: 'integer' | 'number'; value: number; min: number; max: number; step: number | null } |
  { kind: 'choice'; value: string | null; choices: { id: string; label: string }[] }
);
export type ScientificPanel = { format: 'scientific-controls-v1'; panel: string; revision: number; controls: ScientificControl[] };
export type ControlRequest = { panel: string; revision: number; control: string; op: 'set' | 'invoke'; value?: unknown };

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string' && value.length <= 1000;
const identity = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
const number = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER;
const keys = (value: Record<string, unknown>, expected: string[]) => Object.keys(value).sort().join(',') === expected.sort().join(',');

export function scientificPanel(value: unknown): ScientificPanel {
  const fail = () => { throw new Error('The scientific panel returned unsupported controls. Reopen its saved work.'); };
  if (!object(value) || !keys(value, ['format', 'panel', 'revision', 'controls']) || value.format !== 'scientific-controls-v1' ||
      !identity(value.panel) || !Number.isSafeInteger(value.revision) || Number(value.revision) < 1 ||
      !Array.isArray(value.controls) || value.controls.length > 128) return fail();
  const seen = new Set<string>();
  let characters = 0;
  for (const row of value.controls) {
    if (!object(row) || !identity(row.id) || seen.has(row.id) || !text(row.label) || !text(row.help) ||
        typeof row.enabled !== 'boolean' || typeof row.visible !== 'boolean') return fail();
    seen.add(row.id);
    characters += row.id.length + row.label.length + row.help.length;
    const fields = ['id', 'label', 'help', 'enabled', 'visible', 'kind'];
    if (row.kind === 'button') { }
    else if (row.kind === 'label' || row.kind === 'text') {
      fields.push('value');
      if (!text(row.value)) return fail();
      characters += row.value.length;
      if (row.kind === 'text') { fields.push('multiline'); if (typeof row.multiline !== 'boolean') return fail(); }
    } else if (row.kind === 'boolean') { fields.push('value'); if (typeof row.value !== 'boolean') return fail(); }
    else if (row.kind === 'number' || row.kind === 'integer') {
      fields.push('value', 'min', 'max', 'step');
      if (!number(row.value) || !number(row.min) || !number(row.max) || row.min > row.max || row.value < row.min || row.value > row.max ||
          (row.step !== null && (!number(row.step) || row.step <= 0)) || (row.kind === 'integer' && !Number.isSafeInteger(row.value))) return fail();
    } else if (row.kind === 'choice') {
      fields.push('value', 'choices');
      if (!Array.isArray(row.choices) || row.choices.length > 256) return fail();
      const choices = new Set<string>();
      for (const choice of row.choices) {
        if (!object(choice) || !keys(choice, ['id', 'label']) || !identity(choice.id) || choices.has(choice.id) || !text(choice.label)) return fail();
        choices.add(choice.id);
        characters += choice.id.length + choice.label.length;
        if (characters > 64 * 1024) return fail();
      }
      if (row.value !== null && (typeof row.value !== 'string' || !choices.has(row.value))) return fail();
    } else return fail();
    if (!keys(row, fields)) return fail();
    if (characters > 64 * 1024) return fail();
  }
  if (new TextEncoder().encode(JSON.stringify(value)).length > 64 * 1024) return fail();
  return structuredClone(value) as ScientificPanel;
}

type Input = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
type Row = { box: HTMLElement; input?: Input; button?: HTMLButtonElement; definition: string; choices?: string };
type Draft = { panel: string; revision: number; generation: number; value: unknown; label: string; display: string };
type Transport = { send(request: ControlRequest): Promise<unknown>; refresh(): Promise<unknown>; changed?(): void };
export class ScientificControls {
  readonly root: HTMLElement;
  private readonly transport: Transport;
  private state: ScientificPanel | null = null;
  private session: string | null = null;
  private rows = new Map<string, Row>();
  private drafts = new Map<string, Draft>();
  private generation = 0;
  private busy = false;
  private available = true;
  private canRefresh = true;
  private uncertain = false;
  private reportedSettled = true;
  private closed = false;
  private body = document.createElement('div');
  private status = document.createElement('p');
  private recovery = document.createElement('ul');
  private discard = document.createElement('button');
  private refresh = document.createElement('button');

  settled(): boolean { return this.drafts.size === 0 && !this.busy && !this.uncertain; }
  constructor(root: HTMLElement, transport: Transport) {
    this.root = root; this.transport = transport;
    this.status.setAttribute('role', 'status');
    this.discard.textContent = 'Discard unsent changes';
    this.discard.addEventListener('click', () => { this.drafts.clear(); this.status.textContent = ''; this.paint(); });
    this.refresh.textContent = 'Check current settings';
    this.refresh.addEventListener('click', () => void this.recheck());
    root.append(this.body, this.status, this.recovery, this.discard, this.refresh);
  }

  bindSession(session: string): void {
    if (this.session === session) return;
    this.session = session; this.state = null; this.uncertain = this.busy = false; this.suspend();
  }

  setAvailable(available: boolean, canRefresh = available): void { this.available = available; this.canRefresh = canRefresh; this.paint(); }
  suspend(): void { this.setAvailable(false); }

  update(raw: unknown) {
    if (this.closed) return;
    const next = scientificPanel(raw);
    if (this.state && next.panel !== this.state.panel) throw new Error('This reply belongs to another scientific panel.');
    if (this.state && next.revision < this.state.revision) return;
    if (this.state && next.revision === this.state.revision && JSON.stringify(next) !== JSON.stringify(this.state))
      throw new Error('The scientific panel changed without a new revision.');
    if (!this.state) this.status.textContent = '';
    this.state = next; this.available = this.canRefresh = true;
    this.paint();
  }

  close() { this.closed = true; this.root.replaceChildren(); this.rows.clear(); this.drafts.clear(); }

  private paint() {
    if (this.closed) return;
    const settled = this.settled();
    if (settled !== this.reportedSettled) { this.reportedSettled = settled; this.transport.changed?.(); }
    if (!this.state || !this.available) {
      for (const row of this.rows.values()) { if (row.input) row.input.disabled = true; if (row.button) row.button.disabled = true; }
      this.refresh.disabled = this.busy || !this.canRefresh; this.discard.disabled = true;
      return;
    }
    const stale = [...this.drafts.values()].some(draft => (draft.panel !== this.state!.panel || draft.revision !== this.state!.revision));
    if (stale && !this.uncertain) this.status.textContent = 'Settings changed while you were editing. Your unsent values are kept; review or discard them before continuing.';
    const ids = new Set(this.state.controls.map(control => control.id));
    for (const [id, row] of this.rows) if (!ids.has(id)) { row.box.remove(); this.rows.delete(id); }
    for (const [index, control] of this.state.controls.entries()) {
      const signature = JSON.stringify([control.kind, control.kind === 'text' && control.multiline]);
      let row = this.rows.get(control.id);
      if (!row || (row.definition !== signature && !this.drafts.has(control.id))) {
        row?.box.remove(); row = this.make(control, signature); this.rows.set(control.id, row);
      }
      row.box.hidden = !control.visible;
      const label = row.box.querySelector('label'); if (label) label.textContent = control.label;
      if (row.input) {
        row.input.title = control.help;
        if (control.kind === 'number' || control.kind === 'integer') {
          const input = row.input as HTMLInputElement;
          input.min = String(control.min); input.max = String(control.max); input.step = String(control.step ?? 'any');
        }
        let hint = row.box.querySelector('small');
        if (!hint && control.help) { hint = document.createElement('small'); hint.id = `${row.input.id}-hint`; row.box.append(hint); }
        if (hint) { hint.textContent = control.help; row.input.setAttribute('aria-describedby', hint.id); }
        row.input.disabled = !control.enabled || this.busy || this.uncertain || stale;
        if (!this.drafts.has(control.id) && 'value' in control) {
          if (control.kind === 'choice' && row.choices !== JSON.stringify(control.choices)) {
            row.input.replaceChildren();
            for (const choice of [{ id: '', label: 'Choose…' }, ...control.choices]) {
              const option = document.createElement('option'); option.value = choice.id; option.textContent = choice.label; row.input.append(option);
            }
            row.choices = JSON.stringify(control.choices);
          }
          if (control.kind === 'boolean') (row.input as HTMLInputElement).checked = control.value;
          else row.input.value = String(control.value ?? '');
        }
      }
      if (row.button) {
        row.button.textContent = control.label; row.button.title = control.help;
        row.button.disabled = !control.enabled || this.busy || this.uncertain || this.drafts.size > 0;
      }
      if (control.kind === 'label') row.box.textContent = control.value;
      if (this.body.children[index] !== row.box) this.body.insertBefore(row.box, this.body.children[index] ?? null);
    }
    this.discard.hidden = this.drafts.size === 0;
    this.recovery.replaceChildren();
    if (stale || this.uncertain) for (const draft of this.drafts.values()) {
      const item = document.createElement('li'); item.textContent = `Unsent ${draft.label}: ${draft.display}`; this.recovery.append(item);
    }
    this.discard.disabled = this.busy;
    this.refresh.disabled = this.busy || !this.canRefresh;
    this.root.setAttribute('aria-busy', String(this.busy));
  }

  private make(control: ScientificControl, definition: string): Row {
    if (control.kind === 'button') {
      const button = document.createElement('button'); button.textContent = control.label; button.title = control.help;
      button.addEventListener('click', () => { if (!button.disabled) void this.send(control.id, 'invoke'); });
      return { box: button, button, definition };
    }
    if (control.kind === 'label') return { box: document.createElement('p'), definition };
    const choices = control.kind === 'choice' ? control.choices : [];
    const field: Field = { key: control.id, label: control.label, hint: control.help, required: true, numeric: false,
      kind: control.kind === 'integer' ? 'number' : control.kind, choices: choices.map(choice => choice.id),
      said: Object.fromEntries(choices.map(choice => [choice.id, choice.label])), initial: control.value,
      ...(control.kind === 'number' || control.kind === 'integer' ? { min: control.min, max: control.max, ...(control.step === null ? {} : { step: control.step }) } : {}) };
    const values: Record<string, unknown> = { [control.id]: control.value };
    const box = parameterControl(this.state!.panel, field, values);
    let input = (box.querySelector('input') ?? box.querySelector('select')) as Input;
    if (control.kind === 'text' && control.multiline) {
      const area = document.createElement('textarea'); area.id = input.id; area.title = control.help;
      area.value = control.value; box.insertBefore(area, input); input.remove(); input = area;
    }
    input.addEventListener('input', () => {
      if (input.disabled || this.closed) return;
      const value = control.kind === 'boolean' ? (input as HTMLInputElement).checked : input.value;
      const current = this.state!.controls.find(row => row.id === control.id)!;
      const display = current.kind === 'choice' ? current.choices.find(choice => choice.id === value)?.label ?? 'Unavailable choice' : String(value);
      this.drafts.set(control.id, { panel: this.state!.panel, revision: this.state!.revision, generation: ++this.generation, value, label: current.label, display });
      this.paint();
    });
    input.addEventListener('change', () => { if (!input.disabled) void this.send(control.id, 'set'); });
    return { box, input, definition };
  }

  private async send(id: string, op: 'set' | 'invoke') {
    if (!this.available || !this.state || this.closed || this.busy || this.uncertain) return;
    const control = this.state.controls.find(control => control.id === id), draft = this.drafts.get(id);
    if (!control?.enabled || !control.visible || (op === 'set' && !draft) || (op === 'invoke' && this.drafts.size)) return;
    if (draft && (draft.panel !== this.state.panel || draft.revision !== this.state.revision)) { this.paint(); return; }
    let value = draft?.value;
    if (control.kind === 'integer' || control.kind === 'number') {
      value = value === '' ? NaN : Number(value);
      if (!number(value) || value < control.min || value > control.max || (control.kind === 'integer' && !Number.isSafeInteger(value))) {
        this.status.textContent = `Enter a valid value for ${control.label}.`; return;
      }
    }
    const request: ControlRequest = { panel: this.state.panel, revision: this.state.revision, control: id, op,
      ...(op === 'set' ? { value } : {}) };
    const session = this.session;
    this.busy = true; this.status.textContent = 'Applying…'; this.paint();
    try {
      const reply = scientificPanel(await this.transport.send(request));
      if (this.closed || this.session !== session) return;
      if (reply.panel !== request.panel || reply.revision <= request.revision) throw new Error('No current acknowledgement was returned.');
      this.update(reply);
      if (draft && this.drafts.get(id)?.generation === draft.generation) this.drafts.delete(id);
      this.status.textContent = 'Applied.';
    } catch {
      if (this.closed || this.session !== session) return;
      this.uncertain = true;
      this.status.textContent = 'The callback did not return a confirmed result. It may have run. Check current settings before continuing; unsent values are kept.';
    } finally { if (this.session === session) { this.busy = false; this.paint(); } }
  }

  private async recheck() {
    if (!this.canRefresh || this.closed || this.busy) return;
    const session = this.session;
    this.busy = true; this.paint();
    try {
      const reply = scientificPanel(await this.transport.refresh()); if (this.closed || this.session !== session) return;
      if (this.state && reply.revision < this.state.revision) throw new Error('Settings check returned an older revision.');
      this.update(reply); this.uncertain = false;
      this.status.textContent = 'Current settings loaded. Review them before another action; the previous callback may have run.';
    } catch { if (!this.closed && this.session === session) this.status.textContent = 'Settings could not be checked. Your saved work and unsent values are kept.'; }
    finally { if (this.session === session) { this.busy = false; this.paint(); } }
  }
}

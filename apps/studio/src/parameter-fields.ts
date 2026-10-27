export type FieldKind = 'text' | 'number' | 'boolean' | 'choice';
export interface Field {
  key: string; label: string; kind: FieldKind; required: boolean; hint: string;
  /** Its choices (the values sent) and how each is said. */
  choices: string[]; said: Record<string, string>;
  /** A number the tool takes, chosen from a list ("40" is sent as 40). */
  numeric: boolean;
  /** What it starts as (a yes/no the tool takes as yes unless told otherwise). */
  initial?: unknown;
  /** A capability's declared range, step and unit (catalog format 2), and whether it is under More options. */
  min?: number; max?: number; step?: number; unit?: string; advanced?: boolean;
  /** The model options it applies to (contract/actions.json `fields` … `for`): shown and sent only when one is chosen. */
  for?: string[];
}

export function parameterError(fields: Field[], values: Record<string, unknown>): string | null {
  for (const field of fields) {
    const value = values[field.key];
    if (value === undefined || value === '') {
      if (field.required && field.initial === undefined) return `Fill in “${field.label}”`;
      continue;
    }
    if (field.kind === 'number' || (field.kind === 'choice' && field.numeric)) {
      const number = Number(value);
      if (!Number.isFinite(number)) return `Enter a finite number for ${field.label}`;
      if (field.min !== undefined && number < field.min) return `${field.label} must be at least ${field.min}`;
      if (field.max !== undefined && number > field.max) return `${field.label} must be at most ${field.max}`;
      if (field.step && Math.abs((number - (field.min ?? 0)) / field.step - Math.round((number - (field.min ?? 0)) / field.step)) > 1e-8)
        return `${field.label} must use steps of ${field.step}`;
    }
    if (field.kind === 'choice' && !field.choices.includes(String(value))) return `Choose a listed value for ${field.label}`;
  }
  return null;
}

export function parameterValues(fields: Field[], values: Record<string, unknown>): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const field of fields) {
    const value = values[field.key];
    if (value === undefined || value === '') continue;
    if (field.kind === 'number' || (field.kind === 'choice' && field.numeric)) {
      const number = Number(value);
      if (Number.isFinite(number)) args[field.key] = number;
    } else if (field.kind === 'boolean') {
      args[field.key] = value === true || value === 'true';
    } else {
      args[field.key] = String(value).trim();
    }
  }
  return args;
}

/** `run`: the run page's look (the mock's field): the label alone (its unit beside the value), the declared default as
 * the value shown (not sent until changed: empty sends the default), and a slider beside a bounded number. */
export interface ControlLook { run?: boolean }
function decimals(step: number | undefined): number {
  const text = String(step ?? 1);
  return text.includes('.') ? text.split('.')[1].length : 0;
}
export function parameterControl(prefix: string, field: Field, values: Record<string, unknown>, look: ControlLook = {}): HTMLElement {
  const id = `${prefix}-${field.key}`;
  const input = document.createElement(field.kind === 'choice' ? 'select' : 'input');
  input.id = id;
  if (field.kind === 'choice') {
    const choices = field.required ? field.choices : ['', ...field.choices];
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice ? field.said[choice] ?? choice : `Default${field.initial === undefined ? '' : ` (${field.initial})`}`;
      input.append(option);
    }
    input.value = String(values[field.key] ?? (field.required ? field.initial ?? field.choices[0] ?? '' : ''));
    if (field.required) values[field.key] = input.value;
  } else {
    const control = input as HTMLInputElement;
    control.type = field.kind === 'boolean' ? 'checkbox' : field.kind === 'number' ? 'number' : 'text';
    control.setAttribute('type', control.type);
    control.autocomplete = 'off';
    control.checked = (values[field.key] ?? field.initial) === true;
    control.value = String(values[field.key] ?? '');
    control.step = String(field.step ?? 'any');
    if (field.min !== undefined) control.min = String(field.min);
    if (field.max !== undefined) control.max = String(field.max);
    if (field.initial !== undefined) control.placeholder = String(field.initial);
  }
  input.dataset.toolboxFocus = id;
  input.title = field.hint;
  for (const event of ['input', 'change']) input.addEventListener(event, () => {
    values[field.key] = field.kind === 'boolean' ? (input as HTMLInputElement).checked
      : field.kind === 'number' && (input as HTMLInputElement).validity?.badInput ? Number.NaN : input.value;
  });
  const box = document.createElement('div');
  box.className = field.kind === 'boolean' ? 'action-check' : 'action-field';
  const label = document.createElement('label');
  label.htmlFor = id;
  label.textContent = field.unit && !look.run ? `${field.label} (${field.unit})` : field.label;
  if (look.run && field.kind === 'number') {
    // The run page: the value in a box with its unit, a slider beside it when the range is bounded and stepped.
    const control = input as HTMLInputElement;
    if (control.value === '' && typeof field.initial === 'number') control.value = field.initial.toFixed(decimals(field.step));
    const numbox = document.createElement('span');
    numbox.className = 'numbox';
    numbox.append(control);
    if (field.unit) { const unit = document.createElement('span'); unit.className = 'numbox-unit'; unit.textContent = field.unit; numbox.append(unit); }
    const ctl = document.createElement('span');
    ctl.className = 'field-ctl';
    if (field.min !== undefined && field.max !== undefined && field.step && (field.max - field.min) / field.step <= 1000) {
      const slider = document.createElement('input');
      slider.type = 'range'; slider.setAttribute('type', 'range');
      slider.min = String(field.min); slider.max = String(field.max); slider.step = String(field.step);
      slider.value = control.value || String(field.initial ?? field.min);
      slider.setAttribute('aria-label', field.label);
      slider.tabIndex = -1; // the number box is the keyboard's control; the slider is the pointer's
      slider.addEventListener('input', () => {
        control.value = Number(slider.value).toFixed(decimals(field.step));
        control.dispatchEvent(new Event('input'));
      });
      control.addEventListener('input', () => { if (control.value !== '' && Number.isFinite(Number(control.value))) slider.value = control.value; });
      ctl.append(slider);
    }
    ctl.append(numbox);
    box.append(label, ctl);
  } else box.append(label, input);
  if (field.hint) {
    const hint = document.createElement('small');
    hint.id = `${id}-hint`; hint.textContent = field.hint;
    input.setAttribute('aria-describedby', hint.id); box.append(hint);
  }
  return box;
}

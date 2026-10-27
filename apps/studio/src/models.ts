import type { Panel } from './panels';
import { openAddKey, sourceWords } from './addkey';
import type { ArenaKind, ArenaState, CapabilitiesWire, KeyFacts, OptionGroup, Role, RoleOption, Settings } from './protocol';
import { optionNote, readinessWords } from './readiness';

type Group = { key: keyof Settings; from: OptionGroup; title: string; hint: string };

// "Compare with" is not a role: a second model from the same list answers typed questions too.
const COMPARE: Group = { key: 'compare', from: 'vision', title: 'Compare with', hint: 'a second model answers too, without outlines' };

/** The two popups in the header, side by side and built alike:
 * - "models": what does each job. Changing a choice changes the model itself (roles of kind
 *   "model" in the capability list, and "Compare with").
 * - "behaviour": how the app works with those models and with you. Changing a choice keeps the
 *   models and changes how they are pointed, told, checked or heard (roles of kind "method" and
 *   "policy"), plus your own preferences (speaking up, explaining, spending, the objective). */
export type PickerScope = 'models' | 'behaviour';

/** Which option groups a popup lists, in the capability list's order: one rule, the role's kind.
 * Every role that is a setting is in exactly one of the two (capabilities.test.ts checks). */
export function pickerGroups(scope: PickerScope, roles: Role[]): Group[] {
  const out: Group[] = roles
    .filter((r) => !r.fixed && (r.kind === 'model') === (scope === 'models'))
    .map((r) => ({ key: r.id as keyof Settings, from: r.id as OptionGroup, title: r.title, hint: r.hint ?? r.purpose }));
  if (scope === 'models') {
    const at = out.findIndex((g) => g.key === 'outline');
    out.splice(at < 0 ? out.length : at + 1, 0, COMPARE);
  }
  return out;
}

/** A settings popup (top right): which model does each job, or how the app behaves. It stays
 * open while you change options; the server's echo only updates the checked state in place. A
 * click outside closes it, and so does Escape (panels.ts), even from one of its fields. */
export class SettingsPicker implements Panel {
  readonly escapePriority = 100;
  readonly id: PickerScope;
  readonly whileTyping = true;
  private readonly scope: PickerScope;
  private readonly form: HTMLFormElement;
  private readonly toggle: HTMLButtonElement;
  private readonly onChange: (changes: Partial<Settings>) => void;
  private builtFor = ''; // what the form was built from (rebuilt only when that changes)
  private readonly onObjective: (objective: number | null) => void;
  private readonly diagnostics?: HTMLElement;
  /** What it opens under (the gear, whose menu holds its toggle); else its own toggle. */
  anchor: HTMLElement | null = null;
  /** The control it was opened from instead of its toggle (the composer's model chip, openFrom): it opens just above
   * that control, over everything, and that control closes it again; a click outside both closes it. */
  private opener: HTMLElement | null = null;
  private edge: HTMLElement | null = null;

  constructor(scope: PickerScope, form: HTMLFormElement, toggle: HTMLButtonElement, onChange: (changes: Partial<Settings>) => void,
    onObjective: (objective: number | null) => void = () => {}, diagnostics?: HTMLElement) {
    this.id = scope;
    this.scope = scope;
    this.form = form;
    this.toggle = toggle;
    this.onChange = onChange;
    this.onObjective = onObjective;
    this.diagnostics = diagnostics;
    toggle.addEventListener('click', () => { this.opener = null; this.setOpen(this.form.hidden === true); });
    closeOnOutsideClick(form, toggle, () => this.setOpen(false), () => this.opener);
    const commit = (input: HTMLInputElement) => {
      if (input.name === 'objective') {
        this.onObjective(input.value ? Number(input.value) : null);
        return;
      }
      this.onChange({ [input.name]: input.type === 'checkbox' ? input.checked : input.value } as Partial<Settings>);
    };
    form.addEventListener('change', (event) => commit(event.target as HTMLInputElement));
    form.addEventListener('submit', (event) => {
      event.preventDefault(); // Enter in the allowance field must never navigate away from the Studio.
      const input = document.activeElement as HTMLInputElement | null;
      // Commit only the focused text field, through the same validation path as a change on blur.
      if (input?.tagName === 'INPUT' && input.type === 'text' && form.contains(input)) commit(input);
    });
  }

  /** Open (or close, when it is open from there) from `opener`, a control that is not its toggle. It opens 6 px above
   * that control with its right edge on `edge`'s (by default the opener's form: the composer the chip sits in). */
  openFrom(opener: HTMLElement, edge: HTMLElement | null = opener.closest?.('form') ?? null): void {
    if (!this.form.hidden && this.opener === opener) { this.setOpen(false); return; }
    this.opener = opener;
    this.edge = edge;
    this.setOpen(true);
    // Focus moves into it, to its first control ("All models and setup"), so Tab goes through it before Talk and Send;
    // Escape closes it and gives focus back to the chip (close()).
    this.form.querySelector<HTMLElement>('.models-all-link, a[href], button:not(:disabled), input:not(:disabled)')?.focus?.({ preventScroll: true });
  }

  private setOpen(open: boolean): void {
    this.form.hidden = !open;
    this.opener?.setAttribute('aria-expanded', String(open));
    if (open && this.opener) {
      // Over everything, 6 px above the control it was opened from, its right edge on the composer's, inside the
      // window. Its width is the opened look's (360 px), so that look is set before it is measured.
      this.form.dataset.opener = 'above';
      const r = this.opener.getBoundingClientRect();
      const right = (this.edge ?? this.opener).getBoundingClientRect().right;
      const width = this.form.offsetWidth || 360;
      const left = Math.max(8, Math.min(Math.round(right - width), Math.round(innerWidth - width - 8)));
      this.form.setAttribute('style', `left: ${left}px; bottom: ${Math.round(innerHeight - r.top + 6)}px; max-height: ${Math.max(160, Math.min(Math.round(r.top - 16), Math.round(innerHeight * 0.7)))}px`);
    } else if (open) {
      delete this.form.dataset.opener;
      // Under its own button, wherever the header has put it (a wrapped header moves the buttons
      // left; at the right edge this is where the models popup always was); measured once shown, as
      // a hidden popup has no offset parent.
      // Not `form.style`: a form's fields are its properties, and the outline-style choice is named "style".
      const bar = this.form.offsetParent?.getBoundingClientRect();
      if (bar) {
        const under = bar.right - (this.anchor ?? this.toggle).getBoundingClientRect().right;
        const room = bar.width - this.form.offsetWidth - 16; // never past the left edge (a phone: the same place as models)
        this.form.setAttribute('style', `right: ${Math.round(Math.max(16, Math.min(under, room)))}px`);
      }
    }
    this.toggle.setAttribute('aria-expanded', String(open));
  }

  isOpen(): boolean {
    return !this.form.hidden;
  }

  close(): void {
    const back = this.opener ?? this.anchor ?? this.toggle;
    this.setOpen(false);
    back.focus();
  }

  footprint(): null {
    return null; // a menu over everything for a moment: nothing moves for it
  }

  /** Both menus draw only the capability list (capabilities.py `wire`), the one the Capabilities
   * page draws: each role's options, the one in use and why one can't be chosen, the preferences'
   * values (the objective too), what each role is used for. Nothing here is kept in step with
   * another message: a change comes back as a new list, and both menus and the page redraw from it. */
  render(wire: CapabilitiesWire): void {
    const values = pickerValues(wire);
    const vision = wire.roles.find((r) => r.id === 'vision');
    if (this.scope === 'models') {
      const visionLabel = vision?.options.find((o) => o.id === vision.current)?.label ?? vision?.current ?? 'Models';
      // On the bar it is one word with its icon; the model seeing for it is said on hover and to a screen reader.
      const words = this.toggle.querySelector('.bar-models-words');
      if (words) words.textContent = 'Models';
      else this.toggle.textContent = `Models · ${visionLabel}`;
      this.toggle.title = `Models: ${visionLabel} sees for it. Choose which models do each job`;
      this.toggle.setAttribute('aria-label', `Models: ${visionLabel}. Choose which models do each job`);
    } else {
      this.toggle.textContent = 'Behaviour';
      this.toggle.title = 'Choose how it works with the models and with you';
      this.toggle.setAttribute('aria-label', 'Behaviour: choose how it works with the models and with you');
    }
    // Rebuilt when what is offered changes, not when a choice does ("Compare with" lists all but the model in use).
    const signature = JSON.stringify([wire.roles.map((r) => [r.id, r.title, r.hint, r.purpose, r.label, r.fallback, r.powers, r.fallbackFor,
      r.options.map((o) => [o.id, o.label, o.said, o.note, o.available, o.why, o.readiness])]), wire.setup?.keys, vision?.current, wire.capabilities.map((c) => [c.id, c.title])]);
    if (signature !== this.builtFor) {
      this.builtFor = signature;
      this.build(wire);
    }
    if (this.scope === 'models') {
      for (const group of this.form.querySelectorAll<HTMLElement>('[data-picker-group]')) {
        const key = group.dataset.pickerGroup!;
        const role = wire.roles.find(r => r.id === (key === 'compare' ? 'vision' : key));
        const current = role?.options.find(o => o.id === values[key]);
        const name = group.querySelector<HTMLElement>('.model-current');
        const readiness = group.querySelector<HTMLElement>('.model-readiness');
        if (name) name.textContent = current?.said ?? current?.label ?? (key === 'compare' ? 'No comparison' : String(values[key] ?? 'Not chosen'));
        if (readiness) readiness.textContent = current
          ? current.available ? 'Ready' : current.readiness?.word || current.why || 'Needs setup' : '';
      }
    }
    for (const input of this.form.querySelectorAll<HTMLInputElement>('input')) {
      const value = values[input.name];
      if (input.type === 'text') {
        if (document.activeElement !== input) input.value = value === null || value === undefined ? '' : String(value);
      } else input.checked = input.type === 'checkbox' ? value === true : String(value ?? '') === input.value;
    }
  }

  private build(wire: CapabilitiesWire): void {
    const expanded = new Set([...this.form.querySelectorAll<HTMLElement>('[data-picker-group]')]
      .filter(group => group.getAttribute('open') !== null).map(group => group.dataset.pickerGroup));
    const focused = document.activeElement as HTMLInputElement | null;
    const focusedControl = focused && this.form.contains(focused) ? focused.dataset.pickerControl : undefined;
    const focusedGroup = focused?.closest<HTMLElement>('[data-picker-group]')?.dataset.pickerGroup;
    const summaryFocused = focused?.tagName === 'SUMMARY';
    const focusKey = focused && this.form.contains(focused) ? `${focused.name}=${focused.value}` : null;
    const roles = new Map(wire.roles.map((r) => [r.id, r]));
    const titles = new Map(wire.capabilities.map((c) => [c.id, c.title]));
    const vision = roles.get('vision');
    const fixed = this.scope === 'models' ? fixedModels(wire, titles) : [];
    this.form.replaceChildren(
      ...(this.scope === 'models' && wire.models ? [allModelsLink(wire, () => this.setOpen(false))] : []),
      ...(this.scope === 'behaviour' && this.diagnostics ? [this.diagnostics] : []),
      ...pickerGroups(this.scope, wire.roles).map(({ key, from, title, hint }) => {
        const role = roles.get(from);
        const choices: RoleOption[] = key === 'compare'
          ? [{ id: '', label: 'No comparison', note: 'one answer at a time', available: true },
            ...(vision?.options ?? []).filter((o) => o.id !== vision?.current && o.runs)]
          : role?.options ?? [];
        const choicesSet = fieldset(title, hint,
          choices.map((option) =>
            // A model is named with where it runs ("EdgeTAM (on this Mac)", names.said); a way of working by its label.
            choiceLabel('radio', key, option.id, role?.kind === 'model' ? option.said ?? option.label : option.label,
              optionNote(option.note, option.available, option.readiness, option.why), option.available)),
          key === 'compare' ? '' : usedBy(role, titles));
        if (this.scope !== 'models') return choicesSet;
        const group = disclosure(key, title, [choicesSet]);
        const summary = group.querySelector('summary')!;
        const name = document.createElement('span'); name.className = 'model-current';
        const readiness = document.createElement('span'); readiness.className = 'model-readiness';
        summary.append(name, readiness);
        return group;
      }),
      ...(this.scope === 'models' ? [
        ...(wire.setup?.keys ? [disclosure('keys', 'API keys', [keysFieldset(wire.setup.keys)])] : []),
        ...(fixed.length ? [disclosure('fixed', 'Supporting models', fixed)] : []),
      ] : this.preferences()),
    );
    for (const group of this.form.querySelectorAll<HTMLElement>('[data-picker-group]')) {
      if (expanded.has(group.dataset.pickerGroup)) group.setAttribute('open', '');
      if (summaryFocused && focusedGroup === group.dataset.pickerGroup) group.querySelector<HTMLElement>('summary')?.focus({ preventScroll: true });
    }
    if (focusedControl) [...this.form.querySelectorAll<HTMLElement>('[data-picker-control]')]
      .find(control => control.dataset.pickerControl === focusedControl)?.focus({ preventScroll: true });
    if (!summaryFocused && !focusedControl && focusKey) this.form.querySelector<HTMLInputElement>(`input[name="${focusKey.split('=')[0]}"][value="${focusKey.split('=')[1]}"]`)?.focus();
  }

  /** Your own preferences: not a model or a way of using one, so they sit under behaviour. */
  private preferences(): HTMLFieldSetElement[] {
    return [
      fieldset('Speaking up', 'what it says and shows without being asked', [
        choiceLabel('checkbox', 'quieter', 'on', 'Quieter', 'only say what I asked for; other findings go to the notebook', true),
        choiceLabel('checkbox', 'userLeads', 'on', 'Let me find things', 'it points and highlights only when I ask; also by voice ("let me find it myself")', true),
      ]),
      fieldset('Explaining', 'how it explains, kept until you change it; also by voice ("explain it more simply", "be technical", "give me hints first")', [
        choiceLabel('radio', 'level', 'simple', 'Simply', 'everyday words, terms explained', true),
        choiceLabel('radio', 'level', 'technical', 'Technically', 'the proper terms, basics not explained', true),
        choiceLabel('checkbox', 'hintsFirst', 'on', 'Hints first', 'a hint before the answer when I ask what something is, and in quizzes', true),
      ]),
      fieldset('Research spending', 'all researchers together, per day; also by voice ("cap research at $10")', [capField()]),
      fieldset('Objective', 'gives outlines sizes and shows a scale bar (~ is an estimate); also by voice ("I\'m on 40x")',
        [['', 'Not set'], ['4', '4x'], ['10', '10x'], ['20', '20x'], ['40', '40x'], ['60', '60x'], ['100', '100x']].map(([value, label]) =>
          choiceLabel('radio', 'objective', value, label, '', true))),
    ];
  }
}

/** Each setting's value now, from the capability list: a role's choice, a preference's value. */
export function pickerValues(wire: CapabilitiesWire): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const r of wire.roles) if (!r.fixed) values[r.id] = r.current;
  for (const p of wire.preferences) values[p.id] = p.value;
  return values;
}

/** What a role is used for, by the capabilities' own titles, and what it takes over when another
 * model cannot run: so a menu says that Outlining is clicks and boxes, not "Find all like this". */
export function usedBy(role: Role | undefined, titles: Map<string, string>): string {
  if (!role) return '';
  const names = (ids: string[]) => [...new Set(ids.map((id) => titles.get(id) ?? id))].join(', ');
  const parts = [];
  if (role.powers.length) parts.push(`Used by: ${names(role.powers)}.`);
  if (role.fallbackFor?.length) parts.push(`Also takes over ${names(role.fallbackFor)} when its own model can’t run.`);
  return parts.join(' ');
}

/** The Models menu's way to the whole list (the Models page, #models): every model, what runs here and how to turn
 * each on, with how many need you now. */
export function allModelsLink(wire: CapabilitiesWire, close: () => void): HTMLAnchorElement {
  const needs = (wire.models ?? []).filter((m) => m.group === 'needs-you').length;
  const link = document.createElement('a');
  link.className = 'models-all-link';
  link.dataset.pickerControl = 'all-models';
  link.href = '#models';
  link.textContent = needs ? `All models and setup (${needs} need${needs === 1 ? 's' : ''} you) →` : 'All models and setup →';
  link.addEventListener('click', close);
  return link;
}

/** The models fixed in the code, listed in the Models menu without a choice: what each is, the
 * one in use (its fallback when the first can't run), and what it is used by. */
/** Optional cloud keys: each service, where its key comes from, and Add key (or its key, to replace or remove). A key
 * missing here is why the models that need it below say so. */
export function keysFieldset(keys: Record<string, KeyFacts>): HTMLFieldSetElement {
  const rows = Object.values(keys).map((key) => {
    const row = document.createElement('div');
    row.className = 'key-row';
    row.dataset.present = String(key.present);
    const name = document.createElement('span');
    name.className = 'option-name';
    name.textContent = key.short;
    const note = document.createElement('span');
    note.className = 'option-note';
    note.textContent = sourceWords(key);
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'mm-control key-open';
    open.dataset.pickerControl = `key:${key.service}`;
    open.textContent = key.present ? 'Key…' : 'Add key';
    open.setAttribute('aria-label', key.present ? `Your ${key.short} key` : `Add your ${key.short} key`);
    open.addEventListener('click', () => openAddKey(key, open));
    row.append(name, note, open);
    return row;
  });
  return fieldset('Optional cloud models', 'Add a key when you want to use a cloud model. The example works without one.', rows);
}

function fixedModels(wire: CapabilitiesWire, titles: Map<string, string>): HTMLFieldSetElement[] {
  const roles = new Map(wire.roles.map((r) => [r.id, r]));
  const fixed = wire.roles.filter((r) => r.fixed && r.kind === 'model');
  // What takes over a capability it powers when it can't run (find all like this: Seeing and Outlining).
  const takeover = (r: Role) => wire.capabilities.filter((c) => c.roles?.[0] === r.id).flatMap((c) => (c.fallbacks ?? []).map((fb) =>
    `When ${fb.when}: ${fb.roles.map((id) => roles.get(id)?.title ?? id).join(' and ')} (above) take over.`));
  if (!fixed.length) return [];
  return [fieldset('Fixed in the code', 'no menu chooses these; the dot is the one in use', fixed.map((r) => {
    const row = document.createElement('div');
    row.className = 'fixed-model';
    const head = document.createElement('span');
    head.className = 'option-name';
    head.textContent = `${r.title}: ${r.label || r.current || ''}`;
    // What the one in use needs here, when it can't run (readiness.py: a key, with how).
    const inUse = r.fallback?.active ? r.fallback : r.primary;
    const needs = inUse && !inUse.available ? readinessWords(inUse.readiness) || inUse.why || '' : '';
    const notes = [r.fallback?.active ? `its fallback, because ${r.fallback.when} (${r.primary?.label ?? ''} otherwise)`
      : r.fallback ? `${r.fallback.label} when ${r.fallback.when}` : '', needs ? `${needs}.` : '', usedBy(r, titles), ...takeover(r)].filter(Boolean);
    const note = document.createElement('span');
    note.className = 'option-note';
    note.textContent = notes.join(' ');
    const dot = document.createElement('span');
    dot.className = 'in-use';
    dot.title = 'In use now';
    row.append(dot, head, note);
    return row;
  }))];
}

export type ArenaChanges = Partial<Record<ArenaKind, string[]>>;

const ARENA_GROUPS: { kind: ArenaKind; title: string; hint: string }[] = [
  { kind: 'outline', title: 'Outlines', hint: 'when you click something' },
  { kind: 'ask', title: 'Answers', hint: 'when you ask a question' },
];

/** What the blind test compares (opened from the test strip). Pick 2 to 4 per kind. */
export class ComparePicker {
  private readonly body: HTMLElement;
  private readonly onChange: (changes: ArenaChanges) => void;
  private builtFor = '';

  constructor(body: HTMLElement, onChange: (changes: ArenaChanges) => void) {
    this.body = body;
    this.onChange = onChange;
    body.addEventListener('change', (event) => {
      const input = event.target as HTMLInputElement;
      const kind = input.name as ArenaKind;
      const checked = [...body.querySelectorAll<HTMLInputElement>(`input[name="${kind}"]:checked`)].map((i) => i.value);
      this.onChange({ [kind]: checked });
    });
  }

  render(arena: ArenaState): void {
    const signature = JSON.stringify(arena.choices);
    if (signature !== this.builtFor) {
      this.builtFor = signature;
      this.body.replaceChildren(
        ...ARENA_GROUPS.map(({ kind, title, hint }) =>
          fieldset(`${title}`, `${hint}; pick 2 to 4`, arena.choices[kind].map((c) => choiceLabel('checkbox', kind, c.id, c.label, '', true))),
        ),
      );
    }
    for (const input of this.body.querySelectorAll<HTMLInputElement>('input')) {
      input.checked = arena.arms[input.name as ArenaKind]?.includes(input.value) ?? false;
    }
  }
}

/** Native disclosure keeps options keyboard accessible without expanding every job at once. */
function disclosure(key: string, title: string, content: HTMLElement[]): HTMLDetailsElement {
  const group = document.createElement('details');
  group.className = 'model-job'; group.dataset.pickerGroup = key;
  const summary = document.createElement('summary');
  const name = document.createElement('span'); name.className = 'model-job-name'; name.textContent = title;
  summary.append(name); group.append(summary, ...content);
  return group;
}

function fieldset(title: string, hint: string, labels: HTMLElement[], use = ''): HTMLFieldSetElement {
  const set = document.createElement('fieldset');
  const legend = document.createElement('legend');
  legend.append(title, ' ');
  const hintEl = document.createElement('span');
  hintEl.textContent = hint;
  legend.append(hintEl);
  set.append(legend);
  if (use) {
    const useEl = document.createElement('p');
    useEl.className = 'used-by';
    useEl.textContent = use;
    set.append(useEl);
  }
  set.append(...labels);
  return set;
}

function choiceLabel(type: 'radio' | 'checkbox', name: string, value: string, labelText: string, noteText: string, available: boolean): HTMLLabelElement {
  const label = document.createElement('label');
  const input = document.createElement('input');
  input.type = type;
  input.name = name;
  input.value = value;
  input.disabled = !available;
  const text = document.createElement('span');
  const nameEl = document.createElement('span');
  nameEl.className = 'option-name';
  nameEl.textContent = labelText;
  text.append(nameEl);
  if (noteText) {
    const note = document.createElement('span');
    note.className = 'option-note';
    note.textContent = noteText;
    text.append(note);
  }
  if (!available) label.dataset.unavailable = '';
  label.append(input, text);
  return label;
}

/** The daily research cap: dollars, or empty for no limit (the server's Settings.research_cap). */
function capField(): HTMLLabelElement {
  const label = document.createElement('label');
  label.className = 'cap';
  const input = document.createElement('input');
  input.type = 'text';
  input.name = 'researchCap';
  input.inputMode = 'decimal';
  input.placeholder = 'no limit';
  input.setAttribute('aria-label', 'Daily research cap in dollars; empty for no limit');
  const text = document.createElement('span');
  text.className = 'option-note';
  text.textContent = 'dollars a day; empty: no limit';
  label.append('$', input, text);
  return label;
}

export function closeOnOutsideClick(panel: HTMLElement, toggle: HTMLElement, close: () => void, opener?: () => HTMLElement | null): void {
  document.addEventListener('click', (event) => {
    const target = event.target as Node;
    // A click on something the panel just re-rendered is no longer in the page; it was
    // inside the panel, so it must not close it. The control it was opened from toggles it itself.
    if (panel.hidden || !target.isConnected || panel.contains(target) || toggle.contains(target) || opener?.()?.contains(target)) return;
    close();
  });
}

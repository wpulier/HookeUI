// Which popup lists which setting (models.ts): one rule, the role's kind in the capability list.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { pickerGroups, pickerValues, SettingsPicker, usedBy } from './models';
import type { CapabilitiesWire, Role, Settings } from './protocol';
import { TestElement } from './workspace-windows.test-support';

const role = (id: string, kind: Role['kind'], fixed = false): Role =>
  ({ id, kind, title: id, purpose: '', current: null, fixed, options: [], powers: [], agentCanChange: [] });

const ROLES: Role[] = [
  role('vision', 'model'), role('pointing', 'method'), role('outline', 'model'), role('quality', 'policy'),
  role('zeroshot', 'model'), role('track', 'method'), role('fixed:video', 'model', true),
];

describe('the two settings popups', () => {
  it('lists every setting in exactly one: models where changing it changes the model, the rest under behaviour', () => {
    const models = pickerGroups('models', ROLES).map((g) => g.key);
    const behaviour = pickerGroups('behaviour', ROLES).map((g) => g.key);
    expect(models).toEqual(['vision', 'outline', 'compare', 'zeroshot']); // "Compare with" right after outlines
    expect(behaviour).toEqual(['pointing', 'quality', 'track']);
    const settings = ROLES.filter((r) => !r.fixed).map((r) => r.id);
    expect([...models, ...behaviour].filter((k) => k !== 'compare').sort()).toEqual([...settings].sort()); // nothing left out, nothing twice
  });
});

describe('one source for the menus', () => {
  it('reads every value from the capability list, and says what each job is used for', () => {
    const outline = { ...role('outline', 'model'), current: 'edgetam', powers: ['click', 'box'], fallbackFor: ['like'] };
    const wire = { roles: [outline, { ...role('fixed:find-all', 'model', true), current: 'fal-ai/x' }],
      preferences: [{ id: 'quieter', title: '', purpose: '', value: true, agentCanChange: [] }, { id: 'objective', title: '', purpose: '', value: 40, agentCanChange: [] }],
    } as unknown as CapabilitiesWire;
    expect(pickerValues(wire)).toEqual({ outline: 'edgetam', quieter: true, objective: 40 }); // a fixed role is no setting
    const titles = new Map([['click', 'Click to outline'], ['box', 'Drag a box'], ['like', 'Find all like this']]);
    expect(usedBy(outline, titles)).toBe('Used by: Click to outline, Drag a box. Also takes over Find all like this when its own model can’t run.');
  });
});

// Actual picker over synthetic DOM events: no browser, connection, ledger or provider.
class Field extends TestElement {
  name = '';
  type = '';
  checked = false;
  focus() { (globalThis.document as unknown as { activeElement: unknown }).activeElement = this; }
}
afterEach(() => vi.unstubAllGlobals());

function settingsForm(scope: 'models' | 'behaviour' = 'behaviour') {
  vi.stubGlobal('document', Object.assign(new EventTarget(), {
    createElement: (tag: string) => new Field(tag), activeElement: null,
  }));
  const form = new Field('form'), toggle = new Field('button');
  const changes: Partial<Settings>[] = [], objectives: (number | null)[] = [];
  const picker = new SettingsPicker(scope, form as unknown as HTMLFormElement,
    toggle as unknown as HTMLButtonElement, change => changes.push(change), objective => objectives.push(objective));
  const wire: CapabilitiesWire = { type: 'capabilities', families: [], actors: { you: 'You', agent: 'Agent', auto: 'Automatic', researcher: 'Researcher' },
    capabilities: [], roles: [], preferences: [{ id: 'researchCap', title: 'Research spending', purpose: '', value: null, agentCanChange: [] }],
    pending: 0, build: 'synthetic-settings', agentText: '' };
  picker.render(wire);
  const input = (name: string, value?: string) => form.querySelectorAll('input')
    .map(node => node as Field).find(node => node.name === name && (value === undefined || node.value === value))!;
  const change = (field: Field) => {
    const event = new Event('change', { bubbles: true });
    Object.defineProperty(event, 'target', { value: field });
    form.dispatchEvent(event);
  };
  return { form, picker, wire, input, change, changes, objectives };
}

it('a committed daily allowance change sends only that field and keeps the draft through a focused echo', () => {
  const ui = settingsForm();
  const cap = ui.input('researchCap');
  cap.focus(); cap.value = '5';
  ui.picker.render(ui.wire); // a server echo while the user is still typing cannot replace the draft
  expect(cap.value).toBe('5');
  ui.change(cap);
  expect(ui.changes).toEqual([{ researchCap: '5' }]); // text validation belongs to the existing server parser
  const quieter = ui.input('quieter'); quieter.checked = true; ui.change(quieter);
  ui.change(ui.input('objective', '40'));
  expect(ui.changes).toEqual([{ researchCap: '5' }, { quieter: true }]);
  expect(ui.objectives).toEqual([40]);
});

it('Enter commits the focused daily allowance without navigating or resubmitting other preferences', () => {
  const ui = settingsForm();
  const cap = ui.input('researchCap'); cap.focus(); cap.value = '5';
  const submit = new Event('submit', { cancelable: true });
  ui.form.dispatchEvent(submit);
  expect(submit.defaultPrevented).toBe(true);
  expect(ui.changes).toEqual([{ researchCap: '5' }]);
  expect(ui.objectives).toEqual([]);
  expect(document.activeElement).toBe(cap);
});


it('Models leads with compact jobs and the current choice, retaining disclosures through capability updates', () => {
  const ui = settingsForm('models');
  const cells: Role = { ...role('cells', 'model'), title: 'Cells', current: 'basic', options: [
    { id: 'basic', label: 'Bright objects', available: true, note: 'On this Mac' },
    { id: 'cloud', label: 'Cloud cells', available: false, why: 'Add your key', note: '' },
  ] };
  const wire: CapabilitiesWire = { ...ui.wire, roles: [cells] };
  ui.picker.render(wire);
  const job = ui.form.querySelector('[data-picker-group="cells"]')!;
  expect(job.tagName).toBe('DETAILS');
  expect(job.getAttribute('open') !== null).toBe(false);
  expect(job.querySelector('summary')!.textContent).toContain('Cells');
  expect(job.querySelector('summary')!.textContent).toContain('Bright objects');
  expect(job.textContent).toContain('Add your key');
  expect(ui.input('cells', 'cloud').disabled).toBe(true);
  job.setAttribute('open', '');
  ui.picker.render({ ...wire, roles: [{ ...cells, options: cells.options.map(o => ({ ...o, note: 'Updated fact' })) }] });
  expect(ui.form.querySelector('[data-picker-group="cells"]')!.getAttribute('open') !== null).toBe(true);
  ui.picker.render({ ...wire, roles: [{ ...cells, current: 'cloud' }] });
  expect(ui.form.querySelector('[data-picker-group="cells"]')!.querySelector('summary')!.textContent).toContain('Cloud cells');
  expect(ui.changes).toEqual([]); // reading readiness and opening details cannot choose a model
});


it('readiness updates preserve focus on setup controls without pulling it back from outside the picker', () => {
  const ui = settingsForm('models');
  const key = { service: 'openai', name: 'OpenAI API', short: 'OpenAI', getKey: '', enables: 'Research',
    present: false, source: null, sourceSays: '', canSave: true };
  const wire: CapabilitiesWire = { ...ui.wire, models: [], setup: { edition: 'open', options: {}, keys: { OPENAI_API_KEY: key } } };
  ui.picker.render(wire);
  const keys = ui.form.querySelector('[data-picker-group="keys"]')!;
  keys.setAttribute('open', '');
  (keys.querySelector('button') as Field).focus();
  ui.picker.render({ ...wire, setup: { ...wire.setup!, keys: { OPENAI_API_KEY: { ...key, enables: 'Research and images' } } } });
  expect(document.activeElement).toBe(ui.form.querySelector('[data-picker-group="keys"]')!.querySelector('button'));
  (ui.form.querySelector('a') as Field).focus();
  ui.picker.render(wire);
  expect(document.activeElement).toBe(ui.form.querySelector('a'));
  const outside = new Field('button'); outside.focus();
  ui.picker.render({ ...wire, setup: { ...wire.setup!, keys: { OPENAI_API_KEY: { ...key, enables: 'Research and images' } } } });
  expect(document.activeElement).toBe(outside);
});

it('opened from the composer’s model chip: 6 px above it, its right edge on the composer’s, measured at its opened width', () => {
  const ui = settingsForm('models');
  vi.stubGlobal('innerWidth', 1440); vi.stubGlobal('innerHeight', 900);
  // Its width is the opened look's (360 px) only once it is marked as opened from a control; before, it is wider.
  Object.defineProperty(ui.form, 'offsetWidth', { get: () => ui.form.dataset.opener === 'above' ? 360 : 640 });
  const rect = (left: number, top: number, right: number, bottom: number) => () => ({ left, top, right, bottom, width: right - left, height: bottom - top, x: left, y: top, toJSON: () => ({}) });
  const composer = new Field('form'), chip = new Field('button');
  composer.append(chip);
  Object.assign(composer, { getBoundingClientRect: rect(1097, 708, 1424, 844) });
  Object.assign(chip, { getBoundingClientRect: rect(1153, 800, 1278, 828) });
  ui.picker.openFrom(chip as unknown as HTMLElement); // its edge: the form the chip sits in
  expect(ui.form.hidden).toBe(false);
  expect(chip.getAttribute('aria-expanded')).toBe('true');
  const style = ui.form.getAttribute('style')!;
  expect(style).toContain('left: 1064px'); // 1424 − 360: the composer's right edge, not the chip's left
  expect(style).toContain('bottom: 106px'); // 900 − 800 + 6
  ui.picker.openFrom(chip as unknown as HTMLElement); // the chip closes it again
  expect(ui.form.hidden).toBe(true);
  expect(chip.getAttribute('aria-expanded')).toBe('false');
  // A narrow window keeps it inside, 8 px from the edge.
  vi.stubGlobal('innerWidth', 600);
  Object.assign(composer, { getBoundingClientRect: rect(300, 708, 596, 844) });
  ui.picker.openFrom(chip as unknown as HTMLElement);
  expect(ui.form.getAttribute('style')).toContain('left: 232px'); // 600 − 360 − 8
});

it('opened from the model chip, focus moves to its first control, All models and setup; closing gives it back to the chip', () => {
  const ui = settingsForm('models');
  vi.stubGlobal('innerWidth', 1440); vi.stubGlobal('innerHeight', 900);
  ui.picker.render({ ...ui.wire, models: [] });
  const composer = new Field('form'), chip = new Field('button');
  composer.append(chip);
  ui.picker.openFrom(chip as unknown as HTMLElement);
  expect(document.activeElement).toBe(ui.form.querySelector('.models-all-link'));
  ui.picker.close();
  expect(document.activeElement).toBe(chip);
});

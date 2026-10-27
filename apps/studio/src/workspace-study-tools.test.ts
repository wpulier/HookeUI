// Synthetic navigation evidence; public browser evidence covers the destination sheets.
import { afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire } from './workspace-windows.test-support';
import { toolsBrowser } from './workspace-study-tools';

afterEach(() => vi.unstubAllGlobals());

function browse() {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  vi.stubGlobal('MutationObserver', class { observe() {} });
  const actions = new TestElement('section'), search = new TestElement('input'), bright = new TestElement('details');
  // The Actions panel's By task / By model switch (actions-panel.ts), which the workspace moves to the top.
  const tabs = new TestElement('div'); tabs.className = 'tools-view';
  for (const mode of ['task', 'model']) { const b = new TestElement('button'); b.dataset.mode = mode; b.setAttribute('aria-pressed', String(mode === 'task')); tabs.append(b); }
  Object.assign(actions, { querySelector: (selector: string) => selector === '.tools-view' ? tabs : null });
  const navigate = vi.fn();
  const browser = toolsBrowser(actions as unknown as HTMLElement, search as unknown as HTMLInputElement, bright as unknown as HTMLElement, navigate);
  return { browser, tabs, search, bright, navigate };
}

test('Add your own offers every existing capability journey, without running work or losing the tool search', () => {
  const { browser, search, navigate } = browse();
  const entry = browser.entry as unknown as TestElement;
  expect(entry.hidden).toBeFalsy(); // always there, below the entries to set up and the New ones
  search.value = 'my custom measurement';
  for (const route of ['find', 'add', 'create']) {
    fire(entry.querySelector(`[data-way="${route}"]`)!, 'click');
    expect(navigate).toHaveBeenLastCalledWith(`#toolbox/add-${route}`);
  }
  expect(navigate).toHaveBeenCalledTimes(3);
  expect(search.value).toBe('my custom measurement');
});

test('the By task / By model switch is the panel’s own, moved to the top; By model hides the task-only Bright objects', () => {
  const { browser, tabs, bright, search } = browse();
  expect(browser.tabs).toBe(tabs);
  expect(bright.hidden).toBe(false);
  expect((browser.hint as unknown as TestElement).textContent).toContain('the model it will use');
  tabs.children[0].setAttribute('aria-pressed', 'false'); tabs.children[1].setAttribute('aria-pressed', 'true');
  fire(search, 'input'); // any redraw reads the switch
  expect(bright.hidden).toBe(true);
  expect((browser.hint as unknown as TestElement).textContent).toContain('Every model in Models, in its sections');
});

test('calm first view: Tools opens closed until the user goes there, and has no subtitle (hooke-words "Calm first view")', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const study = readFileSync(fileURLToPath(new URL('./workspace-study.ts', import.meta.url)), 'utf8');
  expect(study).toContain('drawer: saved.drawer === true,'); // only a saved choice reopens it
  expect(study).toMatch(/catch \{ return \{[^}]*drawer: false/);
  const { browser } = browse();
  expect((browser.hint as unknown as TestElement).className).toContain('visually-hidden'); // read aloud, never shown
});

test('rows are the panel’s own (a task to set up is listed under Can be set up by actions-panel.ts): nothing is moved or hidden here', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  vi.stubGlobal('MutationObserver', class { observe() {} });
  const actions = new TestElement('section'), search = new TestElement('input'), bright = new TestElement('details');
  const group = new TestElement('section'); group.className = 'actions-group';
  const row = (title: string, available: boolean) => {
    const r = new TestElement('div'); r.className = 'action-row'; r.dataset.available = String(available); r.dataset.title = title;
    return r;
  };
  const waitA = row('Tell me when', false), ready = row('Count them', true), waitB = row('What just happened', false);
  group.append(waitA, ready, waitB);
  Object.assign(actions, { querySelector: () => null, querySelectorAll: (selector: string) => selector === '.actions-list .actions-group' ? [group] : [] });
  toolsBrowser(actions as unknown as HTMLElement, search as unknown as HTMLInputElement, bright as unknown as HTMLElement, vi.fn());
  // Shown, and left in place: the panel draws its sections, so its redraws are never fought.
  expect(group.children.map(r => r.dataset.title)).toEqual(['Tell me when', 'Count them', 'What just happened']);
  expect(group.children.every(r => !r.hidden)).toBe(true);
  expect(group.hidden).toBeFalsy();
  const before = [...group.children];
  fire(search, 'input'); // a redraw moves nothing
  expect(group.children).toEqual(before);
});

test('Bright objects’ own card stands in only when its Find bright regions row is not listed (one row for one thing)', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  vi.stubGlobal('MutationObserver', class { observe() {} });
  const actions = new TestElement('section'), search = new TestElement('input'), bright = new TestElement('details');
  const list = new TestElement('div'); list.className = 'actions-list';
  let listed = true;
  Object.assign(list, { querySelector: (selector: string) => selector === '[data-action="catalog:basic-bright-objects"]' && listed ? new TestElement('div')
    : selector === '[aria-label="Common"]' ? null : null, querySelectorAll: () => [], prepend: () => {} });
  Object.assign(actions, { querySelector: (selector: string) => selector === '.actions-list' ? list : null, querySelectorAll: () => [] });
  toolsBrowser(actions as unknown as HTMLElement, search as unknown as HTMLInputElement, bright as unknown as HTMLElement, vi.fn());
  expect(bright.hidden).toBe(true);
  listed = false; fire(search, 'input');
  expect(bright.hidden).toBe(false);
});

test('a Tools page coming back docked left slides out from behind the rail with the Stage, clipped at the rail (b041)', async () => {
  const { sideTracks } = await import('./side-motion');
  const studio = {} as HTMLElement, page = {} as HTMLElement, panel = {} as HTMLElement;
  const right = { node: panel, width: 360 };
  const closed = { viewport: 1440, rail: 72, stage: { left: 72, right: 360 }, left: null, right, under: 360 };
  // The page is in the top layer, above the rail: its left edge slides with the Stage's while its clip keeps it from
  // ever showing over the rail. Docked, it takes the Tools width, so the Stage lands where the Tools list would.
  const shown = { ...closed, stage: { left: 412, right: 360 }, left: { node: page, width: 340, layer: true } };
  const [stage, slide] = sideTracks(closed, shown, studio, null);
  expect(stage.props).toEqual({ marginLeft: [72, 412], width: [1008, 668] });
  expect(slide).toMatchObject({ node: page, props: { left: [-268, 72] }, clip: [340, 0], closing: false });
  // Going, the deck hides it at once: a press that closes it moves only the Stage, nothing slides out over the rail.
  expect(sideTracks(shown, closed, studio, null).map((t) => t.node)).toEqual([studio]);
});

// The Hooke menu's build line says how this copy is really updated (F-L8-5): the signed Mac app through Sparkle's Check
// for Updates, a source checkout through its checkout. The packaged app used to say "Updates through your source checkout".
import { afterEach, expect, test, vi } from 'vitest';
import { WorkspaceAppMenu } from './workspace-app-menu';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());

function buildLine(native: boolean): string {
  const dom = syntheticDOM();
  vi.stubGlobal('document', Object.assign(dom.document, { documentElement: new TestElement('html') }));
  const host = new EventTarget();
  if (native) Object.assign(host, { webkit: { messageHandlers: { hookeUpdates: { postMessage: vi.fn() } } } });
  vi.stubGlobal('window', host);
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
  const opener = new TestElement('button');
  const menu = new WorkspaceAppMenu({ opener: opener as unknown as HTMLButtonElement, storageKey: 'test', sessions: () => [],
    newSession: () => {}, openSession: () => {}, navigate: () => {} });
  const find = (node: TestElement): TestElement | undefined => node.className === 'workspace-menu-build' ? node
    : node.children.map(find).find(Boolean);
  return find(menu.root as unknown as TestElement)?.textContent ?? '';
}

test('the signed Mac app names its real update path, never the source checkout', () => {
  const line = buildLine(true);
  expect(line).toContain('Check for Updates');
  expect(line).not.toContain('source checkout');
});

test('a source checkout in a browser says it updates through its checkout', () => {
  expect(buildLine(false)).toContain('Updates through your source checkout');
});

// Synthetic queued popover events, not browser layout or keyboard qualification.
test('closing Projects clears its own expanded state before the queued toggle event', () => {
  const dom = syntheticDOM();
  const create = dom.document.createElement;
  dom.document.createElement = (tag: string) => Object.assign(create(tag), { focus: vi.fn() });
  vi.stubGlobal('document', Object.assign(dom.document, { documentElement: new TestElement('html') }));
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('innerHeight', 900);
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
  const opener = Object.assign(new TestElement('button'), { focus: vi.fn() });
  const projects = Object.assign(new TestElement('button'), { focus: vi.fn() });
  const menu = new WorkspaceAppMenu({ opener: opener as unknown as HTMLButtonElement, storageKey: 'test',
    sessions: () => [], newSession: () => {}, openSession: () => {}, navigate: () => {} });
  let open = false;
  Object.assign(menu.root, {
    matches: (selector: string) => selector === ':popover-open' && open,
    showPopover: () => { open = true; }, hidePopover: () => { open = false; },
  });
  menu.showProjects(projects as unknown as HTMLButtonElement);
  menu.root.dispatchEvent(new Event('toggle'));
  expect(projects.getAttribute('aria-expanded')).toBe('true');
  menu.root.querySelector('button')!.dispatchEvent(new Event('click')); // New project closes the menu.
  expect(open).toBe(false);
  expect(projects.getAttribute('aria-expanded')).toBe('false');
  expect(projects.focus).toHaveBeenCalledOnce();
  menu.root.dispatchEvent(new Event('toggle')); // Browser delivers close later.
  expect(projects.getAttribute('aria-expanded')).toBe('false');
  expect(opener.getAttribute('aria-expanded')).toBe('false');
});

// Ticket 67: the menu said "Earlier tool sessions" for the sheet that lists the running ones, titled Scientific sessions.
test('the menu names the scientific sessions sheet by its own title', () => {
  const dom = syntheticDOM();
  vi.stubGlobal('document', Object.assign(dom.document, { documentElement: new TestElement('html') }));
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
  const menu = new WorkspaceAppMenu({ opener: new TestElement('button') as unknown as HTMLButtonElement, storageKey: 'test',
    sessions: () => [], newSession: () => {}, openSession: () => {}, navigate: () => {}, scientificSessions: () => {} });
  const text = (menu.root as unknown as TestElement).textContent;
  expect(text).toContain('Scientific sessions');
  expect(text).not.toContain('Earlier tool sessions');
});

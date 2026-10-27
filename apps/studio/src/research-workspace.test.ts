// Synthetic DOM evidence; viewport/layout acceptance is a separate real-browser check (ticket 66 D3 screenshots beside
// the mock's research, research-docs, research-sources and research-narrow frames).
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ResearchWorkspace, openOnResearcherTab, provideResearchReader, researchReader, showInResearchReader, type WorkspaceHandlers } from './research-workspace';
import { readLayout, researchColumns } from './research-tab';
import type { ReaderEntry } from './data-reader';
import { fire, syntheticDOM, TestElement } from './workspace-windows.test-support';

const memory = new Map<string, string>();
const storage = { getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string, v: string) => { memory.set(k, v); }, removeItem: (k: string) => { memory.delete(k); } };
// Menus give focus back to their button when they close.
Object.assign(TestElement.prototype, { focus() {} });
beforeEach(() => { memory.clear(); vi.stubGlobal('document', syntheticDOM().document); vi.stubGlobal('localStorage', storage); vi.stubGlobal('sessionStorage', storage);
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {}))); provideResearchReader(null); });
afterEach(() => { provideResearchReader(null); vi.unstubAllGlobals(); });

const make = (send: (text: string) => void = () => {}) => {
  const root = new TestElement('article');
  const workspace = new ResearchWorkspace(root as unknown as HTMLElement, send);
  return { root, workspace, field: () => root.querySelector('textarea')! as TestElement & { placeholder: string } };
};
const handlers = (overrides: Partial<WorkspaceHandlers> = {}): WorkspaceHandlers => ({ show: vi.fn(), showItem: vi.fn(), tool: vi.fn(), main: vi.fn(), attachFile: vi.fn(),
  attachSection: vi.fn(), newResearcher: vi.fn(), ...overrides });
const state = { key: 'day/r3', rid: 'r3', title: 'Bright regions in Human cells', working: true, writable: true, canSend: true, researcher: 'Researcher 3', project: 'p', item: 'r3', kind: 'Presentation' };
const entry = (fields: Partial<ReaderEntry>): ReaderEntry => ({ id: 'e7', kind: 'population', title: 'Bright regions · Bright objects · 22:18', summary: '271 bright regions detected by the basic local method',
  state: 'saved', actions: [], day: 'p', version: 0, role: 'analysis', maker: { kind: 'researcher', id: 'r3', name: 'Researcher 3' }, ...fields } as ReaderEntry);
const made = [entry({ id: 'r3', kind: 'research', title: 'Bright regions in Human cells', role: 'writeup', forms: [{ label: 'Presentation', outputId: 'o1', state: 'writing' }] }),
  entry({}), entry({ id: 'r3:table-1', kind: 'finding-table', title: 'Region areas', summary: '271 rows, 2 columns', source: { index: 0 } as ReaderEntry['source'] }),
  entry({ id: 'r3:source-0123456789ab', kind: 'source', role: 'source', title: 'nature.com/x', untrusted: true, values: { url: 'https://nature.com/x', site: 'nature.com', title: 'Cellpose' },
    source: { citedIn: ['r3'] } as unknown as ReaderEntry['source'] }),
  entry({ id: 'e9', title: 'Cell outlines · Cellpose-SAM · 10:00', maker: { kind: 'you', name: 'You' } })];

test('two columns told apart: the conversation with its composer, and on the right the one reader', () => {
  const { root, workspace } = make();
  const page = root.querySelector('.rpage')!;
  expect(root.querySelector('.rwrap')!.children[0]).toBe(page);
  expect(page.children.map((n) => n.className)).toEqual(['rconv research-conversation', 'rart research-artifact']);
  expect(page.dataset).toMatchObject({ one: 'conv' });
  const conv = page.children[0];
  expect(conv.children.map((n) => n.className)).toEqual(['rhead rconv-head', 'rconv-body', 'rcomp']);
  // No Files column, no file Tabs and no "+" beside the conversation: what it made opens in the reader.
  expect(root.querySelectorAll('.rfiles')).toEqual([]);
  expect(root.querySelectorAll('.ctab-btn').map((b) => b.textContent)).toEqual(['Conversation', 'What it made']);
  const art = page.children[1];
  expect(art.children.map((n) => n.className)).toEqual(['rdiv', 'data-reader data-reader-research']);
  expect(art.children[1]).toBe(researchReader()!.root);
  expect(art.children[1].id).toBe('data-reader-research'); // the screen report's identity, never empty
  // Its page (handed to the reader): the desk with its progress line and the page, then Sections in its own band.
  workspace.update(state);
  const host = root.querySelector('.rpage-host')!;
  expect(host.children.map((n) => n.className)).toEqual(['desk', 'rart-foot']);
  expect(root.querySelector('.desk')!.children.map((n) => n.className)).toEqual(['research-presentation-state', 'desk-pages', 'desk-empty']);
  expect(root.querySelector('.rart-foot')!.children[0].className).toBe('fbar');
  // No Talk in a researcher's composer: +, the model chip, Send.
  expect(root.querySelector('.mm-composer-actions')!.children.map((n) => n.className)).toEqual(['mm-composer-icon mm-composer-attach', 'mm-composer-model', 'mm-composer-spacer', 'mm-composer-send']);
  expect(root.querySelector('.talk')).toBe(null);
});

test('the breakpoints are container queries on the page: the two columns from 860 px (sharing the width below 1240), one below', () => {
  expect([researchColumns(1368), researchColumns(1240), researchColumns(1028), researchColumns(860), researchColumns(859), researchColumns(708)]).toEqual([2, 2, 2, 2, 1, 1]);
  const css = readFileSync(new URL('./workspace-feel.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  expect(css).toMatch(/\.rwrap \{[^}]*container: rpage \/ inline-size/);
  expect(css).toMatch(/\.research-workspace \.rpage \{ grid-template-columns: minmax\(400px, var\(--conv-w, 456px\)\) minmax\(480px, 1fr\)/);
  const block = (query: string) => { const at = css.indexOf(`@container rpage ${query}`); expect(at, query).toBeGreaterThan(-1); return css.slice(at, css.indexOf('\n}', at)); };
  expect(block('(min-width: 860px) and (max-width: 1239px)')).toMatch(/\.research-workspace \.rpage \{ grid-template-columns: minmax\(360px, var\(--conv-w2, 392px\)\) minmax\(0, 1fr\)/);
  const one = block('(max-width: 859px)');
  expect(one).toMatch(/\.research-workspace \.rpage \{ grid-template-columns: minmax\(0, 1fr\)/); expect(one).toMatch(/\.ctab-art \{ display: flex/);
  expect(one).toMatch(/\[data-one='conv'\] \.rart \{ display: none/); expect(one).toMatch(/\[data-one='art'\] \.rconv-body \{ display: none/);
  // The Study home keeps its three columns (its own experiments column on the left).
  expect(css).toMatch(/\.rpage \{[^}]*grid-template-columns: var\(--files-w, 240px\) minmax\(400px, var\(--conv-w, 456px\)\) minmax\(520px, 1fr\)/);
  // Never the window: no media query decides the columns.
  for (let at = css.indexOf('@media'); at > -1; at = css.indexOf('@media', at + 1)) {
    let depth = 0, end = css.indexOf('{', at);
    for (let i = end; i < css.length; i++) { if (css[i] === '{') depth++; else if (css[i] === '}' && --depth === 0) { end = i; break; } }
    expect(css.slice(at, end)).not.toMatch(/\.rpage \{[^}]*grid-template-columns/);
  }
});

test('the divider’s widths are remembered per researcher and reset on a double-click; what an older studio kept of Files is ignored', () => {
  const { root, workspace } = make();
  memory.set('hooke-research-layout:r4', JSON.stringify({ conv: 520, files: 'folded', used: 'closed' }));
  expect(readLayout('r4', storage)).toEqual({ conv: 520 });
  workspace.update({ ...state, key: 'day/r4', rid: 'r4' });
  expect(root.querySelector('.rpage')!.style['--conv-w']).toBe('520px');
  fire(root.querySelector('.rdiv')!, 'dblclick');
  expect(readLayout('r4', storage)).toEqual({});
  expect(root.querySelector('.rpage')!.style['--conv-w']).toBeUndefined();
  // A browser that blocks site storage outright (reading localStorage itself throws): switching researchers still draws.
  vi.unstubAllGlobals(); vi.stubGlobal('document', syntheticDOM().document); vi.stubGlobal('sessionStorage', storage); vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });
  try {
    expect(() => workspace.update({ ...state, key: 'day/r9', rid: 'r9' })).not.toThrow();
    expect(() => fire(root.querySelector('.rdiv')!, 'dblclick')).not.toThrow();
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original); else delete (globalThis as { localStorage?: unknown }).localStorage;
  }
});

test('progress preserves the composer node and its draft; Send sends the words and clears the field', () => {
  const sent: string[] = [];
  const { root, workspace, field } = make((text) => sent.push(text));
  workspace.update(state);
  const input = field();
  input.value = 'Please compare the captured fields';
  fire(input, 'input');
  workspace.update({ ...state, title: 'Study updated' });
  expect(field()).toBe(input);
  expect(input.value).toBe('Please compare the captured fields');
  fire(root.querySelector('form')!, 'submit');
  expect(sent).toEqual(['Please compare the captured fields']);
  expect(input.value).toBe('');
});

test('switching researchers restores their separate drafts and read-only history cannot send', () => {
  const send = vi.fn();
  const { root, workspace, field } = make(send);
  workspace.update({ ...state, working: false });
  field().value = 'My first draft';
  fire(field(), 'input');
  workspace.update({ ...state, key: 'today/r2', rid: 'r2' });
  expect(field().value).toBe('');
  workspace.update({ ...state, working: false });
  expect(field().value).toBe('My first draft');
  workspace.update({ ...state, key: 'yesterday/r1', rid: 'r1', writable: false });
  expect(field().placeholder).toBe('Earlier work · read only');
  field().value = 'Do more';
  fire(root.querySelector('form')!, 'submit');
  expect(send).not.toHaveBeenCalled();
});

test('the composer says what the state invites, names the fixed engine, and its + offers only what this researcher made', () => {
  const { root, workspace } = make();
  workspace.update({ ...state, placeholder: 'Tell Researcher 3 something. It reads it at its next step.', engine: 'GPT-6.1 Sol' });
  const field = () => root.querySelector('textarea') as unknown as { placeholder: string };
  expect(field().placeholder).toBe('Tell Researcher 3 something. It reads it at its next step.');
  const chip = root.querySelector('.mm-composer-model')!;
  expect(chip.textContent).toBe('GPT-6.1 Sol');
  expect(chip.getAttribute('aria-label')).toBe('Researcher 3 runs on GPT-6.1 Sol');
  workspace.update({ ...state, placeholder: 'Answer, or tell Researcher 4 more.' });
  expect(field().placeholder).toBe('Answer, or tell Researcher 4 more.');
  expect(root.querySelector('.mm-composer-attach')!.getAttribute('aria-label')).toBe('Ask about a file it made');
});

test('a retained suggestion fills the draft without sending or dropping its exact selection', () => {
  const send = vi.fn();
  const { root, workspace, field } = make(send);
  const finished = { ...state, working: false };
  workspace.update(finished);
  workspace.attach('Their areas', () => {});
  Object.assign(field(), { focus: vi.fn() });
  workspace.suggest(['What would distinguish the alternatives?']);
  fire(root.querySelector('.research-suggestions')!.querySelector('button')!, 'click');
  expect(field().value).toBe('What would distinguish the alternatives?');
  expect(send).not.toHaveBeenCalled();
  expect(root.querySelector('.rtab-selection')!.textContent).toContain('Their areas');
  workspace.update({ ...finished, key: 'today/r2', rid: 'r2' });
  workspace.update(finished);
  expect(field().value).toBe('What would distinguish the alternatives?');
  workspace.update({ ...finished, writable: false });
  expect(root.querySelector('.research-suggestions')!.hidden).toBe(true);
  workspace.suggest(['Unavailable on an earlier day']);
  expect(root.querySelector('.research-suggestions')!.hidden).toBe(true);
});

test('the reader shows its page with its state in the server’s words and its clock; Fit, download and ⋯ are in its header', () => {
  const { root, workspace } = make();
  workspace.bind(handlers());
  workspace.update({ ...state, pill: { cell: 'working', words: 'Writing the presentation', since: Date.now() / 1000 - 75, version: '', title: '' } });
  workspace.renderArtifact('none', [], { title: 'Bright regions in Human cells', empty: 'It will write up its findings here.' });
  const reader = root.querySelector('.data-reader-research')!;
  expect(reader.hidden).toBe(false);
  expect(reader.dataset.readerPage).toBe('true');
  expect(reader.querySelector('.rd-body-host')!.children[0]).toBe(root.querySelector('.rpage-host'));
  expect(reader.querySelector('.rd-switch')!.textContent).toBe('Bright regions in Human cells');
  expect(reader.querySelector('.rd-by')!.textContent).toBe('Presentation · Writing the presentation 1:15');
  expect(reader.querySelectorAll('[data-view]').map((tab) => tab.textContent)).toEqual(['Page', 'How it was made']);
  expect(reader.querySelector('.rd-top')!.children.map((n) => n.className.split(' ')[0])).toEqual(['rd-ic', 'rd-words', 'tbtn', 'rd-more']);
  // No ×: the reader is this Tab's own right; Talk to Researcher 3 would go nowhere.
  expect(reader.querySelectorAll('.rd-close')).toEqual([]);
  expect(reader.querySelectorAll('.rd-talk')).toEqual([]);
  workspace.update({ ...state, working: false, pill: { cell: 'partial', words: 'Partial findings', since: null, version: 'v1', title: '' } });
  workspace.renderArtifact('none', [], { title: '' });
  expect(reader.querySelector('.rd-by')!.textContent).toBe('Presentation · Partial findings');
  workspace.setVisible(false);
});

test('the page is drawn on paper in its own theme, paper when none was chosen, and says one line before anything is written', () => {
  const { root, workspace } = make();
  workspace.update(state);
  workspace.renderArtifact('none', [], { title: 'Bright regions', empty: 'It will write up its findings here.' });
  expect(root.querySelector('.page')!.hidden).toBe(true);
  expect(root.querySelector('.desk-empty')!.textContent).toBe('It will write up its findings here.');
  expect(root.querySelector('.fbar')!.hidden).toBe(true);
  const report = new TestElement('div'); report.className = 'viewer-summary';
  const section = new TestElement('section'); section.dataset.anchor = 'report:methods';
  const heading = new TestElement('h3'); heading.textContent = 'Methods'; section.append(heading); report.append(section);
  workspace.renderArtifact('one', [report as unknown as HTMLElement], { title: 'Bright regions', theme: 'graphite' });
  expect(root.querySelector('.page')!.hidden).toBe(false);
  expect(root.querySelector('.page')!.dataset.theme).toBe('graphite');
  workspace.renderArtifact('two', [report as unknown as HTMLElement], { title: 'Bright regions', theme: 'not-a-theme' });
  expect(root.querySelector('.page')!.dataset.theme).toBe('paper');
  // The outline stays inside the page: Sections, the page bar's one control (no page count or arrows on a flowing page).
  const bar = root.querySelector('.fbar')!;
  expect(bar.hidden).toBe(false);
  expect(bar.children.map((b) => b.textContent)).toEqual(['Sections']);
  expect(bar.querySelector('[aria-disabled]')).toBe(null);
  expect(root.querySelectorAll('.orow')).toEqual([]);
});

test('what it made opens in the same reader, never leaving the Tab: a step’s link, a table’s Open, its sources, a Data row', () => {
  const leave = vi.fn(), talk = vi.fn();
  provideResearchReader({ entries: () => made, deps: { leave, talk } });
  const showItem = vi.fn();
  const { root, workspace } = make();
  workspace.bind(handlers({ showItem }));
  workspace.setVisible(true);
  workspace.update(state);
  workspace.renderArtifact('none', [], { title: 'Bright regions in Human cells' });
  const reader = researchReader()!;
  expect(reader.current()?.id).toBe('r3');
  // A step's link (a result it had the app make) and a table's Open on its page.
  workspace.openFileTab('record:e7');
  expect([reader.current()?.id, root.querySelector('.data-reader-research')!.dataset.readerPage]).toEqual(['e7', 'false']);
  expect(root.querySelector('.rd-by')!.textContent).toBe('Outlines · 271 bright regions');
  workspace.openFileTab('table:0');
  expect(reader.current()?.id).toBe('r3:table-1');
  // The switcher: everything it made (its page first), Sources · 1, then All its work in Data; never what others made.
  fire(root.querySelector('.rd-switch')!, 'click');
  const menu = () => (document as unknown as { body: TestElement }).body.querySelector('.workspace-add-menu')!;
  expect(menu().children.map((n) => n.textContent)).toEqual(['Made by Researcher 3', 'Bright regions in Human cells · Presentation', 'Bright regions · Outlines, 271 bright regions',
    '✓Region areas · Table, 271 rows', 'Sources · 1']);
  fire(menu().children.find((n) => n.textContent === 'Sources · 1')!, 'click');
  expect(reader.current()?.kind).toBe('sources');
  expect(root.querySelectorAll('.rd-src').map((card) => card.querySelector('.rd-src-open')!.textContent)).toEqual(['Cellpose']);
  // Its page again: the switcher asks its page to show it.
  fire(root.querySelector('.rd-switch')!, 'click');
  fire(menu().children.find((n) => n.textContent.startsWith('Bright regions in Human cells'))!, 'click');
  expect(showItem).toHaveBeenCalledWith('r3');
  expect(reader.current()?.id).toBe('r3');
  // Something else's Data row clicked while this Tab is in front shows here, with Talk to its maker only if a researcher.
  expect(showInResearchReader(made[4])).toBe(true);
  expect(reader.current()?.id).toBe('e9');
  // ⋯ on anything but its page: Show on Main ↗ leaves the Tab, on purpose.
  fire(root.querySelector('.rd-more')!, 'click');
  const show = menu().children.find((n) => n.textContent === 'Show on Main ↗')!;
  fire(show, 'click');
  expect(leave).toHaveBeenCalledWith(made[4]);
  workspace.setVisible(false);
  expect(showInResearchReader(made[1])).toBe(false);
});

test('each researcher’s reader comes back as it was; Talk from another Tab opens the same item on its right', () => {
  provideResearchReader({ entries: () => [...made, entry({ id: 'r4:table-1', kind: 'finding-table', title: 'Counts', maker: { kind: 'researcher', id: 'r4', name: 'Researcher 4' } })], deps: {} });
  const { workspace } = make();
  workspace.bind(handlers());
  workspace.setVisible(true);
  workspace.update(state); workspace.renderArtifact('a', [], { title: '' });
  workspace.openFileTab('table:0');
  expect(researchReader()!.current()?.id).toBe('r3:table-1');
  workspace.update({ ...state, key: 'day/r4', rid: 'r4', researcher: 'Researcher 4', item: 'r4' }); workspace.renderArtifact('b', [], { title: '' });
  expect(researchReader()!.current()?.id).toBe('r4');
  workspace.update(state); workspace.renderArtifact('c', [], { title: '' });
  expect(researchReader()!.current()?.id).toBe('r3:table-1');
  openOnResearcherTab('r4', { id: 'r4:table-1', day: 'p' });
  workspace.update({ ...state, key: 'day/r4', rid: 'r4', researcher: 'Researcher 4', item: 'r4' }); workspace.renderArtifact('d', [], { title: '' });
  expect(researchReader()!.current()?.id).toBe('r4:table-1');
});

test('How it was made is the reader’s second view: its versions to choose, then what it worked from and its record', () => {
  const choose = vi.fn(), main = vi.fn();
  const { root, workspace } = make();
  workspace.bind(handlers({ main }));
  workspace.update(state);
  // research.ts draws its record afresh each time How it was made opens.
  const box = () => { const div = new TestElement('div'); div.className = 'rhow-body'; const p = new TestElement('p'); p.textContent = 'From findings v1'; div.append(p); return div as unknown as HTMLElement; };
  workspace.artifactControls({ versions: [{ label: 'Latest (follows new versions)', value: '', checked: true }, { label: 'v1 · Oct 24', value: 'findings:1', checked: false }],
    choose, how: box, more: [{ label: 'Show beside the live view', act: () => {} }], download: null });
  workspace.renderFiles([], [{ key: 'specimen', kind: 'specimen', name: 'Human cells', open: { main: true } }]);
  workspace.renderArtifact('x', [], { title: '' });
  workspace.showHow();
  const reader = root.querySelector('.data-reader-research')!;
  expect(reader.dataset.view).toBe('how');
  const versions = reader.querySelectorAll('.rd-ver-pick');
  expect(versions.map((v) => [v.textContent, v.getAttribute('aria-pressed')])).toEqual([['Latest (follows new versions)', 'true'], ['v1 · Oct 24', 'false']]);
  fire(versions[1], 'click');
  expect(choose).toHaveBeenCalledWith('findings:1');
  expect(researchReader()!.currentView()).toBe('thing');
  workspace.showHow();
  expect(reader.querySelector('.rhow-h')!.textContent).toBe('Worked from');
  fire(reader.querySelector('.rhow-used')!.querySelector('.frow')!, 'click');
  expect(main).toHaveBeenCalled();
  expect(reader.textContent).toContain('From findings v1');
});

test('show chips follow what is on the right while the page is in view', () => {
  const { root, workspace } = make();
  workspace.setVisible(true);
  workspace.update({ ...state, output: 'findings:1' });
  const chip = new TestElement('button'); chip.dataset.show = 'findings:1';
  workspace.renderThread('chip', [chip as unknown as HTMLElement]);
  expect(chip.getAttribute('aria-pressed')).toBe('true');
  workspace.update({ ...state, output: 'output:o2' });
  expect(chip.getAttribute('aria-pressed')).toBe('false');
  workspace.update({ ...state, output: 'findings:1' });
  workspace.setVisible(false);
  expect(chip.getAttribute('aria-pressed')).toBe('false');
  expect(root.querySelector('.rlog')!.children[0]).toBe(chip);
});

test('each researcher’s conversation keeps its own scroll: switching away and back returns to where the reader was', () => {
  const { root, workspace } = make();
  const log = root.querySelector('.rlog')! as TestElement & { scrollTop: number; scrollHeight: number; clientHeight: number };
  Object.assign(log, { scrollHeight: 2000, clientHeight: 400, scrollTop: 0 });
  const said = (text: string) => { const p = new TestElement('p'); p.textContent = text; p.dataset.messageId = text; return p as unknown as HTMLElement; };
  workspace.update(state); workspace.renderThread('r3', [said('one')]);
  log.scrollTop = 300; fire(log, 'scroll');
  workspace.update({ ...state, key: 'day/r4', rid: 'r4' }); workspace.renderThread('r4', [said('two')]);
  expect(log.scrollTop).toBe(2000); // a researcher first seen opens at its latest message
  log.scrollTop = 500; fire(log, 'scroll');
  workspace.update(state); workspace.renderThread('r3 again', [said('one')]);
  expect(log.scrollTop).toBe(300);
  workspace.update({ ...state, key: 'day/r4', rid: 'r4' }); workspace.renderThread('r4 again', [said('two')]);
  expect(log.scrollTop).toBe(500);
});

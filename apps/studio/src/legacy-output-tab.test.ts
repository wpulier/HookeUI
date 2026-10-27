// 0.3.0's "Researcher N results" Tabs (ticket 66 D5; legacy-output-tab.ts, the mock's legacy frame): a pointer to the
// researcher instead of the old report window, its words only as far as Data says them, its saved windows kept as they
// were, and old "Go to results" notices opening the researcher. Synthetic DOM evidence; the look is the legacy screenshots.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
vi.mock('./workspace-study-artifacts', () => ({ studyArtifacts: () => ({}) }));
import { inDataWords, legacyCard, legacyResearcher, legacyTitle, legacyWords } from './legacy-output-tab';
import { WorkspaceViews } from './workspace-views';
import type { ReaderEntry } from './data-reader';
import { fire, syntheticDOM, TestElement } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());
const r3 = { kind: 'researcher' as const, id: 'r810833', name: 'Researcher 3' };
const entry = (fields: Partial<ReaderEntry>): ReaderEntry => ({ id: 'e6', kind: 'population', title: 'Bright regions · Bright objects · 22:18', summary: '', state: 'saved',
  actions: [], day: 'p', role: 'analysis', maker: r3, ...fields } as ReaderEntry);
const report = entry({ id: 'r810833', kind: 'research', title: 'Bright-region analysis of the fluorescent nuclei', role: 'writeup', forms: [{ label: 'Presentation', outputId: 'o1' }, { label: 'Findings v1' }] });
const output = { id: 'o-r3', title: 'Researcher 3 results', kind: 'output', provenance: { researcher: 'r810833', run: 'x', from: 'main' }, specimen: { visible: false, rect: null },
  items: [{ placementId: 'legacy-1', resource: { day: 'p', id: 'r810833', version: 2, picture: null }, title: 'Researcher 3 results', zoom: 1, pan: [0.5, 0.5] }], notes: [] };

describe('which Tabs point to their researcher', () => {
  test('a Tab of kind output that names its researcher; never any other Tab', () => {
    expect(legacyResearcher(output)).toBe('r810833');
    expect(legacyResearcher({ ...output, provenance: { run: 'x' } })).toBe(null);
    expect(legacyResearcher({ ...output, kind: 'reader' })).toBe(null);
    expect(legacyResearcher({ kind: undefined })).toBe(null);
    expect(legacyResearcher(null)).toBe(null);
  });
});

describe('its words: the researcher, its result, and what of it is in Data, only as far as Data says', () => {
  test('the mock’s line: its presentation, its table and its outlines are also in Data', () => {
    const made = [report, entry({ id: 'r810833:table-1', kind: 'finding-table', title: 'Region areas' }), entry({})];
    expect(inDataWords(made, 'r810833')).toBe('Its presentation, its table and its outlines are also in Data.');
    expect(legacyWords({ rid: 'r810833', name: 'Researcher 3', title: legacyTitle(made, 'r810833'), entries: made })).toEqual({
      title: 'Researcher 3’s results now open in its own Tab',
      line: 'Bright-region analysis of the fluorescent nuclei. Its presentation, its table and its outlines are also in Data.',
      open: 'Open Researcher 3' });
  });
  test('counts and kinds as they are: a report, two tables, the sources it cited; one thing is; nothing says nothing', () => {
    const made = [entry({ id: 'r810833', kind: 'research', title: 'R', forms: [{ label: 'Findings v2' }] }), entry({ id: 'r810833:table-1', kind: 'finding-table' }),
      entry({ id: 'r810833:table-2', kind: 'finding-table' }), entry({ id: 'r810833:source-0123456789ab', kind: 'source', role: 'source' }),
      entry({ id: 'r810833:source-ba9876543210', kind: 'source', role: 'source' }), entry({ id: 'e9', maker: { kind: 'you', name: 'You' } })];
    expect(inDataWords(made, 'r810833')).toBe('Its report, its 2 tables and the 2 sources it cited are also in Data.');
    expect(inDataWords([report], 'r810833')).toBe('Its presentation is also in Data.');
    expect(inDataWords([entry({})], 'r810833')).toBe('Its outlines are also in Data.');
    expect(inDataWords([entry({ maker: { kind: 'you', name: 'You' } })], 'r810833')).toBe('');
    // Nothing in Data yet: the line is its title alone.
    expect(legacyWords({ rid: 'r810833', name: 'Researcher 3', title: 'Count the nuclei', entries: [] }).line).toBe('Count the nuclei.');
  });
  test('a researcher this Project does not list: said so, with Close this Tab only', () => {
    expect(legacyWords({ rid: 'r1', name: null, title: '', entries: [] })).toEqual({ title: 'An earlier version made this Tab', line: 'Its researcher is not in this Project now.', open: null });
  });
});

describe('the card', () => {
  test('Open Researcher 3 (primary) and Close this Tab act; without a researcher only Close this Tab', () => {
    vi.stubGlobal('document', syntheticDOM().document);
    const open = vi.fn(), close = vi.fn();
    const card = legacyCard(legacyWords({ rid: 'r810833', name: 'Researcher 3', title: 'Count the nuclei', entries: [report] }), 'ready', { open, close }) as unknown as TestElement;
    expect(card.querySelector('.legacy-tab-title')!.textContent).toBe('Researcher 3’s results now open in its own Tab');
    expect(card.querySelector('.legacy-tab-line')!.textContent).toBe('Count the nuclei. Its presentation is also in Data.');
    const buttons = card.querySelectorAll('button');
    expect(buttons.map((b) => [b.textContent, b.className])).toEqual([['Open Researcher 3', 'legacy-tab-open mm-primary'], ['Close this Tab', 'legacy-tab-close']]);
    fire(buttons[0], 'click'); fire(buttons[1], 'click');
    expect([open.mock.calls.length, close.mock.calls.length]).toEqual([1, 1]);
    const alone = legacyCard(legacyWords({ rid: 'r1', name: null, title: '', entries: [] }), null, { open, close }) as unknown as TestElement;
    expect(alone.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Close this Tab']);
  });
});

describe('the Tab itself (workspace-views.ts)', () => {
  beforeEach(() => { vi.stubGlobal('window', { dispatchEvent: vi.fn() }); vi.stubGlobal('document', { body: { dataset: {} } }); });
  test('its saved windows are kept as they were: a capture on it changes nothing (nothing is lost)', () => {
    const view = structuredClone(output);
    const current = Object.assign(Object.create(WorkspaceViews.prototype), { session: 'p', changing: false, completionRestores: new Set(), active: () => view,
      specimen: { hidden: true }, opts: { captureItems: () => [] }, visibleRoots: () => [], rect: () => [0, 0, 1, 1] });
    Reflect.get(WorkspaceViews.prototype, 'capture').call(current);
    expect(view.items).toEqual(output.items);
  });
  test('it says so before the Tab context changes, and draws its pointer, never the old window; the Stage bar names it', () => {
    const legacy = vi.fn(), reader = vi.fn(), slot = { hidden: true };
    const current = Object.assign(Object.create(WorkspaceViews.prototype), { stageReader: slot, opts: { legacy, reader } });
    Reflect.get(WorkspaceViews.prototype, 'showReader').call(current, output);
    expect((document as unknown as { body: { dataset: Record<string, string> } }).body.dataset.legacyTab).toBe('true');
    expect([slot.hidden, reader.mock.calls[0][0], legacy.mock.calls[0][0]]).toEqual([false, null, output]);
    Reflect.get(WorkspaceViews.prototype, 'showReader').call(current, { ...output, kind: undefined, provenance: undefined });
    expect((document as unknown as { body: { dataset: Record<string, string> } }).body.dataset.legacyTab).toBeUndefined();
    expect([slot.hidden, legacy.mock.calls[1][0]]).toEqual([true, null]);
  });
  test('an old "Go to results" notice opens its researcher’s own Tab', () => {
    vi.stubGlobal('document', syntheticDOM().document);
    const openResearcher = vi.fn(), goTo = vi.fn();
    const note = new TestElement('div');
    const current = Object.assign(Object.create(WorkspaceViews.prototype), { completionNote: note, layout: { active: 'main', views: [{ id: 'main' }, output] },
      completions: [{ origin: { tab: 'main' }, notice: 'Researcher 3 finished.', state: 'placed', outputTab: { id: 'o-r3' } }],
      tabOwner: () => ({}), ownsTabChoice: () => true, goTo, opts: { openResearcher } });
    Reflect.get(WorkspaceViews.prototype, 'renderCompletions').call(current);
    const go = note.querySelectorAll('button').find((b) => b.textContent === 'Go to results')! as TestElement & { onclick: () => void };
    go.onclick(); // workspace-views.ts's buttons act through onclick
    expect(openResearcher).toHaveBeenCalledWith('r810833');
    expect(goTo).not.toHaveBeenCalled();
  });
});

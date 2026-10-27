import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyRailBadge, applyRailMark, railBadges, researchersMark } from './rail-badges';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

const none = { results: 0, researchers: 0, modelsNeedYou: 0 };

describe('the rail’s badges and marks', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('shows nothing when nothing is behind an entry, or its count is not known yet', () => {
    expect(railBadges(none)).toEqual({});
    expect(railBadges({ ...none, results: null, resultsNew: 3 })).toEqual({});
    expect(researchersMark(none)).toBe(null);
  });
  it('Data counts its New results as a quiet grey count; Researchers and Models count what needs you in blue', () => {
    const badges = railBadges({ results: 3, resultsNew: 2, researchers: 2, researchersNeedYou: 1, modelsNeedYou: 1 });
    expect(badges.data).toEqual({ text: '2', label: '2 new results in this Project', tone: 'quiet' });
    expect(badges.researchers).toEqual({ text: '1', label: '1 researcher needs you', tone: 'needs' });
    expect(badges.models).toEqual({ text: '1', label: '1 model needs you', tone: 'needs' });
    // Results already seen are not news: no count for them.
    expect(railBadges({ ...none, results: 140 }).data).toBe(undefined);
    expect(railBadges({ ...none, results: 140, resultsNew: 140 }).data?.text).toBe('99+');
  });
  it('Researchers shows the lit cell while any researcher works, unless one needs you (the count says that)', () => {
    expect(researchersMark({ researchers: 2 })).toEqual({ kind: 'cell', label: '2 researchers working' });
    expect(researchersMark({ researchers: 2, researchersNeedYou: 1 })).toBe(null);
  });
  it('keeps the entry’s name as its name, says the count as its description, and takes the badge off when it is gone', () => {
    const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
    const button = new TestElement('button'); button.setAttribute('aria-label', 'Models');
    const target = button as unknown as HTMLElement;
    applyRailBadge(target, 'Models', railBadges({ ...none, modelsNeedYou: 2 }).models);
    expect(button.getAttribute('aria-label')).toBe('Models');
    expect(button.getAttribute('aria-description')).toBe('2 models need you');
    expect(button.querySelector('.study-rail-badge')!.textContent).toBe('2');
    applyRailBadge(target, 'Models', undefined);
    expect(button.getAttribute('aria-label')).toBe('Models');
    expect(button.getAttribute('aria-description')).toBe(null);
    expect(button.querySelector('.study-rail-badge')).toBe(null);
  });
  it('puts a lit cell or a dot on the corner, keeps the same cell while it stays, and says it in the description', () => {
    const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
    const button = new TestElement('button'), target = button as unknown as HTMLElement;
    const cell = vi.fn(() => new TestElement('svg') as unknown as Element);
    applyRailMark(target, { kind: 'cell', label: 'A run is working' }, cell);
    applyRailMark(target, { kind: 'cell', label: 'A run is working' }, cell);
    expect(cell).toHaveBeenCalledOnce(); // never rebuilt, so its breathing never restarts
    expect(button.querySelector('.study-rail-mark')!.dataset.kind).toBe('cell');
    expect(button.getAttribute('aria-description')).toBe('A run is working');
    applyRailMark(target, { kind: 'dot', label: 'Outline every cell is open; Tools brings it back' }, cell);
    expect(button.querySelector('.study-rail-mark')!.dataset.kind).toBe('dot');
    expect(button.getAttribute('aria-description')).toBe('Outline every cell is open; Tools brings it back');
    applyRailMark(target, null, cell);
    expect(button.querySelector('.study-rail-mark')).toBe(null);
    expect(button.getAttribute('aria-description')).toBe(null);
  });
});

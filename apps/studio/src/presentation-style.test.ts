import { afterEach, expect, test, vi } from 'vitest';
import { renderResearchReport } from './research-report';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';
import type { ResearchSession } from './protocol';

afterEach(() => vi.unstubAllGlobals());

const finding = (style?: object) => ({
  title: 'Pith cells', summary: 'Large thin-walled cells',
  limitations: ['No scale or stain known'],
  report: { methods: 'Looked', observations: 'Seen', measurements: 'None', limitations: 'No scale or stain known' },
  captures: [{ title: 'Your slide', kind: 'clip', state: 'held', summary: 'Two frames', at: 1,
    source: { day: '2026-10-11', id: 'k1', version: null, contentRevision: 'x' }, frames: [] }],
  presentation: { id: 'o2', version: 2, plan: { layout: 'briefing', blocks: [
    { ref: 'summary' }, { ref: 'report:observations' }, { ref: 'limitations' }, { ref: 'capture:0' }], ...(style ? { style } : {}) } },
}) as unknown as NonNullable<ResearchSession['result']>;

test('a restyle changes the look: theme, density, emphasis and where the pictures go', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const plain = renderResearchReport(finding()) as unknown as TestElement;
  expect(plain.dataset.theme).toBeUndefined();
  const styled = renderResearchReport(finding({ theme: 'paper', density: 'spacious', pictures: 'lead',
    emphasis: ['report:observations', 'not-a-block'] })) as unknown as TestElement;
  expect(styled.dataset.theme).toBe('paper');
  expect(styled.dataset.density).toBe('spacious');
  const blocks = styled.querySelector('.research-presented-blocks')!;
  expect(blocks.children.map((node) => node.dataset.anchor)).toEqual(['summary', 'capture:0', 'report:observations', 'limitations']);
  expect(styled.querySelector('[data-anchor="report:observations"]')!.classList.contains('research-emphasis')).toBe(true);
});

test('an unknown look is left out, never guessed, and the facts are unchanged', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const styled = renderResearchReport(finding({ theme: 'neon', density: 'compact' })) as unknown as TestElement;
  expect(styled.dataset.theme).toBeUndefined();
  expect(styled.dataset.density).toBe('compact');
  expect(styled.textContent).toContain('Large thin-walled cells');
  expect(styled.textContent).not.toContain('This presentation is invalid');
});

test('the limitations list stands for the report section: a plan with the list alone is valid', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const rendered = renderResearchReport(finding()) as unknown as TestElement;
  expect(rendered.textContent).not.toContain('This presentation is invalid');
});

import { describe, expect, it } from 'vitest';
import { defaultResearchOutput, isResearcherPage } from './research';
import type { ResearchOutput, ResearchSession } from './protocol';

const page = { id: 'o1', kind: 'html', path: 'outputs/01-presentation-x.html', url: '/research/2026-10-30/r1/outputs/01-presentation-x.html',
  caption: 'Presentation: x', content: { kind: 'presentation', title: 'x', summary: 's', data_from: ['e1'] }, current: true } as unknown as ResearchOutput;
const lesson = { ...page, id: 'o2', content: { kind: 'lesson', title: 'l', key_ideas: [], steps: [] } } as unknown as ResearchOutput;

describe("a researcher's own page (make_page)", () => {
  it('is what a research deliverable opens on, rendered, not its findings as text', () => {
    const s = { rid: 'r1', deliverable: 'research', outputs: [page] } as unknown as ResearchSession;
    expect(isResearcherPage(page)).toBe(true);
    expect(defaultResearchOutput(s, null)?.id).toBe('o1');
  });
  it('is never taken for a lesson, and a lesson deliverable still opens its lesson', () => {
    expect(isResearcherPage(lesson)).toBe(false);
    const s = { rid: 'r1', deliverable: 'lesson', outputs: [page, lesson] } as unknown as ResearchSession;
    expect(defaultResearchOutput(s, null)?.kind).toBe('html');
  });
});

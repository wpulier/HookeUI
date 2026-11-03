// A Project card (project-card.ts; brain/toolbox/projects.py ProjectCards): the tidy Hooke proposes with Do it and Not
// now, then what it did with Undo; the Projects a request may mean, each with Open; every word the server's.
import { describe, expect, test, vi } from 'vitest';
import { ProjectCard, projectCardCell, type ProjectCardWire } from './project-card';
import { syntheticDOM, fire, type TestElement } from './workspace-windows.test-support';

function wire(fields: Partial<ProjectCardWire> = {}): ProjectCardWire {
  return { id: 'tidy-o1', kind: 'tidy', session: '2026-10-28', thread: 't1', requestId: 'RUN/r3',
    title: 'Tidy “Leaf epidermis”', status: 'proposed', said: 'Nothing changes until you say yes',
    lines: ['Rename the conversation “Outline the stomata” to “Stomata count”', 'Close the Tab “Tab 2” (empty; All Tabs reopens it)'],
    choices: [], actions: [{ id: 'apply', label: 'Do it', primary: true }, { id: 'dismiss', label: 'Not now' }], ...fields };
}

describe('a Project card', () => {
  test('a proposed tidy shows what it would change and asks; done offers Undo; declined offers nothing', () => {
    vi.stubGlobal('document', syntheticDOM().document);
    try {
      const press = vi.fn();
      const card = new ProjectCard('tidy-o1', press);
      card.update(wire(), true);
      const text = (card.root as unknown as TestElement).textContent;
      expect(text).toContain('Tidy “Leaf epidermis”');
      expect(text).toContain('Nothing changes until you say yes');
      expect(text).toContain('Close the Tab “Tab 2”');
      fire(card.root.querySelector('[data-action="apply"]') as unknown as TestElement, 'click');
      expect(press).toHaveBeenCalledWith({ action: 'apply' }, expect.objectContaining({ id: 'tidy-o1' }));
      card.update(wire({ status: 'done', said: 'Done · Undo puts it back', lines: ['Called conversation t1 “Stomata count”'],
        actions: [{ id: 'undo', label: 'Undo' }] }), false);
      expect(card.root.querySelector('[data-action="apply"]')).toBeNull();
      expect(card.root.querySelector('[data-action="undo"]')).not.toBeNull();
      card.update(wire({ status: 'declined', said: 'Not done · nothing changed', actions: [] }), false);
      expect(card.root.querySelector('[data-action]')).toBeNull();
      expect(projectCardCell(wire({ status: 'declined' }))).toBe('stopped');
      expect(projectCardCell(wire({ status: 'partly' }))).toBe('attention');
    } finally { vi.unstubAllGlobals(); }
  });

  test('"which Project?" lists each with Open, the open one said so; Open asks for that Project', () => {
    vi.stubGlobal('document', syntheticDOM().document);
    try {
      const press = vi.fn();
      const card = new ProjectCard('which-1', press);
      card.update(wire({ id: 'which-1', kind: 'choose', status: 'asked', title: 'Which Project?', said: 'Say which, or open one',
        lines: [], actions: [], choices: [{ session: '2026-10-28', name: 'Leaf epidermis', detail: 'last used Oct 28 · leaf epidermis' },
          { session: '2026-10-04', name: 'Oct 4', detail: 'last used Oct 4 · leaves' }] }), true, '2026-10-04');
      const buttons = card.root.querySelectorAll('[data-session]') as unknown as TestElement[];
      expect(buttons.map((b) => b.textContent)).toEqual(['Open', 'Open now']);
      expect((buttons[1] as unknown as HTMLButtonElement).disabled).toBe(true);
      fire(buttons[0], 'click');
      expect(press).toHaveBeenCalledWith({ open: '2026-10-28' }, expect.objectContaining({ id: 'which-1' }));
      expect(projectCardCell(wire({ status: 'asked' }))).toBe('needs');
    } finally { vi.unstubAllGlobals(); }
  });
});

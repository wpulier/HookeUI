// markdown.ts: the safe subset the chat renders. Parsing is pure; rendering builds DOM nodes from text only.
import { afterEach, expect, test, vi } from 'vitest';
import { inline, parse, renderMarkdown } from './markdown';
import { TestElement } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());

test('the live answer of 2026-10-11 reads as a list with bold names, not asterisks run together', () => {
  const answer = 'Here are the segmentation options available right now:\n'
    + '- **EdgeTAM** (default): Runs locally on your Mac.\n'
    + '- **SAM 2.1 tiny**: Runs locally on your Mac, accepting a point or box prompt.\n'
    + '- **micro-sam (light microscopy)**: Tailored for light microscope images.\n'
    + 'If you want to try one, just let me know!';
  const blocks = parse(answer);
  expect(blocks.map(b => b.kind)).toEqual(['p', 'ul', 'p']);
  const list = blocks[1] as { kind: 'ul'; items: ReturnType<typeof inline>[] };
  expect(list.items).toHaveLength(3);
  expect(list.items[0][0]).toEqual({ kind: 'strong', text: 'EdgeTAM' });
});

test('numbered lists, code and italics; snake_case and arithmetic stay text', () => {
  expect(parse('1. first\n2. second').map(b => b.kind)).toEqual(['ol']);
  expect(inline('use `sam2.1-tiny` here')).toEqual([{ kind: 'text', text: 'use ' }, { kind: 'code', text: 'sam2.1-tiny' }, { kind: 'text', text: ' here' }]);
  expect(inline('a *guard* cell')[1]).toEqual({ kind: 'em', text: 'guard' });
  expect(inline('micro_sam_lm and 2*3*4')).toEqual([{ kind: 'text', text: 'micro_sam_lm and 2*3*4' }]);
  // Still being spoken: an unclosed marker stays as it is until the rest arrives.
  expect(inline('**Edge')).toEqual([{ kind: 'text', text: '**Edge' }]);
});

test('a pipe table becomes a table; a lone pipe line stays a paragraph', () => {
  const blocks = parse('| Model | Cells |\n|---|---|\n| Cellpose-SAM | 321 |\n| micro-sam | 280 |');
  expect(blocks).toHaveLength(1);
  const table = blocks[0] as { kind: 'table'; head: unknown[]; rows: unknown[][] };
  expect(table.kind).toBe('table');
  expect(table.head).toHaveLength(2);
  expect(table.rows).toHaveLength(2);
  expect(parse('| not a table |').map(b => b.kind)).toEqual(['p']);
});

test('markup in the text is never markup: it is rendered as text nodes', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const target = new TestElement('span');
  renderMarkdown(target as unknown as HTMLElement, '<img src=x onerror=alert(1)> **<b>bold</b>**\n- <script>x</script>');
  const tags = (node: TestElement): string[] => node.children.filter(child => child.tagName !== '#TEXT')
    .flatMap(child => [child.tagName.toLowerCase(), ...tags(child)]);
  expect(tags(target)).toEqual(['p', 'strong', 'ul', 'li']);
  expect(target.textContent).toContain('<img src=x onerror=alert(1)>');
  expect(target.textContent).toContain('<script>x</script>');
});

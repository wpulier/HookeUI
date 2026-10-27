// Chat answers in a safe subset of Markdown (the user's decision of 2026-10-11: lists and data read as lists and
// tables, not as asterisks run together). Built as DOM nodes from text, never innerHTML: paragraphs and line breaks,
// bullet and numbered lists, **bold**, *italic*, `code` and simple pipe tables. Anything else stays its own text.
// An answer still being spoken grows; it is parsed again whole each time, and an unclosed "**Edge" stays text.

export type Inline = { kind: 'text' | 'strong' | 'em' | 'code' | 'br'; text: string };
export type Block =
  | { kind: 'p'; inline: Inline[] }
  | { kind: 'ul' | 'ol'; items: Inline[][] }
  | { kind: 'table'; head: Inline[][]; rows: Inline[][][] };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d{1,3}[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
// Code first (nothing inside it is formatted); emphasis only at word edges, so snake_case and 2*3*4 stay text.
const INLINE = /`([^`\n]+)`|\*\*([^*\n]+?)\*\*|(?<![\w*])\*([^*\s][^*\n]*?)\*(?![\w*])|(?<![\w_])_([^_\s][^_\n]*?)_(?![\w_])/g;

export function inline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) out.push({ kind: 'text', text: text.slice(last, at) });
    if (match[1] !== undefined) out.push({ kind: 'code', text: match[1] });
    else if (match[2] !== undefined) out.push({ kind: 'strong', text: match[2] });
    else out.push({ kind: 'em', text: match[3] ?? match[4] ?? '' });
    last = at + match[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
}

export function parse(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (!paragraph.length) return;
    const parts: Inline[] = [];
    paragraph.forEach((line, n) => { if (n) parts.push({ kind: 'br', text: '' }); parts.push(...inline(line)); });
    blocks.push({ kind: 'p', inline: parts });
    paragraph = [];
  };
  for (let n = 0; n < lines.length; n++) {
    const line = lines[n];
    if (!line.trim()) { flush(); continue; }
    if (TABLE_ROW.test(line) && n + 1 < lines.length && TABLE_RULE.test(lines[n + 1])) {
      flush();
      const head = cells(line).map(inline);
      const rows: Inline[][][] = [];
      n += 2;
      while (n < lines.length && TABLE_ROW.test(lines[n])) rows.push(cells(lines[n++]).map(inline));
      n -= 1;
      blocks.push({ kind: 'table', head, rows });
      continue;
    }
    const bullet = BULLET.exec(line), numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      flush();
      const kind = bullet ? 'ul' : 'ol';
      const previous = blocks[blocks.length - 1];
      const item = inline((bullet ?? numbered)![1]);
      if (previous && previous.kind === kind) previous.items.push(item);
      else blocks.push({ kind, items: [item] });
      continue;
    }
    // A line that continues a list item (indented, after an item) joins it.
    const previous = blocks[blocks.length - 1];
    if (!paragraph.length && previous && (previous.kind === 'ul' || previous.kind === 'ol') && /^\s{2,}\S/.test(line)) {
      previous.items[previous.items.length - 1].push({ kind: 'text', text: ' ' }, ...inline(line.trim()));
      continue;
    }
    paragraph.push(line);
  }
  flush();
  return blocks;
}

function inlineNodes(parts: Inline[]): (Node | string)[] {
  return parts.map((part) => {
    if (part.kind === 'text') return part.text;
    if (part.kind === 'br') return document.createElement('br');
    const element = document.createElement(part.kind === 'strong' ? 'strong' : part.kind === 'em' ? 'em' : 'code');
    element.textContent = part.text;
    return element;
  });
}

/** Replaces `target`'s content with the answer as formatted DOM, built from text nodes only. */
export function renderMarkdown(target: HTMLElement, text: string): void {
  const nodes: Node[] = [];
  for (const block of parse(text)) {
    if (block.kind === 'p') {
      const p = document.createElement('p'); p.append(...inlineNodes(block.inline)); nodes.push(p);
    } else if (block.kind === 'table') {
      const table = document.createElement('table');
      const head = document.createElement('thead'), row = document.createElement('tr');
      for (const cell of block.head) { const th = document.createElement('th'); th.append(...inlineNodes(cell)); row.append(th); }
      head.append(row); table.append(head);
      const body = document.createElement('tbody');
      for (const cellsOfRow of block.rows) {
        const tr = document.createElement('tr');
        for (const cell of cellsOfRow) { const td = document.createElement('td'); td.append(...inlineNodes(cell)); tr.append(td); }
        body.append(tr);
      }
      table.append(body); nodes.push(table);
    } else {
      const list = document.createElement(block.kind);
      for (const item of block.items) { const li = document.createElement('li'); li.append(...inlineNodes(item)); list.append(li); }
      nodes.push(list);
    }
  }
  target.replaceChildren(...nodes);
}

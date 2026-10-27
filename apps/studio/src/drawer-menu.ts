// A small menu under a button (the mock's .menu): the left drawers' "+ New", Data's ⋯ and every row's ⋯ (Stop,
// Activity, Open; Add to Tab…, Measurements…). It looks like the Tab row's "+" and ⌄ menus (.workspace-add-menu in
// workspace-feel.css). Opening one closes any other; Escape, a choice or a click outside closes it and focus goes back
// to its button. Nothing in it acts until chosen.

export type MenuItem =
  // `checked`: one of a set of choices (the open conversation), said and ticked; absent, an action.
  | { label: string; act: () => void; title?: string; kbd?: string; danger?: boolean; disabled?: boolean; checked?: boolean;
    /** Its accessible name when it differs from its words (a control moved into a menu keeps its own name). */
    aria?: string }
  | { heading: string }
  | 'separator';

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node;
};

let open: { menu: HTMLElement; close: (refocus: boolean) => void } | null = null;

/** Opens a menu of `items` under `anchor` (its right edge on the anchor's), or closes it when it is already open there.
 * `above`: over the anchor instead (a control at the bottom of the window, as the composer's +); `start`: from the
 * anchor's left edge. */
export function popMenu(anchor: HTMLElement, items: MenuItem[], label: string, options: { above?: boolean; start?: boolean } = {}): HTMLElement | null {
  const again = open?.menu.dataset.anchor === anchor.dataset.menuId && !!anchor.dataset.menuId;
  open?.close(false);
  if (again) return null;
  anchor.dataset.menuId ||= String(Math.random()).slice(2);
  const menu = el('div', 'workspace-add-menu mm-drawer-menu'); menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', label);
  menu.dataset.anchor = anchor.dataset.menuId;
  const outside = (event: Event) => { if (!menu.contains(event.target as Node) && !anchor.contains(event.target as Node)) close(false); };
  const close = (refocus: boolean) => {
    menu.remove(); document.removeEventListener('pointerdown', outside, true); anchor.setAttribute('aria-expanded', 'false');
    if (open?.menu === menu) open = null;
    if (refocus) anchor.focus({ preventScroll: true });
  };
  for (const item of items) {
    if (item === 'separator') { menu.append(el('hr')); continue; }
    if ('heading' in item) { const h = el('div', 'workspace-menu-heading', item.heading); h.setAttribute('role', 'presentation'); menu.append(h); continue; }
    const b = el('button'); b.type = 'button'; b.setAttribute('role', item.checked === undefined ? 'menuitem' : 'menuitemradio');
    if (item.checked !== undefined) { b.setAttribute('aria-checked', String(item.checked)); b.append(el('span', 'workspace-menu-check', item.checked ? '✓' : '')); }
    b.append(el('span', 'workspace-menu-label', item.label));
    if (item.kbd) b.append(el('kbd', '', item.kbd));
    if (item.title) b.title = item.title;
    if (item.aria) b.setAttribute('aria-label', item.aria);
    if (item.danger) b.dataset.danger = 'true';
    b.disabled = !!item.disabled;
    b.addEventListener('click', () => { close(true); item.act(); });
    menu.append(b);
  }
  menu.addEventListener('keydown', (event) => {
    const buttons = [...menu.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')], at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); }
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); buttons[(at + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus(); }
    else if (event.key === 'Tab') close(false);
  });
  const r = anchor.getBoundingClientRect();
  const viewport = typeof innerWidth === 'number' ? innerWidth : 1440;
  if (options.above) menu.style.bottom = `${Math.round((typeof innerHeight === 'number' ? innerHeight : 900) - r.top + 4)}px`;
  else menu.style.top = `${Math.round(r.bottom + 4)}px`;
  // Left of its button when there is room on the left (a drawer's header), else from its left edge.
  if (r.right > 240 && !options.start) menu.style.right = `${Math.max(8, Math.round(viewport - r.right))}px`; else menu.style.left = `${Math.max(8, Math.round(r.left))}px`;
  document.body.append(menu);
  anchor.setAttribute('aria-expanded', 'true');
  document.addEventListener('pointerdown', outside, true);
  open = { menu, close };
  menu.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true });
  return menu;
}

/** A ⋯ button that opens `items()` (read when pressed, so the menu says the state of that moment). */
export function moreButton(label: string, items: () => MenuItem[], className = 'mm-row-more'): HTMLButtonElement {
  const b = el('button', className); b.type = 'button';
  b.setAttribute('aria-label', label); b.setAttribute('aria-haspopup', 'menu'); b.setAttribute('aria-expanded', 'false');
  b.append(dots());
  b.addEventListener('click', (event) => { event.stopPropagation(); popMenu(b, items(), label); });
  return b;
}

/** The ⋯ mark (three dots on the 20 px grid). */
export function dots(): SVGSVGElement {
  const svg = (document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg')) as SVGSVGElement;
  svg.setAttribute('viewBox', '0 0 20 20'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', 'mm-dots');
  svg.innerHTML = '<circle cx="5" cy="10" r="1.3"/><circle cx="10" cy="10" r="1.3"/><circle cx="15" cy="10" r="1.3"/>';
  return svg;
}

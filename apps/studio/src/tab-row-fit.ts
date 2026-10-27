// The Tab row fits like a browser's (workspace-feel plan, "The surfaces / Workspace"; the mock's fitTabs()). An
// unselected Tab's × never takes room (CSS: it floats over the Tab's end on hover and focus), so a short name is whole.
// When Tabs run short, first the work Tabs shrink (CSS: `flex: 0 1 auto`, down to 96 px, the longest names ellipsized;
// researcher, Study and draft Tabs and Main keep their size); once a name is cut or the row is over, a recording's
// label shortens (`data-fit="tight"`); then pictures and recordings with a long name (more than 12 characters) keep only
// their specimen's thumbnail, one at a time from the right; one with no thumbnail, or a short name, is never reduced to
// a mark that could not be told apart from the next. Only then do the least urgent Tabs fold into ⌄: finished
// researcher, Study and draft Tabs first, then the person's own Tabs, rightmost first each; what that frees gives
// thumbnails their names back. That departs from the mock's
// fitTabs order (work Tabs first), in a case its frames never show: Will's first complaint was losing his own Tabs
// ("yours … and researchers you made, side by side"), and a folded finished researcher stays reachable from its green
// strip pill, Researchers and ⌄. Main, the Tab showing, and a Tab whose lit cell is `working` (working), `needs` (needs
// you), `still` (Stop requested) or `queued` (waiting for a free slot) never fold: a researcher the person just started
// opens as a Tab beside theirs and stays in sight while it waits. Every other Tab may fold when nothing else fits: one
// whose cell says its work ended (`ready`, `partial`, `attention`, `stopped`), a draft's `idle` cell, or a Tab with no
// cell (the person's own, a Study). Whatever still does not fit scrolls by whole Tabs, and ⌄ counts every Tab out of
// sight, coloured for the most urgent (needs you, then working or Stop requested; a waiting one is counted, never
// called working). Layout reads happen only when the row changes (a render, a resize, a scroll coming to rest), never
// per frame.

/** A lit cell ⌄ colours for when its Tab is out of sight: working, needing the person, or Stop requested. */
const URGENT = '[data-kind="lit-cell"]:is([data-state="working"],[data-state="needs"],[data-state="still"])';
/** A lit cell whose Tab never folds: the urgent ones, and one waiting for a free slot (a researcher just started). */
const STAYS = '[data-kind="lit-cell"]:is([data-state="working"],[data-state="needs"],[data-state="still"],[data-state="queued"])';
const NEEDS = '[data-kind="lit-cell"][data-state="needs"]';
const ICON_ONLY = new Set(['picture', 'recording']);
const RESEARCH = new Set(['researcher', 'study', 'draft']);
/** A name this short ("Tab 2", "Tab 12") is never reduced to a mark: it fits whole in a 96 px Tab. */
const SHORT_NAME = 12;

export interface TabRowParts {
  /** The row (gets `data-fit`). */
  strip: HTMLElement;
  /** The scroller holding the Tabs (the tablist). */
  tabs: HTMLElement;
  /** The overlay fade at the scroller's right edge (shown only while the Tabs move). */
  fade: HTMLElement;
  /** ⌄'s count of Tabs out of sight. */
  count: HTMLElement;
  /** ⌄ itself, named with that count. */
  more: HTMLElement;
}

const items = (tabs: HTMLElement) =>
  (Array.from(tabs.children) as HTMLElement[]).filter((node) => node.classList.contains('workspace-view-tab'));
const selected = (tab: HTMLElement) => tab.querySelector('[role="tab"]')?.getAttribute('aria-selected') === 'true';
const labelOf = (tab: HTMLElement) => tab.querySelector<HTMLElement>('.workspace-tab-label');
/** A Tab in sight whose name is cut (its ellipsis shows). */
const cut = (tab: HTMLElement) => {
  if (tab.hidden || tab.dataset.icon) return false;
  const label = labelOf(tab);
  return !!label && label.scrollWidth > label.clientWidth + 1;
};
/** A picture or recording that may keep only its thumbnail: not showing, a long name, and a thumbnail to tell it by. */
const iconable = (tab: HTMLElement) => ICON_ONLY.has(tab.dataset.tabKind ?? '') && !selected(tab)
  && (labelOf(tab)?.textContent?.trim().length ?? 0) > SHORT_NAME && !!tab.querySelector('.workspace-tab-thumb');

/** Fit the row from scratch: undo the last fit, then shorten, iconify and fold only as far as needed. */
export function fitTabRow(parts: TabRowParts): void {
  const { strip, tabs } = parts;
  const all = items(tabs);
  tabs.style.maxWidth = '';
  for (const tab of all) { tab.hidden = false; delete tab.dataset.icon; }
  delete strip.dataset.fit;
  // 1 px of rounding is allowed here and in measureTabRow alike, so the fit and the count agree.
  const over = () => tabs.scrollWidth > tabs.clientWidth + 1;
  if (all.length && (over() || all.some(cut))) strip.dataset.fit = 'tight';
  if (all.length && over()) {
    for (const tab of all.filter(iconable).reverse()) {
      if (!over()) break;
      tab.dataset.icon = 'true';
    }
  }
  if (all.length && over()) {
    const can = all.filter((t) => t.dataset.home !== 'true' && !selected(t) && !t.querySelector(STAYS));
    const research = can.filter((t) => RESEARCH.has(t.dataset.tabKind ?? '')).reverse();
    const work = can.filter((t) => !research.includes(t)).reverse();
    for (const tab of [...research, ...work]) {
      if (!over()) break;
      tab.hidden = true;
    }
    // What folding freed goes back to names: a Tab still in sight keeps its name again, from the left, while it fits.
    for (const tab of all.filter((t) => !t.hidden && t.dataset.icon)) {
      delete tab.dataset.icon;
      if (over()) { tab.dataset.icon = 'true'; break; }
    }
  }
  const shown = all.filter((t) => !t.hidden);
  const home = shown[0];
  const homeWidth = home ? home.offsetWidth : 0;
  // The selected Tab is scrolled clear of sticky Main and of the fade.
  tabs.style.scrollPaddingInlineStart = `${homeWidth}px`;
  const active = shown.find(selected);
  if (active && active !== home) {
    const left = active.offsetLeft, right = left + active.offsetWidth, room = tabs.clientWidth;
    if (left - tabs.scrollLeft < homeWidth) tabs.scrollLeft = left - homeWidth;
    else if (right - tabs.scrollLeft > room) tabs.scrollLeft = right - room;
  }
  measureTabRow(parts);
}

/** After a fit or a scroll at rest: end the row on a whole Tab and say on ⌄ how many are out of sight. */
export function measureTabRow({ tabs, fade, count, more }: TabRowParts): void {
  const all = items(tabs);
  const homeWidth = all[0] ? all[0].offsetWidth : 0;
  const scroll = tabs.scrollLeft, room = tabs.clientWidth;
  const out: HTMLElement[] = [];
  let lastRight = homeWidth, cut = false;
  for (const tab of all.slice(1)) {
    if (tab.hidden) { out.push(tab); continue; }
    const left = tab.offsetLeft - scroll, right = left + tab.offsetWidth;
    // The same 1 px the fit allows (`over`): a Tab rounding 1 px past the edge fits, and is neither cut nor counted.
    if (left >= homeWidth - 1 && right <= room + 1) lastRight = Math.max(lastRight, right);
    else { out.push(tab); if (left > 0) cut = true; }
  }
  // At rest the row ends on a whole Tab: the scroller stops at the last Tab that fits, and ⌄ counts the rest.
  if (cut) tabs.style.maxWidth = `${Math.ceil(lastRight)}px`;
  const urgency = out.some((t) => t.querySelector(NEEDS)) ? 'needs' : out.some((t) => t.querySelector(URGENT)) ? 'working' : '';
  count.textContent = out.length ? String(out.length) : '';
  if (urgency) count.dataset.urgency = urgency; else delete count.dataset.urgency;
  more.setAttribute('aria-label', out.length
    ? `All Tabs, ${out.length} more${urgency === 'needs' ? ', one needs you' : urgency === 'working' ? ', working' : ''}`
    : 'All Tabs');
  fade.dataset.on = 'false';
  fade.style.left = `${tabs.offsetLeft + tabs.clientWidth - 36}px`;
}

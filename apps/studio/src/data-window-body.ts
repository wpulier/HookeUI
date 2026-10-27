// The Data window's body (workspace-study.ts; the mock's dataDrawer() and dataList()): Search Data, the Made by filter,
// then the sections (data-list.ts: Pictures and recordings, Analyses, Write-ups, Sources, Other). While they show they
// stay mounted and only what is in the filter and the sections is redrawn, so Search Data keeps focus and its caret
// while you type and while a background refresh redraws the list (removing a focused box from the page blurs it, which
// is what happened when the whole body was rebuilt on every keystroke). Activity, the window's second tab, or a notice
// take their place.

export class DataWindowBody {
  readonly root: HTMLElement;
  /** Search Data, the filter and the sections, in one section. */
  readonly results: HTMLElement;
  /** "Made by everyone ▾", or the chosen maker's chip: redraw its contents, never the element. */
  readonly filter: HTMLElement;
  /** The sections and their rows, or the empty line. */
  readonly list: HTMLElement;

  constructor(root: HTMLElement, search: HTMLElement, filter: HTMLElement, list: HTMLElement) {
    this.root = root;
    this.filter = filter;
    this.list = list;
    this.results = document.createElement('section'); this.results.className = 'study-data-results';
    this.results.append(search, filter, list);
  }

  /** Shows Search Data and the sections, mounting them only when something else was showing. */
  showResults(): void {
    const shown = this.root.children;
    if (shown.length === 1 && shown[0] === this.results) return;
    this.root.replaceChildren(this.results);
  }

  /** Shows something else in their place: Activity, or a notice such as "Retained results could not be read". */
  show(...nodes: Node[]): void {
    const shown = this.root.children;
    if (nodes.length === shown.length && nodes.every((node, i) => shown[i] === node)) return;
    this.root.replaceChildren(...nodes);
  }
}

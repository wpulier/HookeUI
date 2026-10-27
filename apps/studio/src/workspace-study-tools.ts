/** Presentation only: native Actions owns readiness, generated forms, drafts and execution. */
import { capabilityWays } from './keystrip';
import { tabMark, type TabMark } from './tab-marks';

/** A task's mark from its words, on the one icon set (tab-marks.ts, the mock's 20 px grid): Bright objects' own card and
 * anything else that names a tool by its title. */
export function toolIcon(label: string): SVGSVGElement {
  const t = label.toLowerCase();
  const name: TabMark = /measure|table|size/.test(t) ? 'measure' : /bright/.test(t) ? 'bright' : /cell|outline|segment|mask/.test(t) ? 'outline'
    : /track|motion|follow/.test(t) ? 'track' : /camera|record|video/.test(t) ? 'camera' : /find|search|look|identify/.test(t) ? 'find'
    : /image|picture|frame|view/.test(t) ? 'picture' : /research|explain|ask|text|describe/.test(t) ? 'output' : 'look';
  return tabMark(name, 'study-tool-icon');
}

/** The workspace's Tools around the Actions panel (actions-panel.ts), which draws its By task / By model list itself
 * (task rows with their model, Can be set up at the bottom; Models' sections By model): this moves the switch to the
 * top, keeps Bright objects' own card for when its Find bright regions row is not listed, says when nothing is ready,
 * and keeps Add your own below. It moves no row: the panel's redraws own them. */
export function toolsBrowser(actions: HTMLElement, search: HTMLInputElement, bright: HTMLElement, navigate: (hash: string) => void) {
  const tabs = actions.querySelector<HTMLElement>('.tools-view') ?? document.createElement('div');
  tabs.classList.add('study-tool-tabs');
  // Panels have a title and no subtitle (docs/design/2026-10-20-hooke-words.md, "Calm first view"): the hint stays
  // for screen readers only, naming which view this is.
  const hint = document.createElement('p'); hint.className = 'study-tool-hint visually-hidden';
  const entry = capabilityWays({ add: way => navigate(`#toolbox/add-${way}`) });
  entry.classList.add('study-tool-entry');
  const empty = document.createElement('p'); empty.className = 'study-tools-empty'; empty.setAttribute('role', 'status');
  const byModel = () => tabs.querySelector('[data-mode="model"]')?.getAttribute('aria-pressed') === 'true';
  function refresh() {
    const model = byModel();
    const words = model ? 'Every model in Models, in its sections.' : 'Each task with the model it will use.';
    if (hint.textContent !== words) hint.textContent = words;
    const query = search.value.trim().toLowerCase();
    const list = actions.querySelector('.actions-list');
    // Bright objects is a task row of its own (Find bright regions, its page with its own settings): its card only stands
    // in when that row is not listed (an older server, or the catalog entry turned off).
    const offered = !!list?.querySelector('[data-action="catalog:basic-bright-objects"]');
    bright.hidden = model || offered || !!query && !'bright objects local threshold'.includes(query);
    let common = list?.querySelector<HTMLElement>('[aria-label="Common"]');
    if (!bright.hidden && list && !common) {
      common = document.createElement('section'); common.className = 'actions-group'; common.setAttribute('aria-label', 'Common');
      const heading = document.createElement('h3'); heading.className = 'mm-dsection'; heading.textContent = 'Common'; common.append(heading); list.prepend(common);
    }
    if (!bright.hidden && common && bright.parentElement !== common) common.querySelector('h3')!.after(bright);
    const ready = list ? [...list.querySelectorAll<HTMLElement>('.trow')].filter((row) => row.dataset.available === 'true') : [];
    const count = ready.length + (bright.hidden ? 0 : 1);
    actions.querySelectorAll<HTMLElement>('.actions-view,.actions-foot,.actions-none').forEach(node => { node.hidden = true; });
    empty.hidden = model || count > 0;
    const message = query ? 'No ready task matches. Those listed may need a model set up first.' : 'No tasks are ready here yet. Set up a model below.';
    if (empty.textContent !== message) empty.textContent = message;
  }
  const observer = new MutationObserver(refresh);
  observer.observe(actions, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-pressed'] });
  search.addEventListener('input', refresh);
  actions.addEventListener('tools-mode', refresh);
  refresh();
  return { tabs, hint, entry, empty, showReady() {
    tabs.querySelector<HTMLButtonElement>('[data-mode="task"]')?.click(); search.value = ''; search.dispatchEvent(new Event('input')); refresh();
  } };
}

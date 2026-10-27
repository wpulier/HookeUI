import { WorkingStrip } from './working-strip';

const byId = (id: string): HTMLElement => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Studio shell requires #${id}`);
  return node;
};
function node(tag: string, className: string): HTMLElement {
  return Object.assign(document.createElement(tag), { className });
}
function icon(name: string): SVGSVGElement {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('viewBox', '0 0 16 16');
  const use = document.createElementNS(svg.namespaceURI, 'use'); use.setAttribute('href', `#i-${name}`); svg.append(use);
  return svg;
}

/** Hosts existing live components; it never owns resources, research or conversation state. The session's things (its
 * assets, Made, Researchers, Activity) are the side bar's alone; the status meters sit at the right end of the Working
 * strip under the Stage (working-strip.ts), beside the work at hand. */
export class StudioShell {
  readonly toolbar = node('div', 'dock-toolbar');
  readonly meterSlot = node('div', 'dock-status');
  /** The Working strip: a fixed row of the workspace grid under the Stage (and the Assistant's column), always there. */
  readonly strip: WorkingStrip;

  constructor() {
    document.body.classList.add('studio-shell');
    const keyboardInset = () => {
      const viewport = window.visualViewport;
      document.documentElement.style.setProperty('--keyboard-inset', `${viewport ? Math.max(0, innerHeight - viewport.height - viewport.offsetTop) : 0}px`);
    };
    window.visualViewport?.addEventListener('resize', keyboardInset);
    window.visualViewport?.addEventListener('scroll', keyboardInset);
    keyboardInset();
    const dock = document.querySelector<HTMLElement>('.dock')!;
    document.querySelector('.notebook')!.before(dock); // visual and keyboard order agree
    this.toolbar.id = 'dock-toolbar';
    const tools = dock.querySelector<HTMLElement>('.tools')!;
    const groups = [['cells', 'clear'], ['whole-frame', 'spots', 'map']];
    const glyphs: Record<string, string> = { cells: 'cells', clear: 'clear', 'whole-frame': 'frame', spots: 'spots', map: 'map' };
    for (const ids of groups) {
      const group = node('div', 'tool-group');
      for (const id of ids) {
        const control = byId(id); const words = node('span', 'tool-label'); words.textContent = control.textContent;
        control.title ||= control.textContent || ''; control.replaceChildren(icon(glyphs[id]), words); group.append(control);
      }
      tools.append(group);
    }
    this.toolbar.append(tools, node('div', 'dock-gap'), this.meterSlot);
    // The Working strip (Will, 2026-10-24): a fixed 40 px row under the Stage, so the view never resizes for work that
    // comes and goes. The shelf (#shelf) is its row of pills, the view's passing line (#hint) its quiet left, and the
    // status line (the performance meter, Spending) its right end, off the Stage's corner. It reaches past the Stage
    // under the Assistant's column (working-strip.ts place).
    const studio = document.querySelector<HTMLElement>('.studio');
    this.strip = new WorkingStrip({ pills: byId('shelf'), hint: byId('hint'), end: this.toolbar, stage: byId('stage'), studio });
    (studio ?? document.body).append(this.strip.root); // in the page before anything inside it is looked up by id
    const conversation = document.querySelector('.conversation-toolbar')!;
    conversation.prepend(byId('thread'));
    conversation.append(byId('chat-hide'));
    // The Seeing-key note heads the chat, never the view (Will, 2026-10-14); the stage's top corner keeps the player.
    conversation.after(byId('setup-note'));
    const top = node('div', 'stage-corner stage-top'); top.append(byId('feed-badge'));
    const bottom = node('div', 'stage-corner stage-bottom'); bottom.append(byId('layer-controls'));
    byId('stage').append(top, bottom);
    document.body.dataset.tray = 'collapsed'; // the dock is its row alone (the stylesheet's rules for an open tray never apply)
    // Keep native history semantics without letting an id fragment scroll a page out of its frame.
    document.addEventListener('click', (event) => {
      if (!(event instanceof MouseEvent) || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const a = (event.target as Element).closest<HTMLAnchorElement>('a[href^="#"]');
      if (!a || !STUDIO_ROUTE.test(a.getAttribute('href') || '')) return;
      event.preventDefault(); navigateStudio(a.getAttribute('href')!);
    });
    // Native fragments target the page panel IDs on direct entry. Keep route URLs,
    // but do not let that initial fragment scroll the phone header off screen.
    window.addEventListener('load', resetStudioRouteScroll, { once: true });
    window.addEventListener('hashchange', resetStudioRouteScroll);
    window.addEventListener('popstate', () => window.dispatchEvent(new Event('hashchange')));
  }
}

/** The studio's pages, as the address says them: the live workspace ("#" or none), a researcher's work, the library
 * (All assets), All sessions, test footage, Datasets, Capabilities, Models. */
export const STUDIO_ROUTE = /^#(?:research(?:\/.*)?|assets|sessions|footage|datasets|capabilities|(?:models|toolbox)(?:\/(?:[\w./-]|%[\dA-Fa-f]{2})+)?|measurements(?:\/[\w-]+)?)?$/;

export function resetStudioRouteScroll(): void {
  const hash = location.hash;
  // Research detail owns its reading position; match the detail routes accepted by main.route().
  if (/^#research\/[\w-]+(?:\?.*)?$/.test(hash)) return;
  // The Toolbox's two views are one area: a switch between them resets at once, never a frame later as a visible snap
  // (2026-10-15); the frame's reset below still undoes a native fragment scroll on direct entry.
  const top = { top: 0, left: 0, behavior: 'instant' } as const;
  if (/^#(?:models|toolbox|capabilities)\b/.test(hash)) window.scrollTo(top);
  requestAnimationFrame(() => {
    if (location.hash !== hash) return; // an earlier page must not reset the newly opened one
    window.scrollTo(top);
  });
}

export function navigateStudio(hash: string): void {
  if (location.hash === hash || (!location.hash && hash === '#')) return;
  history.pushState(null, '', hash === '#' ? location.pathname + location.search : hash);
  window.dispatchEvent(new Event('hashchange'));
}

/** Transport observations, not a claim about whether the desktop app is running. */
export function workspaceConnectionNotice(connected: boolean, observed: boolean, hasHealth: boolean) {
  if (connected && hasHealth) return null;
  if (connected) return {title:'Loading workspace status…',lines:['Connected. Waiting for the workspace status; your work is kept.']};
  if (!observed) return {title:'Connecting to the workspace…',lines:['Waiting for the local app. No setup action is needed yet.']};
  return {title:'The workspace connection was interrupted',lines:[
    'Reconnecting automatically. Keep this page open so your drafts stay available.',
    'If this continues, check that Hooke is open. After reconnecting, check Activity before retrying work that may still be running.',
  ]};
}

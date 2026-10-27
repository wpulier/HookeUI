/** Return the ancestors of a surface; revisiting an ancestor unwinds nested details. */
export function surfaceTrail(history: string[], current: string, target: string): string[] {
  if (current === target) return history;
  const ancestor = history.lastIndexOf(target);
  return ancestor < 0 ? [...history, current] : history.slice(0, ancestor);
}

export interface ActivitySurface {
  showing(): boolean;
  show(open: boolean): void;
}

/** Chat and reply links follow the surface that actually owns the Activity nodes. */
export class ActivityNavigation {
  private surface: ActivitySurface;
  constructor(surface: ActivitySurface) { this.surface = surface; }
  bind(surface: ActivitySurface): void { this.surface = surface; }
  showing(): boolean { return this.surface.showing(); }
  show(open: boolean): void { this.surface.show(open); }
  toggle(): void { this.show(!this.showing()); }
}


/** A mounted Study owns its stage before its first draw. Closing its Researchers drawer
 * synchronously routes to the workspace; that route must not invalidate the new owner.
 * Legacy researcher details still close when their address is left. */
export function bindResearchStageRoute(hasStudyPage: () => boolean, close: () => void): () => void {
  const changed = () => { if (!location.hash.startsWith('#research/') && !hasStudyPage()) close(); };
  window.addEventListener('hashchange', changed);
  return () => window.removeEventListener('hashchange', changed);
}

/** The Researchers drawer's own routes (`#researchers`, `#research`, either with a query). Closing the drawer leaves
 * only these; a researcher's route (`#research/<rid>…`) is the page being opened, never cancelled by the drawer
 * closing on its way. */
export const researchListRoute = (hash: string): boolean => /^#research(ers)?(\?.*)?$/.test(hash);

/** Open the Researchers drawer. Its list route is taken only while no researcher, Study or draft page is on the Stage:
 * the route would leave that page (bindResearchStageRoute), so there the drawer opens over it, as the strip's +N does. */
export function openResearchersDrawer(stageHidden: boolean, navigate: (hash: string) => void, show: () => void): void {
  if (stageHidden) navigate('#researchers');
  show();
}

/** Escape on a control whose popup is open (the composer's model chip with its models popover, a ⋯ with its menu) is
 * that popup's: it closes only the popup (panels.ts, drawer-menu.ts), never the panel the control sits in. */
export function escapeIsPopups(active: Element | null, popupOpen: () => boolean = () => false): boolean {
  return !!active && typeof active.getAttribute === 'function' && active.getAttribute('aria-expanded') === 'true' || popupOpen();
}

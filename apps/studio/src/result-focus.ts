/** Phone presentation preference only. Sources, open/close state and iframe nodes stay with their owners. */
export class ResultFocus {
  private readonly rows = new Map<string, { root: HTMLElement; title: () => string; legacy: boolean }>();
  private active: string | null = null;
  private requested: {id: string; matches: () => boolean; until: number} | null = null;
  private readonly width: () => number;
  private readonly observer: MutationObserver;
  constructor(width = () => window.innerWidth) {
    this.width = width;
    this.observer = new MutationObserver(() => this.sync());
  }
  register(id: string, root: HTMLElement, title: () => string, legacy = false): void {
    if (this.rows.get(id)?.root === root) return;
    this.rows.set(id, {root, title, legacy});
    this.observer.observe(root, {attributes:true, attributeFilter:['hidden']});
    this.sync();
  }
  /** A local Open waits for its exact retained selection; newer selections cancel it. */
  request(id: string, matches: () => boolean): void {
    this.requested = {id, matches, until: performance.now() + 30_000}; this.sync();
  }
  /** Only explicit user selection or an admitted presentation command calls this. */
  activate(id: string): void {
    const row = this.rows.get(id);
    if (!row || row.root.hidden) return;
    this.requested = null; this.active = id; this.sync();
  }
  sync(): void {
    let removed = false;
    for (const [id, row] of this.rows) if (!row.root.isConnected) { this.rows.delete(id); removed = true; }
    if (removed) {
      this.observer.disconnect();
      for (const row of this.rows.values()) this.observer.observe(row.root, {attributes:true, attributeFilter:['hidden']});
    }
    if (this.requested) {
      const wanted = this.requested;
      if (performance.now() > wanted.until) this.requested = null;
      else if (this.rows.get(wanted.id)?.root.hidden === false && wanted.matches()) { this.active = wanted.id; this.requested = null; }
    }
    const available = [...this.rows].filter(([, row]) => !row.root.hidden);
    if (!available.some(([id]) => id === this.active)) this.active = available.at(-1)?.[0] ?? null;
    for (const [id, row] of this.rows) {
      const tucked = row.root.dataset.studyArtifact !== 'true' && this.width() <= 960 && !row.root.hidden && id !== this.active;
      row.root.dataset.resultTucked = String(tucked);
    }
  }
  /** Additional recovery rows only: native views already have exact-address Made today rows. */
  retainedViews() {
    this.sync();
    return [...this.rows].filter(([, row]) => row.legacy && !row.root.hidden).map(([id, row]) => ({
      id, title: row.title(), visible: row.root.dataset.resultTucked !== 'true', resource:null,
    }));
  }
  restore(id: string): boolean {
    const row = this.rows.get(id);
    if (!row?.legacy || row.root.hidden) return false;
    this.activate(id); return true;
  }
}

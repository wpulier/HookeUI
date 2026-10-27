// The Assistant beside a researcher or a Study (workspace-feel step 7, researcher-spec.md section 2): on those Tabs the
// Assistant panel steps aside, and the Stage bar's Assistant opens it as a 360 px layer over the artifact
// (workspace-feel.css `data-assistant-over`). A layer of its own, never the person's panel preference, so a work Tab
// finds the panel as it was. Escape or × closes it; opening it puts the caret in its composer, closing it puts focus
// back on the Stage bar's Assistant.
export interface AssistantLayerOptions {
  /** The Assistant panel: Escape inside it closes the layer. */
  panel: HTMLElement;
  /** The Stage bar's Assistant button: focus returns here on close. */
  button: HTMLElement;
  /** The Assistant's composer field: focus goes here on open. */
  field: () => HTMLElement | null;
  /** A researcher's or a Study's Tab is in front (the layer exists only there). */
  active: () => boolean;
  /** Redraw the layout with the layer shown or hidden. */
  changed: () => void;
}

export class AssistantLayer {
  private shown = false;
  private readonly options: AssistantLayerOptions;
  constructor(options: AssistantLayerOptions) {
    this.options = options;
    options.panel.addEventListener('keydown', (event) => {
      const key = event as KeyboardEvent;
      if (key.key !== 'Escape' || !this.shown || !options.active() || key.defaultPrevented) return;
      key.preventDefault(); key.stopPropagation();
      this.set(false);
    });
  }
  get open(): boolean { return this.shown; }
  set(open: boolean): void {
    this.shown = open;
    this.options.changed();
    (open ? this.options.field() : this.options.button)?.focus?.();
  }
  toggle(): void { this.set(!this.shown); }
  /** Leaving the researcher's or Study's Tab: the layer goes, without moving focus. */
  reset(): void { this.shown = false; }
}

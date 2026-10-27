// The lit cell (workspace-feel plan, "Product design: the one bold element"): Hooke's cork cell, the brand mark's own
// hexagon, as the one status sign of all work: Tabs, the Working strip's pills, chat cards, researcher rows, Activity,
// readiness in Tools and Models, the rail. Gold while working (breathing, CSS only), green when ready, blue with a light
// centre when it needs the person; outlines for the quiet states. The look is workspace-feel.css's; the target is
// design/workspace-feel/ (the mock's cell()). It never shows an invented percent: setup fills only from a measured
// fraction, and without one it is the empty gold outline.

const SVG = 'http://www.w3.org/2000/svg';
/** The brand cell, in a 12 x 14 box. */
const POINTS = '6,1 11,4 11,10 6,13 1,10 1,4';
/** The settle's keyframes in workspace-feel.css. */
const SETTLE = 'mm-lit-settle';

export type LitCellState =
  | 'working'   // a researcher, a run or a setup step at work: gold, breathing
  | 'still'     // Stop requested and not yet confirmed: gold, not breathing
  | 'queued'    // waiting for a free slot
  | 'setup'     // setting up: gold outline, filled bottom-up only from a measured fraction
  | 'needs'     // needs the person: the action colour with a light centre
  | 'ready'     // ready, or findings ready: green
  | 'partial'   // partial findings: green outline (never a fill that reads as a percent)
  | 'idle'      // not set up (the step is on the button)
  | 'attention' // needs attention: red outline
  | 'stopped';  // stopped, or not on this Mac: dim outline

/** The state's word: the cell's accessible name unless the caller names it. */
export const LIT_CELL_WORDS: Readonly<Record<LitCellState, string>> = {
  working: 'Working',
  still: 'Stop requested',
  queued: 'Waiting for a free slot',
  setup: 'Setting up',
  needs: 'Needs you',
  ready: 'Ready',
  partial: 'Partial findings',
  idle: 'Not set up',
  attention: 'Needs attention',
  stopped: 'Stopped',
};

export type LitCellOptions = {
  /** Width in px (default 12); the height keeps the cell's 12:14 shape. */
  size?: number;
  /** For `setup` only: a measured fraction 0..1. Absent, null or not a number leaves the outline empty. */
  fraction?: number | null;
  /** The accessible name; the state's word when absent. */
  label?: string;
  /** A tooltip (an SVG <title>), for a state that needs a sentence, such as Stop requested. */
  title?: string;
  /** Play the 240 ms settle once (a cell that just lit or just turned ready). Reduced motion skips it. */
  settle?: boolean;
  /** The words beside it already say the state: hide the cell from assistive technology. */
  decorative?: boolean;
};

let clipSeq = 0;
const make = (tag: string): SVGElement =>
  (document.createElementNS?.(SVG, tag) ?? document.createElement(tag)) as unknown as SVGElement;

/** A lit cell for `state`. Update it in place with `setLitCell`, so a pill that changes every few seconds is never rebuilt. */
export function litCell(state: LitCellState, options: LitCellOptions = {}): SVGSVGElement {
  const svg = make('svg') as SVGSVGElement;
  svg.setAttribute('class', 'lit-cell');
  svg.setAttribute('data-kind', 'lit-cell');
  svg.setAttribute('viewBox', '0 0 12 14');
  const base = make('polygon');
  base.setAttribute('class', 'lit-cell-base');
  base.setAttribute('points', POINTS);
  const centre = make('circle');
  centre.setAttribute('class', 'lit-cell-centre');
  centre.setAttribute('cx', '6');
  centre.setAttribute('cy', '7');
  centre.setAttribute('r', '1.6');
  svg.append(base, centre);
  setLitCell(svg, state, options);
  return svg;
}

/** Change a lit cell's state (and its fraction, name, tooltip) in place. */
export function setLitCell(svg: SVGSVGElement, state: LitCellState, options: LitCellOptions = {}): void {
  const size = options.size ?? Number(svg.getAttribute('width') ?? 12);
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(Math.round(size * 7 / 6)));
  svg.setAttribute('data-state', state);
  if (options.decorative) {
    svg.setAttribute('aria-hidden', 'true');
    svg.removeAttribute('role');
    svg.removeAttribute('aria-label');
  } else {
    svg.removeAttribute('aria-hidden');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', options.label ?? LIT_CELL_WORDS[state]);
  }
  setTitle(svg, options.title);
  setFill(svg, state === 'setup' ? measured(options.fraction) : null);
  if (options.settle) settle(svg);
}

/** A real fraction, clamped to 0..1; anything else is no fraction. */
function measured(fraction: number | null | undefined): number | null {
  return typeof fraction === 'number' && Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : null;
}

function setTitle(svg: SVGSVGElement, text: string | undefined): void {
  let title = childOf(svg, 'lit-cell-title');
  if (!text) { title?.remove(); return; }
  if (!title) {
    title = make('title');
    title.setAttribute('class', 'lit-cell-title');
    svg.prepend(title);
  }
  title.textContent = text;
}

/** The setup fill: the cell clipped to its lower `fraction`, measured from the cell's own top (y 1) to bottom (y 13). */
function setFill(svg: SVGSVGElement, fraction: number | null): void {
  let clip = childOf(svg, 'lit-cell-clip'), fill = childOf(svg, 'lit-cell-fill');
  if (fraction === null) { clip?.remove(); fill?.remove(); return; }
  if (!clip || !fill) {
    clip?.remove(); fill?.remove();
    const id = `lit-cell-clip-${++clipSeq}`;
    clip = make('clipPath');
    clip.setAttribute('class', 'lit-cell-clip');
    clip.setAttribute('id', id);
    const rect = make('rect');
    rect.setAttribute('x', '0');
    rect.setAttribute('width', '12');
    rect.setAttribute('height', '14');
    clip.append(rect);
    fill = make('polygon');
    fill.setAttribute('class', 'lit-cell-fill');
    fill.setAttribute('points', POINTS);
    fill.setAttribute('clip-path', `url(#${id})`);
    childOf(svg, 'lit-cell-base')!.after(clip, fill);
  }
  clip.children[0]!.setAttribute('y', String(14 - 13 * fraction));
}

/** One settle: the class is taken off when it ends, so moving or redrawing the cell never replays it. Only the svg's own
 *  settle ends it: a working cell turning ready cancels the polygon's breathing, and that event bubbles up to the svg.
 *  A settle already playing is left to finish (restarting it would let its own cancel end the new one). */
function settle(svg: SVGSVGElement): void {
  if (svg.classList.contains('lit-cell-settle')) return;
  svg.classList.add('lit-cell-settle');
  const done = (event: Event) => {
    if (event.target !== svg || (event as AnimationEvent).animationName !== SETTLE) return;
    svg.classList.remove('lit-cell-settle');
    svg.removeEventListener('animationend', done);
    svg.removeEventListener('animationcancel', done);
  };
  svg.addEventListener('animationend', done);
  svg.addEventListener('animationcancel', done);
}

function childOf(svg: SVGSVGElement, name: string): SVGElement | null {
  for (const child of Array.from(svg.children) as SVGElement[]) if (child.classList.contains(name)) return child;
  return null;
}

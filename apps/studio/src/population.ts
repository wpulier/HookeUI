// The population layer (docs/design/2026-10-06-captures-operations-evidence.md, contracts 3 and 10):
// what a cell finder or a matcher found, kept as evidence and shown over the live view member by
// member, each only once the server has checked its own pixels where the slide's move puts it, in the
// current motion epoch. A member's colour comes from its id, the same in its rows, its close-up and its
// highlight. Pure: drawing is view.ts's.
import contract from '../../../contract/overlay.json';
import type { FrameHeader, Point } from './protocol';
import { frameToStill, stillToFrame, type Affine, type Space } from './spaces';
import type { ScreenItem } from './screen';

export interface PopulationMember {
  id: string;
  polygons: Point[][]; // in its capture's pixels
  colour: number; // an index into PALETTE (evidence.colour_of)
  placement: string;
  measure: Record<string, number | boolean | number[] | null> | null;
}

export interface PopulationWire {
  type: 'population';
  space: Space; // capture: members' polygons are in `capture`'s pixels
  id: string;
  title?: string; // its plain title ("Cell outlines · 18:04")
  operation: string;
  madeBy: Record<string, unknown>;
  capture: number;
  epoch: number | null;
  offset: [number, number];
  at: number;
  revision: number;
  session?: string;
  originalCount?: number | null;
  canUndo?: boolean;
  status: string;
  count: number;
  coverage: number | null;
  basis: { unit: string; umPerPx: number | null; scale: string | null; objective: unknown };
  members: PopulationMember[];
}

/** Where the bridge puts a still in its frames (orchestrator/still_fit.py): a pixel (x, y) of the still's original is at
 * (a x + b y + c, d x + e y + f) in frames `frame` px wide and high; `rect` is the picture's place inside the letterbox. */
export interface StillFit {
  frame: [number, number];
  rect: [number, number, number, number];
  matrix: Affine;
  orientation?: number;
}

export interface LayerWire {
  lastChange?: { by: 'user' | 'assistant'; at: number }; // confirmed Screen property ownership
  type: 'layer';
  space: Space; // frame: view, offset and correction are live-frame terms
  population: string | null;
  style: LayerStyle;
  verified: string[];
  why?: string; // why the others are not drawn now (the last check's reason)
  correction?: [number, number]; // camera px added to the slide's move: what the members' own pixels say
  view?: { epoch: number | null; offset: [number, number] } | null; // its view now (a rejoin may carry it to another epoch)
  /** A population found on the Stage's still: its fit, checked on frames of `view`'s epoch (S5a); else absent or null. */
  fit?: StillFit | null;
}

/** Installed structural projection of the current population layer; no native media authority. */
export function populationScreenItem(view: { readonly population: PopulationWire | null; readonly layer: LayerWire | null;
  hideOverlays: boolean; overlayPresentation(kind: 'population'): { id: string; title: string; open: boolean } | null }): ScreenItem | null {
  const pop = view.population;
  const layer = view.layer;
  const presentation = view.overlayPresentation('population');
  if (presentation && layer) {
    const { opacity, values: _values, legend: _legend, ...style } = layer.style;
    return { id: `layer:${presentation.id}`, kind: 'population', title: presentation.title, shows: presentation.id,
      open: presentation.open, visible: presentation.open && !view.hideOverlays,
      opacity, style, placement: presentation.open && pop ? `${layer.verified.length} of ${pop.count} verified in place` : 'hidden', z: 1 };
  }
  return null;
}

/** How the layer is drawn (the present verbs; screen.py): each member in its own colour ("id"), one
 * colour ("uniform", `value`), or by a measured column (`values`: 0-1 per member, with its legend); filled
 * or edges only; a selection emphasised with the rest faded or hidden. By a category ("category": the look
 * groups, K1): `values` is each member's category index, and the legend lists the categories. */
export interface LayerStyle {
  opacity: number;
  colour: string; // id | uniform | column | category
  value?: string;
  column?: string;
  legend?: { label: string; min?: number; max?: number; unit?: string; categories?: LegendCategory[] };
  values?: Record<string, number>;
  fill?: boolean;
  emphasis?: { members: string[]; rest: 'fade' | 'hide' };
}

export interface MemberSelected {
  type: 'member-selected';
  population: string | null;
  member: string | null;
  words?: string;
}

/** The populations' wires the studio keeps, by id (the newest `most`): the one shown may change before
 * or after its wire comes. */
export function keepPopulation(kept: Map<string, PopulationWire>, pop: PopulationWire, most = 4): Map<string, PopulationWire> {
  const next = new Map(kept);
  next.delete(pop.id);
  next.set(pop.id, pop);
  while (next.size > most) next.delete(next.keys().next().value as string);
  return next;
}

/** The members' colours (contract/overlay.json memberColours): the server reads the same table and names what is
 * drawn (orchestrator/colours.py), so a request can pick "the red ones" (going live, G3). */
export const PALETTE: string[] = contract.memberColours.palette;
const HEAT = contract.memberColours.heat as [number, number, number][];
const UNMEASURED = contract.memberColours.unmeasured;

export function colourOf(member: PopulationMember): string {
  return PALETTE[((member.colour % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

/** A colour along the measured range (0 low, 1 high): blue, through teal and yellow, to red. */
export function heat(t: number): string {
  const stops = HEAT;
  const x = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const [a, b] = [stops[i], stops[i + 1]];
  const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

// The emphasised members as a set, made once per list (memberLook runs for every member every frame).
let lastMembers: string[] | null = null;
let lastSet = new Set<string>();
function emphasised(members: string[]): Set<string> {
  if (members !== lastMembers) {
    lastMembers = members;
    lastSet = new Set(members);
  }
  return lastSet;
}

/** The colour menu's entry for a layer's style (the studio's layer controls): its legend's word for a measured
 * column, "look groups" or "your label" for a category, "yellow", or each in its own colour ("id"). An entry the
 * menu lacks is shown as none (main.ts), never as the last one picked (K1 re-check note 7). */
export function colourChoice(s: LayerStyle): string {
  if (s.colour === 'column') return s.legend?.label ?? '';
  if (s.colour === 'category') return s.column === 'group' ? 'look groups' : s.column === 'yourLabel' ? 'your label' : '';
  if (s.colour === 'uniform') return 'yellow';
  return 'id';
}

/** One category of a layer coloured by a category: a look group ("g2"), or a name (your label, K1), with its count. */
export interface LegendCategory {
  id: string;
  n: number;
  label?: string; // the words to show when they are not the group's ("platelets")
}

/** A category's line in the legend: "group 2 (14)", or its own words ("platelets (9)"). */
export function categoryWords(c: LegendCategory): string {
  return `${c.label ?? `group ${c.id.replace(/^g/, '')}`} (${c.n})`;
}

/** A category's colour (the look groups): the palette in order, so the legend and the cells agree. */
export function categoryColour(index: number): string {
  return PALETTE[((index % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

/** How one member is drawn under the layer's style: its colour, how strongly (an emphasised selection
 * keeps the rest faint or hides them), and whether it is filled. */
export function memberLook(member: PopulationMember, style: LayerStyle | undefined): { colour: string; alpha: number; hidden: boolean; fill: boolean } {
  const s = style ?? { opacity: 1, colour: 'id' };
  let colour = colourOf(member);
  if (s.colour === 'uniform' && s.value) colour = s.value;
  else if (s.colour === 'column') colour = s.values && member.id in s.values ? heat(s.values[member.id]) : UNMEASURED;
  else if (s.colour === 'category') colour = s.values && member.id in s.values ? categoryColour(s.values[member.id]) : UNMEASURED;
  const chosen = s.emphasis ? emphasised(s.emphasis.members).has(member.id) : true;
  return { colour, alpha: chosen ? 1 : 0.2, hidden: !chosen && s.emphasis?.rest === 'hide', fill: !!s.fill };
}

const NONE: { members: PopulationMember[]; shift: Point; matrix: Affine } = { members: [], shift: [0, 0], matrix: [1, 0, 0, 0, 1, 0] };

/** The members to draw on the live view now, with where their capture's pixels are in this frame (`matrix`: for the
 * live view the shift from their capture, `shift`): only the shown population's verified members, only in the epoch it
 * was made in, and only on a frame the motion tracker registered (never at an unverified place; going live, G2). One
 * found on the Stage's still is placed by the still's fit instead, the slide's offset playing no part (a still never
 * moves), and only on frames of the size and epoch that fit was checked on (S5a). Clicks and hover on members use it too. */
export function drawable(pop: PopulationWire | null, layer: LayerWire | null, h: FrameHeader):
  { members: PopulationMember[]; shift: Point; matrix: Affine } {
  if (!pop || !layer || layer.population !== pop.id || h.tracked === false) return NONE;
  const verified = new Set(layer.verified);
  const fit = layer.fit;
  if (fit) {
    if (!layer.view || layer.view.epoch !== h.epoch || fit.frame[0] !== h.w || fit.frame[1] !== h.h) return NONE;
    return { members: pop.members.filter((m) => verified.has(m.id)), shift: [fit.matrix[2], fit.matrix[5]], matrix: fit.matrix };
  }
  const epoch = layer.view ? layer.view.epoch : pop.epoch;
  const offset = layer.view ? layer.view.offset : pop.offset;
  if (!offset || epoch !== h.epoch) return NONE;
  const [cx, cy] = layer.correction ?? [0, 0];
  const shift: Point = [h.dx - offset[0] + cx, h.dy - offset[1] + cy];
  return { members: pop.members.filter((m) => verified.has(m.id)), shift, matrix: [1, 0, shift[0], 0, 1, shift[1]] };
}

function inside([x, y]: Point, poly: Point[]): boolean {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

/** The drawn member under `point` (camera px of frame `h`), or null: the point taken back to the members' own pixels. */
export function memberAt(pop: PopulationWire | null, layer: LayerWire | null, h: FrameHeader, point: Point): PopulationMember | null {
  const { members, matrix } = drawable(pop, layer, h);
  if (!members.length) return null;
  const local = frameToStill(point, matrix);
  return members.find((m) => m.polygons.some((poly) => inside(local, poly))) ?? null;
}

/** A member's point in frame px of the frame it is drawn on (`drawable`'s matrix). */
export const placed = (matrix: Affine, p: Point): Point => stillToFrame(p, matrix);

/** A member's measurements in a line, with what its units rest on (µm only by the capture's scale). */
export function memberWords(pop: PopulationWire, member: PopulationMember): string {
  const m = member.measure ?? {};
  const um = pop.basis.umPerPx;
  const parts: string[] = [];
  const area = typeof m.areaPx === 'number' ? m.areaPx : null;
  if (area !== null) parts.push(um ? `${(area * um * um).toFixed(1)} µm²` : `${Math.round(area)} px²`);
  const length = typeof m.feretMaxPx === 'number' ? m.feretMaxPx : null;
  if (length !== null) parts.push(um ? `${(length * um).toFixed(1)} µm long` : `${Math.round(length)} px long`);
  if (typeof m.elongation === 'number') parts.push(`elongation ${m.elongation.toFixed(2)}`);
  if (typeof m.circularity === 'number') parts.push(`roundness ${m.circularity.toFixed(2)}`);
  if (m.edgeCut) parts.push('cut by the edge');
  return `${member.id} of ${pop.id}: ${parts.join(', ')}${um ? ` (${pop.basis.scale} scale)` : ''}`;
}

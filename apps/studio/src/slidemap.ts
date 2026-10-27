// The slide map (orchestrator/slidemap.py): the still views seen so far, pasted where they are on
// the slide, with the view now (a circle, from the newest frame's offset) and the outlines (marks,
// from their anchor offsets) drawn over it. The picture is real camera pixels from the server; the
// circle and the marks are drawn here, so they follow the slide at once. It sits in the side column
// (a bottom sheet on phones) and is one panel of the stack, so Escape closes it. Open or closed is
// the server's state (the brain opens it on "show me the map"); the geometry below is pure, so it
// is unit tested (slidemap.test.ts).

import type { Panel } from './panels';
import type { ClientCommand, FrameHeader, SceneObject, SlideMapState } from './protocol';

export interface Circle {
  x: number;
  y: number;
  r: number;
}

export interface Mark {
  id: string;
  label: string;
  x: number;
  y: number;
}

/** Map picture px of frame px (x, y) in a frame whose slide offset is (dx, dy). */
export function onMap(state: SlideMapState, x: number, y: number, dx: number, dy: number): [number, number] | null {
  if (!state.origin) return null;
  return [(x - dx) * state.scale - state.origin[0], (y - dy) * state.scale - state.origin[1]];
}

/** Where the view is on the map now: the eyepiece disc of the newest frame, when it is in the
 * map's epoch (otherwise its place against the map is not known). */
export function viewOnMap(state: SlideMapState, header: FrameHeader | null): Circle | null {
  if (!header?.fov || header.epoch !== state.epoch) return null;
  const at = onMap(state, header.fov[0], header.fov[1], header.dx, header.dy);
  return at ? { x: at[0], y: at[1], r: header.fov[2] * state.scale } : null;
}

/** The outlines made in the map's epoch, at their centres (blind test arms and cells left out). */
export function marksOnMap(state: SlideMapState, objects: SceneObject[]): Mark[] {
  const marks: Mark[] = [];
  for (const o of objects) {
    if (o.trial || o.anchorEpoch !== state.epoch || (o.kind !== 'highlight' && o.kind !== 'part')) continue;
    const points = o.polygons.flat();
    if (!points.length) continue;
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    const at = onMap(state, (Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2,
      o.anchorOffset[0], o.anchorOffset[1]);
    if (at) marks.push({ id: o.id, label: o.label, x: at[0], y: at[1] });
  }
  return marks;
}

/** The line under the title. */
export function mapNote(state: SlideMapState): string {
  if (!state.url) return 'Empty so far: it fills in with every still view as you move the slide and hold it.';
  const earlier = state.maps > 1
    ? ` ${state.maps - 1} earlier map${state.maps > 2 ? 's' : ''} from before the view lost track ${state.maps > 2 ? 'are' : 'is'} kept.`
    : '';
  return `Still views seen so far, where they are on the slide. The circle is the view now.${earlier}`;
}

const SVG = 'http://www.w3.org/2000/svg';

export class SlideMapPanel implements Panel {
  readonly id = 'slidemap';
  private readonly root = document.getElementById('slidemap')!;
  private readonly image = document.getElementById('slidemap-image') as HTMLImageElement;
  private readonly marks = document.getElementById('slidemap-marks') as unknown as SVGSVGElement;
  private readonly note = document.getElementById('slidemap-note')!;
  private state: SlideMapState | null = null;
  private shown: SlideMapState | null = null; // the state whose picture is loaded
  private objects: SceneObject[] = [];
  private drawnFor = '';
  onToggle: (open: boolean) => void = () => {};
  private readonly send: (command: ClientCommand) => void;
  private readonly header: () => FrameHeader | null;

  /** `header`: the newest frame's (the view's circle follows it). */
  constructor(send: (command: ClientCommand) => void, header: () => FrameHeader | null) {
    this.send = send;
    this.header = header;
    document.getElementById('slidemap-close')!.addEventListener('click', () => this.close());
  }

  isOpen(): boolean {
    return !this.root.hidden;
  }

  close(): void {
    this.root.hidden = true; // at once; the server's echo confirms it
    this.onToggle(false);
    this.send({ type: 'slidemap', open: false });
  }

  toggle(): void {
    if (this.isOpen()) this.close();
    else this.send({ type: 'slidemap', open: true });
  }

  footprint(): DOMRect | null {
    return this.isOpen() ? this.root.getBoundingClientRect() : null;
  }

  apply(state: SlideMapState): void {
    this.state = state;
    this.root.hidden = !state.open;
    this.onToggle(state.open);
    this.note.textContent = mapNote(state);
    if (!state.url || !state.size) {
      this.shown = null; // nothing on the map of this epoch yet
      this.image.hidden = true;
      this.marks.replaceChildren();
      this.drawnFor = '';
      return;
    }
    if (state.url === this.shown?.url) return;
    // The new picture replaces the old one once loaded, with its geometry, so the marks never
    // sit on a picture they were not worked out for.
    const next = new Image();
    next.onload = () => {
      if (this.state?.url !== state.url) return;
      this.shown = state;
      this.image.src = state.url!;
      this.image.hidden = false;
      this.marks.setAttribute('viewBox', `0 0 ${state.size![0]} ${state.size![1]}`);
      this.drawnFor = '';
    };
    next.src = state.url;
  }

  setObjects(objects: SceneObject[]): void {
    this.objects = objects;
    this.drawnFor = '';
  }

  /** Each animation frame: the view's circle follows the slide. */
  frame(): void {
    const state = this.shown;
    if (!this.isOpen() || !state) return;
    const header = this.header();
    const key = `${state.url}|${header?.id}`;
    if (key === this.drawnFor) return;
    this.drawnFor = key;
    const size = Math.max(...(state.size ?? [100, 100]));
    const children: SVGElement[] = [];
    for (const mark of marksOnMap(state, this.objects)) {
      const dot = document.createElementNS(SVG, 'circle');
      dot.setAttribute('cx', String(mark.x));
      dot.setAttribute('cy', String(mark.y));
      dot.setAttribute('r', String(size / 90));
      dot.setAttribute('class', 'slidemap-mark');
      children.push(dot);
      if (mark.label) {
        const text = document.createElementNS(SVG, 'text');
        text.setAttribute('x', String(mark.x + size / 60));
        text.setAttribute('y', String(mark.y + size / 120));
        text.setAttribute('font-size', String(size / 32));
        text.setAttribute('class', 'slidemap-label');
        text.textContent = mark.label;
        children.push(text);
      }
    }
    const view = viewOnMap(state, header);
    if (view) {
      const ring = document.createElementNS(SVG, 'circle');
      ring.setAttribute('cx', String(view.x));
      ring.setAttribute('cy', String(view.y));
      ring.setAttribute('r', String(view.r));
      ring.setAttribute('class', 'slidemap-view');
      children.push(ring);
    }
    this.marks.replaceChildren(...children);
  }
}

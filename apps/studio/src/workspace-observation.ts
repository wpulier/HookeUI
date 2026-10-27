/** Pure request-time interaction facts. The caller supplies registered physical hits.
 * No DOM reads, editor values, capture calls, semantic targeting or specimen inference.
 * Coordinates are CSS pixels; the observer adapter owns event delivery and registry truth.
 */
const MAX_CANDIDATES = 8;
const MAX_CONTACTS = 2;
const MAX_OMITTED_IDS = 8;

type Modality = 'mouse' | 'touch' | 'pen';
type PointerStatus = 'none' | 'present' | 'outside' | 'released' | 'cancelled';

export interface HitFacts {
  readonly presentationId: string | null;
  readonly sourceKey: string | null;
  readonly sourceGeneration?: number;
  readonly sourcePoint?: readonly number[] | null;
  readonly hitPath: readonly string[];
  readonly targetCandidates: readonly string[];
}

export interface FocusFacts {
  readonly presentationId: string | null;
  readonly controlId: string | null;
  readonly role: string;
  readonly editable: boolean;
}

interface PointerFacts extends HitFacts {
  readonly pointerId: number | null;
  readonly modality: Modality | 'none';
  readonly state: PointerStatus;
  readonly css: readonly number[] | null;
  readonly buttons: number;
  readonly capturedBy: string | null;
  readonly eventMonoMs: number | null;
}

interface ContentPointer extends PointerFacts {
  readonly historical: true;
}

interface ContentFocus extends FocusFacts {
  readonly eventMonoMs: number;
}

export interface InteractionState {
  readonly pointer: PointerFacts;
  readonly contacts: readonly PointerFacts[];
  readonly focus: FocusFacts;
  readonly lastContentPointer: ContentPointer | null;
  readonly lastContentFocus: ContentFocus | null;
  readonly visible: boolean;
  readonly lastEventAt: number;
  // Only omitted identities are retained, with a fixed bound; no extra hit/geometry records.
  readonly omittedIds: readonly number[];
  readonly overflowUnknown: boolean;
}

type PointerEvent = {
  readonly type: 'pointer-move' | 'pointer-down';
  readonly pointerId: number;
  readonly modality: Modality;
  readonly primary: boolean;
  readonly css: readonly number[];
  readonly buttons: number;
  readonly atMs: number;
  readonly capturedBy?: string | null;
  readonly hit: HitFacts;
};

export type InteractionEvent = PointerEvent | {
  readonly type: 'pointer-up' | 'pointer-leave' | 'pointer-cancel';
  readonly pointerId: number;
  readonly atMs: number;
} | {
  readonly type: 'focus';
  readonly focus: FocusFacts;
  readonly atMs: number;
} | {
  readonly type: 'visibility';
  readonly visible: boolean;
  readonly atMs: number;
};

export interface SourceView {
  readonly contentRect: readonly number[];
  readonly clipRect: readonly number[];
  readonly viewTransform: {
    readonly sourceToWorkspace: readonly number[];
    readonly originalSize: readonly number[];
    readonly sourceToContent?: readonly number[];
    readonly contentOrigin?: readonly number[];
  };
}

function number(value: number, nonnegative = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || (nonnegative && value < 0)) {
    throw new Error('INVALID_GEOMETRY: expected a finite CSS number');
  }
  return value;
}

function integer(value: number): number {
  number(value, true);
  if (!Number.isSafeInteger(value)) throw new Error('INVALID_REPORT: expected a safe integer');
  return value;
}

function identity(value: string): string {
  if (typeof value !== 'string' || !value || value.length > 256) {
    throw new Error('INVALID_REPORT: expected a bounded registered identity');
  }
  return value;
}

function optionalIdentity(value: string | null | undefined): string | null {
  return value == null ? null : identity(value);
}

function boolean(value: boolean): boolean {
  if (typeof value !== 'boolean') throw new Error('INVALID_REPORT: expected a boolean');
  return value;
}

function vector(value: readonly number[], length: number): readonly number[] {
  if (!Array.isArray(value) || value.length !== length) {
    throw new Error('INVALID_GEOMETRY: unexpected vector length');
  }
  return Object.freeze(value.map((v) => number(v)));
}

function ids(value: readonly string[]): readonly string[] {
  if (!Array.isArray(value) || value.length > MAX_CANDIDATES) {
    throw new Error('TARGET_NOT_REPRESENTABLE: registered candidate bound exceeded');
  }
  return Object.freeze(value.map(identity));
}

function focusFacts(value: FocusFacts): FocusFacts {
  return Object.freeze({
    presentationId: optionalIdentity(value.presentationId),
    controlId: optionalIdentity(value.controlId), role: identity(value.role),
    editable: boolean(value.editable),
  });
}

function emptyPointer(state: PointerStatus = 'none'): PointerFacts {
  return Object.freeze({ pointerId: null, modality: 'none', state, css: null,
    buttons: 0, capturedBy: null, eventMonoMs: null, presentationId: null,
    sourceKey: null, sourcePoint: null, hitPath: Object.freeze([]), targetCandidates: Object.freeze([]) });
}

function clearPointer(pointer: PointerFacts, state: PointerStatus, atMs: number): PointerFacts {
  return Object.freeze({ ...pointer, state, css: null, sourcePoint: null, buttons: 0,
    presentationId: null, sourceKey: null, sourceGeneration: undefined,
    hitPath: Object.freeze([]), targetCandidates: Object.freeze([]), capturedBy: null, eventMonoMs: atMs });
}

function pointerFacts(event: PointerEvent): PointerFacts {
  if (!['mouse', 'touch', 'pen'].includes(event.modality)) throw new Error('INVALID_REPORT: unknown modality');
  boolean(event.primary);
  const sourceKey = optionalIdentity(event.hit.sourceKey);
  const sourceGeneration = event.hit.sourceGeneration == null ? undefined : integer(event.hit.sourceGeneration);
  return Object.freeze({
    pointerId: integer(event.pointerId), modality: event.modality, state: 'present',
    css: vector(event.css, 2), buttons: integer(event.buttons),
    capturedBy: optionalIdentity(event.capturedBy), eventMonoMs: number(event.atMs, true),
    presentationId: optionalIdentity(event.hit.presentationId), sourceKey, sourceGeneration,
    sourcePoint: sourceKey && event.hit.sourcePoint != null ? vector(event.hit.sourcePoint, 2) : null,
    hitPath: ids(event.hit.hitPath), targetCandidates: ids(event.hit.targetCandidates),
  });
}

export function createInteractionState(): InteractionState {
  return Object.freeze({ pointer: emptyPointer(), contacts: Object.freeze([]),
    focus: focusFacts({ presentationId: null, controlId: null, role: 'none', editable: false }),
    lastContentPointer: null, lastContentFocus: null, visible: true, lastEventAt: 0,
    omittedIds: Object.freeze([]), overflowUnknown: false });
}

/** Every transition returns a new frozen state. Primary never becomes latest-touch by accident. */
export function reduceInteraction(state: InteractionState, event: InteractionEvent): InteractionState {
  const atMs = number(event.atMs, true);
  if (atMs < state.lastEventAt) throw new Error('REPORT_REPLAYED: interaction event moved backwards');
  let pointer = state.pointer;
  let contacts = [...state.contacts];
  let focus = state.focus;
  let lastContentPointer = state.lastContentPointer;
  let lastContentFocus = state.lastContentFocus;
  let visible = state.visible;
  let omittedIds = [...state.omittedIds];
  let overflowUnknown = state.overflowUnknown;

  switch (event.type) {
    case 'pointer-move':
    case 'pointer-down': {
      const facts = pointerFacts(event);
      if (!visible) break; // hidden pages cannot restore current interaction validity
      const index = contacts.findIndex((c) => c.pointerId === event.pointerId);
      // Hovering pens/mice are not active contacts. Moves cannot invent a touch-down.
      const active = event.type === 'pointer-down' || index >= 0 || omittedIds.includes(event.pointerId);
      if (event.modality === 'touch' && !active) break;
      if (event.primary) pointer = facts;
      if (active) {
        if (index >= 0) contacts[index] = facts;
        else if (contacts.length < MAX_CONTACTS) {
          contacts.push(facts);
          omittedIds = omittedIds.filter((id) => id !== event.pointerId);
        } else if (event.primary) {
          // Keep the primary among the bounded contact facts; remember the displaced ID only.
          const displaced = contacts.pop();
          contacts.unshift(facts);
          omittedIds = omittedIds.filter((id) => id !== event.pointerId);
          if (displaced?.pointerId != null && !omittedIds.includes(displaced.pointerId)) {
            if (omittedIds.length < MAX_OMITTED_IDS) omittedIds.push(displaced.pointerId);
            else overflowUnknown = true;
          }
        } else if (!omittedIds.includes(event.pointerId)) {
          if (omittedIds.length < MAX_OMITTED_IDS) omittedIds.push(event.pointerId);
          else overflowUnknown = true;
        }
      }
      // Secondary touches do not replace the primary's most recent content point.
      if (event.primary && facts.presentationId != null && facts.sourceKey != null) {
        lastContentPointer = Object.freeze({ ...facts, historical: true });
      }
      break;
    }
    case 'pointer-up':
    case 'pointer-cancel':
    case 'pointer-leave': {
      integer(event.pointerId);
      const contact = contacts.find((c) => c.pointerId === event.pointerId);
      if (event.type !== 'pointer-leave') {
        contacts = contacts.filter((c) => c.pointerId !== event.pointerId);
        omittedIds = omittedIds.filter((id) => id !== event.pointerId);
      } else {
        contacts = contacts.map((c) => c.pointerId === event.pointerId ? clearPointer(c, 'outside', atMs) : c);
      }
      if (pointer.pointerId === event.pointerId) {
        if (event.type === 'pointer-up' && pointer.modality !== 'touch' && contact?.modality !== 'touch') {
          pointer = Object.freeze({ ...pointer, buttons: 0, capturedBy: null, eventMonoMs: atMs });
        } else {
          const status = event.type === 'pointer-leave' ? 'outside' : event.type === 'pointer-cancel' ? 'cancelled' : 'released';
          pointer = clearPointer(pointer, status, atMs);
        }
      }
      break;
    }
    case 'focus': {
      const facts = focusFacts(event.focus); // explicit whitelist excludes editor text/value
      if (!visible) break;
      focus = facts;
      if (facts.presentationId != null && !facts.editable) {
        lastContentFocus = Object.freeze({ ...facts, eventMonoMs: atMs });
      }
      break;
    }
    case 'visibility':
      visible = boolean(event.visible);
      if (!visible) {
        pointer = clearPointer(pointer, 'outside', atMs);
        contacts = [];
        omittedIds = [];
        overflowUnknown = false;
        focus = focusFacts({ presentationId: null, controlId: null, role: 'none', editable: false });
      }
      break;
    default:
      throw new Error('INVALID_REPORT: unknown interaction event');
  }
  return Object.freeze({ pointer, contacts: Object.freeze(contacts), focus,
    lastContentPointer, lastContentFocus, visible, lastEventAt: atMs,
    omittedIds: Object.freeze(omittedIds), overflowUnknown });
}

function sampledPointer(pointer: PointerFacts, nowMs: number) {
  return Object.freeze({ ...pointer, sampledMonoMs: nowMs,
    eventAgeMs: pointer.eventMonoMs == null ? null : nowMs - pointer.eventMonoMs });
}

export function snapshotInteraction(state: InteractionState, options: { nowMs: number; selected: readonly string[] }) {
  const nowMs = number(options.nowMs, true);
  if (nowMs < state.lastEventAt) throw new Error('INVALID_REPORT: snapshot precedes last event');
  const omittedContacts = state.omittedIds.length + (state.overflowUnknown ? 1 : 0);
  const availability = !state.visible ? 'unavailable' : omittedContacts ? 'partial' : 'observed';
  return Object.freeze({
    pointer: sampledPointer(state.pointer, nowMs),
    contacts: Object.freeze(state.contacts.map((c) => sampledPointer(c, nowMs))),
    focus: state.focus,
    lastContentPointer: state.lastContentPointer == null ? null : Object.freeze({
      ...state.lastContentPointer, ageMs: nowMs - (state.lastContentPointer.eventMonoMs ?? nowMs), historical: true,
    }),
    lastContentFocus: state.lastContentFocus == null ? null : Object.freeze({
      ...state.lastContentFocus, ageMs: nowMs - state.lastContentFocus.eventMonoMs, historical: true,
    }),
    selected: ids(options.selected), omittedContacts, availability,
    unknown: Object.freeze(state.overflowUnknown ? ['contact-count-lower-bound'] : []),
  });
}

/** Inverse affine hit, within content, clip and source bounds; CSS coordinates ignore DPR. */
export function workspaceToSource(css: readonly number[], view: SourceView): readonly number[] | null {
  const [x, y] = vector(css, 2);
  for (const rect of [view.contentRect, view.clipRect]) {
    const [left, top, width, height] = vector(rect, 4);
    if (width < 0 || height < 0) throw new Error('INVALID_GEOMETRY: negative rectangle dimensions');
    if (!(left <= x && x < left + width && top <= y && y < top + height)) return null;
  }
  const transform = view.viewTransform;
  const [a, b, c, d, tx, ty] = vector(transform.sourceToWorkspace, 6);
  const [width, height] = vector(transform.originalSize, 2);
  if (width <= 0 || height <= 0) throw new Error('INVALID_GEOMETRY: nonpositive source dimensions');
  if (transform.sourceToContent != null || transform.contentOrigin != null) {
    if (transform.sourceToContent == null || transform.contentOrigin == null) {
      throw new Error('TRANSFORM_MISMATCH: partial content mapping');
    }
    const content = [...vector(transform.sourceToContent, 6)];
    const [ox, oy] = vector(transform.contentOrigin, 2);
    content[4] += ox;
    content[5] += oy;
    if (content.some((v, i) => Math.abs(v - transform.sourceToWorkspace[i]) > 1e-6)) {
      throw new Error('TRANSFORM_MISMATCH: content and workspace affine maps disagree');
    }
  }
  const determinant = a * d - b * c;
  if (!Number.isFinite(determinant) || Math.abs(determinant) <= 1e-12) return null;
  const sx = (d * (x - tx) - c * (y - ty)) / determinant;
  const sy = (-b * (x - tx) + a * (y - ty)) / determinant;
  if (!(Number.isFinite(sx) && Number.isFinite(sy) && 0 <= sx && sx < width && 0 <= sy && sy < height)) return null;
  return Object.freeze([sx, sy]);
}

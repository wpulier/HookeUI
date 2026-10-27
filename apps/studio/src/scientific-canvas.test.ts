import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import { ScientificCanvas, type ScientificPaint } from './scientific-canvas';
import { TestElement } from './workspace-windows.test-support';
import { bindScientificPresentation, scientificPresentation, scientificPaint } from './scientific-presentation';
import { ScreenReporter, type StudioSurfaceSnapshot } from './screen';

class Canvas extends TestElement {
  width = 0; height = 0; isConnected = true;
  data: Uint8ClampedArray = new Uint8ClampedArray();
  context = { createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    putImageData: (image: { data: Uint8ClampedArray }) => { this.data = image.data; } };
  constructor() { super('canvas'); this.style.width = '320px'; this.style.height = '240px'; }
  getContext() { return this.context; }
}

let canvas: Canvas, drawing: ScientificCanvas, doc: EventTarget & { visibilityState: string }, win: EventTarget;
let scheduled: Map<number, FrameRequestCallback>, next: number;
let drawn = vi.fn<(paint: ScientificPaint) => void>(), revoked = vi.fn<(draw: string) => void>();
let bitmapRecords: object[];
beforeEach(() => {
  canvas = new Canvas(); scheduled = new Map(); next = 0;
  bitmapRecords = [];
  doc = Object.assign(new EventTarget(), { visibilityState: 'visible' }); win = new EventTarget();
  vi.stubGlobal('document', doc); vi.stubGlobal('window', win);
  vi.stubGlobal('getComputedStyle', (element: Canvas) => ({ opacity: '1', transform: 'none', ...element.style }));
  vi.stubGlobal('MutationObserver', class {
    observe() { }
    disconnect() { bitmapRecords = []; }
    takeRecords() { const records = bitmapRecords; bitmapRecords = []; return records; }
  });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { const id = ++next; scheduled.set(id, callback); return id; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => scheduled.delete(id));
  drawn = vi.fn(); revoked = vi.fn();
  drawing = new ScientificCanvas(canvas as unknown as HTMLCanvasElement, { drawn, revoked });
});
afterEach(() => { drawing.close(); vi.unstubAllGlobals(); });
const pixels = () => new Uint8Array(32 * 24 * 4).fill(127);
function paint() { const callbacks = [...scheduled.values()]; scheduled.clear(); callbacks.forEach(callback => callback(0)); }

test('scientific document association cannot be copied through DOM attributes or stale cleanup', () => {
  const root = new TestElement('section'); root.append(canvas);
  const element = root as unknown as HTMLElement;
  const dispose = bindScientificPresentation(element, { review: 'review-one', drawing });
  expect(scientificPresentation(element)).toEqual({ paint: null });
  drawing.show(cases.valid, pixels()); paint();
  expect(scientificPresentation(element)?.paint).toMatchObject({ review: 'review-one', width: 32, height: 24 });
  const copied = new TestElement('section'); Object.assign(copied.dataset, root.dataset, { scientificReview: 'review-one' });
  expect(scientificPresentation(copied as unknown as HTMLElement)).toBeNull();
  const replacement = bindScientificPresentation(element, { review: 'review-two', drawing });
  dispose(); expect(scientificPresentation(element)).toEqual({ paint: null });
  drawing.show(cases.valid, pixels()); paint();
  expect(scientificPresentation(element)?.paint?.review).toBe('review-two');
  drawing.invalidate(); expect(scientificPresentation(element)).toEqual({ paint: null });
  replacement(); expect(scientificPresentation(element)).toBeNull();
});

test('a document cannot attribute another canvas drawing to its own blank canvas', () => {
  const blank = new Canvas(), otherRoot = new TestElement('section'); otherRoot.append(blank);
  drawing.show(cases.valid, pixels()); paint();
  expect(() => bindScientificPresentation(otherRoot as unknown as HTMLElement,
    { review: 'wrong-canvas', drawing })).toThrow();
  expect(scientificPresentation(otherRoot as unknown as HTMLElement)).toBeNull();
});

test('canonical screen observations advance on paint and revoke it on hide without changing the saved source', () => {
  const root = new TestElement('section'); root.id = 'scientific-root'; root.append(canvas);
  Object.assign(doc, { body: { contains: (node: unknown) => node === root } });
  const element = root as unknown as HTMLElement;
  bindScientificPresentation(element, { review: 'review-one', drawing });
  const snapshot: StudioSurfaceSnapshot = {
    kind: 'document', open: true, visible: true, rect: [0, 0, 400, 400], z: 1, space: 'workspace',
    legacy: { schema: 'studio-surface-v1', scope: 'dom-observation', document: {
      project: '2026-10-16', tab: 'main', placement: 'one',
      resource: { day: '2026-10-16', id: 'p' + 'a'.repeat(32), version: 3, picture: null },
      representation: 'scientific-review', state: 'private-preview', focused: true,
    } }, contentKey: 'same-saved-document', controls: [],
  };
  const reporter = new ScreenReporter(() => {}, () => []);
  const collect = () => reporter.observeLegacy({ surfaces: [{ id: 'document:one', root: element,
    read: () => ({ ...snapshot, scientificPaint: scientificPresentation(element)!.paint }) }],
    coverage: { complete: false, omittedItems: 0, unknown: [] } }, 'workspace-a', { presentations: [], biological: [] }).items[0];
  const pending = collect(); expect(pending.scientificPaint).toBeNull();
  drawing.show(cases.valid, pixels()); expect(collect().revision).toBe(pending.revision); paint();
  const drawn = collect(); expect(drawn.scientificPaint?.frame).toBe(cases.valid.frame);
  expect(drawn.revision).toBe(pending.revision + 1); expect(drawn.sourceGeneration).toBe(pending.sourceGeneration);
  expect(collect().revision).toBe(drawn.revision);
  snapshot.visible = false; const hidden = collect(); expect(hidden.scientificPaint).toBeNull();
  expect(hidden.revision).toBe(drawn.revision + 1); expect(hidden.load.state).toBe('unknown');
  expect(hidden).not.toHaveProperty('sourceToken');
});

test.each(['scientificPaint','scientificPanelPaint'] as const)('declaring or removing %s advances structural revision before any pixels exist', field => {
  const root = new TestElement('section'); root.id = 'declared-scientific-root'; root.append(canvas);
  Object.assign(doc, { body: { contains: (node: unknown) => node === root } });
  const snapshot: StudioSurfaceSnapshot = {
    kind:'document',open:true,visible:true,rect:[0,0,400,400],z:1,space:'workspace',
    legacy:{schema:'studio-surface-v1',scope:'dom-observation',document:{
      project:'2026-10-16',tab:'main',placement:'one',
      resource:{day:'2026-10-16',id:'p'+'a'.repeat(32),version:3,picture:null},
      representation:'scientific-review',state:'private-preview',focused:true,
    }},contentKey:'same-saved-document',controls:[],
  };
  const reporter=new ScreenReporter(()=>{},()=>[]);
  const collect=()=>reporter.observeLegacy({surfaces:[{id:'document:one',root:root as unknown as HTMLElement,read:()=>snapshot}],
    coverage:{complete:false,omittedItems:0,unknown:[]}},'workspace-a',{presentations:[],biological:[]}).items[0];
  const opening=collect();expect(opening).not.toHaveProperty(field);
  snapshot[field]=null;
  const declared=collect();expect(declared[field]).toBeNull();
  expect(declared.revision).toBe(opening.revision+1);
  expect(declared.sourceGeneration).toBe(opening.sourceGeneration);
  delete snapshot[field];
  const removed=collect();expect(removed).not.toHaveProperty(field);
  expect(removed.revision).toBe(declared.revision+1);
});

test.each([{ width: 0 }, { height: 1025 }, { width: true }, { frame: 'unknown' },
  { review: '' }, { draw: 'x'.repeat(257) }, { contentRect: [0, 0, 0, 2] },
  { contentRect: [NaN, 0, 2, 2] }, { sourceToken: 'not-a-grant' }])('malformed scientific paint is refused: %j', change => {
  expect(() => scientificPaint({ review: 'review-one', frame: 'a'.repeat(64), draw: 'draw-one',
    width: 32, height: 24, contentRect: [20, 40, 160, 120], ...change })).toThrow();
});

test('pixels are copied, no paint is claimed before an animation frame, and coordinates use actual display scaling', () => {
  const bytes = pixels(); drawing.show(cases.valid, bytes); bytes.fill(0);
  expect(drawing.awaiting(cases.valid.frame)).toBe(true);
  expect(drawing.awaiting('f'.repeat(64))).toBe(false);
  expect(canvas.data[0]).toBe(127); expect(drawing.current()).toBeNull(); expect(drawn).not.toHaveBeenCalled();
  paint(); const receipt = drawing.current()!;
  expect(drawing.awaiting(cases.valid.frame)).toBe(false);
  expect(drawn).toHaveBeenCalledTimes(1);
  expect(drawing.position(receipt.draw, 160, 120)).toEqual([12, 16]);
  expect(() => drawing.position(receipt.draw, 320, 120)).toThrow();
  receipt.frame.dimensions.point[0] = 99;
  expect(drawing.current()!.frame.dimensions.point[0]).toBe(1);
});

test('new frames, invalid data and closed views revoke old paint instead of accepting delayed callbacks', () => {
  drawing.show(cases.valid, pixels()); const delayed = [...scheduled.values()][0];
  drawing.show({ ...cases.valid, frame: 'b'.repeat(64) }, pixels()); delayed(0);
  expect(drawing.awaiting('b'.repeat(64))).toBe(true);
  expect(drawn).not.toHaveBeenCalled(); paint();
  const receipt = drawing.current()!;
  expect(receipt.frame.frame).toBe('b'.repeat(64));
  expect(() => drawing.show(cases.valid, new Uint8Array(1))).toThrow();
  expect(revoked).toHaveBeenCalledWith(receipt.draw); expect(drawing.current()).toBeNull();
  drawing.show(cases.valid, pixels()); const afterClose = [...scheduled.values()][0]; drawing.close(); afterClose(0);
  expect(drawing.current()).toBeNull(); expect(canvas.width).toBe(0); expect(drawn).toHaveBeenCalledTimes(1);
});

test.each(['hidden', 'blur', 'detached', 'transparent', 'transformed', 'rotated', 'mirrored', 'padded', 'resized', 'letterboxed', 'cleared', 'contextlost'])('%s view cannot supply current interaction coordinates', reason => {
  drawing.show(cases.valid, pixels()); paint(); const receipt = drawing.current()!;
  if (reason === 'hidden') { doc.visibilityState = 'hidden'; doc.dispatchEvent(new Event('visibilitychange')); }
  if (reason === 'blur') win.dispatchEvent(new Event('blur'));
  if (reason === 'detached') canvas.isConnected = false;
  if (reason === 'transparent') canvas.style.opacity = '0';
  if (reason === 'transformed') canvas.style.transform = 'rotate(90deg)';
  if (reason === 'rotated') canvas.style.rotate = '90deg';
  if (reason === 'mirrored') canvas.style.scale = '-1 1';
  if (reason === 'padded') canvas.style.paddingRight = '4px';
  if (reason === 'resized') canvas.width = 64;
  if (reason === 'letterboxed') { canvas.style.objectFit = 'contain'; canvas.style.height = '320px'; }
  if (reason === 'cleared') { canvas.width = canvas.width; bitmapRecords.push({ attributeName: 'width' }); }
  if (reason === 'contextlost') canvas.dispatchEvent(new Event('contextlost'));
  expect(() => drawing.position(receipt.draw, 160, 120)).toThrow();
  expect(revoked).toHaveBeenCalledWith(receipt.draw);
});

test('context restoration requires fresh pixels and a new paint observation', () => {
  drawing.show(cases.valid, pixels()); paint();
  canvas.dispatchEvent(new Event('contextlost'));
  expect(() => drawing.show(cases.valid, pixels())).toThrow('drawing context');
  canvas.dispatchEvent(new Event('contextrestored'));
  expect(drawing.current()).toBeNull();
  drawing.show(cases.valid, pixels()); paint(); expect(drawing.current()).not.toBeNull();
});

test('a hidden pending canvas or failed observer never leaves usable paint evidence', () => {
  drawing.show(cases.valid, pixels()); canvas.hidden = true; paint();
  expect(drawn).not.toHaveBeenCalled(); expect(drawing.current()).toBeNull();
  canvas.hidden = false; drawn.mockImplementation(() => { throw new Error('owner closed'); });
  drawing.show(cases.valid, pixels()); expect(paint).toThrow('owner closed'); expect(drawing.current()).toBeNull();
});

test('an inert scientific document can be observed before a worker exists without claiming paint', async () => {
  const { declareScientificPresentation } = await import('./scientific-presentation');
  const root = new TestElement('section') as unknown as HTMLElement;
  expect(scientificPresentation(root)).toBeNull();
  const clear = declareScientificPresentation(root);
  expect(scientificPresentation(root)).toEqual({ paint: null });
  const newer = declareScientificPresentation(root);
  clear(); expect(scientificPresentation(root)).toEqual({ paint: null });
  newer(); expect(scientificPresentation(root)).toBeNull();
});

test.each(['resize','device scale'])('%s revokes existing pixels as gesture authority until repainted', change => {
  Object.assign(win,{devicePixelRatio:1});
  drawing.show(cases.valid,pixels()); paint();
  const current = drawing.current()!;
  if (change === 'resize') canvas.style.width = '640px';
  else Object.assign(win,{devicePixelRatio:2});
  expect(drawing.current()).toBeNull();
  expect(revoked).toHaveBeenCalledWith(current.draw);
  expect(() => drawing.position(current.draw,20,20)).toThrow('current scientific preview');
  drawing.show(cases.valid,pixels()); paint();
  expect(drawing.current()).not.toBeNull();
});

test('resize between bitmap preparation and browser paint cannot create a presentation receipt', () => {
  drawing.show(cases.valid,pixels());
  canvas.style.width = '640px'; paint();
  expect(drawn).not.toHaveBeenCalled(); expect(drawing.current()).toBeNull();
});


test('clear discards the previous bitmap and receipt without closing the view', () => {
  drawing.show(cases.valid,pixels()); paint(); const previous = drawing.current()!;
  drawing.clear();
  expect(canvas.width).toBe(0); expect(canvas.height).toBe(0);
  expect(drawing.current()).toBeNull(); expect(revoked).toHaveBeenCalledWith(previous.draw);
  drawing.show(cases.valid,pixels()); paint();
  expect(drawing.current()!.draw).not.toBe(previous.draw);
});

test('waiting for an exact canvas receipt resolves only after paint and refuses revoked frames',async()=>{
 drawing.show(cases.valid,pixels());
 const receipt=drawing.whenDrawn(cases.valid.frame);let complete=false;
 void receipt.then(()=>{complete=true;});await Promise.resolve();expect(complete).toBe(false);
 paint();expect((await receipt).frame.frame).toBe(cases.valid.frame);
 expect((await drawing.whenDrawn(cases.valid.frame)).draw).toBe(drawing.current()!.draw);
 drawing.show({...cases.valid,frame:'b'.repeat(64)},pixels());
 const refused=expect(drawing.whenDrawn('b'.repeat(64))).rejects.toThrow('draw');
 drawing.invalidate();await refused;
});

import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import { PanelStack } from './panels';
import { ScientificAnnotate, annotationState, type AnnotateChange } from './scientific-annotate';
import { scientificFrame } from './scientific-view';
import type { ScientificCanvas } from './scientific-canvas';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
let root: TestElement, canvas: TestElement & { hasPointerCapture(id: number): boolean }, control: ScientificAnnotate;
let win: EventTarget;
beforeEach(() => {
  const dom = syntheticDOM(); root = dom.host; win = new EventTarget();
  vi.stubGlobal('document', dom.document); vi.stubGlobal('window', win);
  class Canvas extends TestElement { hasPointerCapture(id: number): boolean { return this.captured === id; } focus() {} }
  canvas = new Canvas('canvas');
  Object.assign(canvas, { setPointerCapture: (id: number) => { canvas.captured = id; }, releasePointerCapture: () => { canvas.captured = null; } });
});
afterEach(() => { control?.close(); vi.unstubAllGlobals(); });
const field = (name: string) => [...root.querySelectorAll('select')].find(row => row.getAttribute('aria-label') === name)!;
const button = (name: string) => root.querySelectorAll('button').find(row => row.textContent === name)!;
const pointer = (name: string, x: number, y: number, extra: Record<string, unknown> = {}) =>
  fire(canvas, name, { pointerId: 1, button: 0, isPrimary: true, clientX: x, clientY: y, detail: 0, pointerType: 'mouse', ...extra });
const state = (mode: string, creating = false) => ({ mode, selected: 0, creating, undo: 1, redo: 0 });
function open(type: 'shapes' | 'points' = 'shapes', mode = 'pan_zoom', creating = false) {
  const frame = scientificFrame(cases.valid);
  frame.layers = [{ id: 'c'.repeat(32), name: 'Cells', type, visible: true, opacity: 1, scale: [1, 1], translate: [0, 0],
    elementCount: 2, annotation: state(mode, creating) }];
  const paint = { frame, draw: 'draw-one' };
  const drawing = { element: () => canvas, current: () => paint, position: (_draw: string, x: number, y: number) => [y, x] };
  const send = vi.fn(async (_change: AnnotateChange, _frame: string) => {});
  control = new ScientificAnnotate(root as unknown as HTMLElement, drawing as unknown as ScientificCanvas,
    { send, changed: () => {}, labelsEditing: () => false });
  control.bindSession('initial'); control.update(frame); control.setAvailable(true);
  return { send, frame };
}
const press = (key: string, target: unknown, extra: Record<string, unknown> = {}) => {
  const event = Object.assign(new Event('keydown'), { key, ...extra }); Object.defineProperty(event, 'target', { value: target }); win.dispatchEvent(event);
};

test('choosing a mode runs napari\'s own activate action for it', async () => {
  const { send, frame } = open();
  field('Annotation mode').value = 'add_rectangle'; fire(field('Annotation mode'), 'change'); await settleMicrotasks();
  expect(send).toHaveBeenCalledWith({ operation: 'annotate', value: { layer: 'c'.repeat(32), action: 'activate_add_rectangle_mode' } }, frame.frame);
});

test('a drag is one gesture sent on release, with napari\'s Shift lock pressed and released around it', async () => {
  const { send } = open('shapes', 'add_rectangle');
  pointer('pointerdown', 10, 20, { shiftKey: true }); pointer('pointermove', 15, 26, { shiftKey: true }); pointer('pointermove', 15.4, 26.2, { shiftKey: true });
  expect(send).not.toHaveBeenCalled(); expect(control.blocked()).toBe(true);
  pointer('pointerup', 30, 40, { shiftKey: true }); await settleMicrotasks();
  const value = send.mock.calls[0][0].value as { mode: string; events: Array<Record<string, unknown>> };
  expect(value.mode).toBe('add_rectangle');
  expect(value.events.map(row => row.kind)).toEqual(['key', 'move', 'press', 'move', 'move', 'release', 'key']);
  expect(value.events[1]).toEqual({ kind: 'move', point: [20, 10], modifiers: ['Shift'] });
  expect(value.events.at(-1)).toEqual({ kind: 'key', key: 'Shift', down: false });
});

test('a click mode sends each click; the second click of a double click is napari\'s double-click event', async () => {
  const { send } = open('shapes', 'add_polyline');
  pointer('pointerdown', 10, 10); fire(canvas, 'mousedown', { button: 0, detail: 1 });
  pointer('pointerup', 10, 10); await settleMicrotasks();
  pointer('pointerdown', 10, 10); fire(canvas, 'mousedown', { button: 0, detail: 2 });
  pointer('pointerup', 10, 10); await settleMicrotasks();
  const kinds = send.mock.calls.map(call => (call[0].value as { events: Array<{ kind: string }> }).events.map(row => row.kind));
  expect(kinds).toEqual([['move', 'press', 'release'], ['move', 'double', 'release']]);
});

test('a fast double click finishes the owned click after its request, without overlapping execution', async () => {
  let release: () => void = () => {};
  const { send } = open('shapes', 'add_polyline', true);
  send.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  pointer('pointerdown', 10, 10); fire(canvas, 'mousedown', { button: 0, detail: 1 }); pointer('pointerup', 10, 10);
  pointer('pointerdown', 10, 10); fire(canvas, 'mousedown', { button: 0, detail: 2 }); pointer('pointerup', 10, 10);
  fire(canvas, 'dblclick', { detail: 2 });
  expect(send).toHaveBeenCalledTimes(1);
  release(); await settleMicrotasks(); await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(2);
  expect(send.mock.calls[1][0].value).toEqual({ layer: 'c'.repeat(32), action: 'finish_drawing_shape' });
});

test('double-click intent survives the first response arriving between the second press and release', async () => {
  let release: () => void = () => {};
  const { send } = open('shapes', 'add_polyline', true);
  send.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  pointer('pointerdown', 10, 10); pointer('pointerup', 10, 10);
  pointer('pointerdown', 10, 10); fire(canvas, 'mousedown', { button: 0, detail: 2 });
  release(); await settleMicrotasks(); await settleMicrotasks();
  pointer('pointerup', 10, 10); fire(canvas, 'dblclick', { detail: 2 }); await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(2);
  expect(send.mock.calls[1][0].value).toEqual({ layer: 'c'.repeat(32), action: 'finish_drawing_shape' });
});

test.each(['failure', 'close', 'session', 'mode', 'blur', 'escape', 'pointercancel'])('queued double-click finish is discarded on %s', async reason => {
  let release: () => void = () => {}, reject: (error: Error) => void = () => {};
  const { send } = open('shapes', 'add_polyline', true);
  send.mockImplementationOnce(() => new Promise<void>((resolve, fail) => { release = resolve; reject = fail; }));
  pointer('pointerdown', 10, 10); pointer('pointerup', 10, 10);
  pointer('pointerdown', 10, 10); fire(canvas, 'mousedown', { button: 0, detail: 2 });
  pointer('pointerup', 10, 10); fire(canvas, 'dblclick', { detail: 2 });
  if (reason === 'close') control.close();
  if (reason === 'session') control.bindSession('replacement');
  if (reason === 'mode') { field('Annotation mode').value = 'select'; fire(field('Annotation mode'), 'change'); }
  if (reason === 'blur') win.dispatchEvent(new Event('blur'));
  if (reason === 'escape') press('Escape', canvas);
  if (reason === 'pointercancel') pointer('pointercancel', 10, 10);
  if (reason === 'failure') reject(new Error('stale frame')); else release();
  await settleMicrotasks(); await new Promise(resolve => setTimeout(resolve, 0)); await settleMicrotasks();
  expect(send.mock.calls.some(call => 'action' in call[0].value && call[0].value.action === 'finish_drawing_shape')).toBe(false);
});

test('unpressed moves while napari is creating go, in order, with the next click', async () => {
  const { send } = open('shapes', 'add_path', true);
  pointer('pointermove', 5, 5); pointer('pointermove', 9, 7); pointer('pointerdown', 12, 12); pointer('pointerup', 12, 12); await settleMicrotasks();
  const events = (send.mock.calls[0][0].value as { events: Array<{ kind: string; point?: number[] }> }).events;
  expect(events.map(row => row.kind)).toEqual(['move', 'move', 'move', 'press', 'release']);
  expect(events[0].point).toEqual([5, 5]);
});

test('napari\'s default keys on the focused preview run napari\'s actions for the layer type', async () => {
  const { send } = open('points', 'select');
  for (const [key, extra, action] of [['p', {}, 'activate_points_add_mode'], ['a', {}, 'select_all_in_slice'],
    ['a', { shiftKey: true }, 'select_append_all_in_slice'], ['a', { metaKey: true }, 'select_all_data'], ['Backspace', {}, 'delete_selected_points']] as const) {
    press(key, canvas, extra); await settleMicrotasks();
    expect(send).toHaveBeenLastCalledWith({ operation: 'annotate', value: { layer: 'c'.repeat(32), action } }, expect.any(String));
  }
  const calls = send.mock.calls.length; press('p', root); await settleMicrotasks();
  expect(send.mock.calls.length).toBe(calls);  // only the focused preview takes napari's keys
});

test('Escape cancels an unsent drag; Undo and Redo are Hooke\'s annotation history', async () => {
  const { send } = open('shapes', 'select');
  pointer('pointerdown', 10, 10); pointer('pointermove', 20, 20);
  press('Escape', canvas); pointer('pointerup', 20, 20); await settleMicrotasks();
  expect(send).not.toHaveBeenCalled(); expect(control.blocked()).toBe(false);
  fire(button('Undo annotation edit'), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith({ operation: 'annotate', value: { layer: 'c'.repeat(32), history: 'undo' } }, expect.any(String));
});

test('the annotation state is closed and checked', () => {
  expect(annotationState(state('add_polygon', true)).creating).toBe(true);
  for (const bad of [{ ...state('select'), extra: 1 }, { ...state('select'), mode: 'Select!' }, { ...state('select'), undo: -1 }, null])
    expect(() => annotationState(bad)).toThrow();
});

test('typing a mode name keeps the select usable: the first letter\'s mode is sent, and the mode it ends on is sent next', async () => {
  let release: () => void = () => {};
  const { send } = open();
  send.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  field('Annotation mode').value = 'add_path'; fire(field('Annotation mode'), 'change'); await settleMicrotasks();  // 'P' of "Polygon"
  expect(field('Annotation mode').disabled).toBe(false);
  expect(root.getAttribute('aria-busy')).toBe('true');
  field('Annotation mode').value = 'add_polygon'; fire(field('Annotation mode'), 'change'); await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(1);  // never two requests at once
  release(); await settleMicrotasks(); await new Promise(resolve => setTimeout(resolve, 0)); await settleMicrotasks();
  expect(send.mock.calls.map(call => (call[0].value as { action?: string }).action))
    .toEqual(['activate_add_path_mode', 'activate_add_polygon_mode']);
  expect(root.getAttribute('aria-busy')).toBe('false');
});

test('failed hover dispatch cannot continue into a destructive action', async () => {
  const { send } = open('shapes', 'add_path', true);
  send.mockRejectedValueOnce(new Error('stale frame; nothing applied'));
  pointer('pointermove', 9, 7);
  fire(button('Finish shape'), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('stale frame; nothing applied');
});

test.each(['session', 'layer', 'close'])('hover continuation cannot target a replaced %s', async reason => {
  const { send, frame } = open('shapes', 'add_path', true);
  let release!: () => void;
  send.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  pointer('pointermove', 9, 7); fire(button('Finish shape'), 'click');
  expect(send).toHaveBeenCalledTimes(1);
  if (reason === 'session') { control.bindSession('replacement'); control.update(frame); }
  else if (reason === 'layer') {
    const replacement = structuredClone(frame); replacement.layers[0].id = 'd'.repeat(32); control.update(replacement);
  } else control.close();
  release(); await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(1);
  expect(root.textContent).not.toContain('Annotation edit applied');
});

test('successful hover uses the returned frame for the same captured layer', async () => {
  const { send, frame } = open('shapes', 'add_path', true);
  send.mockImplementationOnce(async () => {
    const next = structuredClone(frame); next.frame = 'e'.repeat(32); control.update(next);
  });
  pointer('pointermove', 9, 7); fire(button('Finish shape'), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(2);
  expect(send).toHaveBeenLastCalledWith({ operation: 'annotate', value: { layer: 'c'.repeat(32), action: 'finish_drawing_shape' } }, 'e'.repeat(32));
});

test('failed request discards a mode queued behind it', async () => {
  vi.useFakeTimers();
  try {
    let reject!: (error: Error) => void;
    const { send } = open();
    send.mockImplementationOnce(() => new Promise<void>((_resolve, fail) => { reject = fail; }));
    field('Annotation mode').value = 'add_path'; fire(field('Annotation mode'), 'change');
    field('Annotation mode').value = 'add_polygon'; fire(field('Annotation mode'), 'change');
    reject(new Error('request refused')); await settleMicrotasks(); await vi.runAllTimersAsync();
    expect(send).toHaveBeenCalledTimes(1);
    expect(root.textContent).toContain('request refused');
  } finally { vi.useRealTimers(); }
});

test('the focused annotation preview finishes before workspace Escape can hide it', async () => {
  const { send } = open('shapes', 'add_polygon', true);
  const panels = new PanelStack(), close = vi.fn();
  panels.register({ id: 'scientific', isOpen: () => true, close, footprint: () => null });
  const event = Object.assign(new Event('keydown', { cancelable: true }), { key: 'Escape' });
  Object.defineProperty(event, 'target', { value: canvas });
  canvas.dispatchEvent(event);  // target phase precedes document/window listeners in the browser
  panels.onEscape(event); win.dispatchEvent(event);
  await settleMicrotasks();
  expect(close).not.toHaveBeenCalled();
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0].value).toEqual({ layer: 'c'.repeat(32), action: 'finish_drawing_shape' });
});

test('a preview without annotation keyboard ownership leaves Escape to the workspace', () => {
  open('points', 'pan_zoom');
  const panels = new PanelStack(), close = vi.fn();
  panels.register({ id: 'scientific', isOpen: () => true, close, footprint: () => null });
  const event = Object.assign(new Event('keydown', { cancelable: true }), { key: 'Escape' });
  Object.defineProperty(event, 'target', { value: canvas });
  canvas.dispatchEvent(event); panels.onEscape(event);
  expect(close).toHaveBeenCalledTimes(1);
});

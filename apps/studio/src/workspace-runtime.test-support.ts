// Explicit SYNTHETIC platform facts only: DOM hit node, active element and native capture APIs.
// No WorkspaceRuntime/Windows/reducer imports, identity mapping, report or normalized outcome.
// Pointer delivery supplies the missing propagation in the preserved TestElement fixture.
import { fire } from './workspace-windows.test-support';
import type { syntheticDOM, TestElement } from './workspace-windows.test-support';

type DOM = ReturnType<typeof syntheticDOM>;
export type CapturingElement = TestElement & { hasPointerCapture(pointerId: number): boolean; releasePointerCapture(pointerId: number): void };

export function syntheticPointerPlatform(dom: DOM) {
  const owners = new Map<number, CapturingElement>();
  const active = new Set<number>();
  const installed = new Set<TestElement>();
  const pointerFields = new Map<number, Record<string, unknown>>();
  let physical: TestElement | null = null;
  let focused: TestElement | null = null;
  let lastPoint: readonly number[] | null = null;

  Object.defineProperties(dom.document, {
    elementFromPoint: { configurable: true, value: (x: number, y: number) => lastPoint && x === lastPoint[0] && y === lastPoint[1] ? physical : null },
    activeElement: { configurable: true, get: () => focused },
  });
  // Host gets its capture listener first, then actual recipient/ancestor handlers once each.
  function propagate(target: TestElement, type: string, fields: Record<string, unknown>) {
    const path: TestElement[] = [];
    for (let node: TestElement | null = target; node && node !== dom.host; node = node.parentElement) path.push(node);
    fire(dom.host, type, { target, ...fields });
    for (const node of path) fire(node, type, { target, ...fields });
  }
  function bind(node: TestElement): CapturingElement {
    if (!installed.has(node)) {
      installed.add(node);
      const set = node.setPointerCapture.bind(node), release = node.releasePointerCapture.bind(node);
      Object.defineProperties(node, {
        hasPointerCapture: { configurable: true, value: (pointerId: number) => owners.get(pointerId) === node },
        setPointerCapture: { configurable: true, value: (pointerId: number) => {
          if (!active.has(pointerId)) throw new Error('SYNTHETIC NotFoundError: pointer is not active');
          const previous = owners.get(pointerId);
          if (previous === node) return;
          previous?.releasePointerCapture(pointerId);
          set(pointerId); owners.set(pointerId, node as CapturingElement);
          propagate(node, 'gotpointercapture', { ...pointerFields.get(pointerId), pointerId });
        } },
        releasePointerCapture: { configurable: true, value: (pointerId: number) => {
          if (owners.get(pointerId) !== node) return;
          owners.delete(pointerId); release(); propagate(node, 'lostpointercapture', { ...pointerFields.get(pointerId), pointerId });
        } },
      });
    }
    for (const child of node.children) bind(child);
    return node as CapturingElement;
  }
  function hit(node: TestElement, css: readonly number[]) {
    const box = node.getBoundingClientRect();
    if (!dom.host.contains(node) || css.length !== 2 || !css.every(Number.isFinite) || css[0] < box.left || css[0] >= box.right || css[1] < box.top || css[1] >= box.bottom) throw new Error('SYNTHETIC hit must name a real contained DOM node at its measured CSS point');
    physical = node; lastPoint = css.slice();
  }
  return {
    bind,
    /** The native captured recipient receives the event; hit testing remains physical. */
    pointer(node: TestElement, type: 'pointerdown' | 'pointermove' | 'pointerup' | 'pointercancel', css: readonly number[], pointerId = 1) {
      hit(node, css);
      if (type === 'pointerdown') active.add(pointerId);
      const recipient = owners.get(pointerId) ?? node;
      const fields = { pointerId, pointerType: 'mouse', isPrimary: true, clientX: css[0], clientY: css[1], buttons: type === 'pointerup' || type === 'pointercancel' ? 0 : 1 };
      pointerFields.set(pointerId, fields); propagate(recipient, type, fields);
      if (type === 'pointerup' || type === 'pointercancel') { owners.get(pointerId)?.releasePointerCapture(pointerId); active.delete(pointerId); pointerFields.delete(pointerId); }
    },
    focus(node: TestElement) {
      if (!dom.host.contains(node)) throw new Error('SYNTHETIC focus must name an actual contained node');
      focused = node; propagate(node, 'focusin', {});
    },
  };
}

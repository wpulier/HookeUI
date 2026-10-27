// Synthetic DOM for the sessions views' tests (session-menu.test.ts, session-rail.test.ts, sidebar.test.ts, screen.test.ts): the shared
// TestElement with what an inline rename and focus need. Not a browser: no layout, no CSS.
import { vi } from 'vitest';
import { TestElement } from './workspace-windows.test-support';
import type { SessionRow, SessionState, SessionsList } from './protocol';

export class SessionNode extends TestElement {
  static active: SessionNode | null = null;
  type = '';
  placeholder = '';
  maxLength = -1;
  autocomplete = '';
  spellcheck = true;
  open = false;
  src = '';
  alt = '';
  loading = '';
  decoding = '';
  selectionStart: number | null = null;
  selectionEnd: number | null = null;
  get isConnected(): boolean {
    let node: TestElement | null = this;
    while (node.parentElement) node = node.parentElement;
    return node === sessionRoot;
  }
  focus(): void { SessionNode.active = this; }
  blur(): void { if (SessionNode.active === this) SessionNode.active = null; }
  select(): void { this.selectionStart = 0; this.selectionEnd = this.value.length; }
  setSelectionRange(start: number, end: number): void { this.selectionStart = start; this.selectionEnd = end; }
  replaceWith(node: TestElement): void {
    const parent = this.parentElement;
    if (!parent) return;
    parent.insertBefore(node, this);
    this.remove();
  }
}

/** The document every node here hangs from (isConnected). */
let sessionRoot: SessionNode = new SessionNode('body');

export interface SessionDOM {
  body: SessionNode;
  storage: Map<string, string>;
  byId: Map<string, SessionNode>;
  node(id: string, tag?: string): SessionNode;
  /** A key pressed on a node: its own listeners, then (unless stopped) the document's, as a bubbling keydown would. */
  key(target: SessionNode, key: string): { prevented: boolean };
}

/** Stub `document` (createElement, getElementById, activeElement) and `localStorage` for one test. */
export function sessionDOM(): SessionDOM {
  sessionRoot = new SessionNode('body');
  SessionNode.active = null;
  const storage = new Map<string, string>();
  const byId = new Map<string, SessionNode>();
  const documentListeners = new EventTarget();
  vi.stubGlobal('document', Object.assign(documentListeners, {
    createElement: (tag: string) => new SessionNode(tag),
    getElementById: (id: string) => byId.get(id) ?? null,
    get activeElement() { return SessionNode.active; },
    body: sessionRoot,
  }));
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
    removeItem: (key: string) => { storage.delete(key); },
  });
  return {
    body: sessionRoot, storage, byId,
    node(id: string, tag = 'div') {
      const node = new SessionNode(tag);
      node.id = id;
      byId.set(id, node);
      sessionRoot.append(node);
      return node;
    },
    key(target: SessionNode, key: string) {
      let prevented = false, stopped = false;
      const event = new Event('keydown', { cancelable: true });
      Object.defineProperties(event, {
        key: { value: key }, target: { value: target },
        preventDefault: { value: () => { prevented = true; } }, defaultPrevented: { get: () => prevented },
        stopPropagation: { value: () => { stopped = true; } },
      });
      for (let node: TestElement | null = target; node && !stopped; node = node.parentElement) node.dispatchEvent(event);
      if (!stopped) documentListeners.dispatchEvent(event);
      return { prevented };
    },
  };
}

// --- the wire, as a server sends it -------------------------------------------------------------

/** A local time today (or `days` before), in epoch seconds. */
export function at(hour: number, minute = 0, days = 0, base = new Date(2026, 9, 12)): number {
  return new Date(base.getFullYear(), base.getMonth(), base.getDate() - days, hour, minute).getTime() / 1000;
}

export const NOW = at(15, 30); // Oct 12 2026, 3:30 pm local

export function row(id: string, fields: Partial<SessionRow> = {}): SessionRow {
  return { id, name: '', named: false, started: at(9), lastActive: at(10), days: ['2026-10-12'],
    counts: { made: 0, researchers: 0, conversations: 1 }, specimens: [], working: 0, ...fields };
}

export function state(id: string, fields: Partial<SessionState> = {}): SessionState {
  return { type: 'session', id, label: 'Session: Oct 12', started: at(14, 10), specimen: '', name: '', named: false,
    lastActive: at(15), days: ['2026-10-12'], counts: { made: 0, researchers: 0, conversations: 1 }, suggest: null, ...fields };
}

export function list(current: string, sessions: SessionRow[]): SessionsList {
  return { type: 'sessions', current, sessions };
}

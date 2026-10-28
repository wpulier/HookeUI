import './agent-connections.css';
import { StudioSheet } from './sheet';

/** Outside agents' connections (ADR 0026; services/orchestrator/door/AGENTS.md): the sheet that asks the researcher to
 *  confirm an agent the `hooke` command is connecting, and Settings' list of connected agents with Disconnect. The
 *  server tells open studios about every change (`agent-connections`); the studio also reads the list when it mounts. */

export interface PendingAgent { id: string; label: string; kind: string; code: string; at: number; expiresAt: number; state: string }
export interface AgentConnection { id: string; label: string; kind: string; at: number; revokedAt: number | null }
export interface AgentConnectionsState { pending: PendingAgent[]; connections: AgentConnection[]; playbook?: string }
export interface AgentConnectionsMessage extends AgentConnectionsState { type: 'agent-connections' }

/** What the connection sheet needs of StudioSheet (sheet.ts); a test gives its own. */
export interface SheetLike { open(content: HTMLElement): void; close(notify?: boolean): void; onDismiss: () => void }

type NativeConnect = { postMessage(value: unknown): Promise<unknown> };
/** The Mac app asks in its own dialog and signs the answer (packaging/macos main.swift AgentConnect; ADR 0026); a
 *  browser has no such bridge, and only a developer's checkout (HOOKE_DEV_CONFIRM=1) accepts the web sheet's answer. */
function nativeConnect(): NativeConnect | undefined {
  const host = (typeof window === 'undefined' ? undefined : window) as unknown as
    { webkit?: { messageHandlers?: { hookeConnect?: NativeConnect } } } | undefined;
  return host?.webkit?.messageHandlers?.hookeConnect;
}

/** In the Mac app, bring Hooke forward so the researcher sees the confirm (packaging/macos main.swift AgentAttention).
 *  A browser has no such bridge, and nothing happens. */
function attention(): void {
  const host = (typeof window === 'undefined' ? undefined : window) as unknown as
    { webkit?: { messageHandlers?: { hookeAttention?: { postMessage(value: unknown): void } } } } | undefined;
  try { host?.webkit?.messageHandlers?.hookeAttention?.postMessage({ why: 'agent-connect' }); } catch { /* best effort */ }
}

const HEADERS = { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'agent-connections' };

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object';
const str = (v: unknown) => typeof v === 'string';
const num = (v: unknown) => typeof v === 'number' && Number.isFinite(v);

/** The server's list, checked; anything else is unreadable (never an empty list). */
export function parseAgentConnections(value: unknown): AgentConnectionsState {
  if (!isObject(value) || !Array.isArray(value.pending) || !Array.isArray(value.connections)) throw new Error('Connected agents could not be read.');
  const pending = value.pending.map(p => {
    if (!isObject(p) || !str(p.id) || !str(p.label) || !str(p.kind) || !str(p.code) || !num(p.at) || !num(p.expiresAt) || !str(p.state)) throw new Error('Connected agents could not be read.');
    return { id: p.id, label: p.label, kind: p.kind, code: p.code, at: p.at, expiresAt: p.expiresAt, state: p.state } as PendingAgent;
  });
  const connections = value.connections.map(c => {
    if (!isObject(c) || !str(c.id) || !str(c.label) || !str(c.kind) || !num(c.at) || !(c.revokedAt === null || num(c.revokedAt))) throw new Error('Connected agents could not be read.');
    return { id: c.id, label: c.label, kind: c.kind, at: c.at, revokedAt: c.revokedAt } as AgentConnection;
  });
  // The page an agent follows to connect to this app's channel (door api.playbook); only a hookeui.org page is shown.
  const playbook = str(value.playbook) && /^https:\/\/hookeui\.org\/[\w./-]+\.md$/.test(value.playbook as string)
    ? value.playbook as string : undefined;
  return { pending, connections, ...(playbook ? { playbook } : {}) };
}

/** What the confirm sheet says: who asks, the code to match, and what connecting allows, including that what the agent
 *  reads may go to its provider. The name is the agent's own description of itself, never a checked identity. */
export function confirmWords(p: PendingAgent): { title: string; lead: string; access: string[] } {
  return {
    title: `Connect ${p.label} to Hooke?`,
    lead: `An agent on this Mac calling itself ${p.label} asked to connect. Check that it shows the code ${p.code}.`,
    access: [
      'Through Hooke’s agent connection it can see what Hooke can do, and work in your Projects on this Mac: open files, run tools and keep results. What it does is recorded as done by it.',
      'That connection gives it no way to read your keys, change your settings or delete anything.',
      `What it reads may be sent to the company that runs ${p.label}, as anything you share with it is.`,
      'An agent that runs commands on your Mac can already reach your files as any program you run can, so connect only agents you trust. You can disconnect it at any time in Settings → Connected agents.',
    ],
  };
}

// With the time: two connections of one agent on one day (a reconnect) are told apart.
const day = (seconds: number) => new Date(seconds * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });

/** One row of Settings' list. */
export function connectionWords(c: AgentConnection): string {
  return c.revokedAt === null ? `Connected ${day(c.at)}` : `Disconnected ${day(c.revokedAt)}`;
}

async function refusalWords(response: Response, fallback: string): Promise<string> {
  try {
    const value = await response.json();
    if (isObject(value) && str(value.says)) return value.next ? `${value.says} ${String(value.next).replace(/^./, c => c.toUpperCase())}.` : value.says;
  } catch { /* the body wasn't JSON: say the fallback */ }
  return fallback;
}

export class AgentConnections {
  private state: AgentConnectionsState = { pending: [], connections: [] };
  private sheetHost: SheetLike | null = null;
  private bodyHost: HTMLElement | null = null;
  private showing: string | null = null;
  private expiry: ReturnType<typeof setTimeout> | null = null;
  private readonly answered = new Set<string>();
  private section: HTMLElement | null = null;

  private readonly request: typeof fetch;
  private readonly makeSheet: () => SheetLike;
  /** The Mac app's confirm bridge, when there is one (a test gives its own). */
  native: () => NativeConnect | undefined = nativeConnect;

  constructor(request: typeof fetch = (...args) => fetch(...args), makeSheet: () => SheetLike = () => new StudioSheet('Connect an agent')) {
    this.request = request;
    this.makeSheet = makeSheet;
  }

  /** Made on first use, so importing this module touches no DOM. */
  private get sheet(): SheetLike {
    if (!this.sheetHost) {
      this.sheetHost = this.makeSheet();
      this.sheetHost.onDismiss = () => { this.showing = null; };
    }
    return this.sheetHost;
  }

  private get body(): HTMLElement {
    if (!this.bodyHost) { this.bodyHost = document.createElement('div'); this.bodyHost.className = 'agent-connect'; }
    return this.bodyHost;
  }

  /** A server message or a fresh read. */
  apply(state: AgentConnectionsState): void {
    this.state = state;
    this.renderList();
    const waiting = state.pending.filter(p => p.state === 'pending' && !this.answered.has(p.id));
    if (this.showing && !waiting.some(p => p.id === this.showing)) this.closeSheet();  // answered elsewhere or expired
    if (!this.showing && waiting.length) this.ask(waiting[0]);
  }

  message(message: unknown): void {
    try { this.apply(parseAgentConnections(message)); } catch { /* an unreadable message changes nothing shown */ }
  }

  async refresh(): Promise<void> {
    const response = await this.request('/api/agent-connections', { cache: 'no-store' });
    if (!response.ok) throw new Error(await refusalWords(response, 'Connected agents could not be read.'));
    this.apply(parseAgentConnections(await response.json()));
  }

  private closeSheet(): void {
    if (this.expiry) clearTimeout(this.expiry);
    this.expiry = null;
    this.showing = null;
    this.sheetHost?.close(false);
  }

  private ask(p: PendingAgent): void {
    const native = this.native();
    if (native) {
      // The Mac app's own dialog asks and answers; the workspace shows nothing of its own.
      this.answered.add(p.id);
      void native.postMessage({ id: p.id, label: p.label, code: p.code }).catch(() => { this.answered.delete(p.id); });
      return;
    }
    const words = confirmWords(p);
    const title = document.createElement('h3'); title.textContent = words.title;
    const lead = document.createElement('p'); lead.textContent = words.lead;
    const code = document.createElement('p'); code.className = 'agent-connect-code'; code.textContent = p.code;
    code.setAttribute('aria-label', `Code ${p.code.split('').join(' ')}`);
    const list = document.createElement('ul');
    for (const line of words.access) { const li = document.createElement('li'); li.textContent = line; list.append(li); }
    const status = document.createElement('p'); status.className = 'agent-connect-status'; status.setAttribute('role', 'status');
    const actions = document.createElement('div'); actions.className = 'agent-connect-actions';
    const decline = document.createElement('button'); decline.type = 'button'; decline.textContent = 'Don’t connect';
    const connect = document.createElement('button'); connect.type = 'button'; connect.className = 'primary'; connect.textContent = 'Connect';
    const send = async (answer: 'connect' | 'decline') => {
      connect.disabled = decline.disabled = true; status.textContent = '';
      try {
        const response = await this.request(`/api/agent-connections/pending/${encodeURIComponent(p.id)}`,
          { method: 'POST', headers: HEADERS, body: JSON.stringify({ answer }) });
        if (!response.ok) throw new Error(await refusalWords(response, 'Hooke could not keep your answer.'));
        this.answered.add(p.id);
        this.closeSheet();
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : 'Hooke could not keep your answer.';
        connect.disabled = decline.disabled = false;
      }
    };
    connect.onclick = () => send('connect');
    decline.onclick = () => send('decline');
    actions.append(decline, connect);
    this.body.replaceChildren(title, lead, code, list, status, actions);
    this.showing = p.id;
    this.sheet.open(this.body);
    attention();
    const left = p.expiresAt * 1000 - Date.now();
    if (this.expiry) clearTimeout(this.expiry);
    this.expiry = setTimeout(() => { if (this.showing === p.id) { this.answered.add(p.id); this.closeSheet(); } }, Math.max(0, left));
  }

  /** Settings → Connected agents: each agent with when it connected and Disconnect. */
  mountSettings(settings: HTMLElement): void {
    const section = document.createElement('section'); section.className = 'agent-connections-settings';
    section.setAttribute('aria-label', 'Connected agents');
    this.section = section;
    settings.append(section);
    this.renderList();
    void this.refresh().catch(error => {
      const p = document.createElement('p'); p.setAttribute('role', 'status');
      p.textContent = error instanceof Error ? error.message : 'Connected agents could not be read.';
      section.append(p);
    });
  }

  private renderList(): void {
    const section = this.section;
    if (!section) return;
    const heading = document.createElement('h3'); heading.textContent = 'Connected agents';
    const intro = document.createElement('p');
    intro.textContent = 'Coding agents you connected with the hooke command, such as Claude Code or Codex. Each works in your Projects on this Mac, and what it does is recorded as done by it.';
    const live = this.state.connections.filter(c => c.revokedAt === null);
    const list = document.createElement('ul');
    for (const c of live) {
      const li = document.createElement('li');
      const name = document.createElement('span'); name.className = 'agent-name'; name.textContent = c.label;
      const when = document.createElement('span'); when.className = 'agent-when'; when.textContent = connectionWords(c);
      const status = document.createElement('span'); status.setAttribute('role', 'status');
      const end = document.createElement('button'); end.type = 'button'; end.textContent = 'Disconnect';
      end.setAttribute('aria-label', `Disconnect ${c.label}`);
      end.onclick = async () => {
        end.disabled = true; status.textContent = '';
        try {
          const response = await this.request(`/api/agent-connections/${encodeURIComponent(c.id)}/disconnect`, { method: 'POST', headers: HEADERS, body: '{}' });
          if (!response.ok) throw new Error(await refusalWords(response, 'Hooke could not disconnect it.'));
          await this.refresh();
        } catch (error) {
          status.textContent = error instanceof Error ? error.message : 'Hooke could not disconnect it.';
          end.disabled = false;
        }
      };
      li.append(name, when, end, status);
      list.append(li);
    }
    const none = document.createElement('p'); none.className = 'agent-none';
    const page = (this.state.playbook ?? 'https://hookeui.org/get-started.md').replace(/^https:\/\//, '');
    none.textContent = `No agent is connected. To connect one, ask it to fetch ${page} and follow it.`;
    section.replaceChildren(heading, intro, live.length ? list : none);
  }
}

export const agentConnections = new AgentConnections();

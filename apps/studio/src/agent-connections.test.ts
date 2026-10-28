// An outside agent connecting (ADR 0026): the confirm sheet says who asks, the code to match and what connecting allows;
// the answer goes with the studio's header; Settings lists connected agents with Disconnect.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentConnections, confirmWords, connectionWords, parseAgentConnections, type PendingAgent, type SheetLike } from './agent-connections';
import { TestElement, syntheticDOM } from './workspace-windows.test-support';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

const pending = (over: Partial<PendingAgent> = {}): PendingAgent => ({
  id: 'pc_1', label: 'Claude Code', kind: 'claude-code', code: 'KX7-M3P', at: 1000, expiresAt: Date.now() / 1000 + 600,
  state: 'pending', ...over,
});

function fakeSheet() {
  const sheet = { shown: null as TestElement | null, closed: 0, onDismiss: () => {},
    open(content: HTMLElement) { sheet.shown = content as unknown as TestElement; },
    close() { sheet.shown = null; sheet.closed += 1; } };
  return sheet;
}

function rig(responses: Array<{ ok: boolean; body: unknown }> = []) {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const next = responses.shift() ?? { ok: true, body: { ok: true, pending: [], connections: [] } };
    return { ok: next.ok, json: async () => next.body } as Response;
  });
  const sheet = fakeSheet();
  const connections = new AgentConnections(request as unknown as typeof fetch, () => sheet as unknown as SheetLike);
  return { connections, sheet, calls };
}

const buttons = (root: TestElement): Array<TestElement & { onclick: () => Promise<void> | void; disabled: boolean }> => {
  const found: TestElement[] = [];
  const walk = (node: TestElement) => { if (node.tagName?.toLowerCase() === 'button') found.push(node); node.children.forEach(walk); };
  walk(root);
  return found as never;
};

describe('the words', () => {
  it('names the agent as it names itself, the code to match, and that what it reads may go to its provider', () => {
    const words = confirmWords(pending());
    expect(words.title).toBe('Connect Claude Code to Hooke?');
    expect(words.lead).toContain('calling itself Claude Code');
    expect(words.lead).toContain('KX7-M3P');
    expect(words.access.join(' ')).toContain('may be sent to the company that runs Claude Code');
    expect(words.access.join(' ')).toContain('no way to read your keys, change your settings or delete anything');
    expect(words.access.join(' ')).toContain('connect only agents you trust');
    expect(words.access.join(' ')).toContain('Settings → Connected agents');
  });

  it('reads only a well-formed list', () => {
    expect(parseAgentConnections({ pending: [pending()], connections: [] }).pending[0].code).toBe('KX7-M3P');
    expect(() => parseAgentConnections({ pending: [{ id: 1 }], connections: [] })).toThrow();
    expect(() => parseAgentConnections({})).toThrow();
    expect(connectionWords({ id: 'ac_1', label: 'Codex', kind: 'codex', at: 0, revokedAt: null })).toMatch(/^Connected /);
  });
});

describe('the confirm sheet', () => {
  it('opens for a waiting agent and sends Connect with the studio header, then closes', async () => {
    const { connections, sheet, calls } = rig([{ ok: true, body: { ok: true } }]);
    connections.apply({ pending: [pending()], connections: [] });
    expect(sheet.shown?.textContent).toContain('Connect Claude Code to Hooke?');
    expect(sheet.shown?.textContent).toContain('KX7-M3P');
    const [, connect] = buttons(sheet.shown!);
    await connect.onclick();
    expect(calls[0].url).toBe('/api/agent-connections/pending/pc_1');
    expect(calls[0].init?.method).toBe('POST');
    expect((calls[0].init?.headers as Record<string, string>)['X-MicroscopeMind']).toBe('agent-connections');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ answer: 'connect' });
    expect(sheet.shown).toBeNull();
    // The same request arriving again (the server's next message) does not ask twice.
    connections.apply({ pending: [pending()], connections: [] });
    expect(sheet.shown).toBeNull();
  });

  it('sends Don’t connect as decline, and says a refusal instead of closing', async () => {
    const { connections, sheet, calls } = rig([{ ok: false, body: { ok: false, code: 'connection-expired', says: 'That connection request has expired or is unknown.', next: 'run hooke connect again' } }]);
    connections.apply({ pending: [pending()], connections: [] });
    const [decline] = buttons(sheet.shown!);
    await decline.onclick();
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ answer: 'decline' });
    expect(sheet.shown?.textContent).toContain('That connection request has expired or is unknown. Run hooke connect again.');
  });

  it('closes when the request is answered elsewhere or expires', () => {
    vi.useFakeTimers();
    const { connections, sheet } = rig();
    connections.apply({ pending: [pending()], connections: [] });
    expect(sheet.shown).not.toBeNull();
    connections.apply({ pending: [], connections: [] });
    expect(sheet.shown).toBeNull();
    connections.apply({ pending: [pending({ id: 'pc_2', expiresAt: Date.now() / 1000 + 5 })], connections: [] });
    expect(sheet.shown).not.toBeNull();
    vi.advanceTimersByTime(6000);
    expect(sheet.shown).toBeNull();
  });
});

describe('Settings → Connected agents', () => {
  it('lists connected agents only, and Disconnect sends the studio header then reads the list again', async () => {
    const live = { id: 'ac_1', label: 'Claude Code', kind: 'claude-code', at: 1_700_000_000, revokedAt: null };
    const gone = { id: 'ac_2', label: 'Old agent', kind: 'other', at: 1_600_000_000, revokedAt: 1_650_000_000 };
    const { connections, calls } = rig([
      { ok: true, body: { ok: true, pending: [], connections: [live, gone] } },
      { ok: true, body: { ok: true } },
      { ok: true, body: { ok: true, pending: [], connections: [{ ...live, revokedAt: 1_800_000_000 }, gone],
                          playbook: 'https://hookeui.org/get-started-beta.md' } },
    ]);
    const settings = new TestElement('section');
    connections.mountSettings(settings as unknown as HTMLElement);
    await vi.waitFor(() => expect(buttons(settings)).toHaveLength(1));
    expect(settings.textContent).not.toContain('Old agent');
    const [disconnect] = buttons(settings);
    await disconnect.onclick();
    expect(calls[1].url).toBe('/api/agent-connections/ac_1/disconnect');
    expect((calls[1].init?.headers as Record<string, string>)['X-MicroscopeMind']).toBe('agent-connections');
    expect(settings.textContent).toContain('No agent is connected');
    // The channel's own page (found on a fresh Mac: Beta pointed at Stable's page, which isn't there yet).
    expect(settings.textContent).toContain('fetch hookeui.org/get-started-beta.md');
  });

  it('shows only a hookeui.org page as the one to follow, and the time each agent connected', () => {
    expect(parseAgentConnections({ pending: [], connections: [], playbook: 'https://hookeui.org/get-started-beta.md' }).playbook)
      .toBe('https://hookeui.org/get-started-beta.md');
    expect(parseAgentConnections({ pending: [], connections: [], playbook: 'https://evil.example/x.md' }).playbook).toBeUndefined();
    const morning = connectionWords({ id: 'ac_1', label: 'Claude Code', kind: 'claude-code', at: 1_793_207_440, revokedAt: null });
    const later = connectionWords({ id: 'ac_2', label: 'Claude Code', kind: 'claude-code', at: 1_793_207_440 + 3 * 3600, revokedAt: null });
    expect(morning).not.toBe(later);
  });
});

describe('in the Mac app', () => {
  it('hands the request to the native dialog and shows no web sheet', async () => {
    const { connections, sheet, calls } = rig();
    const posted: unknown[] = [];
    connections.native = () => ({ postMessage: async (value: unknown) => { posted.push(value); return { answer: 'connect', ok: true }; } });
    connections.apply({ pending: [pending()], connections: [] });
    expect(sheet.shown).toBeNull();
    expect(posted).toEqual([{ id: 'pc_1', label: 'Claude Code', code: 'KX7-M3P' }]);
    expect(calls).toEqual([]);  // the workspace sends no answer of its own
    connections.apply({ pending: [pending()], connections: [] });
    expect(posted).toHaveLength(1);  // asked once
  });
});

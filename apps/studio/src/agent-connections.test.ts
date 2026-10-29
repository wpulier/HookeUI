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
    await vi.waitFor(() => expect(buttons(settings)).toHaveLength(3));
    expect(settings.textContent).not.toContain('Old agent');
    const disconnect = buttons(settings).find(b => b.textContent === 'Disconnect')!;
    await disconnect.onclick();
    expect(calls[1].url).toBe('/api/agent-connections/ac_1/disconnect');
    expect((calls[1].init?.headers as Record<string, string>)['X-MicroscopeMind']).toBe('agent-connections');
    expect(settings.textContent).toContain('No agent is connected');
    // The channel's own page (found on a fresh Mac: Beta pointed at Stable's page, which isn't there yet).
    expect(settings.textContent).toContain('Use Copy setup prompt');
    expect(settings.textContent).not.toContain('fetch hookeui.org');
  });

  it('collapses and reopens Project access without losing its form or reloading membership', async () => {
    const live = { id: 'ac_1', label: 'Claude Code', kind: 'claude-code', at: 1_700_000_000, revokedAt: null };
    const { connections, calls } = rig([
      { ok: true, body: { pending: [], connections: [live] } },
      { ok: true, body: { editable: true, projects: [{session:'p1',name:'Research',by:'agent:other',grants:{}}] } },
    ]);
    const settings = new TestElement('section'); connections.mountSettings(settings as unknown as HTMLElement);
    await vi.waitFor(() => expect(buttons(settings)).toHaveLength(3));
    const access = buttons(settings).find(b => b.textContent === 'Project access')!;
    access.onclick();
    await vi.waitFor(() => expect(settings.querySelectorAll('select')).toHaveLength(2));
    const role = settings.querySelectorAll('select')[1] as unknown as HTMLSelectElement; role.value = 'read';
    access.onclick(); access.onclick();
    expect(role.value).toBe('read');
    expect(settings.querySelectorAll('fieldset')).toHaveLength(1);
    expect(calls).toHaveLength(2);
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


describe('Project connection entrance', () => {
  it('shows confirmed connections, not pending/revoked clients or a claim of Project permission', () => {
    const { connections } = rig(); const host = new TestElement('div'); const manage = vi.fn();
    connections.mountEntry(host as unknown as HTMLElement, manage, () => 'p1');
    expect(host.textContent).toContain('Checking agent connections');
    connections.apply({ pending: [], connections: [
      { id: 'ac_1', label: 'Research orchestrator', kind: 'other', at: 1, revokedAt: null },
      { id: 'ac_2', label: 'Old client', kind: 'other', at: 1, revokedAt: 2 },
    ] });
    expect(host.textContent).toContain('Connected to Hooke: Research orchestrator');
    expect(host.textContent).not.toContain('Old client');
    expect(host.textContent).toContain('Project access is managed separately');
    buttons(host).find(b => b.textContent === 'Manage connections')!.onclick(); expect(manage).toHaveBeenCalledOnce();
    connections.apply({ pending: [], connections: [] });
    expect(host.textContent).toContain('Continue with your own agent');
  });

  it('copies the returned channel and captured Project without sending work or changing grants', async () => {
    const { connections, calls } = rig([{ ok: true, body: { pending: [], connections: [], playbook: 'https://hookeui.org/get-started-beta.md' } }]);
    const writeText = vi.fn(async (_text: string) => {}); vi.stubGlobal('navigator', { clipboard: { writeText } });
    let project = 'p1'; const host = new TestElement('div');
    connections.mountEntry(host as unknown as HTMLElement, () => {}, () => project);
    project = 'p2'; await buttons(host)[0].onclick();
    expect(writeText.mock.calls[0][0]).toContain('https://hookeui.org/get-started-beta.md');
    expect(writeText.mock.calls[0][0]).toContain('existing Project p2');
    expect(writeText.mock.calls[0][0]).toContain('Do not create a replacement Project');
    expect(calls).toHaveLength(1); expect(calls[0].init?.method).toBeUndefined();
    expect(host.textContent).toContain('nothing has been sent');
  });

  it('ignores an older copy reply after copying for the next Project', async () => {
    const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
    const replies: Array<(value: Response) => void> = [];
    const request = vi.fn(() => new Promise<Response>(resolve => replies.push(resolve)));
    const connections = new AgentConnections(request as unknown as typeof fetch);
    const writeText = vi.fn(async (_text: string) => {}); vi.stubGlobal('navigator', { clipboard: { writeText } });
    let project = 'p1'; const host = new TestElement('div');
    connections.mountEntry(host as unknown as HTMLElement, () => {}, () => project);
    const first = buttons(host)[0].onclick(); project = 'p2'; const second = buttons(host)[0].onclick();
    const response = () => ({ ok: true, json: async () => ({ pending: [], connections: [], playbook: 'https://hookeui.org/get-started-beta.md' }) }) as Response;
    replies[1](response()); await second; replies[0](response()); await first;
    expect(writeText).toHaveBeenCalledOnce(); expect(writeText.mock.calls[0][0]).toContain('existing Project p2');
  });

  it('offers selectable text when clipboard permission is unavailable', async () => {
    const { connections } = rig([{ ok: true, body: { pending: [], connections: [], playbook: 'https://hookeui.org/get-started-beta.md' } }]);
    vi.stubGlobal('navigator', { clipboard: { writeText: async () => { throw new Error('denied'); } } });
    const host = new TestElement('div'); connections.mountEntry(host as unknown as HTMLElement, () => {}, () => 'p1');
    const group = host.children[0].children[2].children[0];
    const field = group.children[1] as TestElement & { select: () => void; focus: () => void; value: string; hidden: boolean };
    field.select = vi.fn(); field.focus = vi.fn();
    await buttons(host)[0].onclick();
    expect(field.hidden).toBe(false); expect(field.value).toContain('existing Project p1');
    expect(field.select).toHaveBeenCalledOnce(); expect(host.textContent).toContain('Copy the selected prompt');
  });

  it('keeps development setup separate from installed channels', async () => {
    const { connections } = rig([{ ok: true, body: { pending: [], connections: [], channel: 'development', playbook: 'https://hookeui.org/get-started.md' } }]);
    const writeText = vi.fn(async (_text: string) => {}); vi.stubGlobal('navigator', { clipboard: { writeText } });
    const host = new TestElement('div'); connections.mountEntry(host as unknown as HTMLElement, () => {}, () => 'p1');
    await buttons(host)[0].onclick();
    expect(writeText.mock.calls[0][0]).toContain('Do not install or switch to Stable or Beta');
    expect(writeText.mock.calls[0][0]).not.toContain('Fetch https');
  });

  it('refuses unavailable setup and reports an unreadable connection state without claiming none', async () => {
    const { connections } = rig([{ ok: false, body: {} }, { ok: true, body: { pending: [], connections: [] } }]);
    const writeText = vi.fn(); vi.stubGlobal('navigator', { clipboard: { writeText } });
    const host = new TestElement('div'); connections.mountEntry(host as unknown as HTMLElement, () => {}, () => 'p1');
    await expect(connections.refresh()).rejects.toThrow();
    expect(host.textContent).toContain('Connection status unavailable');
    await buttons(host)[0].onclick();
    expect(host.textContent).toContain('has not supplied setup instructions'); expect(writeText).not.toHaveBeenCalled();
  });
});

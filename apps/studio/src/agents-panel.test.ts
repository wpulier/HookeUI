// The Agents panel (ADR 0029): every worker grouped Needs you, Working, Done; an outside agent's ask is answered only by
// the person's own button, sent with the panel's header; replies go as Hooke's notes; refusals say what to do.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentsPanel, agentGroup, agentSub, entryWords, inline, parseAgents, refusalWords, type AgentRow } from './agents-panel';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';

afterEach(() => { vi.unstubAllGlobals(); });

const row = (over: Partial<AgentRow> = {}): AgentRow => ({
  id: 'ag_1', program: 'codex', label: 'Codex', task: 'write a stomata-tracking capability', status: 'working',
  state: 'working', line: 'Ran a command: ls', project: 's1', projectName: 'Leaf', study: 'p' + 'a'.repeat(32),
  assignment: 'assignment:x', by: 'voice', at: 1, endedAt: null, model: 'gpt-6', billing: null, budgetUsd: null,
  costUsd: null, tokens: 1200, why: '', report: {}, asks: [], live: '',
  transcript: [{ n: 1, role: 'hooke', text: '[Hooke] write a stomata-tracking capability', at: 1 }], ...over,
});

function rig(responses: Array<{ ok: boolean; body: unknown }> = []) {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const next = responses.shift() ?? { ok: true, body: { ok: true } };
    return { ok: next.ok, json: async () => next.body } as Response;
  });
  const panel = new AgentsPanel({ request: request as unknown as typeof fetch });
  return { panel, calls, root: panel.root as unknown as TestElement };
}

const all = (root: TestElement, tag: string): TestElement[] => {
  const found: TestElement[] = [];
  const walk = (node: TestElement) => { if (node.tagName?.toLowerCase() === tag) found.push(node); node.children.forEach(walk); };
  walk(root); return found;
};
const button = (root: TestElement, words: string) => all(root, 'button').find((b) => b.textContent === words);

describe('the Agents panel', () => {
  it('reads only rows it understands', () => {
    expect(parseAgents({ agents: [row(), { id: 'x' }], programs: [{ id: 'codex', label: 'Codex', installed: true }, 7] }))
      .toEqual({ agents: [row()], programs: [{ id: 'codex', label: 'Codex', installed: true }] });
    expect(parseAgents({ type: 'agents' })).toBeNull();
  });

  it('says whose words each transcript line is: Hooke’s are never yours', () => {
    expect(entryWords({ n: 1, role: 'hooke', text: '[Hooke] use micro-sam', at: 1, how: 'steered' }))
      .toEqual({ who: 'Hooke, while it worked', text: 'use micro-sam' });
    expect(entryWords({ n: 2, role: 'agent', text: 'Done.', at: 2, final: true }).who).toBe('Answer');
    expect(agentSub(row({ costUsd: 0.314 }))).toBe('Codex · Ran a command: ls · about $0.31 by its own count');
    expect(entryWords({ n: 3, role: 'tool', text: 'Ran a command: hooke status --json', at: 3, state: 'done' }))
      .toEqual({ who: 'Ran', text: 'hooke status --json' });
    expect(entryWords({ n: 5, role: 'tool', text: 'Changed files: its folder/report.md', at: 5, state: 'done' }))
      .toEqual({ who: 'Changed', text: 'its folder/report.md' });
    expect(entryWords({ n: 4, role: 'note', text: 'You allowed it', at: 4 })).toEqual({ who: '', text: 'You allowed it' });
    vi.stubGlobal('document', syntheticDOM().document);
    expect(inline('Saved **report.md** as `artifact`').map((n) => (n as unknown as TestElement).tagName?.toLowerCase() ?? 'text'))
      .toEqual(['span', 'strong', 'span', 'code']);
    expect(inline('Read the [Hooke skill](/x/SKILL.md).').map((n) => (n as unknown as TestElement).textContent).join(''))
      .toBe('Read the Hooke skill.');
    expect(agentGroup(row({ status: 'failed' }))).toBe('done');
    expect(refusalWords({ says: 'Codex has ended.', next: 'start a new agent to continue' }, ''))
      .toBe('Codex has ended. Start a new agent to continue.');
  });

  it('groups workers and answers an ask only on the person’s button', async () => {
    const { panel, calls, root } = rig();
    const ask = { id: 'codex:0', title: 'Run a command', detail: 'pip install micro-sam', always: true };
    panel.message({ type: 'agents', agents: [row({ status: 'needs-you', asks: [ask], line: 'Asks to run a command' }),
      row({ id: 'ag_2', label: 'Grok Build', program: 'grok', status: 'done', line: 'Report returned for review' })],
      programs: [{ id: 'codex', label: 'Codex', installed: true }, { id: 'grok', label: 'Grok Build', installed: false, next: 'install it' }] });
    const groups = root.querySelectorAll('.agents-group').map((g) => g.dataset.group);
    expect(groups).toEqual(['needs', 'done']);
    expect(root.textContent).toContain('Codex · Asks to run a command');
    expect(root.textContent).toContain('pip install micro-sam');
    expect(calls).toEqual([]);  // shown, never answered on its own
    fire(button(root, 'Allow for this session')!, 'click'); await settleMicrotasks();
    expect(calls[0].url).toBe('/api/agents/ag_1/answer');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ permission: 'codex:0', answer: 'allow', always: true });
    expect((calls[0].init?.headers as Record<string, string>)['X-MicroscopeMind']).toBe('agents');
    expect(root.textContent).toContain('Sending your answer…');
    // Not installed: listed, not choosable.
    const options = all(root, 'option');
    expect(options.map((o) => o.textContent)).toEqual(['Codex', 'Grok Build (not installed)']);
  });

  it('starts an agent with the task, and says a refusal with what to do', async () => {
    const { panel, calls, root } = rig([{ ok: false, body: { ok: false, says: 'Codex isn’t installed on this Mac.', next: 'install Codex' } }]);
    panel.message({ type: 'agents', agents: [], programs: [{ id: 'codex', label: 'Codex', installed: true }] });
    const task = all(root, 'textarea')[0]; task.value = 'count the stomata';
    fire(all(root, 'form')[0], 'submit'); await settleMicrotasks(); await settleMicrotasks();
    expect(calls[0].url).toBe('/api/agents');
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({ program: 'codex', task: 'count the stomata' });
    expect(root.textContent).toContain('Not sent: Codex isn’t installed on this Mac. Install Codex.');
  });

  it('opens a peek with the reply box, sent as Hooke’s note', async () => {
    const { panel, calls, root } = rig([{ ok: true, body: { agent: { ...row(), transcript: [
      { n: 1, role: 'hooke', text: '[Hooke] write it', at: 1 }, { n: 2, role: 'tool', text: 'Ran a command: ls', at: 2, state: 'done' }] } } },
      { ok: true, body: { sent: 'steered' } }]);
    panel.message({ type: 'agents', agents: [row({ live: 'Writing the tracker' })], programs: [] });
    fire(root.querySelector('.agents-open')!, 'click'); await settleMicrotasks(); await settleMicrotasks();
    expect(calls[0].url).toBe('/api/agents/ag_1');
    expect(root.textContent).toContain('Ran a command: ls');
    expect(root.textContent).toContain('Writing the tracker');
    const input = all(root, 'input').find((i) => i.attributes['aria-label'] === 'Tell Codex')!;
    input.value = 'use micro-sam';
    fire(root.querySelector('.agents-reply')!, 'submit'); await settleMicrotasks(); await settleMicrotasks();
    expect(calls[1].url).toBe('/api/agents/ag_1/note');
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ text: 'use micro-sam' });
  });

  it('lists Hooke’s own researchers beside the outside agents', () => {
    const { panel, root } = rig();
    const opened: string[] = [];
    panel.configure({ researchers: () => [{ rid: 'r1', name: 'Researcher 1', title: 'Why stomata close', group: 'working', cell: 'working', words: 'Working' }],
      openResearcher: (rid) => opened.push(rid) });
    panel.message({ type: 'agents', agents: [row()], programs: [] });
    expect(root.querySelectorAll('.agents-row').length).toBe(2);
    expect(root.textContent).toContain('Hooke Researcher 1 · Working');
    fire(root.querySelector('.is-researcher')!.querySelector('.mm-row')!, 'click');
    expect(opened).toEqual(['r1']);
    expect(panel.counts()).toEqual({ working: 1, needs: 0 });
  });

  it('shows each waiting ask on the card over the composer, answered there too', async () => {
    const { panel, calls } = rig();
    const card = panel.asksRoot as unknown as TestElement;
    expect(card.hidden).toBe(true);
    const ask = { id: 'claude:1', title: 'Write a file', detail: 'its folder/report.md' };
    panel.message({ type: 'agents', agents: [row({ label: 'Claude Code', program: 'claude-code', status: 'needs-you', asks: [ask] })], programs: [] });
    expect(card.hidden).toBe(false);
    expect(card.textContent).toContain('Claude Code asks to write a file');
    expect(card.textContent).toContain('its folder/report.md');
    expect(card.textContent).toContain('Or say “allow it” or “no”.');
    expect(button(card, 'Allow for this session')).toBeUndefined();  // offered only when the program has a session rule
    fire(button(card, 'Decline')!, 'click'); await settleMicrotasks();
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ permission: 'claude:1', answer: 'deny', always: false });
    panel.message({ type: 'agents', agents: [row({ status: 'working' })], programs: [] });
    expect(card.hidden).toBe(true);
  });

  it('offers the report for review once the Study has it, and counts its time while it works', () => {
    const { panel, root } = rig();
    const opened: string[] = [];
    panel.configure({ openStudy: (id) => opened.push(id) });
    panel.message({ type: 'agents', agents: [row({ status: 'done', line: 'Report returned for review', report: { returned: true, claims: 1 } }),
      row({ id: 'ag_2', status: 'working', at: Date.now() / 1000 - 72 })], programs: [] });
    fire(button(root, 'Review its report and claim ›')!, 'click');
    expect(opened).toEqual(['p' + 'a'.repeat(32)]);
    expect(root.textContent).toMatch(/Working 1:1\d/);
  });
});


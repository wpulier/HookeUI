import { afterEach, describe, expect, it, vi } from 'vitest';
import { fire, settleMicrotasks, TestElement } from './workspace-windows.test-support';
import { maintenanceFacts, maintenanceNode, maintenanceView, POLICIES, readableTime, type MaintenanceMemory, type MaintenanceUpdate } from './maintenance-status';

const update = (over: Partial<MaintenanceUpdate>): MaintenanceUpdate => ({
  state: 'none', says: '', next: null,
  policy: { policy: 'manual', words: 'Update when I choose', effective: 'manual' },
  check: { state: 'ok', at: '2026-10-20T16:00:00' },
  active: { generation: 'a', version: '1', installedAt: '2026-10-19T10:00:00' }, candidate: null, held: [], ...over,
});

afterEach(() => vi.unstubAllGlobals());

describe('an entry’s maintenance on its card', () => {
  it('says the wire’s words and offers the one action the state names', () => {
    const view = maintenanceView(update({ state: 'ready-to-apply', says: 'Ready to apply: 2', next: 'apply' }));
    expect(view.says).toBe('Ready to apply: 2');
    expect(view.action).toBe('Apply');
    expect(maintenanceView(update({ state: 'applied', says: 'Updated to 2', next: 'undo' })).action).toBe('Undo');
    expect(maintenanceView(update({ state: 'preparing', says: 'Preparing update: …', next: 'stop' })).action).toBe('Stop');
    expect(maintenanceView(update({ state: 'pinned', says: 'Pinned to 1' })).action).toBeNull();
  });
  it('lists exactly what needs review', () => {
    const view = maintenanceView(update({ state: 'needs-review', says: 'Needs review: licence changed',
      review: [{ what: 'licence', kind: 'rights', from: 'MIT', to: 'GPL-3.0', says: 'licence changed from MIT to GPL-3.0' }] }));
    expect(view.review).toEqual(['licence changed from MIT to GPL-3.0']);
    expect(view.action).toBeNull();
  });
  it('offers the four policies with the recorded one chosen, Update when I choose by default', () => {
    expect(POLICIES.map((p) => p.label)).toEqual(['Update when I choose', 'Review updates',
      'Automatically maintain compatible updates', 'Pin this version']);
    const view = maintenanceView(update({ policy: { policy: 'auto', words: '', effective: 'review', less: 'you went back to 1' } }));
    expect(view.policies.filter((p) => p.chosen).map((p) => p.id)).toEqual(['auto']);
    expect(maintenanceView(update({})).policies[0].chosen).toBe(true);
  });
  it('keeps each date apart and names what holds a version', () => {
    expect(maintenanceFacts(update({
      active: { generation: 'b', version: '2', installedAt: 'T1', promotedAt: 'T2' },
      check: { state: 'failed', at: 'T3', why: 'no connection' },
      held: [{ generation: 'a', kind: 'recipe', owner: 'r1', at: '' }, { generation: 'a', kind: 'rollback', owner: 'b', at: '' }],
      policy: { policy: 'auto', words: '', effective: 'review', less: 'you undid the update to 2' },
    }))).toEqual(['Running 2, installed T1, made active T2', 'Last check failed T3: no connection', 'Kept for recipe r1',
      'For now it waits for you: you undid the update to 2']);
    expect(maintenanceFacts(update({ check: { state: 'never' } }))[1]).toBe('Not checked for updates yet');
  });
  it('offers Accept for what needs review and the merged version for yours, and says what is left', () => {
    expect(maintenanceView(update({ state: 'needs-review', says: 'Needs review: …', next: 'accept' })).action).toBe('Accept and continue');
    expect(maintenanceView(update({ state: 'ready-to-apply', says: 'Ready to apply: …', next: 'merge' })).action).toBe('Use the merged version');
    const facts = maintenanceFacts(update({ setup: 'Add your fal.ai key', conflicts: ['adapter.py'], derived: '/x',
      maintained: { state: 'unmaintained', why: 'removed from its catalog', since: 'T' },
      defect: { version: '1', why: 'halves areas', link: 'L', severity: 'warn' } }));
    expect(facts).toContain('Not maintained since T: removed from its catalog');
    expect(facts).toContain('Known problem in 1: halves areas (L)');
    expect(facts).toContain('Before it can run: Add your fal.ai key');
    expect(facts).toContain('Resolve adapter.py in /x');
  });
  it('sends back exactly the fingerprint of the review it shows when Accept is pressed', async () => {
    vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
    const accept = vi.fn(async () => ({ says: 'Accepted' }));
    const none = vi.fn(async () => ({}));
    const box = maintenanceNode('dark-spots', update({ state: 'needs-review', says: 'Needs review: …', next: 'accept',
      fingerprint: 'g2:licence' }), { policy: none, check: none, apply: none, undo: none, stop: none, accept, merge: none });
    const button = (box as unknown as TestElement).querySelectorAll('button').find((b) => b.textContent === 'Accept and continue');
    fire(button!, 'click');
    await settleMicrotasks();
    expect(accept).toHaveBeenCalledWith('dark-spots', 'g2:licence');
  });
  it('draws nothing for an entry maintenance doesn’t cover', () => {
    expect(maintenanceNode('x', undefined, undefined)).toBeNull();
  });
});


it('Apply carries the exact workflow receipt shown on the card', async () => {
  vi.stubGlobal('document',{createElement:(tag:string)=>new TestElement(tag)});
  const apply=vi.fn(async()=>({})), none=vi.fn(async()=>({}));
  const receipt={generation:'b'.repeat(16),digest:'c'.repeat(64)};
  const box=maintenanceNode('interactive',update({state:'ready-to-apply',next:'apply',
    candidate:{...receipt,version:'2',workflow:true}}),
    {apply,policy:none,check:none,undo:none,stop:none,accept:none,merge:none});
  fire((box as unknown as TestElement).querySelectorAll('button').find(b=>b.textContent==='Apply')!,'click');
  await settleMicrotasks();expect(apply).toHaveBeenCalledWith('interactive',receipt);
});


// Ticket 67, fix 5: Details → Updates said nothing after a check, closed itself when the page drew again, showed ISO
// times, and a built-in model had no update state at all.
describe('Details → Updates', () => {
  const none = vi.fn(async () => ({}));
  const commands = { policy: none, check: none, apply: none, undo: none, stop: none, accept: none, merge: none };
  it('says its times as people read them, never ISO', () => {
    expect(readableTime('2026-10-27T23:42:00')).toBe('Oct 27, 11:42 pm');
    expect(readableTime('2026-10-28T00:05:09')).toBe('Oct 28, 12:05 am');
    expect(readableTime('2026-10-28T12:30:00')).toBe('Oct 28, 12:30 pm');
    expect(readableTime('T1')).toBe('T1');
    expect(maintenanceFacts(update({}))).toEqual(['Running 1, installed Oct 19, 10:00 am', 'Last checked Oct 20, 4:00 pm']);
  });
  it('stays open after Check and keeps its last reply when the page draws it again', async () => {
    vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
    const memory = new Map<string, MaintenanceMemory>();
    const check = vi.fn(async () => ({ says: 'No newer version: it is up to date' }));
    const first = maintenanceNode('m', update({ says: 'Up to date · checked Oct 20, 4:00 pm' }), { ...commands, check }, memory) as unknown as TestElement;
    const details = first.querySelector('details')!;
    Object.assign(details, { open: true }); fire(details, 'toggle');
    fire(first.querySelectorAll('button').find(b => b.textContent === 'Check for an update')!, 'click');
    await settleMicrotasks();
    const again = maintenanceNode('m', update({ says: 'Up to date · checked Oct 20, 4:05 pm' }), { ...commands, check }, memory) as unknown as TestElement;
    expect((again.querySelector('details') as unknown as { open: boolean }).open).toBe(true);
    expect(again.textContent).toContain('No newer version: it is up to date');
    expect(again.textContent).toContain('Up to date · checked Oct 20, 4:05 pm');
  });
  it('a built-in model says it is updated with Hooke, with no policy or check of its own', () => {
    vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
    const box = maintenanceNode('cellpose-sam', { state: 'none', says: 'Updated with Hooke 0.5.2', next: null, builtIn: true, version: '4.0' },
      undefined) as unknown as TestElement;
    expect(box.textContent).toContain('Updated with Hooke 0.5.2');
    expect(box.querySelector('summary')?.textContent).toBe('Updates');
    expect(box.querySelectorAll('select')).toHaveLength(0);
    expect(box.querySelectorAll('button')).toHaveLength(0);
  });
});

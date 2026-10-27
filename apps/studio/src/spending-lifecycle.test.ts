// The Spending menu's state across saves, reads and closing (Codex's recheck of e38fc57b): the actual component over a
// synthetic DOM, its reads and saves controlled by the test. No browser, ledger or provider.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { SpendingMeter, type Spending } from './spending';
import { TestElement } from './workspace-windows.test-support';

class Element extends TestElement { focus() { (globalThis.document as unknown as { activeElement: unknown }).activeElement = this; } }
let poll: () => void = () => undefined;
beforeEach(() => {
  vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag), activeElement: null });
  vi.stubGlobal('window', { addEventListener() {}, innerWidth: 1200, innerHeight: 800,
    setInterval(fn: () => void) { poll = fn; return 1; }, clearInterval() {} });
});
afterEach(() => vi.unstubAllGlobals());

const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const wire = (cap: number | null): Spending => ({ day: '2026-10-13', cap: null, estimatedUsd: 0, trackingSince: 1791800000,
  unpriced: 0, recordingErrors: 0, entries: [], month: { month: '2026-10', estimatedUsd: 0, unknownCalls: 0, inFlightUsd: 0,
    cap, entries: [] } });
type Node = { textContent: string; onclick?: () => void; value?: string; oninput?: () => void;
  onsubmit?: (e: { preventDefault(): void }) => void };
const find = (meter: SpendingMeter, selector: string) => (meter.root as unknown as { querySelector(s: string): Node | null })
  .querySelector(selector);
const all = (meter: SpendingMeter, selector: string) => (meter.root as unknown as { querySelectorAll(s: string): Node[] })
  .querySelectorAll(selector);
const press = (meter: SpendingMeter, text: string) => {
  const button = all(meter, 'button').find(b => b.textContent === text);
  if (!button) throw new Error(`no ${text} button`);
  button.onclick!();
};
const draft = (meter: SpendingMeter, value: string) => {
  press(meter, 'Set a monthly limit');
  const input = find(meter, 'input')!;
  input.value = value;
  input.oninput!();
};
const submit = (meter: SpendingMeter) => find(meter, 'form')!.onsubmit!({ preventDefault() {} });
const shown = (meter: SpendingMeter) => ({ headline: find(meter, '.spending-headline')?.textContent,
  saved: find(meter, '.spending-saved')?.textContent ?? null, form: find(meter, 'form') !== null });

/** A stand-in for the app: its limit changes only when a save is confirmed; reads may be held back by the test. */
function server(cap: number | null) {
  const state = { cap, held: [] as ((data: Spending) => void)[], hold: false, fail: false, saves: 0 };
  const load = () => {
    if (state.fail) return Promise.reject(new Error('offline'));
    const now = wire(state.cap);  // what the app has at the moment the read is answered
    return state.hold ? new Promise<Spending>(r => { state.held.push(r); }) : Promise.resolve(now);
  };
  const setCap = async (usd: number | null) => { state.saves++; state.cap = usd; return { ok: true, month: { ...wire(usd).month } }; };
  return { state, load, setCap };
}

test('personal API use stays readable while limit accounting is available in supporting detail', async () => {
  const data: Spending = { ...wire(20), cap: 2, recordingErrors: 1,
    month: { ...wire(20).month!, estimatedUsd: 3.62, inFlightUsd: 0.6, unknownCalls: 1, unknownEachUsd: 0.02,
      entries: [{ provider: 'OpenAI', model: 'GPT-Live', area: 'Voice', events: 4, estimatedUsd: 3,
        unpriced: 1, basis: 'Reported tokens', units: 50 }] } };
  const meter = new SpendingMeter(async () => data, async () => undefined);
  press(meter, 'Spending');
  await tick();
  const root = meter.root as unknown as TestElement;
  const supporting = root.querySelector('.spending-details');
  expect(supporting?.tagName).toBe('DETAILS');
  expect(supporting?.querySelector('summary')?.textContent).toBe('How this is counted');
  expect(supporting?.textContent).toContain('$0.60 held for work in progress');
  expect(root.querySelector('.spending-limit-area')?.textContent).not.toContain('held for work in progress');
  expect(root.querySelector('.spending-headline')?.textContent).toBe('Monthly limit: $20.00');
  expect(root.querySelector('.spending-research')?.textContent).toContain('$2.00 a day, for researchers only');
  expect(root.querySelectorAll('div').some(node => node.getAttribute('role') === 'meter')).toBe(false);
  expect(root.querySelector('.spending-total')?.textContent).toContain('$3.00');
  expect(root.querySelector('.spending-total')?.textContent).toContain('plus 1 record of unknown cost');
  expect(root.querySelector('.spending-provider')?.textContent).toContain('GPT-Live');
  expect(root.querySelector('.spending-analytics')?.textContent).toContain('Some usage could not be saved');
  expect(root.querySelector('.spending-context')?.textContent).toContain('stored on this Mac');
  expect(root.querySelector('.spending-context')?.textContent).toContain('requests go to');
  expect(supporting?.textContent).toContain('Local computation has no API charge');
});

test('a reached limit explains an in-progress hold even when no API cost has been recorded yet', async () => {
  const data: Spending = { ...wire(20),
    month: { ...wire(20).month!, estimatedUsd: 20, inFlightUsd: 20 } };
  const meter = new SpendingMeter(async () => data, async () => undefined);
  press(meter, 'Spending');
  await tick();
  const root = meter.root as unknown as TestElement;
  const limit = root.querySelector('.spending-limit-area');
  expect(limit?.textContent).toContain('Limit reached');
  expect(limit?.textContent).toContain('$20.00 held for work in progress');
  expect(root.querySelector('.spending-total')?.textContent).toContain('$0.00');
});

test('a confirmed save is what the limit says at once, and a read begun before it is never drawn', async () => {
  const app = server(null);
  const meter = new SpendingMeter(app.load, app.setCap);
  press(meter, 'Spending');
  await tick();
  draft(meter, '20');
  app.state.hold = true;
  poll();  // a read begins before the save: it was answered with the old limit
  const stale = wire(null);
  app.state.hold = false;
  submit(meter);
  await tick();
  expect(shown(meter)).toEqual({ headline: 'Monthly limit: $20.00', saved: 'Saved: monthly limit $20.00.', form: false });
  app.state.held[0](stale);  // that old answer arrives after the save
  await tick();
  expect(shown(meter).headline).toBe('Monthly limit: $20.00');
});

test('a confirmed save stands when the totals can\u2019t be read again, and the totals say so', async () => {
  const app = server(null);
  const meter = new SpendingMeter(app.load, async usd => { await app.setCap(usd); app.state.fail = true; return undefined; });
  press(meter, 'Spending');
  await tick();
  draft(meter, '20');
  submit(meter);
  await tick();
  expect(shown(meter)).toEqual({ headline: 'Monthly limit: $20.00', saved: 'Saved: monthly limit $20.00.', form: false });
  expect(find(meter, '.spending-error')?.textContent).toBe('The totals could not be loaded just now.');
});

test('a confirmed limit change keeps the server\u2019s stopped-work and billing facts through polling', async () => {
  const app = server(null);
  const says = 'Monthly cap on cloud models: $5.00. 2 active researcher(s) stopped; findings kept. Already-sent calls may still be billed';
  let finish: () => void = () => undefined;
  const meter = new SpendingMeter(app.load, usd => new Promise(resolve => {
    finish = async () => resolve({ ...await app.setCap(usd), says });
  }));
  press(meter, 'Spending');
  await tick();
  draft(meter, '5'); submit(meter);
  expect(shown(meter).headline).toBe('Limit not set');
  expect(shown(meter).saved).toBeNull();
  finish(); await tick();
  expect(shown(meter)).toEqual({ headline: 'Monthly limit: $5.00', saved: `Saved: ${says}`, form: false });
  poll(); await tick();
  expect(shown(meter).saved).toBe(`Saved: ${says}`);
});

test('the app\u2019s own confirmation is what is shown: removal too', async () => {
  const app = server(20);
  const meter = new SpendingMeter(app.load, app.setCap);
  press(meter, 'Spending');
  await tick();
  press(meter, 'Remove limit');
  press(meter, 'Remove');
  await tick();
  expect(shown(meter)).toEqual({ headline: 'Limit not set', saved: 'Saved: no monthly limit.', form: false });
});

test('a failed save keeps the confirmed limit and says why, the draft kept', async () => {
  const meter = new SpendingMeter(async () => wire(null), async () => { throw new Error('the app is not reachable'); });
  press(meter, 'Spending');
  await tick();
  draft(meter, '20');
  submit(meter);
  await tick();
  expect(shown(meter).headline).toBe('Limit not set');
  expect(find(meter, '.spending-error')?.textContent).toBe('Not saved: the app is not reachable. Try again.');
  expect(find(meter, 'input')?.value).toBe('20');
});

test('a draft let go by closing never comes back as a form that does nothing', async () => {
  const app = server(null);
  const meter = new SpendingMeter(app.load, app.setCap);
  press(meter, 'Spending');
  await tick();
  draft(meter, '20');
  meter.close();
  press(meter, 'Spending');
  await tick();
  expect(shown(meter)).toEqual({ headline: 'Limit not set', saved: null, form: false });
  draft(meter, '15');  // a new draft saves
  submit(meter);
  await tick();
  expect(app.state.saves).toBe(1);
  expect(shown(meter).headline).toBe('Monthly limit: $15.00');
});

test('a removal asked and closed is let go too; one being saved finishes while closed', async () => {
  const app = server(20);
  let finish: () => void = () => undefined;
  const meter = new SpendingMeter(app.load, usd => new Promise(r => { finish = () => r(app.setCap(usd)); }));
  press(meter, 'Spending');
  await tick();
  press(meter, 'Remove limit');
  meter.close();
  press(meter, 'Spending');
  await tick();
  expect(all(meter, 'button').some(b => b.textContent === 'Keep it')).toBe(false);
  press(meter, 'Remove limit');
  press(meter, 'Remove');
  meter.close();
  finish();
  await tick();
  press(meter, 'Spending');
  await tick();
  expect(shown(meter).headline).toBe('Limit not set');
  expect(app.state.saves).toBe(1);
});

test('the limit field is named by its label alone: the $ beside it is not read as part of its name', async () => {
  const app = server(null);
  const meter = new SpendingMeter(app.load, app.setCap);
  press(meter, 'Spending');
  await tick();
  draft(meter, '5');
  const currency = find(meter, '.spending-currency') as unknown as { getAttribute(name: string): string | null };
  expect(currency.getAttribute('aria-hidden')).toBe('true');
  expect(find(meter, 'label')?.textContent?.startsWith('Monthly limit, in dollars')).toBe(true);
});

test('the strip\'s Spending button says today\'s amount once there is one, read on load and when work ends, never on a timer while closed', async () => {
  let entries: Spending['entries'] = [];
  const load = vi.fn(async () => ({ ...wire(null), entries }));
  const meter = new SpendingMeter(load);
  const button = () => all(meter, 'button').find(b => (b as unknown as { className: string }).className.includes('spending-button'))!;
  expect(button().textContent).toBe('Spending');
  await meter.refreshTotal(); await tick();
  expect(button().textContent).toBe('Spending'); // nothing spent today
  entries = [{ provider: 'OpenAI', model: 'gpt-6.1-sol', area: 'Research', estimatedUsd: 0.2, unpriced: 0, events: 3, basis: 'list rates' } as unknown as Spending['entries'][number]];
  await meter.refreshTotal(); await tick();
  expect(button().textContent).toBe('$0.20 today'); // the panel's Today total, from the same data
  expect(load).toHaveBeenCalledTimes(2); // only when asked: no polling while closed
});

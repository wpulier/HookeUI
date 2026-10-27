import { afterEach, describe, expect, it, vi } from 'vitest';
import { cardAction, countsWords, newsByline, stepWords, shelfCard, type NewItem, type ShelfCard } from './capabilityshelf';

const card = (over: Partial<ShelfCard>): ShelfCard => ({
  id: 'cells', title: 'Find every cell', what: '', colour: 'red', word: 'Off: Download 1.2 GB', using: '', privacy: 'Runs on this Mac',
  cost: 'Free', service: '', tryOption: 'a', try: 'outline every cell', options: [],
  turnOn: { option: 'a', label: 'A', model: 'm', steps: [{ kind: 'download', says: 'Download 1.2 GB', by: 'app' }] }, ...over,
});

describe('what it can do', () => {
  it('has one button per colour: Turn on, its progress, Try it, or none', () => {
    expect(cardAction(card({}))).toBe('turn-on');
    expect(cardAction(card({ colour: 'yellow', turnOn: null }))).toBe('turning');
    expect(cardAction(card({ turning: { job: 'cells', option: 'a', label: 'A', model: 'm', state: 'waiting', says: 'Add your key' } }))).toBe('turning');
    expect(cardAction(card({ colour: 'green', turnOn: null }))).toBe('try');
    expect(cardAction(card({ colour: 'green', turnOn: null, try: '' }))).toBe('try');
    expect(cardAction(card({ colour: 'grey', turnOn: null }))).toBe('none');
  });
  it('says whose each step is, and which one waits for you now', () => {
    const key = { kind: 'key', says: 'Add your fal.ai key', service: 'fal', by: 'you' as const };
    expect(stepWords(key, { job: 'x', option: 'a', label: '', model: '', state: 'waiting', says: '', step: key }))
      .toEqual({ words: 'Add your fal.ai key', whose: 'You do this', now: true });
    expect(stepWords({ kind: 'download', says: 'Download', by: 'app' }).whose).toBe('The app does this');
  });
  it('counts what is on and what is a press away, never what cannot run here', () => {
    expect(countsWords({ green: 3, yellow: 1, red: 4, grey: 2 })).toBe('3 on · 1 on its way · 4 one press away');
    expect(countsWords({ green: 0, yellow: 0, red: 0, grey: 5 })).toBe('Nothing on yet');
  });
  it('says who added a new one and whether it was reviewed', () => {
    const n = { by: 'Maya Okafor', makers: '', badge: 'reviewed', channel: '' } as NewItem;
    expect(newsByline(n)).toBe('Added by Maya Okafor · reviewed');
    expect(newsByline({ ...n, by: '', makers: 'Lab X', badge: 'community', channel: 'maya' }))
      .toBe('By Lab X · community catalog maya, not reviewed here');
  });
});


it('a proven click-only capability sends the exact Try option instead of an empty chat request', async () => {
  const { TestElement, fire } = await import('./workspace-windows.test-support');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const tryIt = vi.fn(async () => undefined);
  const node = shelfCard(card({ id: 'outline', colour: 'green', try: '', tryOption: 'proved-alternative', using: '' }), undefined,
    { turnOn: async () => {}, cancel: async () => {}, tryIt, open: () => {}, seen: async () => {}, dismiss: async () => {}, get: async () => {} }) as unknown as InstanceType<typeof TestElement>;
  const button = node.querySelectorAll('button').find((b) => b.textContent === 'Try it')!;
  expect(button).toBeDefined(); fire(button, 'click'); await Promise.resolve();
  expect(tryIt).toHaveBeenCalledExactlyOnceWith('outline', 'proved-alternative');
});
afterEach(() => vi.unstubAllGlobals());

it('Allow code uses the channel identity even when its visible label changes', async () => {
  const { TestElement, fire } = await import('./workspace-windows.test-support');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const trust = vi.fn(async () => undefined);
  const node = shelfCard(card({ turnOn: { option: 'mine-x', label: 'X', model: 'lab/x', steps: [
    { kind: 'trust', channel: 'lab-identity', says: 'Allow code from The Friendly Lab', by: 'you' }] } }), undefined,
    { turnOn: async () => {}, cancel: async () => {}, tryIt: async () => {}, open: () => {},
      seen: async () => {}, dismiss: async () => {}, get: async () => {}, trust }) as unknown as InstanceType<typeof TestElement>;
  fire(node.querySelectorAll('button').find((b) => b.textContent === 'Allow code from The Friendly Lab')!, 'click');
  await Promise.resolve(); expect(trust).toHaveBeenCalledExactlyOnceWith('lab-identity');
});

it('submits the exact reviewed plan and displays its destination before Turn on', async () => {
  const { TestElement, fire } = await import('./workspace-windows.test-support');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const plan = { option: 'cloud', label: 'Cloud', model: 'cloud', privacy: 'Sends pictures to Provider', cost: 'Paid per call', steps: [] };
  const turnOn = vi.fn(async () => {});
  const node = shelfCard(card({ privacy: 'Runs on this Mac', cost: 'Free', turnOn: plan }), undefined,
    { turnOn, cancel: async () => {}, tryIt: async () => {}, open: () => {}, seen: async () => {}, dismiss: async () => {}, get: async () => {} }) as unknown as InstanceType<typeof TestElement>;
  expect(node.textContent).toContain('Sends pictures to Provider');
  expect(node.textContent).toContain('Paid per call');
  fire(node.querySelectorAll('button').find((b) => b.textContent === 'Turn on')!, 'click'); await Promise.resolve();
  expect(turnOn).toHaveBeenCalledExactlyOnceWith('cells', plan);
});

it('Get it submits the commit and content offer that this card displayed', async () => {
  const { newsSection } = await import('./capabilityshelf');
  const { TestElement, fire } = await import('./workspace-windows.test-support');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const get = vi.fn(async () => {});
  const item: NewItem = { key: 'test', name: 'Test', what: 'Segments', kind: 'model', tags: [], by: 'Maker', byLink: '',
    makers: '', link: '', badge: 'reviewed', channel: 'reviewed', trusted: true, example: '', relevant: true,
    arrived: '2026-10-13', fetched: false, commit: 'reviewed-commit', offer: 'reviewed-content' };
  const node = newsSection({ new: [item], last: {}, automatic: true },
    { turnOn: async () => {}, cancel: async () => {}, tryIt: async () => {}, open: () => {},
      seen: async () => {}, dismiss: async () => {}, get }) as unknown as InstanceType<typeof TestElement>;
  fire(node.querySelectorAll('button').find((b) => b.textContent === 'Get it')!, 'click'); await Promise.resolve();
  expect(get).toHaveBeenCalledExactlyOnceWith('test', 'reviewed-commit', 'reviewed-content');
});

it('a changed setup requiring review displays the new plan terms before a fresh press', async () => {
  const { TestElement } = await import('./workspace-windows.test-support');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const node = shelfCard(card({ privacy: 'Runs on this Mac', cost: 'Free',
    turning: { job: 'cells', option: 'a', label: 'Old', model: 'm', state: 'failed', says: 'Setup changed; review it',
      privacy: 'Runs on this Mac', cost: 'Free' },
    turnOn: { option: 'a', label: 'New', model: 'm', steps: [], privacy: 'Sends pictures to New Provider', cost: 'Paid per call' } }), undefined,
    { turnOn: async () => {}, cancel: async () => {}, tryIt: async () => {}, open: () => {},
      seen: async () => {}, dismiss: async () => {}, get: async () => {} }) as unknown as InstanceType<typeof TestElement>;
  expect(node.textContent).toContain('Sends pictures to New Provider');
  expect(node.textContent).toContain('Paid per call');
});

it('shows when an update arrived and every declared requirement before Get it', async () => {
  const { TestElement } = await import('./workspace-windows.test-support');
  const { newsSection } = await import('./capabilityshelf');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const n = { key: 'x', name: 'X', what: 'Finds cells', kind: 'model', tags: [], by: 'Maya', byLink: '', makers: '',
    link: '', badge: 'reviewed' as const, channel: 'reviewed', trusted: true, example: '', relevant: true,
    arrived: '2026-10-13T12:00:00', fetched: false, update: true, version: '2', requirements: ['Download 2 GB', 'Runs on this Mac'] };
  const news = newsSection({ new: [n], last: {}, automatic: true }, { turnOn: async () => {}, cancel: async () => {},
    tryIt: async () => {}, open: () => {}, seen: async () => {}, dismiss: async () => {}, get: async () => {} })!;
  expect(news.textContent).toContain('2026-10-13'); expect(news.textContent).toContain('Download 2 GB');
  expect(news.textContent).toContain('Update available');
});

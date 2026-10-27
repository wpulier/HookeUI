import { afterEach, expect, test, vi } from 'vitest';
import { spaceSection } from './capabilityspace';
import { TestElement, fire } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());

test('Space lists shared runtime copies and removes the reviewed revision before refreshing', async () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const item = { id: 'runtime-a', name: 'Shared files', kind: 'files', models: ['outline', 'follow'], bytes: 12,
    revision: 'reviewed-revision', generations: 1 };
  const list = vi.fn().mockResolvedValueOnce({ items: [item], bytes: 12, preserved: 'Your work stays.' })
    .mockResolvedValueOnce({ items: [], bytes: 0, preserved: 'Your work stays.' });
  const remove = vi.fn(async () => undefined);
  const root = spaceSection({ list, remove }) as unknown as TestElement;
  fire(root.querySelectorAll('button')[0], 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(root.textContent).toContain('outline, follow');
  expect(root.textContent).toContain('Your work stays.');
  // A row of its own (workspace-feel.css), never a Models card, whose card rules only fit the grid.
  expect(root.querySelectorAll('.capability-space-item')).toHaveLength(1);
  expect(root.querySelector('.model-card')).toBeNull();
  fire(root.querySelectorAll('button').find((b) => b.textContent === 'Remove runtime copies')!, 'click');
  expect(remove).not.toHaveBeenCalled();  // confirmed first
  fire(root.querySelectorAll('button').find((b) => b.textContent === 'Remove and free up to 12 bytes')!, 'click');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  expect(remove).toHaveBeenCalledExactlyOnceWith('runtime-a', 'reviewed-revision');
  expect(list).toHaveBeenCalledTimes(2);
  expect(root.textContent).not.toContain('Shared files ·');  // gone from the list
  expect(root.textContent).toContain('Removed Shared files.');  // and the removal is said
});

test('Space explains retained software before offering any removal', async () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const remove = vi.fn();
  const root = spaceSection({ remove, list: async () => ({ bytes: 12, preserved: 'Your work stays.', items: [{
    id: 'saved-runtime', name: 'Scientific host', kind: 'environment', models: ['host'], bytes: 12,
    revision: 'one', generations: 1, retention: { reason: 'Kept for 2 saved scientific documents.',
      next: 'Turn off the capability instead of removing its runtime.' },
  }] }) }) as unknown as TestElement;
  fire(root.querySelectorAll('button')[0], 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(root.textContent).toContain('Kept for 2 saved scientific documents.');
  expect(root.textContent).toContain('Turn off the capability');
  expect(root.querySelectorAll('button').find(b => b.textContent === 'Kept for saved work')?.disabled).toBe(true);
  expect(remove).not.toHaveBeenCalled();
});

test('Space retains new server refusal and recovery instructions beside the reviewed storage', async () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const remove = vi.fn().mockRejectedValue(Object.assign(new Error('This runtime is now kept for saved scientific work.'), {
    reply: { next: 'Turn off the capability instead of removing its runtime.' },
  }));
  const root = spaceSection({ remove, list: async () => ({ bytes: 12, preserved: 'Your work stays.', items: [{
    id: 'runtime', name: 'Scientific host', kind: 'environment', models: ['host'], bytes: 12,
    revision: 'reviewed', generations: 1,
  }] }) }) as unknown as TestElement;
  fire(root.querySelectorAll('button')[0], 'click');
  await Promise.resolve(); await Promise.resolve();
  const button = root.querySelectorAll('button').find(b => b.textContent === 'Remove runtime copies')!;
  fire(button, 'click');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Remove and free up to 12 bytes')!, 'click');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  expect(remove).toHaveBeenCalledExactlyOnceWith('runtime', 'reviewed');
  expect(root.textContent).toContain('now kept for saved scientific work.');
  expect(root.textContent).toContain('Turn off the capability instead');
  expect(root.textContent).toContain('Scientific host');
  expect(button.disabled).toBe(false);
});

test('Space offers unfinished cleanup separately from removing a working installation', async () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const remove = vi.fn(async () => undefined);
  const root = spaceSection({ remove, list: async () => ({ bytes: 12, preserved: 'Your work stays.', items: [{
    id: 'withdrawn', name: 'Downloaded files', kind: 'files', models: ['host'], bytes: 12,
    revision: 'partial-cleanup', generations: 1, pendingRemoval: 1,
  }] }) }) as unknown as TestElement;
  fire(root.querySelectorAll('button')[0], 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(root.textContent).toContain('currently installed versions and saved work stay');
  expect(root.textContent).not.toContain('need setup again');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Finish removal')!, 'click');
  await Promise.resolve();
  expect(remove).toHaveBeenCalledExactlyOnceWith('withdrawn', 'partial-cleanup');
});

test('Space confirms what a removal frees, can be cancelled, and keeps the reply said after the list refreshes', async () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const item = { id: 'spot', name: 'Isolated Python: Spot shape', kind: 'environment', models: ['spot-shape'],
    bytes: 97880788, revision: 'r1', generations: 1,
    says: 'Remove frees 93.3 MB. Spot shape then needs Install again before it runs. Saved results, the entries and their original proof inputs stay.' };
  const kept = { id: 'measure', name: 'Isolated Python: Spot measure', kind: 'environment', models: ['spot-measure'],
    bytes: 97878342, revision: 'r2', generations: 1, says: 'Kept: 93.3 MB stays on this Mac because saved work uses it.',
    retention: { reason: 'This runtime is kept for 1 saved scientific document.', next: 'Turn off the capability instead.' } };
  const before = { items: [item, kept], bytes: 195759130, preserved: 'Your Projects stay in ~/MicroscopeMind.',
    says: '186.7 MB in managed copies: 93.3 MB can be freed here, and 93.3 MB is kept because saved work uses it.' };
  const after = { items: [kept], bytes: 97878342, preserved: before.preserved,
    says: '93.3 MB in managed copies: 0 bytes can be freed here, and 93.3 MB is kept because saved work uses it.' };
  const list = vi.fn().mockResolvedValueOnce(before).mockResolvedValueOnce(after);
  const remove = vi.fn(async () => ({ ok: true, freedBytes: 97880788,
    says: 'Removed Isolated Python: Spot shape, freeing 93.3 MB. Spot shape needs Install again before it runs.' }));
  const root = spaceSection({ list, remove }) as unknown as TestElement;
  fire(root.querySelectorAll('button')[0], 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(root.textContent).toContain('93.3 MB can be freed here, and 93.3 MB is kept because saved work uses it.');
  expect(root.textContent).toContain('Spot shape then needs Install again');
  const start = root.querySelectorAll('button').find(b => b.textContent === 'Remove runtime copies')!;
  fire(start, 'click');
  expect(root.textContent).toContain('Remove Isolated Python: Spot shape (up to 93.3 MB)?');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Cancel')!, 'click');
  expect(start.disabled).toBe(false);
  expect(remove).not.toHaveBeenCalled();
  fire(start, 'click');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Remove and free up to 93.3 MB')!, 'click');
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
  expect(remove).toHaveBeenCalledExactlyOnceWith('spot', 'r1');
  expect(list).toHaveBeenCalledTimes(2);
  expect(root.textContent).toContain('Removed Isolated Python: Spot shape, freeing 93.3 MB.');
  expect(root.textContent).toContain('0 bytes can be freed here');
  expect(root.textContent).not.toContain('Remove frees 93.3 MB');
});

test('Space says when the saved-work check could not run instead of calling the copy kept', async () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const remove = vi.fn();
  const root = spaceSection({ remove, list: async () => ({ bytes: 12, preserved: 'Your work stays.',
    says: "12 bytes in managed copies: nothing can be freed here and 12 bytes can't be removed now because the check of saved work could not run.",
    items: [{ id: 'unknown', name: 'Scientific host', kind: 'environment', models: ['host'], bytes: 12, revision: 'one',
      generations: 1, says: 'Not removable now: whether saved work uses these files (12 bytes) could not be checked.',
      retention: { reason: 'Saved runtime associations are unreadable; removal is blocked', next: 'Restore access.', checked: false } }] }) }) as unknown as TestElement;
  fire(root.querySelectorAll('button')[0], 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(root.textContent).toContain('could not be checked');
  expect(root.textContent).not.toContain('Kept for saved work');
  expect(root.querySelectorAll('button').find(b => b.textContent === 'Saved work not checked')?.disabled).toBe(true);
  expect(remove).not.toHaveBeenCalled();
});

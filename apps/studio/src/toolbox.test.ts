// Synthetic navigation seam: old links keep their exact hash while both pages belong to one Toolbox.
import { expect, test } from 'vitest';
import { fijiWords, readinessSaid, toolboxTarget } from './toolbox';

test('routes the catalog and complete atlas as two tabs, retaining every legacy deep link', () => {
  expect(toolboxTarget('#toolbox')).toEqual({ tab: 'tools', id: null });
  expect(toolboxTarget('#toolbox/all')).toEqual({ tab: 'all', id: null });
  expect(toolboxTarget('#models/mine/cells')).toEqual({ tab: 'tools', id: 'mine/cells' });
  expect(toolboxTarget('#models')).toEqual({ tab: 'tools', id: null });
  expect(toolboxTarget('#capabilities')).toEqual({ tab: 'all', id: null });
  expect(toolboxTarget('#toolbox/mine/cells')).toEqual({ tab: 'tools', id: 'mine/cells' });
  expect(toolboxTarget('#toolbox/space')).toEqual({ tab: 'tools', id: 'space' });
  expect(toolboxTarget('#assets')).toBeNull();
});

test('Fiji is said in two honest lines: connected and ready only from each row, and what only installs', () => {
  const counts = { listed: 555, routes: { 'ships-with-fiji': 133, 'update-site': 76, 'none-known': 346 }, bound: [{ id: 'a', name: 'A' }] };
  const listed = '555 extensions listed: 209 install in Fiji, 346 have no install route yet';
  // No Fiji set up here: never said to run.
  expect(fijiWords(counts, [{ id: 'a', status: 'needs-setup' }, { id: 'fiji', status: 'needs-setup' }]))
    .toEqual({ runs: '1 Fiji function connected · set up Fiji to use it', listed });
  expect(fijiWords(counts, undefined).runs).toBe('1 Fiji function connected · set up Fiji to use it'); // no rows yet: not ready
  // Fiji set up, the function not proven on it.
  expect(fijiWords(counts, [{ id: 'a', status: 'needs-setup' }, { id: 'fiji', status: 'ready' }]).runs)
    .toBe('1 Fiji function connected · not ready on this Mac');
  // Its row ready.
  expect(fijiWords(counts, [{ id: 'a', status: 'ready' }, { id: 'fiji', status: 'ready' }]).runs).toBe('1 Fiji function connected · 1 ready here');
  expect(fijiWords({ listed: 3, routes: { 'update-site': 3 } }, []).runs).toBe('No Fiji function connected yet');
});

test('encoded scientific package setup routes preserve the exact owner identity', () => {
  expect(toolboxTarget('#models/mine%2Fome-zarr-reader')).toEqual({tab: 'tools', id: 'mine/ome-zarr-reader'});
  expect(toolboxTarget('#toolbox/bundle-123%2Freader')).toEqual({tab: 'tools', id: 'bundle-123/reader'});
  for (const hash of ['#models/%', '#models/%GG', '#models/%252F', '#models/mine%0Areader', '#models/mine%3Freader'])
    expect(toolboxTarget(hash)).toBeNull();
});

test('readinessSaid says each status in one sentence, needs setup never as not available (readiness.said)', () => {
  {
    expect(readinessSaid({ status: 'ready', word: 'Ready', next: null })).toBe('Ready');
    expect(readinessSaid({ status: 'needs-setup', word: 'Download 1.2 GB', next: null }, 'not downloaded yet: 1.2 GB'))
      .toBe('Needs setup: Download 1.2 GB (not downloaded yet: 1.2 GB)');
    expect(readinessSaid({ status: 'unavailable', word: 'Not on this Mac', next: null }, 'it runs on a Mac with Apple Silicon'))
      .toBe('Unavailable here: Not on this Mac (it runs on a Mac with Apple Silicon)');
    expect(readinessSaid({ status: 'broken', word: 'Broken', next: { kind: 'check', says: 'Its last check failed: it answered nothing' } }))
      .toBe('Broken: Its last check failed: it answered nothing');
    expect(readinessSaid(null, 'not wired in this build')).toBe('Unavailable here: not wired in this build');
  }
});

import { describe, expect, it } from 'vitest';
import { cameraNetworkDoctor } from './native-network';
import type { Doctor } from './bridge-api';

const waiting: Doctor = { source: 'blackmagic', live: false, headline: 'Waiting', first: 'app', items: [] };
describe('native camera access', () => {
  it('turns an observed denial into a Mac recovery step and report', async () => {
    const result = await cameraNetworkDoctor(waiting, { postMessage: async () => ({ state: 'denied' }) });
    expect(result.mode).toBe('problem');
    expect(result.items[0].fix).toContain('System Settings');
    expect(result.report).toContain('refused by macOS');
  });
  it('never calls network permission readiness a live picture', async () => {
    expect(await cameraNetworkDoctor(waiting, { postMessage: async () => ({ state: 'ready' }) })).toBe(waiting);
  });
  it('keeps arriving pictures usable even when control discovery is denied', async () => {
    const live = { ...waiting, live: true };
    expect(await cameraNetworkDoctor(live, { postMessage: async () => ({ state: 'denied' }) })).toBe(live);
  });
  it('stops native discovery when the source is no longer Blackmagic', async () => {
    const calls: unknown[] = [];
    const none = { ...waiting, source: 'none' };
    expect(await cameraNetworkDoctor(none, { postMessage: async message => { calls.push(message); return { state: 'denied' }; } })).toBe(none);
    expect(calls).toEqual([{ op: 'observe', needed: false }]);
  });
});

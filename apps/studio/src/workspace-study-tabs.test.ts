import { describe, expect, it } from 'vitest';
import { activateTab, closeTab, emptyTabs, moveTab, readTabs } from './workspace-study-tabs';
describe('session tabs', () => {
  it('activates existing identities without duplicating or rearranging them', () => {
    const state = activateTab(activateTab(activateTab(emptyTabs(), 'a'), 'b'), 'a');
    expect(state.order).toEqual(['a', 'b']); expect(state.active).toBe('a');
  });
  it('closes to the most recently used remaining session and leaves the last close neutral', () => {
    const state = activateTab(activateTab(activateTab(emptyTabs(), 'a'), 'b'), 'c');
    const moved = activateTab(state, 'a');
    expect(closeTab(moved, 'a').active).toBe('c');
    expect(closeTab(closeTab(closeTab(moved, 'a'), 'c'), 'b').active).toBeNull();
    expect(closeTab(moved, 'b').active).toBe('a');
  });
  it('reorders without changing active session or recency', () => {
    const state = activateTab(activateTab(emptyTabs(), 'a'), 'b');
    expect(moveTab(state, 'b', 0)).toEqual({ ...state, order: ['b', 'a'] });
  });
  it('restores neutral state and sanitizes duplicate or malformed identities', () => {
    expect(readTabs(JSON.stringify({ version: 2, order: ['a','a',null,'bad/id'], active: null, recent: ['a'], closed: ['b'] })))
      .toEqual({version: 2, order: ['a'], active: null, recent: ['a'], closed: ['b']});
    expect(readTabs('{')).toBeNull(); expect(readTabs('{"version":1}')).toBeNull();
  });
});

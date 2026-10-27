import { describe, expect, it } from 'vitest';
import { opensWorkForUser, showingText, ShowingBar } from './showing';
import type { ClientCommand } from './protocol';

const node = () => ({ hidden: true, textContent: '' }) as unknown as HTMLElement;

describe("Show me's bar", () => {
  it('says what is shown, stops once on Stop or Escape, and goes when the server says it ended', () => {
    const root = node();
    const text = node();
    let click: () => void = () => {};
    const stop = { addEventListener: (_: string, f: () => void) => (click = f) } as unknown as HTMLButtonElement;
    const sent: ClientCommand[] = [];
    const bar = new ShowingBar(root, text, stop, (c) => sent.push(c));
    const state = { type: 'showing' as const, id: 'sh1', rid: 'r2', about: "Researcher 2's finding", active: true };
    bar.set(state);
    expect(bar.isOpen()).toBe(true);
    expect(root.hidden).toBe(false);
    expect(text.textContent).toBe(showingText(state));
    expect(text.textContent).toBe("Researcher 2's finding. Stop puts back only what it changed.");
    click();
    bar.close(); // Escape after Stop: nothing more is sent
    expect(sent).toEqual([{ type: 'showing', action: 'stop' }]);
    expect(bar.isOpen()).toBe(false);
    bar.set({ ...state, active: true });
    bar.set({ ...state, active: false }); // stopped by voice: the bar goes, nothing sent
    expect(root.hidden).toBe(true);
    expect(sent.length).toBe(1);
  });
});


describe('work the user opens', () => {
  it('is their change to the result viewer: the shelf, the chat and the Research tab all send presenter show', () => {
    expect(opensWorkForUser({ type: 'presenter', action: 'show', rid: 'r5' })).toBe(true);
    expect(opensWorkForUser({ type: 'presenter', action: 'close' })).toBe(false);
    expect(opensWorkForUser({ type: 'showing', action: 'stop' })).toBe(false);
  });
});

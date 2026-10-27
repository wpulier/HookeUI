import { describe, expect, it } from 'vitest';

import { DEEPER_TEXT } from './notes';

describe('Look deeper asks in plain words', () => {
  it('asks about "this one" (the card sends its outline) and names no effort', () => {
    expect(DEEPER_TEXT).toMatch(/this one/);
    expect(DEEPER_TEXT).not.toMatch(/deep|thorough|in[- ]depth|lesson|video|report/i);
  });
});

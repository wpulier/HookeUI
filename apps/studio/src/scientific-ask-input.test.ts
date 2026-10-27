import { expect, test } from 'vitest';
import { ScientificAskSelection } from './scientific-ask-input';
import type { ScientificAskInput } from './protocol';

test('saved input survives focus and retains a detached exact revision; Project or Tab change clears it', () => {
  const state = new ScientificAskSelection();
  const input: ScientificAskInput = {reference:{kind:'array',session:'p1',id:'native1',revision:2,component:'array.image'},digest:'d'.repeat(64),tab:'main'};
  state.select(input, 'Image'); input.reference.revision = 3;
  const captured = state.current('p1','main')!;
  expect(captured.input.reference.revision).toBe(2);
  captured.input.reference.component='array.other';
  expect(state.current('p1','main')!.input.reference.component).toBe('array.image');
  expect(state.current('p1','other')).toBeNull();
  state.select(input,'Image'); expect(state.current('p2','main')).toBeNull();
});

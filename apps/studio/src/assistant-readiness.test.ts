import {expect, test} from 'vitest';
import {assistantBlockReason, assistantChip} from './assistant-readiness';
import type {Role, RoleOption} from './protocol';
const vision = (available: boolean): Role => ({id:'vision',kind:'model',title:'Seeing',purpose:'Answer',current:'chosen',options:[{id:'chosen',label:'Selected model',note:'',available,why:'Needs configured credentials'}],powers:[],agentCanChange:[]});
test('unknown, disconnected and pending assistants refuse before a message is sent', () => {
  expect(assistantBlockReason(null,true,false)).toContain('Checking');
  expect(assistantBlockReason({roles:[vision(true)]},false,false)).toContain('Reconnect');
  expect(assistantBlockReason({roles:[vision(true)]},true,true)).toContain('previous question');
});
test('selected model readiness is authoritative, not a connected agent or another available model', () => {
  const role=vision(false);role.options.push({id:'other',label:'Other model',note:'',available:true});
  expect(assistantBlockReason({roles:[role]},true,false)).toBe('Selected model: Needs configured credentials.');
  role.options[0].readiness={status:'unavailable',word:'Unavailable in this edition',group:'Unavailable here',next:null,steps:[]};
  expect(assistantBlockReason({roles:[role]},true,false)).toBe('Selected model: Unavailable in this edition.');
  expect(assistantBlockReason({roles:[vision(true)]},true,false)).toBeNull();
});

// The composer's chip names the models that really answer a typed question (beta readiness audit, 5 Nov 2026: it named
// the Seeing model while the Quick decisions model answered from the workspace notes).
const option = (id: string, label: string, available = true, extra: Partial<RoleOption> = {}): RoleOption => ({id, label, note: '', available, ...extra});
const role = (id: string, title: string, current: string, options: RoleOption[]): Role => ({id, kind: 'model', title, purpose: '', current, options, powers: [], agentCanChange: []});
const seeing = (available = true, extra: Partial<RoleOption> = {}) =>
  role('vision', 'Seeing', 'claude-opus-5-5-fast', [option('claude-opus-5-5-fast', 'Claude Opus 5.5 (fast mode)', available, extra), option('gemini-3.8-flash', 'Gemini 3.8 Flash')]);
const quick = (current = 'gemini-3.8-flash', available = true) =>
  role('fast', 'Quick decisions', current, [option('gemini-3.8-flash', 'Gemini 3.8 Flash', available), option('claude-opus-5-5-fast', 'Claude Opus 5.5 (fast mode)'), option('off', 'Always look first')]);

test('the chip names the model that answers first, with +1 for the other, and both in full in its tooltip', () => {
  const chip = assistantChip({roles: [seeing(), quick()]});
  expect(chip.words).toBe('Gemini 3.8 Flash');
  expect(chip.more).toBe('+1');
  expect(chip.title).toBe('The Assistant answers with Gemini 3.8 Flash from the workspace notes, and with Claude Opus 5.5 (fast mode) when it needs to look. Choose the models for each job');
  expect(chip.needs).toBe('');
});
test('with no quick decision, or one that cannot run, the Seeing model answers alone', () => {
  const alone = {words: 'Claude Opus 5.5 (fast mode)', more: ''};
  const shown = (wire: Parameters<typeof assistantChip>[0]) => { const {words, more} = assistantChip(wire); return {words, more}; };
  expect(shown({roles: [seeing(), quick('off')]})).toEqual(alone);
  expect(shown({roles: [seeing(), quick('gemini-3.8-flash', false)]})).toEqual(alone);
  expect(shown({roles: [seeing()]})).toEqual(alone);
  expect(assistantChip({roles: [seeing(), quick('off')]}).title).toBe('The Assistant answers with Claude Opus 5.5 (fast mode). Choose the models for each job');
});
test('one model in both jobs is named once', () => {
  const chip = assistantChip({roles: [seeing(), quick('claude-opus-5-5-fast')]});
  expect(chip.words).toBe('Claude Opus 5.5 (fast mode)');
  expect(chip.more).toBe('');
  expect(chip.title).toBe('The Assistant answers with Claude Opus 5.5 (fast mode). Choose the models for each job');
});
test('a Seeing model without its key is the one the chip names, since nothing answers until the key is added', () => {
  const key = {status: 'unavailable', word: 'Needs an Anthropic key', group: 'Needs you', next: {kind: 'key'}, steps: []} as unknown as RoleOption['readiness'];
  const chip = assistantChip({roles: [seeing(false, {readiness: key}), quick()]});
  expect(chip.words).toBe('Claude Opus 5.5 (fast mode)');
  expect(chip.more).toBe('');
  expect(chip.needs).toBe('needs a key');
  expect(chip.title).toBe('Claude Opus 5.5 (fast mode): needs an Anthropic key. Add it in Models › Keys, or choose the models for each job');
});
test('the chip says where a model runs in its tooltip, as the app says it', () => {
  const chip = assistantChip({roles: [seeing(true, {said: 'Claude Opus 5.5 (fast mode) in the cloud'}), quick('off')]});
  expect(chip.words).toBe('Claude Opus 5.5 (fast mode)');
  expect(chip.title).toBe('The Assistant answers with Claude Opus 5.5 (fast mode) in the cloud. Choose the models for each job');
});
test('before the capability list arrives the chip is empty and opens the picker', () => {
  expect(assistantChip(null)).toEqual({words: '', more: '', title: 'Choose the models for each job', needs: ''});
});

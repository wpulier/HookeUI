import {afterEach, expect, test, vi} from 'vitest';
import {LauncherAssistant} from './launcher-assistant';
import {syntheticDOM, fire} from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());

test('a suggested starter needs a choice; an explicit open uses the existing Project action and carries the same visit', async () => {
  const {document} = syntheticDOM();
  vi.stubGlobal('document', document);
  vi.stubGlobal('window', {});
  const host = document.createElement('section');
  const requests: Record<string,unknown>[] = [];
  const replies = [
    {ok:true, visit:'opaque', answer:'You could start blank.', action:'blank', requested:false},
    {ok:true, visit:'opaque', answer:'Opening Pond.', action:'recent:p', requested:true},
    {ok:true},
  ];
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return {ok:true, json:async () => replies.shift()};
  }));
  const assistant = new LauncherAssistant();
  const activate = vi.fn();
  assistant.mount(host as unknown as HTMLElement, {activate, visible:() => true,
    recent:() => [{id:'p', name:'Pond', when:'yesterday'}],
    choices:() => ({blank:'Start blank', 'recent:p':'Pond'}), context:() => 'water sample'});
  const form = host.querySelector('form')!, field = host.querySelector('textarea')!;
  field.value = 'How can I start?'; fire(form, 'submit');
  await vi.waitFor(() => expect(host.textContent).toContain('You could start blank.'));
  expect(activate).not.toHaveBeenCalled();
  field.value = 'Open yesterday’s Project'; fire(form, 'submit');
  await vi.waitFor(() => expect(activate).toHaveBeenCalledWith('recent:p'));
  expect(requests[1]).toMatchObject({visit:'opaque', choices:{'recent:p':'Pond · yesterday'}});
  await assistant.adopt('p');
  expect(requests[2]).toEqual({visit:'opaque',session:'p',count:2});
  expect(host.textContent).not.toContain('Opening Pond.');
});

test('failed transfer can be retried for the same Project after a later launcher visit', async () => {
  const {document} = syntheticDOM();
  vi.stubGlobal('document', document); vi.stubGlobal('window', {});
  const host = document.createElement('section'), recovery = document.createElement('aside');
  let session = 'pond';
  let attachCalls = 0;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url.endsWith('/ask')) return {ok:true, json:async () => ({ok:true, visit:'original-visit', answer:'Compare fields.'})};
    expect(JSON.parse(options.body)).toEqual({visit:'original-visit', session:'pond', count:1});
    if (++attachCalls === 1) throw new Error('Connection lost');
    return {ok:true, json:async () => ({ok:true})};
  }));
  const assistant = new LauncherAssistant();
  assistant.mountRecovery(recovery as unknown as HTMLElement, () => session);
  assistant.mount(host as unknown as HTMLElement, {activate:vi.fn(), visible:()=>true, recent:()=>[], choices:()=>({})});
  host.querySelector('textarea')!.value = 'How should I start?';
  fire(host.querySelector('form')!, 'submit');
  await vi.waitFor(() => expect(host.textContent).toContain('Compare fields.'));
  await expect(assistant.adopt(session)).rejects.toThrow('Connection lost');
  assistant.reset();
  expect(recovery.textContent).toContain('Connection lost');
  const oldButton = recovery.querySelector('button')!;
  session = 'other'; assistant.refreshRecovery(); fire(oldButton, 'click');
  expect(recovery.hidden).toBe(true); expect(attachCalls).toBe(1);
  session = 'pond'; assistant.refreshRecovery();
  fire(recovery.querySelector('button')!, 'click');
  await vi.waitFor(() => expect(recovery.hidden).toBe(true));
  expect(attachCalls).toBe(2);
});

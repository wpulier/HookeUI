// The camera's shared pieces (the camera page and the studio's Phone panel): the words, the bridge's wire, the
// checklist and the phone's controls, on a stand-in DOM and a stand-in bridge.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fire, settleMicrotasks, TestElement } from '../workspace-windows.test-support';
import { BridgeApi, BridgeError, type Doctor, type DoctorItem, type PhoneState } from './bridge-api';
import { Checklist } from './checklist';
import { PhoneControls } from './phone-controls';
import { checksSummary, dotState, headline, held, lensChoices, setupSteps, smoothness, tab, timeLeft, zoomWords } from './words';

const item = (id: string, ok: boolean | null, extra: Partial<DoctorItem> = {}): DoctorItem =>
  ({ id, title: id, ok, evidence: `${id} evidence`, fix: '', action: null, problem: '', ...extra });
const doctorWith = (items: DoctorItem[], extra: Partial<Doctor> = {}): Doctor =>
  ({ source: 'blackmagic', live: true, headline: 'Live', first: null, items, ...extra });

describe('the words', () => {
  it('say how evenly frames arrive', () => {
    expect(smoothness(30, 40, true)).toEqual({ word: 'smooth', line: 'a frame every 33 ms' });
    expect(smoothness(30, 68, false)).toEqual({ word: 'slight stutter', line: '1 in 20 frames 35 ms late' });
    expect(smoothness(18, 290, false).word).toBe('stutters');
  });

  it('say zoom, time left and the lens buttons plainly', () => {
    expect([zoomWords(0), zoomWords(0.5), zoomWords(1)]).toEqual(['none', '50%', 'closest']);
    expect([timeLeft(36441), timeLeft(600)]).toEqual(['10 h 7 min', '10 min']);
    expect(lensChoices({ lenses: [48, 24, 100], focalLength: 24 })).toEqual([
      { mm: 24, current: true }, { mm: 48, current: false }, { mm: 100, current: false }]);
    expect(lensChoices({ focalLength: 48 })).toEqual([]);
  });

  it('take the old pinned sources as Blackmagic', () => {
    expect([tab('wifi'), tab('usb'), tab('blackmagic'), tab('continuity'), tab(undefined)]).toEqual(
      ['blackmagic', 'blackmagic', 'blackmagic', 'continuity', 'continuity']);
  });

  it('follow the doctor for the dot and the headline', () => {
    const live = doctorWith([item('arriving', true)], { via: 'Wi-Fi' });
    expect(dotState(live)).toBe('live');
    expect(headline(live)).toBe('Blackmagic Camera is live, over Wi-Fi');
    const broken = doctorWith([item('app', false)], { live: false, first: 'app', headline: "Not live: Blackmagic Camera isn't open" });
    expect(dotState(broken)).toBe('fault');
    expect(headline(broken)).toBe("Not live: Blackmagic Camera isn't open");
    expect(dotState({ ...broken, first: null })).toBe('work');
    expect(dotState(null)).toBe('off');
    expect(headline(null, 'continuity')).toBe('Switching to the iPhone');
  });

  it('count the checks and the setup steps from the same facts', () => {
    expect(checksSummary(doctorWith([item('a', true), item('b', true)]))).toEqual({ ok: true, text: 'All 2 checks pass' });
    expect(checksSummary(doctorWith([item('a', true), item('b', false)])).text).toBe('1 of 2 checks need attention');
    const setup = setupSteps(doctorWith([item('app', true), item('settings', false), item('quality', null)], { live: false }));
    expect(setup).toEqual({ show: true, done: [true, true, false, false], now: 2 }); // the app answered: it has it
    expect(setupSteps(doctorWith([item('app', false)], { live: false })).now).toBe(0); // first: get the app
    expect(setupSteps(doctorWith([item('app', false)], { live: false }), true).now).toBe(1); // has it: let the Mac reach it
    expect(setupSteps(doctorWith([item('app', null)], { live: true })).show).toBe(false); // its picture came first
    expect(setupSteps(doctorWith([item('app', false)], { live: false, setUp: true })).show).toBe(false); // set up before: only "open it"
    expect(setupSteps(doctorWith([item('app', true), item('settings', true), item('quality', true)])).show).toBe(false);
    expect(setupSteps(doctorWith([item('app', false)], { source: 'continuity' })).show).toBe(false);
  });

  it('hold means autofocus and auto exposure off at 30 fps', () => {
    expect(held({ autofocus: false, autoExposure: 'Off', fps: '30.00' })).toBe(true);
    expect(held({ autofocus: true, autoExposure: 'Off', fps: '30' })).toBe(false);
  });
});

describe("the bridge's wire", () => {
  it('keeps a camera recovery explanation instead of displaying HTTP 200 as an error', async () => {
    const api = new BridgeApi(() => '', async () => new Response(JSON.stringify({
      ok: false, message: 'The receiver is listening, but no camera picture has arrived.',
    }), { status: 200 }));
    await expect(api.reconnect()).rejects.toThrow('no camera picture has arrived');
  });

  it('sends writes with their header and turns a refusal into an error with its words', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const answer = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
    const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push([String(url), init]);
      if (String(url).includes('do=lens')) return answer(502, { ok: false, error: 'switching lenses from the Mac isn\'t proven' });
      return answer(200, { ok: true, phone: { reachable: true, zoom: 0.5 } });
    }) as unknown as typeof fetch;
    const api = new BridgeApi(() => 'http://bridge', fetcher);
    expect(await api.act('zoom', 0.5)).toEqual({ reachable: true, zoom: 0.5 });
    expect(calls[0][0]).toBe('http://bridge/api/phone?do=zoom&value=0.5');
    expect((calls[0][1]?.headers as Record<string, string>)['X-MicroscopeMind']).toBe('camera-control');
    await expect(api.act('lens', 24)).rejects.toThrow(BridgeError);
    await expect(api.act('lens', 24)).rejects.toThrow("switching lenses from the Mac isn't proven");
    await api.source('continuity');
    expect((calls.at(-1)?.[1]?.headers as Record<string, string>)['X-MicroscopeMind']).toBe('camera-source');
    expect(api.page('#phone-check')).toBe('http://bridge/camera/#phone-check');
  });
});

describe('the shared views', () => {
  beforeEach(() => vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) }));
  afterEach(() => vi.unstubAllGlobals());

  const PHONE: PhoneState = { reachable: true, autofocus: false, autoExposure: 'Off', fps: '30.00', focus: 0.4, iso: 200, shutter: 60,
    whiteBalance: 5600, tint: 0, zoom: 0, focalLength: 24, lenses: [24, 48], recording: false, recordTimeLeftS: 3600, stabilization: false };

  function controls(phone: PhoneState = PHONE, options = {}) {
    const acts: [string, unknown][] = [];
    const api = { act: vi.fn(async (action: string, value?: unknown) => { acts.push([action, value]); return { ...phone, zoom: 0.25 }; }),
      photo: vi.fn(async () => ({ name: 'Blackmagic-Camera-1.jpg', path: '/x' })) } as unknown as BridgeApi;
    const view = new PhoneControls(api, options);
    view.update(phone);
    return { view, acts, root: view.root as unknown as TestElement };
  }
  const button = (root: TestElement, text: string) => root.querySelectorAll('button').find((b) => b.textContent === text) as TestElement;

  it('switches only to a proven lens, and not to the one in use', async () => {
    const { root, acts } = controls();
    fire(button(root, '24'), 'click');
    fire(button(root, '48'), 'click');
    await settleMicrotasks();
    expect(acts).toEqual([['lens', 48]]);
    expect(button(root, 'Test lens switching').hidden).toBe(true);
    const unproven = controls({ ...PHONE, lenses: [] }, { openPhoneCheck: () => {} });
    expect(button(unproven.root, 'Test lens switching').hidden).toBe(false);
  });

  it('never moves a slider under the hand, and offers Auto white balance only once proven', async () => {
    const { view, root } = controls();
    const zoom = root.querySelectorAll('input')[0];
    zoom.value = '0.6';
    fire(zoom, 'input');
    view.update({ ...PHONE, zoom: 0.1 });
    expect(zoom.value).toBe('0.6'); // still where the user holds it
    expect(button(root, 'Auto').hidden).toBe(false); // focus's Auto
    const wbAuto = root.querySelectorAll('button').filter((b) => b.textContent === 'Auto');
    expect(wbAuto.at(-1)?.hidden).toBe(true); // Colour's Auto: not proven on this phone
    view.update({ ...PHONE, autoWhiteBalance: true });
    expect(root.querySelectorAll('button').filter((b) => b.textContent === 'Auto').at(-1)?.hidden).toBe(false);
  });

  it('records, says what it is recording, and shows frame rate chips only where asked', async () => {
    const { view, root, acts } = controls(PHONE, { frameRate: true });
    fire(button(root, 'Record on the phone'), 'click');
    await settleMicrotasks();
    expect(acts[0]).toEqual(['record', 'on']);
    view.update({ ...PHONE, recording: true, clipName: 'A001', timecode: '00:00:05:00' });
    expect(button(root, 'Stop recording')).toBeTruthy();
    expect(root.querySelector('.cam-state')?.textContent).toBe('Recording A001 · 00:00:05:00');
    expect(button(root, '30').getAttribute('aria-pressed')).toBe('true');
    const panel = controls();
    expect(button(panel.root, '30')).toBeUndefined();
  });

  it('says where a photo and a recording went, and offers the photo in the Finder', async () => {
    const { view, root } = controls();
    fire(button(root, 'Photo'), 'click');
    await settleMicrotasks();
    expect(root.querySelector('.cam-state')?.textContent).toBe('Photo saved on this Mac: Blackmagic-Camera-1.jpg');
    expect(button(root, 'Show in Finder').hidden).toBe(false);
    view.update({ ...PHONE, recording: true, clipName: 'A001_C009' });
    view.update({ ...PHONE, recording: false });
    expect(root.querySelector('.cam-state')?.textContent).toBe("Saved on the phone: A001_C009, in Blackmagic Camera's media");
    expect(button(root, 'Show in Finder').hidden).toBe(true); // the clip is the phone's: nothing to show on the Mac
  });

  it('the checklist leads with the first failing check, unless the page already explains it', () => {
    const api = {} as BridgeApi;
    const opened: string[] = [];
    const list = new Checklist(api, { openPhoneCheck: () => opened.push('phone-check'), onChanged: () => {}, onError: () => {},
      covered: (d) => (d.first === 'app' ? ['app'] : []) });
    const root = list.root as unknown as TestElement;
    const failing = doctorWith([item('proven', false, { fix: 'Run the Phone check.', action: 'phone-check' }), item('room', true)],
      { live: true, first: 'proven' });
    list.update(failing);
    const first = root.querySelector('.cam-first') as TestElement;
    expect(first.hidden).toBe(false);
    expect(first.textContent).toContain('Run the Phone check.');
    fire(button(first, 'Run the Phone check'), 'click');
    expect(opened).toEqual(['phone-check']);
    expect(root.querySelector('summary')?.textContent).toBe('1 of 2 checks need attention');
    list.update(doctorWith([item('app', false, { fix: 'Open the app' })], { live: false, first: 'app' }));
    expect(first.hidden).toBe(true); // the setup card covers it
    expect(button(root, 'Reconnect').dataset.strong).toBe('true'); // not live: Reconnect is the strong button
  });

  it('reset and reconnect say what they are doing, then what they did', async () => {
    vi.useFakeTimers();
    let finish = () => {};
    const api = { reset: () => new Promise<void>((resolve) => { finish = resolve; }) } as unknown as BridgeApi;
    const list = new Checklist(api, { openPhoneCheck: () => {}, onChanged: () => {}, onError: () => {} });
    const root = list.root as unknown as TestElement;
    list.update(doctorWith([item('arriving', false)], { live: false, first: 'arriving', waiting: 'Waiting for the iPhone' }));
    fire(button(root, 'Reset camera'), 'click');
    const running = button(root, 'Resetting…');
    expect(running.dataset.busy).toBe('true');
    finish();
    await vi.advanceTimersByTimeAsync(800);
    expect(button(root, 'Reset camera').dataset.busy).toBe('false');
    const note = root.querySelector('.cam-done') as TestElement;
    expect(note.hidden).toBe(false);
    expect(note.textContent).toBe('The camera link was restarted. Waiting for the iPhone…');
    await vi.advanceTimersByTimeAsync(8000);
    expect(note.hidden).toBe(true);
    vi.useRealTimers();
  });

  it('a camera not connected yet is a guide, never a red box', () => {
    const list = new Checklist({} as BridgeApi, { openPhoneCheck: () => {}, onChanged: () => {}, onError: () => {} });
    const root = list.root as unknown as TestElement;
    const connect = doctorWith([item('listed', false, { fix: 'Wake the iPhone' }), item('room', true)], {
      source: 'continuity', live: false, first: 'listed', mode: 'connect', headline: 'Connect your iPhone',
      steps: ['If the Blackmagic Camera app is open on the iPhone, close it.', 'Lock the iPhone: press its side button.'],
      help: ['Wi-Fi and Bluetooth are on.'], waiting: 'Waiting for the iPhone' });
    list.update(connect);
    expect((root.querySelector('.cam-first') as TestElement).hidden).toBe(true);
    const guide = root.querySelector('.cam-connect') as TestElement;
    expect(guide.hidden).toBe(false);
    expect(guide.textContent).toContain('Lock the iPhone: press its side button.');
    expect(guide.textContent).toContain('Waiting for the iPhone…');
    expect(button(root, 'Reconnect').hidden).toBe(true); // nothing to reconnect to yet
    expect(button(guide, 'Try connecting again').hidden).toBeFalsy(); // in plain view, under the steps
    expect(dotState(connect)).toBe('work');
    expect(checksSummary(connect).text).toBe("Where it's up to");
    expect((root.querySelector('.cam-fold') as unknown as { open: boolean }).open).toBe(true); // open while connecting
    list.update(doctorWith([item('arriving', true)])); // it came: the guide goes
    expect(guide.hidden).toBe(true);
    expect(button(root, 'Reconnect').hidden).toBe(false);
  });
});


it('does not call saved media a live camera or claim zero checks passed', () => {
  const media = doctorWith([], { source: 'footage', chosen: 'blackmagic', mode: 'live' });
  expect(headline(media)).toBe('A picture or video is playing; camera checks are paused');
  expect(checksSummary(media).text).toBe('No camera checks to report');
});

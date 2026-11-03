// The Microphone picker (mic-picker.ts): the Mac's inputs by name, the bridge's phone as "iPhone (camera)", Automatic
// named by what Talk would choose, and the pick kept as talk-mic.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fire, TestElement } from './workspace-windows.test-support';
import { MIC_KEY, notePhoneCamera, savedMicrophone } from './mic-choice';
import { MicPicker } from './mic-picker';

const mic = (deviceId: string, label: string) => ({ kind: 'audioinput', deviceId, label });
let store: Map<string, string>;
let devices: ReturnType<typeof mic>[];

beforeEach(() => {
  store = new Map();
  devices = [mic('default', 'Default - iPhoneWild Microphone'), mic('phone', 'iPhoneWild Microphone'), mic('mac', 'MacBook Pro Microphone'), mic('pods', 'AirPods Pro')];
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  vi.stubGlobal('localStorage', { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) });
  vi.stubGlobal('navigator', { mediaDevices: { enumerateDevices: async () => devices, addEventListener: () => {} } });
});
afterEach(() => vi.unstubAllGlobals());

const options = (picker: MicPicker) => (picker.select as unknown as TestElement).children.map((o) => o.textContent);

test('it lists the inputs, the phone as "iPhone (camera)", and Automatic as the Mac’s own', async () => {
  notePhoneCamera('iPhoneWild Camera');
  const picker = new MicPicker();
  await picker.refresh();
  expect(options(picker)).toEqual(['Automatic · MacBook Pro Microphone', 'iPhone (camera)', 'MacBook Pro Microphone', 'AirPods Pro']);
  expect(picker.select.value).toBe('');
});

test('a pick is kept as talk-mic with its label, and survives being read again', async () => {
  const picker = new MicPicker();
  await picker.refresh();
  picker.select.value = 'pods';
  fire(picker.select as unknown as TestElement, 'change');
  expect(JSON.parse(store.get(MIC_KEY)!)).toEqual({ id: 'pods', label: 'AirPods Pro' });
  const again = new MicPicker();
  await again.refresh();
  expect(again.select.value).toBe('pods');
  // Unplugged: still shown as the pick, not connected; Automatic puts it back to Talk's own choice.
  devices = devices.filter((d) => d.deviceId !== 'pods');
  await again.refresh();
  expect(options(again).at(-1)).toBe('AirPods Pro (not connected)');
  again.select.value = '';
  fire(again.select as unknown as TestElement, 'change');
  expect(savedMicrophone()).toBeNull();
});

test('before the microphone is allowed there are no names: it says what Talk uses instead of an empty list', async () => {
  devices = [mic('', ''), mic('', '')];
  const picker = new MicPicker();
  await picker.refresh();
  expect(picker.listed).toBe(false);
  expect(picker.select.hidden).toBe(true);
});

// Which microphone Talk and Record use (mic-choice.ts; Will, 3 Nov): the Mac's own, never the iPhone unless picked.
import { afterEach, expect, test, vi } from 'vitest';
import {
  chooseMicrophone, isPhone, MIC_KEY, MicChoiceError, microphoneName, notePhoneCamera, openMicrophone, saveMicrophone,
  savedMicrophone,
} from './mic-choice';

afterEach(() => vi.unstubAllGlobals());

const input = (deviceId: string, label: string) => ({ kind: 'audioinput' as const, deviceId, label });
const MAC = input('mac', 'MacBook Pro Microphone');

test('"iPhoneWild Microphone" is a phone: no word break is needed after iPhone', () => {
  expect(isPhone('iPhoneWild Microphone', [])).toBe(true);
  expect(isPhone('Will’s iPad Microphone', [])).toBe(true);
  expect(isPhone('MacBook Pro Microphone', [])).toBe(false);
  const devices = [input('default', 'Default - iPhoneWild Microphone'), input('phone', 'iPhoneWild Microphone'), MAC];
  expect(chooseMicrophone(devices, null, [])?.deviceId).toBe('mac');
});

test("a renamed phone is known by the bridge's camera name, with Microphone for Camera", () => {
  const devices = [input('phone', 'Wild Microphone'), MAC];
  expect(isPhone('Wild Microphone', [])).toBe(false);
  expect(isPhone('Wild Microphone', ['wild'])).toBe(true);
  expect(chooseMicrophone(devices, null, ['wild'])?.deviceId).toBe('mac');
  // As the camera menu tells it, from the bridge's /api/health.
  notePhoneCamera('Wild Camera');
  notePhoneCamera('FaceTime HD'); // not a camera name the bridge gives its phone: ignored
  expect(isPhone('Wild Microphone')).toBe(true);
  expect(isPhone('FaceTime HD Microphone')).toBe(false);
  expect(microphoneName(input('phone', 'Wild Microphone'))).toBe('iPhone (camera)');
});

test('a saved pick wins, by id or by its label when the id changed; the phone only when it is the pick', () => {
  const devices = [input('phone', 'iPhoneWild Microphone'), MAC, input('usb', 'Shure MV7')];
  expect(chooseMicrophone(devices, { id: 'usb', label: 'Shure MV7' }, [])?.deviceId).toBe('usb');
  expect(chooseMicrophone(devices, { id: 'old-id', label: 'Shure MV7' }, [])?.deviceId).toBe('usb');
  expect(chooseMicrophone(devices, { id: 'phone', label: 'iPhoneWild Microphone' }, [])?.deviceId).toBe('phone');
  // A pick that is not here: Talk's own choice, still never the phone.
  expect(chooseMicrophone(devices, { id: 'gone', label: 'Rode NT-USB' }, [])?.deviceId).toBe('mac');
});

test('AirPods (or a headset) the Mac made its default are kept; with the phone as default the Mac’s own wins', () => {
  const airpods = input('pods', 'AirPods Pro');
  expect(chooseMicrophone([input('default', 'Default - AirPods Pro'), airpods, MAC], null, [])?.deviceId).toBe('pods');
  expect(chooseMicrophone([airpods, MAC], null, [])?.deviceId).toBe('pods'); // WebKit lists the default first
  expect(chooseMicrophone([input('phone', 'iPhoneWild Microphone'), airpods, MAC], null, [])?.deviceId).toBe('mac');
  // No Mac microphone (a Mac mini): another real one, never the phone, a capture card or a loopback.
  expect(chooseMicrophone([input('phone', 'iPhoneWild Microphone'), input('bm', 'Blackmagic UltraStudio Audio'),
    input('bh', 'BlackHole 2ch'), airpods], null, [])?.deviceId).toBe('pods');
  expect(chooseMicrophone([input('phone', 'iPhoneWild Microphone'), input('bh', 'BlackHole 2ch')], null, [])).toBeNull();
});

test("the Mac's own microphone is known in other languages", () => {
  const phoneFirst = (label: string) => [input('phone', 'iPhoneWild Microphone'), input('usb', 'Shure MV7'), input('own', label)];
  for (const label of ['Micrófono integrado', 'MacBook Pro-Mikrofon', 'Microphone intégré', '内蔵マイク', 'Microfono MacBook Air']) {
    expect(chooseMicrophone(phoneFirst(label), null, [])?.deviceId).toBe('own');
  }
});

test('no labels (no permission yet) chooses nothing', () => {
  expect(chooseMicrophone([input('a1', ''), input('b2', '')], null, [])).toBeNull();
});

test('the pick is kept as talk-mic, and blocked storage is never an error', () => {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k),
  });
  saveMicrophone({ id: 'usb', label: 'Shure MV7' });
  expect(JSON.parse(store.get(MIC_KEY)!)).toEqual({ id: 'usb', label: 'Shure MV7' });
  expect(savedMicrophone()).toEqual({ id: 'usb', label: 'Shure MV7' });
  saveMicrophone(null);
  expect(savedMicrophone()).toBeNull();
  vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } });
  expect(savedMicrophone()).toBeNull();
  expect(() => saveMicrophone({ id: 'x', label: 'y' })).not.toThrow();
});

function stream(label: string) {
  const track = { label, stop: vi.fn() };
  return { getTracks: () => [track], getAudioTracks: () => [track], track } as unknown as MediaStream & { track: { stop: ReturnType<typeof vi.fn> } };
}

test('with labels, the chosen microphone is the first and only one opened', async () => {
  const asked: unknown[] = [];
  const mac = stream('MacBook Pro Microphone');
  const media = {
    enumerateDevices: async () => [input('default', 'Default - iPhoneWild Microphone'), input('phone', 'iPhoneWild Microphone'), MAC],
    getUserMedia: async (c: MediaStreamConstraints) => { asked.push((c.audio as MediaTrackConstraints).deviceId); return mac; },
  } as unknown as MediaDevices;
  expect(await openMicrophone({}, () => true, media)).toBe(mac);
  expect(asked).toEqual([{ exact: 'mac' }]);
});

test('first run (no labels): the default is opened for its label, a phone stopped at once, then the Mac’s own opened', async () => {
  const asked: unknown[] = [];
  const phone = stream('iPhoneWild Microphone');
  const mac = stream('MacBook Pro Microphone');
  let allowed = false;
  const media = {
    enumerateDevices: async () => allowed ? [input('phone', 'iPhoneWild Microphone'), MAC] : [input('', ''), input('', '')],
    getUserMedia: async (c: MediaStreamConstraints) => {
      const id = (c.audio as MediaTrackConstraints).deviceId;
      asked.push(id);
      if (id) { expect(phone.track.stop).toHaveBeenCalledOnce(); return mac; } // the phone was already stopped
      allowed = true;
      return phone;
    },
  } as unknown as MediaDevices;
  expect(await openMicrophone({}, () => true, media)).toBe(mac);
  expect(asked).toEqual([undefined, { exact: 'mac' }]);
});

test('only the phone: it is stopped and Talk says so, rather than listening across the room', async () => {
  const phone = stream('iPhoneWild Microphone');
  const media = {
    enumerateDevices: async () => [input('phone', 'iPhoneWild Microphone')],
    getUserMedia: async () => phone,
  } as unknown as MediaDevices;
  await expect(openMicrophone({}, () => true, media)).rejects.toBeInstanceOf(MicChoiceError);
  const firstRun = { getUserMedia: async () => phone } as unknown as MediaDevices; // opened for its label: a phone
  await expect(openMicrophone({}, () => true, firstRun)).rejects.toBeInstanceOf(MicChoiceError);
  expect(phone.track.stop).toHaveBeenCalledOnce();
});

test('a refusal is the browser’s, and is passed on', async () => {
  const media = {
    enumerateDevices: async () => [MAC],
    getUserMedia: async () => { throw Object.assign(new Error('denied'), { name: 'NotAllowedError' }); },
  } as unknown as MediaDevices;
  await expect(openMicrophone({}, () => true, media)).rejects.toMatchObject({ name: 'NotAllowedError' });
});

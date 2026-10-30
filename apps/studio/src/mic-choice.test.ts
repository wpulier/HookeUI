import { expect, test } from 'vitest';
import { betterMicrophone } from './mic-choice';

const input = (deviceId: string, label: string) => ({ kind: 'audioinput' as const, deviceId, label });

test("an iPhone's microphone (Continuity) gives way to the Mac's own", () => {
  const devices = [
    input('default', 'Default - Will’s iPhone Microphone'),
    input('a1', 'Will’s iPhone Microphone'),
    input('b2', 'MacBook Pro Microphone (Built-in)'),
  ];
  expect(betterMicrophone('Will’s iPhone Microphone', devices)).toBe('b2');
});

test("the Mac's own microphone wins over any other, but a phone gives way to another microphone when the Mac has none", () => {
  const devices = [input('a1', 'iPhone Microphone'), input('c3', 'Shure MV7'), input('b2', 'Built-in Microphone')];
  expect(betterMicrophone('iPhone Microphone', devices)).toBe('b2');
  expect(betterMicrophone('iPhone Microphone', [input('a1', 'iPhone Microphone'), input('c3', 'Shure MV7')])).toBe('c3');
});

test('a microphone that is not a phone is kept, whatever else is plugged in', () => {
  const devices = [input('a1', 'iPhone Microphone'), input('d4', 'AirPods Pro'), input('b2', 'MacBook Air Microphone')];
  expect(betterMicrophone('AirPods Pro', devices)).toBeNull();
  expect(betterMicrophone('MacBook Air Microphone', devices)).toBeNull();
});

test("a phone is kept when it is the only microphone, and a capture card's audio is never chosen instead", () => {
  expect(betterMicrophone('iPhone Microphone', [input('a1', 'iPhone Microphone')])).toBeNull();
  const devices = [input('a1', 'iPad Microphone'), input('e5', 'Blackmagic UltraStudio Audio'), input('f6', 'BlackHole 2ch')];
  expect(betterMicrophone('iPad Microphone', devices)).toBeNull();
});

test('no labels (no permission yet) means no change', () => {
  expect(betterMicrophone('', [input('a1', ''), input('b2', '')])).toBeNull();
});

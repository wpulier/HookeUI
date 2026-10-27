import { expect, test } from 'vitest';
import { bridgeAddress } from './bridge-address';
test('a missing runtime bridge never falls back to another service',()=>{
 expect(()=>bridgeAddress('')).toThrow('unavailable');
 expect(bridgeAddress('http://127.0.0.1:19765/')).toBe('http://127.0.0.1:19765');
});

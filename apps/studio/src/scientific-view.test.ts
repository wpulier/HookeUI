import { expect, test } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import { scientificFrame, scientificInventory } from './scientific-view';

test('scientific descriptor preserves native view metadata without aliases', () => {
  const value = structuredClone(cases.valid), parsed = scientificFrame(value);
  value.dimensions.steps[0] = 4;
  expect(parsed.dimensions.steps[0]).toBe(1);
});

test.each(cases.otherValid)('$name preserves its scientific coordinates', ({ value }) => {
  expect(scientificFrame(value)).toEqual(value);
});

test.each(cases.invalid)('$name is refused in the browser and parent', ({ path, value }) => {
  const frame = structuredClone(cases.valid);
  let target: Record<string, unknown> = frame;
  for (const part of path.slice(0, -1)) target = target[part] as Record<string, unknown>;
  target[path.at(-1)!] = value;
  expect(() => scientificFrame(frame)).toThrow();
});

test.each([NaN, Infinity, 2 ** 54, true])('invalid coordinate %s is refused', value => {
  const frame = structuredClone(cases.valid);
  (frame.camera.center as unknown[])[0] = value;
  expect(() => scientificFrame(frame)).toThrow();
});


test('layer inventory is detached and cannot masquerade as a rendered frame',()=>{
 const value={format:'scientific-layers-v1',layers:structuredClone(cases.valid.layers)};
 const layers=scientificInventory(value);value.layers[0].name='changed';
 expect(layers[0].name).not.toBe('changed');
 expect(()=>scientificFrame(value)).toThrow();
 expect(()=>scientificInventory({...value,frame:'a'.repeat(64)})).toThrow();
});

test.each(cases.invalid.filter(c=>c.path[0]==='layers'))('inventory refuses $name',({path,value})=>{
 const inventory={format:'scientific-layers-v1',layers:structuredClone(cases.valid.layers)};
 let target:Record<string,unknown>=inventory;
 for(const part of path.slice(0,-1))target=target[part] as Record<string,unknown>;
 target[path.at(-1)!]=value;
 expect(()=>scientificInventory(inventory)).toThrow();
});


test('empty structured layers preserve zero counts without inventing image object counts',()=>{
 const layer={id:'a'.repeat(32),name:'Detections',type:'points',visible:true,opacity:1,scale:[1,1],translate:[0,0],elementCount:0};
 expect(scientificInventory({format:'scientific-layers-v1',layers:[layer]})[0].elementCount).toBe(0);
 for(const bad of [-1,true,.5,2**53])expect(()=>scientificInventory({format:'scientific-layers-v1',layers:[{...layer,elementCount:bad}]})).toThrow();
 expect(()=>scientificInventory({format:'scientific-layers-v1',layers:[{...layer,type:'image'}]})).toThrow();
});


test.each([
 ['labels','instance-labels',['z','y','x']], ['image','probabilities',['z','y','x']],
 ['points','point-set',['z','y','x']], ['tracks','track-coordinates',['t','y','x']],
])('declared %s output meaning survives both surfaces without edit authority', (type,semantic,axes) => {
 const layer={...structuredClone(cases.valid.layers[0]),type,viewKey:'result',
   viewGeometry:{affine:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],axes:['z','y','x'],units:['um','um','um']},
   outputMeaning:{semantic,axes}};
 const frame={...structuredClone(cases.valid),layers:[layer]};
 expect(scientificFrame(frame).layers[0].outputMeaning).toEqual({semantic,axes});
 const parsed=scientificInventory({format:'scientific-layers-v1',layers:[layer]});
 expect(parsed[0].outputMeaning).toEqual({semantic,axes});expect(parsed[0].editableInput).toBeUndefined();
 for(const change of [{viewKey:null},{editableInput:'raw'},
   {outputMeaning:{semantic:'unrecognized',axes}}, {outputMeaning:{semantic,axes:['x','x','x']}},
   {outputMeaning:{semantic,axes:['y','x']}}, {outputMeaning:{semantic,axes,authority:true}}]) {
   const bad={...layer,...change};
   expect(()=>scientificInventory({format:'scientific-layers-v1',layers:[bad]})).toThrow();
   expect(()=>scientificFrame({...frame,layers:[bad]})).toThrow();
 }
});

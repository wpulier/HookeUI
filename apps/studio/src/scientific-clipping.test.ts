import {expect,test} from 'vitest';
import frameCases from '../../../contract/scientific-view-cases.json';
import viewCases from '../../../contract/scientific-view-preferences-cases.json';
import {clippingPlanes} from './scientific-clipping';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
const plane={position:[2,11,13],normal:[1,0,0],enabled:true};
test.each([null,{},[{}],[{...plane,enabled:1}],[{...plane,normal:[0,0,0]}],
 [{...plane,position:[NaN,0,1]}],[{...plane,position:[true,2,3]}],[{...plane,normal:[1e-200,0,0]}]])('refuses malformed clipping %j',raw=>{
 expect(()=>clippingPlanes(raw)).toThrow();
 const frame=scientificFrame(frameCases.valid);frame.layers[0].clippingPlanes=raw as never;
 expect(()=>scientificFrame(frame)).toThrow();
});
test('retains every plane, including disabled planes, and refuses older writers',()=>{
 const frame=scientificFrame(frameCases.valid);const planes=[plane,{...plane,normal:[0,1,0],enabled:false}];
 frame.layers[0].clippingPlanes=clippingPlanes(planes);frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry=structuredClone(viewCases.valid.layers[0].geometry);
 const saved=rememberScientificFrame(scientificFrame(frame));expect(saved.schema).toBe(5);expect(saved.layers[0].clippingPlanes).toEqual(planes);
 expect(scientificViewPreferences(saved)).toEqual(saved);
 for(const schema of [1,2,3,4])expect(()=>scientificViewPreferences({...saved,schema})).toThrow();
});


test('refuses shader-sized lists before sending a change',()=>{
  expect(()=>clippingPlanes(Array.from({length:65},()=>({position:[0,0,0],normal:[1,1,1],enabled:true})))).toThrow('64');
});

import {expect,test} from 'vitest';
import frameCases from '../../../contract/scientific-view-cases.json';
import viewCases from '../../../contract/scientific-view-preferences-cases.json';
import {imagePlane} from './scientific-image-plane';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
const plane={depiction:'plane',position:[2,11,13],normal:[1,0,0],thickness:2};

test.each([{normal:[0,0,0]},{normal:[NaN,0,1]},{position:[1,2]},{thickness:-1},{thickness:true},{depiction:'unknown'},{depiction:['plane']},{thickness:Infinity},{normal:[1e-200,0,0]}])('rejects malformed plane %j',patch=>{
 expect(()=>imagePlane({...plane,...patch})).toThrow();
 const frame=scientificFrame(frameCases.valid);frame.layers[0].imagePlane={...plane,...patch} as never;
 expect(()=>scientificFrame(frame)).toThrow();
});
test('remembers exact plane settings and rejects legacy writers carrying new fields',()=>{
 const frame=scientificFrame(frameCases.valid);frame.layers[0].imagePlane=imagePlane(plane);
 frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry=structuredClone(viewCases.valid.layers[0].geometry);
 expect(scientificFrame(frame).layers[0].imagePlane).toEqual(plane);
 const saved=rememberScientificFrame(frame);expect(saved.schema).toBe(4);expect(saved.layers[0].imagePlane).toEqual(plane);
 for(const schema of [1,2,3])expect(()=>scientificViewPreferences({...saved,schema})).toThrow();
});

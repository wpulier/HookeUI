import {expect,test} from 'vitest';
import {pointAppearance} from './scientific-point-appearance';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
import frames from '../../../contract/scientific-view-cases.json';
import shared from '../../../contract/scientific-point-appearance-cases.json';
test.each(shared.invalid)('point display refuses $name',({patch})=>{
 const value={...shared.valid,...patch};expect(()=>pointAppearance(value)).toThrow();
 const frame=scientificFrame(frames.valid);frame.layers[0].type='points';
 expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],pointAppearance:value}]})).toThrow();
});
test.each([NaN,Infinity])('nonfinite point display refuses %s',value=>{
 expect(()=>pointAppearance({...shared.valid,antialiasing:value})).toThrow();
});
test('point display has explicit versioned intent and cannot be attached to another layer kind',()=>{
 const frame=scientificFrame(frames.valid);frame.layers[0].type='points';frame.layers[0].pointAppearance=pointAppearance(shared.valid);
 frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry={affine:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],axes:['z','y','x'],units:['um','um','um']};
 const saved=rememberScientificFrame(frame);expect(saved.schema).toBe(10);expect(saved.layers[0].pointAppearance).toEqual(shared.valid);
 expect(()=>scientificViewPreferences({...saved,schema:9})).toThrow();
 expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],type:'image'}]})).toThrow();
});

test('continuous canvas limits are not restricted to integer identity precision',()=>{
 expect(pointAppearance({...shared.valid,canvasLimits:[0,1e19]}).canvasLimits).toEqual([0,1e19]);
});

import {expect,test} from 'vitest';
import {sliceMargins,imageProjection} from './scientific-slices';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
import frames from '../../../contract/scientific-view-cases.json';
import cases from '../../../contract/scientific-slice-cases.json';
test.each(cases.invalid)('slice margins refuse $name',({value})=>{
 expect(()=>sliceMargins(value,5)).toThrow();
});
test.each([NaN,Infinity,-Infinity])('slice margins refuse nonfinite %s',value=>{
 expect(()=>sliceMargins({left:[value,0],right:[0,0]},2)).toThrow();
});
test.each(cases.projections)('native image projection accepts %s',mode=>expect(imageProjection(mode)).toBe(mode));
test.each(['average','median',true,null,{}])('undeclared projection refuses %s',mode=>expect(()=>imageProjection(mode)).toThrow());
test('remembered thickness and projection require schema 11 and matching layer kind',()=>{
 const frame=scientificFrame(frames.valid);frame.dimensions.margins={left:[1,2,3],right:[3,2,1]};
 frame.layers[0].imageProjection='max';frame.layers[0].viewKey='raw';
 frame.layers[0].viewGeometry={affine:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],axes:['z','y','x'],units:['um','um','um']};
 expect(scientificFrame(frame).dimensions.margins).toEqual(frame.dimensions.margins);
 const saved=rememberScientificFrame(frame);expect(saved.schema).toBe(11);expect(saved.dimensions.margins).toEqual(frame.dimensions.margins);
 expect(saved.layers[0].imageProjection).toBe('max');expect(()=>scientificViewPreferences({...saved,schema:10})).toThrow();
 expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],type:'labels'}]})).toThrow();
 expect(()=>scientificFrame({...frame,dimensions:{...frame.dimensions,margins:{left:[0],right:[0]}}})).toThrow();
});

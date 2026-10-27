import {expect,test} from 'vitest';
import {surfaceAppearance} from './scientific-surface-appearance';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
import frames from '../../../contract/scientific-view-cases.json';
export const display={shading:'smooth',contrastLimits:[1,4],gamma:1,autoContrast:false,
 wireframe:{visible:true,width:1.5,color:[1,0,0,1]},faceNormals:{visible:true,width:2,length:3,color:[0,1,0,1]},
 vertexNormals:{visible:false,width:1,length:5,color:[0,0,1,1]}};
test.each([{gamma:0},{gamma:Infinity},{shading:'wrong'},{contrastLimits:[4,1]},{autoContrast:1},
 {wireframe:{visible:true,width:-1,color:[1,0,0,1]}},{faceNormals:{visible:true,width:1,length:1,color:[1,0,0,2]}}, {extra:1}])('invalid surface controls refuse %j',patch=>{
 expect(()=>surfaceAppearance({...display,...patch})).toThrow();
});
test('surface display has a versioned retained view without changing geometry',()=>{
 const frame=scientificFrame(frames.valid);frame.layers[0].type='surface';frame.layers[0].surfaceAppearance=surfaceAppearance(display);
 frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry={affine:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],axes:['z','y','x'],units:['um','um','um']};
 const saved=rememberScientificFrame(frame);expect(saved.schema).toBe(9);expect(saved.layers[0].surfaceAppearance).toEqual(display);
 expect(()=>scientificViewPreferences({...saved,schema:8})).toThrow();
 expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],type:'image'}]})).toThrow();
});
test('surface contrast preserves finite scientific values above the identity range',()=>{
 expect(surfaceAppearance({...display,contrastLimits:[1e19,2e19]}).contrastLimits).toEqual([1e19,2e19]);
});
import shared from '../../../contract/scientific-surface-appearance-cases.json';
test.each(shared.invalid)('shared Surface contract refuses $name',({patch})=>{
 const value={...shared.valid,...patch};expect(()=>surfaceAppearance(value)).toThrow();
 const frame=scientificFrame(frames.valid);frame.layers[0].type='surface';
 expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],surfaceAppearance:value}]})).toThrow();
});

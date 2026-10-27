import {expect,test} from 'vitest';
import {pathAppearance} from './scientific-path-appearance';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
import frames from '../../../contract/scientific-view-cases.json';
const settings={kind:'vectors',length:2.5,width:3,style:'arrow',projection:'none'};
test.each([{length:Infinity},{length:true},{width:-1},{style:'wrong'},{projection:'wrong'},{extra:1}])('refuses unsupported vector display %j',patch=>{
 expect(()=>pathAppearance({...settings,...patch})).toThrow();
});
test('native path display is typed and remembered independently of scientific coordinates',()=>{
 const frame=scientificFrame(frames.valid);frame.layers[0].type='vectors';frame.layers[0].pathAppearance=pathAppearance(settings);
 frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry={affine:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],axes:['z','y','x'],units:['um','um','um']};
 const saved=rememberScientificFrame(frame);expect(saved.schema).toBe(8);expect(saved.layers[0].pathAppearance).toEqual(settings);
 expect(()=>scientificViewPreferences({...saved,schema:7})).toThrow();
 expect(()=>pathAppearance(settings,'tracks')).toThrow();
 expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],type:'image'}]})).toThrow();
});

import {expect,test} from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import preferences from '../../../contract/scientific-view-preferences-cases.json';
import {scientificFrame} from './scientific-view';
import {scientificViewPreferences,rememberScientificFrame} from './scientific-view-preferences';
import {textAppearance,textAnchors,textBlending} from './scientific-text-appearance';
const valid={visible:true,size:18.5,scaling:false,blending:'translucent',anchor:'upper_left',translation:0,rotation:25.5};
test.each([{size:0},{rotation:Infinity},{visible:1},{scaling:null},{anchor:'center-left'},{translation:[1,2]},{translation:[]},{translation:[1,NaN,3]},{blending:'opaque'},{extra:true}])('refuses invalid native text display %j',patch=>{
 expect(()=>textAppearance({...valid,...patch},3)).toThrow();
});
test('native text modes retain scalar, singleton and dimensional offsets without touching encoded values',()=>{
 for(const anchor of textAnchors)for(const blending of textBlending)for(const translation of [0,[2],[1,2,3]]) {
  const value={...valid,anchor,blending,translation};expect(textAppearance(value,3)).toEqual(value);
 }
});
test.each(['points','shapes'])('feature text on %s round-trips in version 13 with exact layer identity',type=>{
 const raw=structuredClone(cases.valid);Object.assign(raw.layers[0],{type,textAppearance:valid,viewKey:'raw',viewGeometry:preferences.valid.layers[0].geometry});
 const frame=scientificFrame(raw),saved=rememberScientificFrame(frame);
 expect(saved.schema).toBe(13);expect(saved.layers[0].textAppearance).toEqual(valid);
 expect(scientificViewPreferences(saved)).toEqual(saved);
 expect(()=>scientificViewPreferences({...saved,schema:12})).toThrow();
 expect(()=>scientificFrame({...raw,layers:[{...raw.layers[0],type:'image'}]})).toThrow();
});

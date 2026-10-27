import {expect,test} from 'vitest';
import frameCases from '../../../contract/scientific-view-cases.json';
import viewCases from '../../../contract/scientific-view-preferences-cases.json';
import {layerBlending,scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';

test.each(layerBlending)('remembers %s and rejects unknown modes and legacy writers',mode=>{
  const frame=scientificFrame(frameCases.valid);frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry=structuredClone(viewCases.valid.layers[0].geometry);frame.layers[0].blending=mode;
  expect(scientificFrame(frame).layers[0].blending).toBe(mode);
  const saved=rememberScientificFrame(frame);expect(saved.schema).toBe(3);expect(saved.layers[0].blending).toBe(mode);
  for(const schema of [1,2])expect(()=>scientificViewPreferences({...saved,schema})).toThrow();
  for(const blending of ['',true,'unknown',null]){
    expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],blending}]})).toThrow();
    expect(()=>scientificViewPreferences({...saved,layers:[{...saved.layers[0],blending}]})).toThrow();
  }
});

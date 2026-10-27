import {expect,test} from 'vitest';
import cases from '../../../contract/scientific-image-appearance-cases.json';
import frames from '../../../contract/scientific-view-cases.json';
import views from '../../../contract/scientific-view-preferences-cases.json';
import {imageAppearance} from './scientific-image-appearance';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';

test.each(cases.invalid)('$name fails identically before rendering or dispatch',({patch})=>{
  const settings={...cases.valid,...patch};
  expect(()=>imageAppearance(settings)).toThrow();
  const frame=structuredClone(frames.valid);
  expect(()=>scientificFrame({...frame,layers:[{...frame.layers[0],imageAppearance:settings}]})).toThrow();
});
test('remembered appearance has a new schema while legacy views remain readable',()=>{
  const raw=structuredClone(frames.valid);
  const frame=scientificFrame({...raw,layers:[{...raw.layers[0],viewKey:'raw',viewGeometry:views.valid.layers[0].geometry,imageAppearance:cases.valid}]});
  const saved=rememberScientificFrame(frame);
  expect(saved.schema).toBe(2);expect(saved.layers[0].imageAppearance).toEqual(cases.valid);
  frame.layers[0].imageAppearance!.gamma=2;
  expect(saved.layers[0].imageAppearance!.gamma).toBe(1.4);
  expect(()=>scientificViewPreferences({...saved,schema:1})).toThrow();
  expect(()=>scientificViewPreferences({...saved,layers:[{...saved.layers[0],type:'labels'}]})).toThrow();
  expect(scientificViewPreferences(views.valid)).toEqual(views.valid);
});

test('floating intensity settings are not restricted to integer identity ranges',()=>{
  const settings={...cases.valid,contrastLimits:[1e17,2e17],isoThreshold:1.5e17};
  expect(imageAppearance(settings)).toEqual(settings);
  const raw=structuredClone(frames.valid);
  expect(scientificFrame({...raw,layers:[{...raw.layers[0],imageAppearance:settings}]}).layers[0].imageAppearance).toEqual(settings);
});

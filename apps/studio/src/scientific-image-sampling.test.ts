import {expect,test} from 'vitest';
import {imageSampling} from './scientific-image-sampling';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
import frames from '../../../contract/scientific-view-cases.json';
import views from '../../../contract/scientific-view-preferences-cases.json';
const settings={interpolation2d:'nearest',interpolation3d:'linear',kernel2d:[[1]]};
test.each([{interpolation3d:'custom'},{interpolation2d:'missing'},{kernel2d:[]},{kernel2d:[[1],[2,3]]},{kernel2d:[[NaN]]},{kernel2d:[[true]]},{kernel2d:[[1e39]]}])('rejects invalid display sampling %j',patch=>{
 expect(()=>imageSampling({...settings,...patch})).toThrow();
});
test('sampling is remembered without changing scientific geometry',()=>{
 const frame=scientificFrame(frames.valid);frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry=views.valid.layers[0].geometry;
 frame.layers[0].imageSampling=imageSampling(settings);
 const saved=rememberScientificFrame(frame);expect(saved.schema).toBe(6);expect(saved.layers[0].imageSampling).toEqual(settings);
 for(const schema of [1,2,3,4,5])expect(()=>scientificViewPreferences({...saved,schema})).toThrow();
});

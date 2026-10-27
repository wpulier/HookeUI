import {expect,test} from 'vitest';
import {imageColormap,colormapChoices} from './scientific-colormap';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
import frames from '../../../contract/scientific-view-cases.json';
import views from '../../../contract/scientific-view-preferences-cases.json';
export const colorMap={name:'custom-test',colors:[[0,0,0,1],[1,.5,0,1]],controls:[0,1],interpolation:'linear',nanColor:[1,0,1,0],highColor:null,lowColor:null};
test.each([{colors:[]},{colors:[[1,0,0]]},{colors:[[true,0,0,1]]},{colors:[[NaN,0,0,1]]},{colors:[[1e300,0,0,1]]},{controls:[1,0]},{controls:[0,.5]},{interpolation:'zero'},{interpolation:'unknown'},{nanColor:null},{highColor:[0,0,0,2]}])('refuses malformed colors %j',patch=>expect(()=>imageColormap({...colorMap,...patch})).toThrow());
test('remembers definitions independently from registry choices and preserves legacy schemas',()=>{
 const frame=scientificFrame(frames.valid);frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry=views.valid.layers[0].geometry;
 frame.layers[0].imageColormap=imageColormap(colorMap);frame.layers[0].colormapChoices=['gray','viridis'];
 const saved=rememberScientificFrame(scientificFrame(frame));expect(saved.schema).toBe(7);expect(saved.layers[0].imageColormap).toEqual(colorMap);expect(saved.layers[0]).not.toHaveProperty('colormapChoices');
 for(const schema of [1,2,3,4,5,6])expect(()=>scientificViewPreferences({...saved,schema})).toThrow();
});
test.each([['gray','gray'],[1],[''],Array(1025).fill('gray')])('refuses invalid palette inventory %j',choices=>expect(()=>colormapChoices(choices)).toThrow());

import {expect,test} from 'vitest';
import cases from '../../../contract/scientific-grid-cases.json';
import views from '../../../contract/scientific-view-cases.json';
import {scientificGrid} from './scientific-grid';
import {scientificFrame} from './scientific-view';
import {scientificViewPreferences,rememberScientificFrame} from './scientific-view-preferences';

test.each(cases.valid)('accept exact grid settings %#',value=>{
 const result=scientificGrid(value);expect(result).toEqual(value);expect(result).not.toBe(value);
});
test.each(cases.invalid)('refuse invalid grid settings %#',value=>expect(()=>scientificGrid(value)).toThrow());
test.each([NaN,Infinity,-Infinity])('refuse nonfinite spacing %s',spacing=>expect(()=>scientificGrid({...cases.valid[0],spacing})).toThrow());
test('legacy frames remain accepted; new grids use their own remembered schema',()=>{
 const old=scientificFrame(views.valid);expect(old.grid).toBeUndefined();
 const frame=scientificFrame({...views.valid,grid:cases.valid[1]});
 // Bind the fixture through its ordinary identity fields, never inventing a runtime receipt.
 frame.layers=[];
 const remembered=rememberScientificFrame(frame);expect(remembered.schema).toBe(12);expect(remembered.grid).toEqual(cases.valid[1]);
 expect(()=>scientificViewPreferences({...remembered,schema:11})).toThrow();
 expect(()=>scientificFrame({...views.valid,grid:{...cases.valid[0],stride:0}})).toThrow();
});

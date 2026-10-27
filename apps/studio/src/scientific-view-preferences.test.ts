import {expect,test} from 'vitest';
import cases from '../../../contract/scientific-view-preferences-cases.json';
import frames from '../../../contract/scientific-view-cases.json';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';

test('only declared layer identities can be remembered and snapshots are detached',()=>{
 const frame=scientificFrame(frames.valid);
 expect(()=>rememberScientificFrame(frame)).toThrow('temporary layers');
 frame.layers[0].viewKey='raw';frame.layers[0].viewGeometry=structuredClone(cases.valid.layers[0].geometry);
 const saved=rememberScientificFrame(frame);
 expect(saved).toEqual(cases.valid);
 frame.layers[0].opacity=.25;
 expect(saved.layers[0].opacity).toBe(1);
});
test.each(cases.otherValid)('$name',({value})=>expect(scientificViewPreferences(value)).toEqual(value));
test.each(cases.invalid)('$name refuses inconsistent settings',({path,value})=>{
 const bad:any=structuredClone(cases.valid);let target=bad;
 for(const key of path.slice(0,-1))target=target[key];target[path.at(-1)!]=value;
 expect(()=>scientificViewPreferences(bad)).toThrow();
});

import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import {ScientificInspection,inspectionReply,type InspectionRequest} from './scientific-inspection';
import {scientificFrame} from './scientific-view';
import type {ScientificCanvas} from './scientific-canvas';
import {TestElement,fire,settleMicrotasks,syntheticDOM} from './workspace-windows.test-support';
let root:TestElement,canvas:TestElement,control:ScientificInspection;
beforeEach(()=>{const dom=syntheticDOM();root=dom.host;canvas=new TestElement('canvas');vi.stubGlobal('document',dom.document);});
afterEach(()=>{control?.close();vi.unstubAllGlobals();});
const request:InspectionRequest={op:'inspect',frame:'a'.repeat(64),layer:'b'.repeat(32),position:[10,20]};
const sample={frame:request.frame,layer:request.layer,world:[10,20],data:[1,2],axes:['y','x'],units:['um','um'],level:null,projection:'none',dtype:'<u8',values:['18446744073709551615']};
const field=(name:string)=>[...root.querySelectorAll('input'),...root.querySelectorAll('select')].find(e=>e.getAttribute('aria-label')===name)!;
function open(send:(q:InspectionRequest,draw:string)=>Promise<unknown>=vi.fn(async(q:InspectionRequest,_draw:string)=>({...sample,frame:q.frame,layer:q.layer})),select?:(frame:string,value:{layer:string;action:'replace'|'toggle'|'clear';index:number|null})=>Promise<void>){
 const frame=scientificFrame(cases.valid);frame.layers=[{id:request.layer,type:'image',name:'Raw',visible:true,opacity:1,scale:[1,1,1],translate:[0,0,0]}];
 let current:{frame:typeof frame;draw:string}|null={frame,draw:'one'};
 const drawing={element:()=>canvas,current:()=>current,position:(_draw:string,x:number,y:number)=>[y,x]};
 control=new ScientificInspection(root as unknown as HTMLElement,drawing as unknown as ScientificCanvas,{send,changed:()=>{},select});
 control.bindSession('session');control.update(frame);field('Inspect layer').value=request.layer;Object.assign(field('Inspect native values'),{checked:true});control.setAvailable(true);
 return {frame,send,revoke:()=>{current=null;control.revoke();},redraw:()=>{current={frame:current!.frame,draw:'two'};}};
}
test('exact integer text and coordinates stay exact through native readout',async()=>{
 const {send}=open();fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(send).toHaveBeenCalledWith({...request,frame:cases.valid.frame},'one');expect(root.textContent).toContain('18446744073709551615');expect(root.textContent).toContain('y: 10 um (data 1)');expect(control.blocked()).toBe(false);
});
test('late response after revocation cannot display an old value',async()=>{
 let finish!:(v:typeof sample)=>void;const {revoke}=open(vi.fn(()=>new Promise(resolve=>{finish=resolve;})));
 fire(canvas,'click',{clientX:20,clientY:10});expect(control.blocked()).toBe(true);revoke();finish({...sample,frame:cases.valid.frame});await settleMicrotasks();expect(root.textContent).not.toContain('18446744073709551615');expect(control.blocked()).toBe(false);
});
test('grid and 3D cannot dispatch 2D inspection',()=>{
 const {frame,send}=open();for(const change of [{grid:{enabled:true,shape:[1,-1] as [number,number],stride:1,spacing:0}},{dimensions:{...frame.dimensions,ndisplay:3 as const}}]){
 control.update({...frame,...change});fire(canvas,'click',{clientX:20,clientY:10});}expect(send).not.toHaveBeenCalled();
});
test('malformed and wrong-identity native samples cannot look successful',()=>{
 expect(inspectionReply(sample,request).values).toEqual(['18446744073709551615']);for(const patch of [{frame:'other'},{layer:'other'},{values:[2**64]},{world:[NaN,2]},{world:[1,2,3]},{dtype:'bad<u8'},{extra:true},{axes:['x']}])expect(()=>inspectionReply({...sample,...patch},request)).toThrow();
});
test('read failure keeps retry available and says what to do',async()=>{
 open(vi.fn(async()=>{throw new Error('read refused');}));fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();expect(root.textContent).toContain('Refresh the preview and retry');expect(control.blocked()).toBe(false);
});

test('old session completion cannot clear a newer pending read',async()=>{
 const finishes:Array<(value:typeof sample)=>void>=[];
 const {frame}=open(vi.fn(()=>new Promise(resolve=>{finishes.push(resolve);})));fire(canvas,'click',{clientX:20,clientY:10});
 control.bindSession('second');control.update(frame);control.setAvailable(true);fire(canvas,'click',{clientX:20,clientY:10});
 finishes[0]({...sample,frame:cases.valid.frame});await settleMicrotasks();expect(control.blocked()).toBe(true);
 finishes[1]({...sample,frame:cases.valid.frame});await settleMicrotasks();expect(control.blocked()).toBe(false);expect(root.textContent).toContain('18446744073709551615');
});

const pointSample={frame:request.frame,layer:request.layer,world:[10,20],data:[1,2],axes:['y','x'],units:['um','um'],kind:'points',
 hit:{selected:false,index:3,coordinates:['1.25','2.5'],dtype:'<f8',features:[
 {name:'object_id',dtype:'uint64',kind:'integer',value:'18446744073709551615'},
 {name:'category',dtype:'category',kind:'string',value:'<b>literal</b>'},
 {name:'optional',dtype:'UInt64',kind:'missing',value:null}]}};
test('native point features remain exact and malformed replies cannot look successful',()=>{
 expect(inspectionReply(pointSample,request)).toEqual(pointSample);
 for(const patch of [{hit:{...pointSample.hit,selected:1}},{hit:{...pointSample.hit,selected:undefined}},{frame:'bad'},{hit:{...pointSample.hit,index:true}},{hit:{...pointSample.hit,coordinates:['1']}},
  {hit:{...pointSample.hit,features:[{...pointSample.hit.features[0],value:2**64}]}},
  {hit:{...pointSample.hit,features:[pointSample.hit.features[0],pointSample.hit.features[0]]}},
  {hit:{...pointSample.hit,features:[{...pointSample.hit.features[2],value:'unknown'}]}},
  {hit:{...pointSample.hit,features:[{...pointSample.hit.features[1],value:'x'.repeat(4097)}]}}])expect(()=>inspectionReply({...pointSample,...patch},request)).toThrow();
});
test('Points join native inspection with typed feature rows and are cleared when the frame changes',async()=>{
 const send=vi.fn(async(q:InspectionRequest)=>({...pointSample,frame:q.frame,layer:q.layer}));
 // A caller may return either closed inspection schema.
 const state=Reflect.apply(open,null,[send]);state.frame.layers[0].type='points';state.frame.layers[0].name='Annotations';control.update(state.frame);
 expect(field('Inspect layer').textContent).toContain('Annotations');
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();expect(send).toHaveBeenCalledOnce();
 expect(root.textContent).toContain('Point row 3');expect(root.textContent).toContain('18446744073709551615');
 expect(root.textContent).toContain('<b>literal</b>');expect(root.querySelector('b')).toBeNull();
 expect(root.textContent).toContain('Missing');expect(root.querySelector('table')).not.toBeNull();
 state.revoke();expect(root.textContent).not.toContain('18446744073709551615');
});

test('point feature Unicode limits match worker UTF-16 limits',()=>{
 const field={name:'🧬'.repeat(128),dtype:'object',kind:'string',value:'🔬'.repeat(2048)};
 const value={...pointSample,hit:{...pointSample.hit,features:[field]}};
 expect(inspectionReply(value,request)).toEqual(value);
 for(const patch of [{name:'🧬'.repeat(129)},{value:'🔬'.repeat(2049)}])expect(()=>inspectionReply({...value,hit:{...value.hit,features:[{...field,...patch}]}},request)).toThrow();
});

const shapeSample={...pointSample,kind:'shapes',hit:{selected:false,index:0,geometry:'ellipse',dtype:'<f4',vertices:[['1','2'],['1','4'],['3','4'],['3','2']],features:pointSample.hit.features}};
test('shape inspection preserves geometry and rejects malformed vertices or features',()=>{
 expect(inspectionReply(shapeSample,request)).toEqual(shapeSample);
 for(const patch of [{selected:1},{selected:undefined},{geometry:'invented'},{geometry:'line'},{index:true},{vertices:shapeSample.hit.vertices.slice(0,3)},
  {vertices:Array(257).fill(['1','2'])},{vertices:[['nan','2'],...shapeSample.hit.vertices.slice(1)]},
  {vertices:[['1e999','2'],...shapeSample.hit.vertices.slice(1)]},{features:[pointSample.hit.features[0],pointSample.hit.features[0]]}])expect(()=>inspectionReply({...shapeSample,hit:{...shapeSample.hit,...patch}},request)).toThrow();
});
test('Shapes expose native geometry and inert feature rows without inventing polygon approximations',async()=>{
 const send=vi.fn(async(q:InspectionRequest)=>({...shapeSample,frame:q.frame,layer:q.layer}));
 const state=Reflect.apply(open,null,[send]);state.frame.layers[0].type='shapes';state.frame.layers[0].name='Regions';control.update(state.frame);
 expect(field('Inspect layer').textContent).toContain('Regions');fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(root.textContent).toContain('Shape row 0');expect(root.textContent).toContain('ellipse');expect(root.textContent).toContain('not a polygon approximation');
 expect(root.textContent).toContain('4 native control vertices');expect(root.textContent).toContain('18446744073709551615');
 expect(root.textContent).toContain('<b>literal</b>');expect(root.querySelector('b')).toBeNull();expect(root.querySelector('ol')?.children).toHaveLength(4);
 state.revoke();expect(root.textContent).not.toContain('18446744073709551615');
});

test('point selection uses the inspected row and current frame without changing input',async()=>{
 const select=vi.fn(async()=>{});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...pointSample,hit:{...pointSample.hit,selected:false},frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='points';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 const button=root.querySelectorAll('button').find(b=>b.textContent==='Select this point')!;
 fire(button,'click');await settleMicrotasks();
 expect(select).toHaveBeenCalledWith(cases.valid.frame,{kind:'points',layer:request.layer,action:'replace',index:3});
 expect(root.textContent).toContain('Point selection updated');expect(control.blocked()).toBe(false);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 const stale=root.querySelectorAll('button').find(b=>b.textContent==='Add to selection')!;
 state.revoke();fire(stale,'click');await settleMicrotasks();expect(select).toHaveBeenCalledTimes(1);
});
test('a selection after the view redrew is refused in words, never dropped silently',async()=>{
 const select=vi.fn(async()=>{});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...pointSample,hit:{...pointSample.hit,selected:false},frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='points';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 state.redraw();  // the same frame drawn again after the inspection (a background redraw)
 fire(root.querySelectorAll('button').find(b=>b.textContent==='Select this point')!,'click');await settleMicrotasks();
 expect(select).not.toHaveBeenCalled();expect(root.textContent).toContain('The view changed after this inspection. Inspect again, then select.');
});
test('empty point inspection can clear selection without inventing a hit',async()=>{
 const select=vi.fn(async()=>{});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...pointSample,hit:null,frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='points';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(root.textContent).not.toContain('Select this point');
 fire(root.querySelectorAll('button').find(b=>b.textContent==='Clear point selection')!,'click');await settleMicrotasks();
 expect(select).toHaveBeenCalledWith(cases.valid.frame,{kind:'points',layer:request.layer,action:'clear',index:null});
});
test('failed point selection cannot invite replaying an uncertain toggle',async()=>{
 let selected=false;
 const select=vi.fn(async()=>{selected=true;throw new Error('reply lost');});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...pointSample,hit:{...pointSample.hit,selected},frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='points';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 fire(root.querySelectorAll('button').find(b=>b.textContent==='Add to selection')!,'click');await settleMicrotasks();
 expect(root.textContent).toContain('Refresh and inspect the selection before making another change');
 expect(root.textContent).not.toContain('Point selection updated');expect(select).toHaveBeenCalledTimes(1);expect(control.blocked()).toBe(false);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(root.textContent).toContain('Selected in this private view');expect(root.textContent).toContain('Remove from selection');expect(root.textContent).not.toContain('Add to selection');expect(select).toHaveBeenCalledTimes(1);
});

test('changing inspection layer revokes a detached selection button even on the same frame',async()=>{
 const select=vi.fn(async()=>{});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...pointSample,hit:{...pointSample.hit,selected:false},frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='points';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 const old=root.querySelectorAll('button').find(b=>b.textContent==='Select this point')!;
 fire(field('Inspect layer'),'change');fire(old,'click');await settleMicrotasks();
 expect(select).not.toHaveBeenCalled();
});
test('old point selection completion cannot overwrite a newer session read',async()=>{
 let finish!:()=>void;let read!:(value:typeof pointSample)=>void;
 const send=vi.fn(async(q:InspectionRequest)=>({...pointSample,hit:{...pointSample.hit,selected:false},frame:q.frame,layer:q.layer}));
 const state=open(send,()=>new Promise(resolve=>{finish=resolve;}));
 state.frame.layers[0].type='points';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 fire(root.querySelectorAll('button').find(b=>b.textContent==='Select this point')!,'click');
 control.bindSession('second');control.update(state.frame);control.setAvailable(true);
 send.mockImplementationOnce(()=>new Promise(resolve=>{read=resolve;}));
 fire(canvas,'click',{clientX:20,clientY:10});finish();await settleMicrotasks();
 expect(control.blocked()).toBe(true);expect(root.textContent).not.toContain('Point selection updated');
 read({...pointSample,hit:{...pointSample.hit,selected:true},frame:state.frame.frame});await settleMicrotasks();
 expect(control.blocked()).toBe(false);expect(root.textContent).toContain('Selected in this private view');
});

test('shape selection uses the exact inspected row and an authoritative selected readout',async()=>{
 let selected=false;
 const select=vi.fn(async()=>{selected=true;});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...shapeSample,hit:{...shapeSample.hit,selected},frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='shapes';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(root.textContent).toContain('Not selected in this private view');
 fire(root.querySelectorAll('button').find(b=>b.textContent==='Select this shape')!,'click');await settleMicrotasks();
 expect(select).toHaveBeenCalledWith(cases.valid.frame,{kind:'shapes',layer:request.layer,action:'replace',index:0});
 expect(root.textContent).toContain('Shape selection updated');
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(root.textContent).toContain('Selected in this private view');expect(root.textContent).toContain('Remove from selection');
 expect(root.textContent).toContain('18446744073709551615');
 const stale=root.querySelectorAll('button').find(b=>b.textContent==='Remove from selection')!;
 fire(field('Inspect layer'),'change');fire(stale,'click');await settleMicrotasks();expect(select).toHaveBeenCalledTimes(1);
});
test('no-hit shape inspection offers clear without fabricating geometry',async()=>{
 const select=vi.fn(async()=>{});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...shapeSample,hit:null,frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='shapes';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(root.textContent).not.toContain('Select this shape');expect(root.textContent).not.toContain('native control vertices');
 fire(root.querySelectorAll('button').find(b=>b.textContent==='Clear shape selection')!,'click');await settleMicrotasks();
 expect(select).toHaveBeenCalledWith(cases.valid.frame,{kind:'shapes',layer:request.layer,action:'clear',index:null});
});
test('lost shape-toggle response is resolved by reinspection, never by replay',async()=>{
 let selected=false;
 const select=vi.fn(async()=>{selected=true;throw new Error('reply lost');});
 const state=open(vi.fn(async(q:InspectionRequest)=>({...shapeSample,hit:{...shapeSample.hit,selected},frame:q.frame,layer:q.layer})),select);
 state.frame.layers[0].type='shapes';control.update(state.frame);
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 fire(root.querySelectorAll('button').find(b=>b.textContent==='Add to selection')!,'click');await settleMicrotasks();
 expect(root.textContent).toContain('Refresh and inspect the selection');expect(root.textContent).not.toContain('Shape selection updated');
 fire(canvas,'click',{clientX:20,clientY:10});await settleMicrotasks();
 expect(root.textContent).toContain('Selected in this private view');expect(root.textContent).toContain('Remove from selection');expect(select).toHaveBeenCalledTimes(1);
});

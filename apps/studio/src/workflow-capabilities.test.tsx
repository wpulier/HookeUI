import {expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {CapabilityForm,pinnedInputs,type ScientificCapability} from './workflow-capabilities';
const capability:ScientificCapability={id:'response-fit',capability:'response-fit',name:'Fit response model',generation:'pinned',ready:false,reason:'Install the pinned CPU runtime',inputs:[{id:'source',type:'matrix.annotated'}],parameters:[{id:'regularization',type:'number',label:'Regularization',default:1,min:0,max:100}]};
it('shows catalog parameters and exact retained revisions while refusing unavailable execution',()=>{
 const html=renderToStaticMarkup(<CapabilityForm capability={capability} inputs={[{input:{id:'p1',session:'s1',revision:7,digest:'abc'},title:'Training'}]} selected={{source:'p1'}} parameters={{regularization:1}} busy={false} select={()=>{}} parameter={()=>{}} run={()=>{}}/>);
 for(const value of ['Install the pinned CPU runtime','Training · revision 7','Regularization','value="1"','disabled=""'])expect(html).toContain(value);
 expect(html).not.toContain('pinned</');
});
it('a ready capability still requires every declared input',()=>{
 const html=renderToStaticMarkup(<CapabilityForm capability={{...capability,ready:true,reason:null}} inputs={[]} selected={{}} parameters={{}} busy={false} select={()=>{}} parameter={()=>{}} run={()=>{}}/>);
 expect(html).toContain('disabled=""');expect(html).toContain('Choose a retained input');
});

it('preparing an experiment keeps its historical reference even when the inventory has a newer revision',()=>{
 const original={input:{id:'p1',session:'s1',revision:1,digest:'a'},title:'Original'};
 const latest={input:{...original.input,revision:2,digest:'b'},title:'Latest'};
 expect(pinnedInputs([latest],{material:original})).toEqual([original]);
});

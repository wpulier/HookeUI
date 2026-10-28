import {describe,it,expect} from 'vitest';
import {connectStep,removeStep,requestFor, type WorkflowGraph} from './workflow-model';
const graph:WorkflowGraph={format:1,input:{kind:'scientific-scene'},nodes:[
 {id:'a',name:'Review',kind:'review',needs:['input'],settings:{}},
 {id:'b',name:'Measure',kind:'measure',needs:['a'],settings:{}}]};
describe('workflow graph editing',()=>{
 it('rejects cycles without changing accepted graph',()=>{
  expect(()=>connectStep(graph,'b','a')).toThrow(/cycle/);
  expect(graph.nodes[0].needs).toEqual(['input']);
 });
 it('connects only comparison nodes to multiple predecessors',()=>{
  const connected=connectStep(graph,'input','b');expect(connected.nodes[1].needs).toEqual(['input']);
  expect(graph.nodes[1].needs).toEqual(['a']);
 });
 it('refuses removing a connected step until downstream connections are edited',()=>{
  expect(()=>removeStep(graph,'a')).toThrow(/connections/);
  expect(removeStep(graph,'b').nodes).toHaveLength(1);
 });
 it('keeps an unchanged retry identity but changes it when the command changes',()=>{
  const state={body:'',request:''};const first=requestFor(state,{name:'A'},()=> 'a'.repeat(32));
  expect(requestFor(state,{name:'A'},()=> 'b'.repeat(32))).toBe(first);
  expect(requestFor(state,{name:'B'},()=> 'b'.repeat(32))).not.toBe(first);
 });
});

it('keeps unconfirmed retries but retires successful invocation identities',async()=>{
 const {confirmRequest}=await import('./workflow-model');
 const state={body:'',request:''},body={action:'start',workflow:'w',revision:1,inputs:[]};
 const first=requestFor(state,body,()=> 'a'.repeat(32));
 expect(requestFor(state,body,()=> 'b'.repeat(32))).toBe(first);
 confirmRequest(state,first);
 const second=requestFor(state,body,()=> 'b'.repeat(32));expect(second).not.toBe(first);
 confirmRequest(state,first); // An older acknowledgement cannot erase a newer pending retry.
 expect(requestFor(state,body,()=> 'c'.repeat(32))).toBe(second);
});

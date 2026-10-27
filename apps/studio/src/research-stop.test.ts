import {beforeEach,expect,test,vi} from 'vitest';
import type {ResearchSession} from './protocol';

const row=(rid:string,status:string):ResearchSession=>({rid,n:1,name:rid,label:rid,brief:'',deliverable:null,effort:'quick',engine:'x',purpose:'research',objectId:null,status,step:'',spent:0,cap:0,seconds:0,created:1,started:1,finished:null,snapshot:'',outputs:[],items:[],findings:[],corrections:[],history:[],canFollowUp:true,sitting:'p1',result:null} as ResearchSession);
const storage=()=>{const m=new Map<string,string>();return {getItem:(k:string)=>m.get(k)??null,setItem:(k:string,v:string)=>void m.set(k,v),removeItem:(k:string)=>void m.delete(k),clear:()=>m.clear(),key:()=>null,length:0} as Storage;};
beforeEach(()=>{vi.resetModules();vi.stubGlobal('sessionStorage',storage());});

test('Stop sends research-cancel and reads Stop requested until the server lists the researcher as not working',async()=>{
 const {requestStop,stopRequested,stopButtonWords}=await import('./research-stop');
 const send=vi.fn();requestStop('r1',send);
 expect(send).toHaveBeenCalledWith({type:'research-cancel',rid:'r1'});
 expect(stopButtonWords(row('r1','running'))).toMatchObject({text:'Stop requested',disabled:true});
 expect(stopRequested(row('r1','queued'))).toBe(true); // still in line: the request stands
 expect(stopRequested(row('r2','running'))).toBe(false); // another researcher
 expect(stopRequested(row('r1','interrupted'))).toBe(false); // the server listed it as stopped: the request is over
 expect(stopButtonWords(row('r1','running'))).toMatchObject({text:'Stop',disabled:false}); // and a new Stop is possible
});

test('a stop request survives a redraw in the same browser tab',async()=>{
 const first=await import('./research-stop');first.requestStop('r1',vi.fn());
 vi.resetModules();const second=await import('./research-stop');
 expect(second.stopRequested(row('r1','running'))).toBe(true);
});

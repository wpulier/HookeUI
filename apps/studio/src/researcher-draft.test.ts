import {expect,test} from 'vitest';
import {adoptedResearcher,beginStart,draftLine,settleStart} from './researcher-draft';
import type {ResearchSession} from './protocol';

const row=(rid:string,sitting='p1'):ResearchSession=>({rid,n:1,name:rid,label:rid,brief:'',deliverable:null,effort:'quick',engine:'x',purpose:'research',objectId:null,status:'running',step:'',spent:0,cap:0,seconds:0,created:1,started:1,finished:null,snapshot:'',outputs:[],items:[],findings:[],corrections:[],history:[],canFollowUp:true,sitting,result:null});

test('a start says Sending…, then Accepted; it never says a researcher exists until the server lists a new one',()=>{
 const start=beginStart('p1',['r1'],'req-1');
 expect(draftLine(null)).toBe('Not started');expect(draftLine(start)).toBe('Sending…');
 expect(settleStart(start,{requestId:'other',ok:true,error:null})).toEqual({kind:'ignored'});
 const settled=settleStart(start,{requestId:'req-1',ok:true,error:null});
 expect(settled.kind).toBe('accepted');if(settled.kind!=='accepted')throw new Error('accepted');
 expect(draftLine(settled.start)).toBe('Accepted · waiting for the researcher');
 expect(adoptedResearcher(settled.start,[row('r1')],'p1')).toBeNull(); // the one that already existed is not ours
 expect(adoptedResearcher(settled.start,[row('r1'),row('r2','p2')],'p1')).toBeNull(); // another Project's is not ours
 expect(adoptedResearcher(settled.start,[row('r1'),row('r2')],'p1')?.rid).toBe('r2');
 expect(adoptedResearcher(settled.start,[row('r1'),row('r2')],'p2')).toBeNull(); // the Project changed meanwhile
});

test('a request the Assistant took whose start was refused (a missing key) is said at once, never waited on',()=>{
 const start=beginStart('p1',[],'req-1');
 const words='GPT-6.1 Sol, the model researchers use, needs your OpenAI key; add it in Models › Keys, then try again';
 expect(settleStart(start,{requestId:'req-1',ok:true,error:null,refused:words+'.'})).toEqual({kind:'declined',words});
 expect(settleStart(start,{requestId:'req-1',ok:true,error:null,refused:null})).toMatchObject({kind:'accepted'});
});

test('a refused request fails with its reason, trailing stop removed; no reason given is said',()=>{
 const start=beginStart('p1',[],'req-1');
 expect(settleStart(start,{requestId:'req-1',ok:false,error:'the planner did not answer.'})).toEqual({kind:'failed',reason:'the planner did not answer'});
 expect(settleStart(start,{requestId:'req-1',ok:false,error:null})).toEqual({kind:'failed',reason:'no reason given'});
 expect(settleStart(null,{requestId:'req-1',ok:false,error:null})).toEqual({kind:'ignored'});
});

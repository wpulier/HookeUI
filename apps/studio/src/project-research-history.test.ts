// Synthetic domain/transport checks. Actual Project browser evidence is separate.
import {expect,test,vi} from 'vitest';
import {actorOrigin,contributors,history,operationPhase,readProjectActivity,studyActivity,type ResearchOperation} from './project-research-history';
import type {Effort} from './research-efforts';
const study=():Effort=>({id:'s',session:'p',revision:1,digest:'d',objective:'Compare',criteria:'Measured',status:'active',capMicrousd:0,spentMicrousd:0,heldMicrousd:0,availableMicrousd:0,inputs:[],tasks:{},findings:[],experiments:{e:{operation:'o',by:'agent:ac_a',byLabel:'Worker',execution:{id:'r',session:'p'}}}});
const op=():ResearchOperation=>({id:'o',project:'p',by:'agent:ac_a',byLabel:'Worker',state:'succeeded',at:20,workflow:{id:'r',session:'p',status:'completed'}});
test('counts stable identities across Studies, not matching labels or experiment counts',()=>{
 const a=study();a.createdBy={by:'agent:ac_a',byLabel:'Old label'};a.claims=[{by:'agent:ac_b',byLabel:'Worker',request:'c',statement:'Report',evidence:[],dependsOn:[]}];
 const b=study();b.id='other';b.claims=[{by:'agent:ac_a',byLabel:'New label',request:'x',statement:'Next',evidence:[],dependsOn:[]}];
 const rows=contributors([a,b]);expect(rows).toHaveLength(2);expect(rows.find(r=>r.id==='agent:ac_a')?.experiments).toBe(2);
 expect(actorOrigin('agent:chat')).toBe('Hooke Assistant');expect(actorOrigin('studio')).toBe('Person in Hooke');
 a.claims.push({byLabel:'Anonymous',request:'old',statement:'Legacy',evidence:[],dependsOn:[]});expect(contributors([a,b])).toHaveLength(2);
});
test('an open Study and an authorized connection do not imply running work',()=>{
 const s=study();expect(studyActivity(s).title).toBe('Activity needs refreshing');
 expect(studyActivity(s,{operations:[op()]}).title).toBe('No experiments running');
 const r=op();r.state='running';r.workflow!.status='running';expect(operationPhase(r)).toBe('running');
 r.workflow!.executionObservation={state:'interrupted',reason:'No driver'};expect(operationPhase(r)).toBe('check');
 r.workflow!.status='awaiting-review';r.workflow!.executionObservation={state:'idle',reason:'Review required'};expect(operationPhase(r)).toBe('waiting');
 expect(studyActivity(s,{operations:[r],error:'Offline'}).title).toBe('Activity needs refreshing');
});
test('foreign receipts cannot establish current Project activity',()=>{
 const r=op();r.project='other';expect(studyActivity(study(),{operations:[r]}).unavailable).toBe(true);
 r.project='p';r.workflow!.session='other';expect(studyActivity(study(),{operations:[r]}).unavailable).toBe(true);
});
test('pending questions are waiting for pickup, and history orders actual timestamps',()=>{
 const s=study();s.createdAt=10;s.createdBy={by:'agent:ac_a',byLabel:'Lead'};
 s.questions=[{id:'q',at:30,by:'studio',byLabel:'You',question:'Why?',recipient:{actor:'agent:ac_b',label:'Reviewer'},answer:null}];
 const snapshot={operations:[op()]};expect(studyActivity(s,snapshot).title).toBe('1 question awaiting a reply');
 expect(history(s,snapshot).map(e=>e.at)).toEqual([30,20,10]);
 expect(history(s,snapshot)[0].detail).toContain('pickup not confirmed');
});
test('activity reader refuses mismatched evidence and keeps individual failures explicit',async()=>{
 const a=study(),b=study();b.id='second';
 const fetcher=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>({ok:true,study:{id:'wrong',session:'p'},operations:[op()]})}).mockRejectedValueOnce(new Error('Offline'));
 const result=await readProjectActivity([a,b],fetcher);
 expect(result.s.error).toContain('did not match');expect(result.second.error).toBe('Offline');
 expect(fetcher).toHaveBeenCalledTimes(2);
});

test('local-only Studies reconcile original researcher receipts and return refreshed Study state',async()=>{
 const s=study();s.experiments={};s.tasks={local:{status:'running',reservedMicrousd:0,chargedMicrousd:null,execution:{provider:'local-researcher',id:'r1'}}};
 const completed={...s,tasks:{local:{...s.tasks.local,status:'completed'}}};
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({ok:true,study:completed,operations:[]})});
 const result=await readProjectActivity([s],fetcher);expect(fetcher).toHaveBeenCalledOnce();
 expect(result.s.study?.tasks.local.status).toBe('completed');
 expect(studyActivity(result.s.study!,result.s).title).toBe('No experiments running');
});

test('history qualifies interrupted, unstarted and stale receipts instead of repeating stored running',()=>{
 const r=op();r.state='running';r.workflow!.status='running';
 r.workflow!.executionObservation={state:'interrupted',reason:'Worker stopped'};
 expect(history(study(),{operations:[r]})[0].detail).toContain('needs checking — Worker stopped');
 expect(history(study(),{operations:[r],error:'Offline'})[0].detail).toContain('last recorded state');
 r.workflow!.executionObservation={state:'unstarted',reason:'Not dispatched'};
 expect(history(study(),{operations:[r]})[0].detail).toContain('waiting — Not dispatched');
});

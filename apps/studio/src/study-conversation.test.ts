import {afterEach, expect, test, vi} from 'vitest';
import {studyConversation, type QuestionState} from './study-conversation';
import type {Effort} from './research-efforts';
import {syntheticDOM, TestElement, fire} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());
const study:Effort={id:'p'+'a'.repeat(32),session:'2026-10-28',revision:7,digest:'b'.repeat(64),objective:'Compare cells',criteria:'Evidence',status:'active',capMicrousd:0,spentMicrousd:0,heldMicrousd:0,availableMicrousd:0,inputs:[],tasks:{},findings:[]};
const recipient={actor:'agent:ac_'+'c'.repeat(24),label:'Connected reviewer'};
const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status});
function fixture(fetcher:typeof fetch,current=()=>true){const dom=syntheticDOM();vi.stubGlobal('document',dom.document);const open=vi.fn(async()=>{});const component=studyConversation(study,{fetcher,current,open});const root=component.root as unknown as TestElement;return{...component,root,open,field:root.querySelector('textarea')!,form:root.querySelector('form')!,select:root.querySelector('select')!};}

test('a lost reply retries the identical question and context; sends never replace the draft until retained',async()=>{
  const posts:Record<string,unknown>[]=[];let fail=true;
  const fetcher=vi.fn(async(_url,init)=>{if(init?.method==='POST'){posts.push(JSON.parse(String(init.body)));if(fail)throw new Error('Lost reply');return json({ok:true});}return json({ok:true,questions:[],recipients:[recipient]});}) as unknown as typeof fetch;
  const c=fixture(fetcher);await c.refresh();c.select.value=recipient.actor;c.field.value='what does it mean?';
  fire(c.form,'submit',{});fire(c.form,'submit',{});
  await vi.waitFor(()=>expect(c.root.textContent).toContain('Lost reply'));
  expect(posts).toHaveLength(1);expect(c.field.value).toBe('what does it mean?');
  c.update({...study,revision:8,digest:'d'.repeat(64)});fail=false;fire(c.form,'submit',{});
  await vi.waitFor(()=>expect(c.field.value).toBe(''));
  expect(posts).toHaveLength(2);expect(posts[1]).toEqual(posts[0]);expect(posts[0].basis).toEqual({id:study.id,session:study.session,revision:7,digest:study.digest});
});

test('an attributed real response projection stays inline with exact evidence and refresh preserves a follow-up draft',async()=>{
  const ref={id:study.id,session:study.session,revision:7,digest:study.digest};
  const state:QuestionState={recipients:[recipient],questions:[{id:'q_one',question:'what does it mean?',basis:ref,recipient,byLabel:'You',answer:null}]};
  const c=fixture(vi.fn(async()=>json({ok:true,...state})) as unknown as typeof fetch);await c.refresh();
  expect(c.root.textContent).toContain('Waiting for Connected reviewer');
  c.field.value='Follow-up draft';state.questions[0].answer={by:recipient.actor,byLabel:recipient.label,statement:'The generator failed its fixed criterion.',limitations:'Development evidence only.',evidence:[ref]};await c.refresh();
  expect(c.root.textContent).toContain('The generator failed');expect(c.root.textContent).toContain('Development evidence only');expect(c.field.value).toBe('Follow-up draft');
  const link=c.root.querySelectorAll('button').find(n=>n.textContent?.startsWith('Open evidence'))!;fire(link,'click',{});
  expect(c.open).toHaveBeenCalledWith(ref,'Answer evidence');
});

test('changed Project and no contributor never submit; connection readiness is not a claim of execution',async()=>{
  let current=true;const fetcher=vi.fn(async()=>json({ok:true,questions:[],recipients:[]}));const c=fixture(fetcher as unknown as typeof fetch,()=>current);await c.refresh();
  c.field.value='question';fire(c.form,'submit',{});expect(fetcher).toHaveBeenCalledTimes(1);
  current=false;c.select.value=recipient.actor;fire(c.form,'submit',{});await c.refresh();expect(fetcher).toHaveBeenCalledTimes(1);
  expect(c.field.value).toBe('question');expect(c.root.textContent).toContain('No connected contributor');
});

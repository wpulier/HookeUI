import {afterEach,expect,test,vi} from 'vitest';
import {evidenceActions} from './research-evidence';
import {fire,syntheticDOM,TestElement} from './workspace-windows.test-support';
import type {ClientCommand,ResearchSession} from './protocol';
afterEach(()=>vi.unstubAllGlobals());

test('evidence response awaits acknowledgement and retries a lost reply with the same durable identity',async()=>{
  vi.stubGlobal('document',syntheticDOM().document);
  const values=new Map<string,string>();vi.stubGlobal('sessionStorage',{getItem:(k:string)=>values.get(k),setItem:(k:string,v:string)=>values.set(k,v)});
  const session={rid:'ack-test',day:'2026-10-10',snapshot:''} as ResearchSession;
  const question={id:'q1',run:1,what:'hold',why:'check',seconds:10,at:1,status:'asked' as const};
  let reject!: (e:Error)=>void;const sent:ClientCommand[]=[];
  const send=vi.fn((command:ClientCommand)=>{sent.push(command);return new Promise((_resolve,no)=>{reject=no;});});
  const actions=evidenceActions(session,question,send) as unknown as TestElement;
  const hold=actions.querySelector('button')!;fire(hold,'click');
  expect(actions.textContent).not.toContain('Response sent');expect(hold.disabled).toBe(true);
  reject(new Error('Disconnected before acknowledgement'));await Promise.resolve();await Promise.resolve();await Promise.resolve();
  expect(actions.textContent).toContain('Disconnected');expect(hold.disabled).toBe(false);
  send.mockImplementation((command)=>{sent.push(command);return Promise.resolve({ok:true,requestId:'requestId' in command?command.requestId:undefined});});
  fire(hold,'click');await Promise.resolve();await Promise.resolve();await Promise.resolve();
  expect((sent[0] as {requestId:string}).requestId).toBe((sent[1] as {requestId:string}).requestId);
  expect(actions.textContent).toContain('Response sent');expect(hold.disabled).toBe(true);
});

test('missing or rejected evidence acknowledgement never claims success',async()=>{
  vi.stubGlobal('document',syntheticDOM().document);
  const session={rid:'rejected-test',day:'2026-10-10',snapshot:''} as ResearchSession;
  const question={id:'q1',what:'hold',why:'check',seconds:10,at:1,status:'asked' as const};
  const actions=evidenceActions(session,question,()=>Promise.resolve({ok:false,error:'Question no longer writable'})) as unknown as TestElement;
  fire(actions.querySelector('button')!,'click');await Promise.resolve();await Promise.resolve();await Promise.resolve();
  expect(actions.textContent).not.toContain('Response sent');expect(actions.textContent).toContain('Question no longer writable');
  expect(actions.querySelector('button')!.disabled).toBe(false);
});

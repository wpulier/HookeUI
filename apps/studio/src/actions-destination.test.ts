import { afterEach, expect, test, vi } from 'vitest';
import { destinationWords } from './actions-panel';
afterEach(() => vi.unstubAllGlobals());

test('a tool says where its results go: the work Tab in view, by its name', () => {
  vi.stubGlobal('document', { querySelector: (s: string) => (s === '.workspace-view-tabs [aria-selected=true]' ? { textContent: ' Counting ' } : null) });
  expect(destinationWords()).toBe('Results go to Counting');
  vi.stubGlobal('document', { querySelector: () => null });
  expect(destinationWords()).toBe('Results go to this Tab');
  // A researcher's Tab keeps its own work: This Tab says to choose New Tab (no "Run in new Tab" any more).
  vi.stubGlobal('document', { querySelector: () => ({ textContent: 'Researcher 3' }), body: { dataset: { workspaceStudy: 'true', researchOpen: 'true' } } });
  expect(destinationWords()).toBe('A researcher’s Tab keeps its own work: choose New Tab');
});

test('On names a picture by its short title and keeps the camera’s words whole', async () => {
  const { specimenOrCamera } = await import('./actions-panel');
  expect(specimenOrCamera('Human cells · David Root (CC0)')).toBe('Human cells');
  expect(specimenOrCamera('Live · iPhone 17 Pro')).toBe('Live · iPhone 17 Pro');
  expect(specimenOrCamera('Camera · needs attention')).toBe('Camera · needs attention');
});

test('researcher Run requires an explicit New Tab, carries it through cloud consent, and keeps Run one line',async()=>{
  const {ActionsPanel}=await import('./actions-panel');
  const {syntheticDOM,fire,settleMicrotasks}=await import('./workspace-windows.test-support');
  const dom=syntheticDOM();dom.host.dataset.workspaceStudy='true';dom.host.dataset.researchOpen='true';
  Object.assign(dom.document,{querySelector:()=>({textContent:'Counting'})});
  vi.stubGlobal('document',dom.document);
  const root=dom.document.createElement('section');const run=vi.fn<import('./actions-panel').ActionsDeps['run']>(async()=>({ok:true,summary:'Done'}));
  const panel=new ActionsPanel(root as unknown as HTMLElement,{run,stop:async()=>({ok:true}),selected:()=>null,session:()=> 'p',openModels:()=>{},openActivity:()=>{},openTable:()=>{},openCamera:()=>{},cameraPage:()=>'/camera/'});
  panel.setTools({clear:{kind:'effect',family:'scene',params:{type:'object',properties:{}}}});
  panel.setCapabilities({type:'capabilities',families:[],actors:{you:'',agent:'',auto:'',researcher:''},preferences:[],pending:0,build:'',agentText:'',roles:[],capabilities:[{id:'clear',family:'outlining',actor:'agent',title:'Clear',what:'',how:{tool:'clear'},available:true,why:'',verdict:null}]});
  const row=panel.rowsShown()[0];row.cloud='Cloud';
  await panel.start(row);expect(run).not.toHaveBeenCalled();expect(root.textContent).toContain('Choose New Tab under Results go to');
  // Results go to: on a researcher's Tab, This Tab can't take results, and nothing is chosen for it.
  const dest=root.querySelector('.run-dest')!;const [here,next]=dest.querySelectorAll('button');
  expect(here.disabled).toBe(true);expect(next.getAttribute('aria-pressed')).toBe('false');expect(here.title).toBe('A researcher’s Tab keeps its own work: choose New Tab');
  fire(next,'click');expect(run).not.toHaveBeenCalled(); // choosing a destination runs nothing
  await panel.start(row);
  expect(run).not.toHaveBeenCalled();const consent=root.querySelector('.action-ask')!;
  const send=consent.querySelectorAll('button').find(b=>b.textContent==='Send')!;fire(send,'click');await settleMicrotasks();
  expect(run).toHaveBeenCalledOnce();expect(run.mock.calls[0]?.at(7)).toEqual({newTab:true});
  // Back on a work Tab, the New Tab chosen for the researcher's Tab is not inherited.
  delete dom.host.dataset.researchOpen;fire(root.querySelector('.action-again')!,'click');await panel.start(panel.rowsShown()[0]);
  expect(run).toHaveBeenCalledTimes(2);expect(run.mock.calls[1]?.at(7)).toBeUndefined();
  fire(root.querySelector('.action-again')!,'click');
  // The destination moved off the label: Run is one line, and This Tab says where results go.
  const go=root.querySelector('[data-action="clear"]')?.querySelector('.action-go');
  expect(go?.textContent).toBe('Run');expect(go?.textContent).not.toContain('results go to');
  expect(root.querySelector('.run-dest')!.querySelectorAll('button')[0].title).toBe('Results go to Counting');
});

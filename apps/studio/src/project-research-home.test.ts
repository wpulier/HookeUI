// Synthetic rendering/ownership evidence; actual retained-Project browser acceptance is separate.
import {afterEach, expect, test, vi} from 'vitest';
import {ProjectResearchHome} from './project-research-home';
import type {Effort} from './research-efforts';
import {TestElement, syntheticDOM, fire} from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());
const study = (session = 'p1'): Effort => ({id:'s1',session,revision:1,digest:'digest',title:'Response transfer',
  objective:'Does the fitted response generalize?',criteria:'Measured error',status:'active',capMicrousd:0,
  spentMicrousd:0,heldMicrousd:0,availableMicrousd:0,inputs:[{id:'data',revision:2,digest:'data-digest'}],tasks:{},
  createdAt:1793230000,createdBy:{by:'agent:ac_one',byLabel:'Codex'},
  experiments:{first:{by:'agent:ac_one',operation:'op1',byLabel:'Codex',execution:{id:'run1',session}}},findings:[],
  claims:[{by:'agent:ac_two',request:'claim',statement:'Observed a limitation',byLabel:'Claude Code',evidence:[],dependsOn:[]}]});
function setup() {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const host = new TestElement('section'); let current = 'p1';
  const actions = {current: () => current, openStudy:vi.fn(),data:vi.fn(),inputs:vi.fn(),retry:vi.fn()};
  const home = new ProjectResearchHome(host as unknown as HTMLElement, actions);
  const root = home.root as unknown as TestElement;
  const button = (text: string) => root.querySelectorAll('button').find(item => item.textContent === text) as unknown as HTMLButtonElement;
  return {home,host,root,actions,button,setCurrent:(id:string) => {current=id;}};
}
test('retained question and evidence replace new-project onboarding without starting research', () => {
  const {home,host,root,actions,button} = setup(); home.update('p1',[study()]);
  expect(host.dataset.projectResearch).toBe('true'); expect(root.hidden).toBe(false);
  expect(root.textContent).toContain('Does the fitted response generalize?');
  expect(root.textContent).toContain('1 inputs'); expect(root.textContent).toContain('1Experiments');
  expect(root.textContent).toContain('1 reports'); expect(root.textContent).toContain('0Findings');
  expect(root.textContent).toContain('Latest report'); expect(root.textContent).toContain('Observed a limitation');
  expect(root.textContent).not.toContain('Start a Project'); expect(actions.openStudy).not.toHaveBeenCalled();
  fire(button('Open Study') as unknown as TestElement, 'click'); expect(actions.openStudy).toHaveBeenCalledWith('s1');
  fire(button('Project data') as unknown as TestElement, 'click'); expect(actions.data).toHaveBeenCalledOnce();
  fire(button('Add material') as unknown as TestElement, 'click'); expect(actions.inputs).toHaveBeenCalledOnce();
});
test('another Project, a stale button and a late foreign Study cannot redirect the current workspace', () => {
  const {home,host,root,actions,button,setCurrent} = setup();home.update('p1',[study()]);const old=button('Open Study');
  setCurrent('p2'); fire(old as unknown as TestElement, 'click'); expect(actions.openStudy).not.toHaveBeenCalled();
  home.update('p2',[study()]); expect(root.hidden).toBe(true); expect(host.dataset.projectResearch).toBe('false');
  setCurrent('p1');home.update('p1',[study()]);fire(old as unknown as TestElement, 'click');expect(actions.openStudy).not.toHaveBeenCalled();
  fire(button('Open Study') as unknown as TestElement, 'click');expect(actions.openStudy).toHaveBeenCalledOnce();
});
test('unchanged polling preserves the action node and a failed refresh qualifies the retained overview', () => {
  const {home,root,button} = setup(); home.update('p1',[study()]);const first=button('Open Study');
  home.update('p1',[study()]);expect(button('Open Study')).toBe(first);
  home.update('p1',[study()],'Reconnect to refresh.');expect(root.textContent).toContain('Showing saved history. Reconnect to refresh.');
  home.update('p1',[]);expect(root.hidden).toBe(true);
});

test('latest attributed report remains distinct from findings and exposes its limitations', () => {
  const {home,root} = setup(); const value = study();
  value.claims!.push({request:'new',statement:'<script>untrusted</script> New result',byLabel:'Codex',evidence:[],dependsOn:[],limitations:'No independent validation'});
  value.findings.push({request:'finding',statement:'Retained finding',tasks:[]});
  home.update('p1',[value]);
  expect(root.textContent).toContain('2 reports'); expect(root.textContent).toContain('1Findings');
  expect(root.textContent).toContain('No independent validation');
  expect(root.textContent).toContain('Researcher report · not an accepted finding');
  expect(root.textContent).toContain('Research history');
  expect(root.querySelectorAll('script')).toHaveLength(0);
  expect(root.textContent).toContain('Contributors & origins');
  expect(root.textContent).toContain('2Agents contributed');
});
test('a study with no report offers an honest empty state and retains its question', () => {
  const {home,root} = setup();const value=study();value.claims=[];home.update('p1',[value]);
  expect(root.textContent).toContain('Reports will appear here as the work develops.');
  expect(root.textContent).toContain('Measured error');
  expect(root.textContent).not.toContain('Latest report');
});

test('same-Project updates preserve disclosures, but a new report or Project starts closed', () => {
  const {home,root} = setup(); home.update('p1',[study()]);
  const details = () => root.querySelectorAll('details') as unknown as HTMLDetailsElement[];
  details().forEach(item => { item.open = true; });
  const changed = study(); changed.revision++;
  home.update('p1',[changed], 'Refreshing');
  expect(details().every(item=>item.open)).toBe(true);
  changed.claims!.push({request:'replacement',statement:'Next result',byLabel:'Codex',evidence:[],dependsOn:[]});
  home.update('p1',[changed]);
  expect(details().find(item=>item.dataset.disclosure==='study:s1:question')?.open).toBe(true);
  expect(details().find(item=>item.dataset.disclosure?.endsWith(':report:replacement'))?.open).not.toBe(true);
  home.update('p2',[study('p2')]);
  expect(details().some(item => item.open)).toBe(false);
});

test('Main includes only this Projects researchers and preserves their identities across same-day Projects',()=>{
 const {home,root}=setup();
 const researchers=[
  {rid:'r1',day:'shared-day',sitting:'p1',purpose:'research',name:'Here',brief:'Our question',status:'done',engine:'test',created:10},
  {rid:'r2',day:'p1',sitting:'p2',purpose:'research',name:'Elsewhere',brief:'Other question',status:'running',engine:'test',created:20},
 ] as import('./protocol').ResearchSession[];
 home.update('p1',[study()],'',{},researchers);
 expect(root.textContent).toContain('Here');expect(root.textContent).not.toContain('Elsewhere');
 expect(root.textContent).toContain('0 Hooke researchers working');expect(root.textContent).toContain('3Agents contributed');
});
test('Main aggregates all owned Studies without counting another Projects effort',()=>{
 const {home,root}=setup();const a=study(),b=study();b.id='s2';b.title='Second question';
 home.update('p1',[a,b,study('other')]);
 expect(root.textContent).toContain('2Studies');expect(root.textContent).toContain('2Agents contributed');
 expect(root.textContent).toContain('2Experiments');expect(root.textContent).toContain('Second question');
});

test('retained material replaces camera onboarding before any Study exists',()=>{
 const {home,host,root,button,actions,setCurrent}=setup();
 home.update('p1',[],'',{},[],{count:3,groups:['Reference documents']});
 expect(host.dataset.projectResearch).toBe('true');expect(root.hidden).toBe(false);
 expect(root.textContent).toContain('Your Project material');
 expect(root.textContent).toContain('3 Project inputs');
 expect(root.textContent).toContain('Reference documents');
 expect(root.textContent).toContain('No Study yet');
 expect(root.textContent).not.toContain('0Experiments');
 const browse=button('Browse inputs');fire(browse as unknown as TestElement,'click');
 expect(actions.inputs).toHaveBeenCalledOnce();
 setCurrent('p2');fire(browse as unknown as TestElement,'click');expect(actions.inputs).toHaveBeenCalledOnce();
 home.update('p2',[]);expect(root.hidden).toBe(true);
});
test('material refresh failures qualify retained counts without clearing the Project',()=>{
 const {home,root}=setup();home.update('p1',[],'',{},[],{count:1,groups:[],error:'Project material could not be refreshed.'});
 expect(root.textContent).toContain('1 Project input');
 expect(root.textContent).toContain('Project material could not be refreshed.');
 expect(root.hidden).toBe(false);
});

test('material freshness does not invalidate independent workflow observations',()=>{
 const {home,root}=setup();const value=study();value.experiments={};
 home.update('p1',[value],'',{s1:{operations:[]}},[],{count:1,groups:[],error:'Material refresh failed'});
 expect(root.textContent).toContain('Material refresh failed');
 expect(root.textContent).toContain('No experiments running');
 home.update('p1',[value],'',{s1:{operations:[]}},[],{count:1,groups:[]});
 expect(root.textContent).not.toContain('Material refresh failed');
});


test('initial loading and failed reads never masquerade as an empty Project',()=>{
 const {home,host,root,actions,button}=setup();
 home.update('p1',[],'',{},[],{count:0,groups:[],loading:true});
 expect(host.dataset.projectResearch).toBe('true');
 expect(root.textContent).toContain('Opening your Project');
 expect(root.textContent).not.toContain('No Study yet');
 home.update('p1',[],'',{},[],{count:0,groups:[],error:'Material read failed'});
 expect(root.textContent).toContain('Project history could not be loaded');
 fire(button('Retry') as unknown as TestElement,'click');expect(actions.retry).toHaveBeenCalledOnce();
 home.update('p1',[],'',{},[],{count:0,groups:[]});
 expect(host.dataset.projectResearch).toBe('false');expect(root.hidden).toBe(true);
});
test('material arriving before Studies keeps the populated overview through refresh',()=>{
 const {home,root}=setup();
 home.update('p1',[],'',{},[],{count:2,groups:[],loading:true});
 expect(root.textContent).toContain('2 Project inputs');
 expect(root.textContent).not.toContain('Opening your Project');
 expect(root.textContent).not.toContain('No Study yet');
 home.update('p1',[study()],'',{},[],{count:2,groups:[],loading:false});
 expect(root.textContent).toContain('Response transfer');
});


test('a return to the same Project waits for new reads and rejects its previous buttons',()=>{
 const {home,root,button,actions,setCurrent}=setup();
 home.update('p1',[study()]);const original=button('Open Study');
 setCurrent('p2');home.update('p2',[],'',{},[],{count:0,groups:[],loading:true});
 setCurrent('p1');home.update('p1',[],'',{},[],{count:0,groups:[],loading:true});
 fire(original as unknown as TestElement,'click');expect(actions.openStudy).not.toHaveBeenCalled();
 expect(root.textContent).toContain('Opening your Project');
 home.update('p1',[],'',{},[],{count:2,groups:[],loading:false});
 expect(root.textContent).toContain('2 Project inputs');
});

 test('slow initial reads offer recovery without presenting an empty Project',()=>{
 const {home,root,button,actions}=setup();
 home.update('p1',[],'',{},[],{count:0,groups:[],loading:true,slow:true});
 expect(root.textContent).toContain('Loading is taking longer');
 fire(button('Retry') as unknown as TestElement,'click');expect(actions.retry).toHaveBeenCalledOnce();
 fire(button('Browse Project data') as unknown as TestElement,'click');expect(actions.data).toHaveBeenCalledOnce();
 });

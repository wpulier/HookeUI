// Synthetic rendering/ownership evidence for the Project front page; actual retained-Project browser acceptance is separate.
import {afterEach, expect, test, vi} from 'vitest';
import {ProjectResearchHome, type ProjectFrontState} from './project-research-home';
import type {Effort} from './research-efforts';
import type {ResearchSession} from './protocol';
import {TestElement, syntheticDOM, fire, settleMicrotasks} from './workspace-windows.test-support';
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const NOW = 1793920000;
const study = (session = 'p1', fields: Partial<Effort> = {}): Effort => ({id:'s1',session,revision:1,digest:'digest',title:'Response transfer',
  objective:'Does the fitted response generalize?',criteria:'Measured error',status:'active',capMicrousd:0,
  spentMicrousd:0,heldMicrousd:0,availableMicrousd:0,inputs:[{id:'data',revision:2,digest:'data-digest'}],tasks:{},
  createdAt:1793230000,createdBy:{by:'agent:ac_one',byLabel:'Codex'},findings:[],
  claims:[{by:'agent:ac_two',at:NOW-600,request:'claim',statement:'C3 beat the safety entry on the held-out test',byLabel:'Claude Code',evidence:[],dependsOn:[]}],
  ...fields});
const front = (fields: Partial<ProjectFrontState> = {}): ProjectFrontState => ({about:null,page:null,next:[],revision:1,...fields});
function setup(extra: Record<string, unknown> = {}) {
  vi.useFakeTimers(); vi.setSystemTime(NOW * 1000);
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const host = new TestElement('section'); let current = 'p1';
  const actions = {current: () => current, openStudy:vi.fn(),data:vi.fn(),inputs:vi.fn(),retry:vi.fn(),startResearch:vi.fn(),
    openPage:vi.fn(),draft:vi.fn(),saveAbout:vi.fn(async () => {}),setPage:vi.fn(async () => {}),addStep:vi.fn(async () => {}),finishStep:vi.fn(async () => {}),...extra};
  const home = new ProjectResearchHome(host as unknown as HTMLElement, actions);
  const root = home.root as unknown as TestElement;
  const buttons = () => root.querySelectorAll('button');
  const button = (text: string) => buttons().find(item => item.textContent === text) as unknown as TestElement;
  const column = (key: string) => root.querySelectorAll('.pf-col').find(col => col.dataset.column === key)!;
  return {home,host,root,actions,button,buttons,column,setCurrent:(id:string) => {current=id;}};
}

test('the front page says what the Project is, its page and how it was made, and starts nothing', () => {
  const {home,host,root,actions,button} = setup();
  home.setFront({name:'Virtual Cell Challenge — research',front:front({about:'Predict how cells respond.',
    page:{id:'ppage',revision:2,digest:'d',title:'Our entry',name:'entry.html',savedAt:NOW-60}})});
  home.update('p1',[study()],'',{},[],{count:3,groups:[]});
  expect(host.dataset.projectResearch).toBe('true');
  expect(root.textContent).toContain('Virtual Cell Challenge — research');
  expect(root.textContent).toContain('Predict how cells respond.');
  expect(root.textContent).toContain('1 Study · 3 inputs');
  const frame = root.querySelector('iframe')!;
  expect(frame.attributes.sandbox).toBe('allow-scripts');
  expect((frame as unknown as {src:string}).src).toBe('/api/scientific-files/ppage/page?session=p1&revision=2');
  expect(root.textContent).toContain('Up to date with everything up to');
  expect(root.textContent).toContain('How it was made');
  expect(root.textContent).toContain('Does the fitted response generalize?');
  expect(actions.openStudy).not.toHaveBeenCalled(); expect(actions.openPage).not.toHaveBeenCalled();
  fire(button('Open the page'), 'click'); expect(actions.openPage).toHaveBeenCalledWith(expect.objectContaining({id:'ppage',revision:2}));
  fire(root.querySelector('.pf-study')!, 'click'); expect(actions.openStudy).toHaveBeenCalledWith('s1');
});

test('a page older than the newest result says so and offers a brief to update it', () => {
  const {home,root} = setup();
  home.setFront({name:'P',front:front({page:{id:'ppage',revision:1,digest:'d',title:'Our entry',savedAt:NOW-3600}})});
  home.update('p1',[study()]);
  expect(root.textContent).toContain('1 newer result since this page was saved');
  expect(root.textContent).toContain('Copy a brief to update it');
});

test('without a page the Project explains what a page is and how to make or choose one', async () => {
  const {home,root,actions,button} = setup();
  home.setFront({name:'P',front:front()});
  home.update('p1',[study()],'',{},[],{count:2,groups:[],pages:[{id:'pa',revision:1,title:'entry.html'}]});
  expect(root.textContent).toContain('No page yet');
  expect(root.querySelector('iframe')).toBeNull();
  fire(button('Choose an .html file'), 'click');
  fire(button('entry.html'), 'click'); await settleMicrotasks();
  expect(actions.setPage).toHaveBeenCalledWith({id:'pa',revision:1,title:'entry.html'});
});

test('Just finished lists results by record, never bookkeeping', () => {
  const {home,column} = setup();
  const value = study('p1',{findings:[{request:'f',at:NOW-100,statement:'An accepted finding',tasks:[]}],
    researchPlan:{status:'draft',updatedAt:NOW-50,desiredOutput:'A plan saved after the results'}});
  home.update('p1',[value]);
  const finished = column('finished').textContent;
  expect(finished).toContain('An accepted finding'); expect(finished).toContain('C3 beat the safety entry');
  expect(finished).not.toContain('A plan saved after the results');
});

test('a lapsed claim with nothing running is a step to close, never an execution alarm', () => {
  const {home,root,column} = setup();
  const value = study('p1',{tasks:{'assignment:a':{status:'running',reservedMicrousd:0,chargedMicrousd:null,
    specification:{provider:'collaboration',assignment:{objective:'Lead the campaign',dependsOn:[]}},
    collaboration:{operations:[],claimedAt:NOW-7200,leaseUntil:NOW-6900,claimedBy:{by:'agent:ac_two',byLabel:'Claude Code'}}}}});
  home.update('p1',[value]);
  expect(root.textContent).not.toContain('Execution needs checking');
  expect(root.textContent).not.toContain('Assignment ownership needs checking');
  expect(column('plan').textContent).toContain('Close or continue 1 claimed assignment');
  expect(column('now').textContent).toContain('Nothing is running.');
});

test('a live claim is shown as work going on now, with who holds it', () => {
  const {home,column} = setup();
  const value = study('p1',{tasks:{'assignment:a':{status:'running',reservedMicrousd:0,chargedMicrousd:null,
    specification:{provider:'collaboration',assignment:{objective:'Score C3 on the held-out fold',dependsOn:[]}},
    collaboration:{claimedAt:NOW-60,leaseUntil:NOW+240,claimedBy:{by:'agent:ac_two',byLabel:'Claude Code'}}}}});
  home.update('p1',[value]);
  expect(column('now').textContent).toContain('Score C3 on the held-out fold');
  expect(column('now').textContent).toContain('Claude Code');
});

test('Next in the plan joins planned steps, reports to review, ready assignments and paused Studies', async () => {
  const {home,column,button,actions} = setup();
  home.setFront({name:'P',front:front({next:[{id:'n0123456789abcdef',text:'Record the score',when:'late November',byLabel:'Claude Code'}]})});
  const ready = study('p1',{tasks:{
    'assignment:r':{status:'planned',reservedMicrousd:0,chargedMicrousd:null,specification:{provider:'collaboration',assignment:{objective:'Literature notes',dependsOn:[]}}},
    'assignment:b':{status:'planned',reservedMicrousd:0,chargedMicrousd:null,specification:{provider:'collaboration',assignment:{objective:'Blocked packaging',dependsOn:['assignment:r']}}},
    'assignment:c':{status:'completed',reservedMicrousd:0,chargedMicrousd:null,specification:{provider:'collaboration',assignment:{objective:'Done',dependsOn:[]}},collaboration:{report:{statement:'x'}}}}});
  const paused = {...study('p1'),id:'s2',status:'paused',title:'Measured campaign',claims:[]};
  home.update('p1',[ready,paused]);
  const plan = column('plan').textContent;
  expect(plan).toContain('Record the score'); expect(plan).toContain('late November');
  expect(plan).toContain('Review 1 report'); expect(plan).toContain('Literature notes');
  expect(plan).not.toContain('Blocked packaging'); expect(plan).toContain('Continue or close this Study');
  fire(button('Done'), 'click'); await settleMicrotasks();
  expect(actions.finishStep).toHaveBeenCalledWith('n0123456789abcdef');
});

test('your own next step drafts words for the Assistant and starts nothing itself', () => {
  const {home,root,actions,column} = setup();
  home.update('p1',[study()]);
  const own = column('own');
  const field = own.querySelector('textarea')!; field.value = 'Why did C1 lose on direction?';
  fire(own.querySelector('form')!, 'submit');
  expect(actions.draft).toHaveBeenCalledWith('Why did C1 lose on direction?');
  expect(root.textContent).toContain('Start a Study');
  fire(root.querySelectorAll('button').find(b => b.dataset.researchFocus === 'start-research')!, 'click');
  expect(actions.startResearch).toHaveBeenCalledOnce();
});

test('another Project, a stale button and a late foreign Study cannot redirect the current workspace', () => {
  const {home,host,root,actions,setCurrent} = setup({startResearch:undefined});
  home.update('p1',[study()]);const old=root.querySelector('.pf-study')!;
  setCurrent('p2'); fire(old, 'click'); expect(actions.openStudy).not.toHaveBeenCalled();
  home.update('p2',[study()]); expect(root.hidden).toBe(true); expect(host.dataset.projectResearch).toBe('false');
  setCurrent('p1');home.update('p1',[study()]);fire(old, 'click');expect(actions.openStudy).not.toHaveBeenCalled();
  fire(root.querySelector('.pf-study')!, 'click');expect(actions.openStudy).toHaveBeenCalledOnce();
});

test('untrusted words are shown as text and only this Projects researchers appear', () => {
  const {home,root,column} = setup();
  const value = study(); value.claims!.push({request:'new',at:NOW-10,statement:'<script>untrusted</script> New result',byLabel:'Codex',evidence:[],dependsOn:[]});
  const researchers = [{rid:'r1',name:'Here',purpose:'research',sitting:'p1',status:'running',brief:'Count cells',created:1},
    {rid:'r2',name:'Elsewhere',purpose:'research',sitting:'p2',status:'running',brief:'Other',created:2}] as unknown as ResearchSession[];
  home.update('p1',[value,{...study('p2'),id:'foreign',title:'Foreign'}],'',{},researchers);
  expect(root.querySelectorAll('script')).toHaveLength(0);
  expect(column('finished').textContent).toContain('<script>untrusted</script> New result');
  expect(column('now').textContent).toContain('Count cells'); expect(root.textContent).not.toContain('Elsewhere');
  expect(root.textContent).not.toContain('Foreign');
});

test('initial loading and failed reads never masquerade as an empty Project', () => {
  const {home,root,actions,button} = setup({startResearch:undefined});
  home.update('p1',[],'',{},[],{count:0,groups:[],loading:true});
  expect(root.textContent).toContain('Opening your Project…');
  home.update('p1',[],'Studies could not be read.',{},[],{count:0,groups:[],error:'Material could not be read.'});
  expect(root.textContent).toContain('Project history could not be loaded.');
  fire(button('Retry'), 'click'); expect(actions.retry).toHaveBeenCalledOnce();
  home.update('p1',[study()],'Reconnect to refresh.');
  expect(root.textContent).toContain('Showing saved history. Reconnect to refresh.');
});

test('saving the sentence about the Project and adding a step go through the owner actions', async () => {
  const {home,root,actions,button,column} = setup();
  home.setFront({name:'P',front:front()});
  home.update('p1',[study()]);
  fire(button('Say in one sentence what this Project is'), 'click');
  const field = root.querySelector('.pf-about-form')!.querySelector('textarea')!; field.value = '  Predict cell responses.  ';
  fire(root.querySelector('.pf-about-form')!, 'submit'); await settleMicrotasks();
  expect(actions.saveAbout).toHaveBeenCalledWith('Predict cell responses.');
  fire(button('Add a step'), 'click');
  const step = column('plan').querySelector('input')!; step.value = 'Record the score';
  fire(column('plan').querySelector('form')!, 'submit'); await settleMicrotasks();
  expect(actions.addStep).toHaveBeenCalledWith('Record the score');
});

test('a refused change is said in the server words and keeps the page', async () => {
  const {home,root,button} = setup({saveAbout:vi.fn(async () => { throw new Error('The active Project changed. — return to the original Project'); })});
  home.setFront({name:'P',front:front()});
  home.update('p1',[study()]);
  fire(button('Say in one sentence what this Project is'), 'click');
  const form = root.querySelector('.pf-about-form')!; form.querySelector('textarea')!.value = 'x';
  fire(form, 'submit'); await settleMicrotasks(); await settleMicrotasks();
  expect(root.textContent).toContain('The active Project changed. — return to the original Project');
  expect(root.textContent).toContain('How it was made');
});

test('a long column shows four, then says how many more wait and shows them on request', () => {
  const {home,column,button} = setup();
  home.setFront({name:'P',front:front({next:Array.from({length:7},(_,i)=>({id:`n${String(i).padStart(16,'0')}`,text:`Step ${i}`}))})});
  home.update('p1',[study()]);
  expect(column('plan').querySelectorAll('.pf-item')).toHaveLength(4);
  fire(button('Show 3 more'), 'click');
  expect(column('plan').querySelectorAll('.pf-item')).toHaveLength(7);
});

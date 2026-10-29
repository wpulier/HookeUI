// Synthetic rendering/ownership evidence; actual retained-Project browser acceptance is separate.
import {afterEach, expect, test, vi} from 'vitest';
import {ProjectResearchHome} from './project-research-home';
import type {Effort} from './research-efforts';
import {TestElement, syntheticDOM, fire} from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());
const study = (session = 'p1'): Effort => ({id:'s1',session,revision:1,digest:'digest',title:'Response transfer',
  objective:'Does the fitted response generalize?',criteria:'Measured error',status:'active',capMicrousd:0,
  spentMicrousd:0,heldMicrousd:0,availableMicrousd:0,inputs:[{id:'data',revision:2,digest:'data-digest'}],tasks:{},
  experiments:{first:{operation:'op1',byLabel:'Codex',execution:{id:'run1',session}}},findings:[],
  claims:[{request:'claim',statement:'Observed a limitation',byLabel:'Claude Code',evidence:[],dependsOn:[]}]});
function setup() {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const host = new TestElement('section'); let current = 'p1';
  const actions = {current: () => current, openStudy:vi.fn(),data:vi.fn(),inputs:vi.fn()};
  const home = new ProjectResearchHome(host as unknown as HTMLElement, actions);
  const root = home.root as unknown as TestElement;
  const button = (text: string) => root.querySelectorAll('button').find(item => item.textContent === text) as unknown as HTMLButtonElement;
  return {home,host,root,actions,button,setCurrent:(id:string) => {current=id;}};
}
test('retained question and evidence replace new-project onboarding without starting research', () => {
  const {home,host,root,actions,button} = setup(); home.update('p1',[study()]);
  expect(host.dataset.projectResearch).toBe('true'); expect(root.hidden).toBe(false);
  expect(root.textContent).toContain('Does the fitted response generalize?');
  expect(root.textContent).toContain('1Inputs'); expect(root.textContent).toContain('1Experiments');
  expect(root.textContent).toContain('1Reports'); expect(root.textContent).toContain('0Findings');
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
  home.update('p1',[study()],'Reconnect to refresh.');expect(root.textContent).toContain('Showing the last saved overview. Reconnect to refresh.');
  home.update('p1',[]);expect(root.hidden).toBe(true);
});

test('latest attributed report remains distinct from findings and exposes its limitations', () => {
  const {home,root} = setup(); const value = study();
  value.claims!.push({request:'new',statement:'<script>untrusted</script> New result',byLabel:'Codex',evidence:[],dependsOn:[],limitations:'No independent validation'});
  value.findings.push({request:'finding',statement:'Retained finding',tasks:[]});
  home.update('p1',[value]);
  expect(root.textContent).toContain('2Reports'); expect(root.textContent).toContain('1Findings');
  expect(root.textContent).toContain('No independent validation');
  expect(root.textContent).toContain('Researcher report · not an accepted finding');
  expect(root.textContent).not.toContain('Observed a limitation');
  expect(root.querySelectorAll('script')).toHaveLength(0);
  expect(root.textContent).toContain('Contributions by Claude Code · Codex');
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
  expect(details().map(item => item.open)).toEqual([true,true]);
  changed.claims!.push({request:'replacement',statement:'Next result',byLabel:'Codex',evidence:[],dependsOn:[]});
  home.update('p1',[changed]);
  expect(details()[0].open).toBe(true); expect(details()[1].open).not.toBe(true);
  home.update('p2',[study('p2')]);
  expect(details().some(item => item.open)).toBe(false);
});

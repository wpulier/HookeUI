// Locator fixtures only: no browser, Qt, rendering or native qualification.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {scientificDocument} from './scientific_document_scope.mjs';

const descendants = nodes => nodes.flatMap(n => [n, ...descendants(n.children ?? [])]);
class Locator {
  constructor(nodes) { this.nodes = nodes; }
  first() { return new Locator(this.nodes.slice(0, 1)); }
  or(other) { return new Locator([...new Set([...this.nodes, ...other.nodes])]); }
  getByRole(role, {name, exact} = {}) {
    assert.ok(name === undefined || exact);
    return new Locator([...new Set(descendants(this.nodes))].filter(n => n.role === role && (name === undefined || n.name === name)));
  }
  getByLabel(name, {exact}) {
    assert.equal(exact, true);
    return new Locator(descendants(this.nodes).filter(n => n.label === name));
  }
  locator(selector) {
    const attributes=[...selector.matchAll(/\[([\w-]+)(\*=|=)("(?:[^"\\]|\\.)*")\]/g)].map(([,key,op,value])=>[key,op,JSON.parse(value)]);
    return new Locator(descendants(this.nodes).filter(n=>{
      if(selector.startsWith('section.study-document-artifact')&&!n.document)return false;
      if(selector.startsWith('section.capability-controls')&&!n.controls)return false;
      return attributes.every(([key,op,value])=>op==='*='?n.attrs?.[key]?.includes(value):n.attrs?.[key]===value);
    }));
  }
  async count() { return this.nodes.length; }
  async isVisible() { return this.nodes.length===1&&!this.nodes[0].hidden; }
  one() { assert.equal(this.nodes.length, 1, 'strict locator requires the exact target'); return this.nodes[0]; }
  async getAttribute(name) { return this.one().attrs?.[name] ?? null; }
  async click(options) {
    assert.equal(options?.force, undefined);
    const target = this.one();
    if (target.owner === 'volume') throw new Error('surface intercepts pointer events aimed at old volume');
    return target.owner;
  }
  async inputValue() { return this.one().owner; }
  async evaluate(read) { return read(this.one()); }
  async allInnerTexts() { return this.nodes.map(n => n.owner); }
}

function fixture() {
  const nodes = [];
  for (const [owner, title, id] of [['volume', 'H05 volume', 'a'], ['surface', 'H05 surface', 'b']]) {
    const saved = {title,placementId:owner, resource:{day:'2026-10-22', id:'p'+id.repeat(32), version:2}};
    nodes.push({document:true, name:title, owner, attrs:{
      'aria-label':title,'data-study-retained':JSON.stringify(saved),
      'data-content-key':`scientific:${saved.resource.day}:${saved.resource.id}:2:${owner}`,
    }, children:[{role:'button',name:'Open session',owner}, {role:'button',name:'Refresh preview',owner},
      {label:'Scientific session status',owner,role:'status'},
      {label:'Private scientific preview. Changes are not saved.',owner}]});
    nodes[nodes.length-1].children.push({role:'button',name:`Show ${title} · revision 2 controls`,owner});
    nodes[nodes.length-1].children.push({controls:true,role:'region',name:`${title} · revision 2 controls`,owner,attrs:{role:'region','aria-label':`${title} · revision 2 controls`},children:[
      {role:'tab',name:'Python',owner}, {role:'tab',name:'View',owner},
      {role:'button',name:'Run code',owner}, {role:'button',name:'Apply view changes',owner},
      {label:'Camera zoom',owner}, {label:'Python code',owner}, {label:'Python cell history',owner},
      {role:'status',owner},
    ]});
  }
  return {nodes, page:new Locator(nodes)};
}

test('opening and controls belong to the surface even while volume and its dock remain mounted', async () => {
  const {page} = fixture(), current = scientificDocument(page, 'H05 surface');
  await current.bind();
  assert.equal(await current.button('Open session').click(), 'surface');
  assert.equal(await current.button('Refresh preview').click(), 'surface');
  assert.equal(await current.tab('Python').click(), 'surface');
  assert.equal(await current.label('Python code').inputValue(), 'surface');
  assert.equal(await current.label('Camera zoom').inputValue(), 'surface');
  assert.equal(await current.button('Run code').click(), 'surface');
  assert.equal(await current.button('Apply view changes').click(), 'surface');
  assert.equal(await current.label('Python cell history').inputValue(), 'surface');
  assert.equal(await current.preview().evaluate(canvas => canvas.owner), 'surface');
  assert.deepEqual(await current.statuses().allInnerTexts(), ['surface', 'surface']);
});

test('missing or ambiguous scientific document never falls back to the first document', async () => {
  const {nodes, page} = fixture();
  await assert.rejects(scientificDocument(page, 'Missing').bind(), /one scientific document/);
  nodes.push({...nodes[1]});
  await assert.rejects(scientificDocument(page, 'H05 surface').bind(), /one scientific document/);
});

test('document identity must match its retained placement before any controls can be used', async () => {
  const {nodes, page} = fixture();
  nodes[1].attrs['data-content-key'] = nodes[0].attrs['data-content-key'];
  await assert.rejects(scientificDocument(page, 'H05 surface').bind(), /retained identity/);
});

test('missing target dock has no fallback to an old document console', async () => {
  const {nodes, page} = fixture();
  nodes[1].children.pop();
  const current = scientificDocument(page, 'H05 surface');
  await current.bind();
  assert.equal(await current.label('Python code').count(), 0);
  await assert.rejects(current.button('Run code').click(), /strict locator/);
});


test('display labels may differ from canonical titles; only the retained owner supplies controls',async()=>{
  const {nodes,page}=fixture(),target=nodes[1];
  target.name='Scientific view · revision 2 — H05 surface';target.attrs['aria-label']=target.name;
  const controls=target.children.at(-1),launcher=target.children.at(-2);
  controls.name=target.name+' controls';controls.attrs['aria-label']=controls.name;launcher.name='Show '+controls.name;
  // A detached lookalike must never enter this owner's control scope.
  nodes.push({role:'region',name:controls.name,children:[{role:'button',name:'Run code',owner:'foreign'}]});
  const current=scientificDocument(page,'H05 surface');
  assert.equal(await current.root.count(),1); // Existing callers wait on root before bind.
  await current.bind();
  assert.equal(await current.button('Run code').click(),'surface');
});

test('explicit retained resource and placement disambiguate identical canonical names',async()=>{
  const {nodes,page}=fixture(),target=nodes[1],saved=JSON.parse(target.attrs['data-study-retained']);
  nodes[0].attrs['data-study-retained']=JSON.stringify({...JSON.parse(nodes[0].attrs['data-study-retained']),title:saved.title});
  await assert.rejects(scientificDocument(page,saved.title).bind(),/one scientific document/);
  const current=scientificDocument(page,saved.title,{resource:saved.resource,placementId:saved.placementId});
  await current.bind();assert.equal(await current.button('Run code').click(),'surface');
});

test('retargeting a bound placement cannot silently select a replacement revision',async()=>{
  const {nodes,page}=fixture(),target=nodes[1],current=scientificDocument(page,'H05 surface');await current.bind();
  const saved=JSON.parse(target.attrs['data-study-retained']);saved.resource.version=3;
  target.attrs['data-study-retained']=JSON.stringify(saved);
  target.attrs['data-content-key']=`scientific:${saved.resource.day}:${saved.resource.id}:3:${saved.placementId}`;
  await assert.rejects(current.bind(),/one scientific document|retained identity/);
});


for (const problem of ['hidden','foreign-launcher','ambiguous-controls']) test(`refuses ${problem} rather than selecting another owner`,async()=>{
  const {nodes,page}=fixture(),target=nodes[1];
  if(problem==='hidden')target.hidden=true;
  if(problem==='foreign-launcher')target.children.at(-2).name='Show foreign controls';
  if(problem==='ambiguous-controls')target.children.push({...target.children.at(-1)});
  await assert.rejects(scientificDocument(page,'H05 surface').bind(),/one scientific document|Controls/);
});

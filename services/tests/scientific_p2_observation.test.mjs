import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readConsoleJSON, stopScientificSession, appearanceTransaction, captureAppearanceSettings, isOwnedViewChange} from './scientific_p2_observation.mjs';

function consolePython() {
  const history = [];
  return async source => {
    history.push(source);
    const run = spawnSync(process.env.PYTHON ?? 'python3', ['-c', history.join('\n')], {encoding:'utf8'});
    assert.equal(run.status, 0, run.stderr);
    const output = run.stdout.trim().split('\n').at(-1);
    assert.ok(Buffer.byteLength(output) < 16384, 'each observation fits the real console bound');
    return output;
  };
}

test('large exact state crosses console bound without changing scalar types or array metadata', async () => {
  const text = await readConsoleJSON(consolePython(), "{'value': 1.0, 'integer': 9007199254740993, 'negative_zero': -0.0, 'array': {'dtype': '>u2', 'shape': [2], 'values': [1, 65535]}, 'text': '\\u03bc' * 20000}", {raw:true});
  assert.match(text, /"value": 1\.0/);
  assert.match(text, /9007199254740993/);
  assert.match(text, /"negative_zero": -0\.0/);
  assert.equal(JSON.parse(text).array.dtype, '>u2');
  assert.equal(JSON.parse(text).text.length, 20000);
});

test('small observation uses one cell and corrupted chunks fail closed', async () => {
  let calls = 0;
  const python = consolePython();
  assert.deepEqual(await readConsoleJSON(async code => { calls++; return python(code); }, "{'actual': 0.4}"), {actual:0.4});
  assert.equal(calls, 1);
  const corrupt = consolePython();
  await assert.rejects(readConsoleJSON(async code => {
    const line = await corrupt(code); const value = JSON.parse(line);
    if (typeof value === 'string') return JSON.stringify('AAAA');
    return line;
  }, "{'text': 'a' * 20000}"), /digest|length|chunk/);
});

test('observation above harness budget refuses rather than truncating', async () => {
  await assert.rejects(readConsoleJSON(consolePython(), "{'text': 'a' * (300 << 10)}"), /bounded observation/);
});

test('owned session stops before browser teardown, including a failed opening observation', async () => {
  let active = true; const events = [];
  const document = {button: name => ({isEnabled:async()=> name === 'Stop scientific session' ? active : !active,
    locator:()=>({getAttribute:async()=>''}),
    click:async()=> {assert.equal(name,'Stop scientific session');events.push('stop');active=false;}})};
  const result = await stopScientificSession(document, async condition => {assert.equal(await condition(),true);events.push('confirmed');});
  assert.deepEqual(events,['stop','confirmed']);assert.deepEqual(result,{ok:true,stopped:true});
  assert.deepEqual(await stopScientificSession(document, async()=>assert.fail('no wait needed')),{ok:true,stopped:false});
});

test('failed Stop does not report successful cleanup', async () => {
  const document = {button: name => ({isEnabled:async()=>name==='Stop scientific session', locator:()=>({getAttribute:async()=>''}), click:async()=>{throw Error('Stop refused');}})};
  await assert.rejects(stopScientificSession(document,async()=>{}),/Stop refused/);
});


test('closed Session details is opened normally before Stop, with bounded locator waits', async () => {
  let active = true, expanded = false; const events = [];
  const summary = {filter: () => summary, click: async options => {
    assert.equal(options.force, undefined); events.push('Session summary'); expanded = true;
  }};
  const details = {getAttribute: async (name, options) => {
    assert.equal(name, 'open'); assert.ok(options.timeout <= 1000); return expanded ? '' : null;
  }, locator: selector => {assert.equal(selector, ':scope > summary'); return summary;}};
  const document = {button: (name, options) => {
    if (name === 'Stop scientific session') assert.equal(options?.includeHidden, true);
    return {isEnabled: async options => {
      assert.ok(options?.timeout <= 1000, 'every poll has a short locator bound');
      return name === 'Stop scientific session' ? active : !active;
    }, locator: selector => {assert.equal(selector, 'xpath=ancestor::details[1]'); return details;},
    click: async options => {
      assert.equal(options.force, undefined); assert.ok(expanded, 'collapsed details hides Stop');
      events.push('Stop'); active = false;
    }};
  }};
  assert.deepEqual(await stopScientificSession(document, async check => assert.ok(await check())), {ok:true, stopped:true});
  assert.deepEqual(events, ['Session summary', 'Stop']);
});


test('appearance replay records the submitted complete payload and exact returned target', () => {
  const settings = {contrastLimits:[0,3000], gamma:1, rendering:'iso', isoThreshold:1500, attenuation:0.05, autoContrast:false};
  const body = {request:{op:'change', request:{frame:'frame-old', operation:'image-presentation', value:{layer:'layer-1',settings}}}};
  const frame = {layers:[{id:'layer-1',name:'Native input',type:'image'}]};
  assert.deepEqual(appearanceTransaction(body, frame, 'Native input', settings), ['image-presentation','Native input',settings]);
  assert.throws(() => appearanceTransaction(body, frame, 'Other input'), /target/);
  assert.throws(() => appearanceTransaction(body, {layers:[{...frame.layers[0],type:'surface'}]}, 'Native input'), /target/);
  assert.throws(() => appearanceTransaction(body, {layers:[]}, 'Native input'), /target/);
  assert.throws(() => appearanceTransaction({...body,request:{...body.request,op:'observe'}}, frame, 'Native input'), /appearance/);
});

for (const [field,changed] of [['contrastLimits',[0,5000]],['attenuation',0.3],['autoContrast',true]]) {
  test(`appearance rejects submitted ${field} differing from the pre-Apply UI`, () => {
    const captured={contrastLimits:[0,3000],gamma:1,rendering:'attenuated_mip',isoThreshold:1500,attenuation:0.4,autoContrast:false};
    const body={request:{op:'change',request:{operation:'image-presentation',value:{layer:'a',settings:{...captured,[field]:changed}}}}};
    const frame={layers:[{id:'a',name:'Native input',type:'image'}]};
    assert.throws(()=>appearanceTransaction(body,frame,'Native input',captured),/pre-Apply/);
  });
}
test('surface replay refuses a changed untouched normal setting',()=>{
 const line={visible:false,width:1,color:[0,0,0,1]};
 const captured={shading:'flat',gamma:1,contrastLimits:[0,3000],autoContrast:false,wireframe:line,faceNormals:{...line,length:10},vertexNormals:{...line,length:10}};
 const submitted=structuredClone(captured);submitted.vertexNormals.length=20;
 const body={request:{op:'change',request:{operation:'surface-presentation',value:{layer:'s',settings:submitted}}}};
 assert.throws(()=>appearanceTransaction(body,{layers:[{id:'s',name:'Measured mesh',type:'surface'}]},'Measured mesh',captured),/pre-Apply/);
});

test('owned image form capture reads every field before submission and retains the snapshot', async()=>{
 const values={'Contrast minimum': '0','Contrast maximum':'3000','Gamma':'1','Volume rendering':'attenuated_mip','Isosurface threshold':'1500','Attenuation':'0.4','Automatic contrast':false};
 const read=[];
 const label=full=>{const name=full.replace(/ Native input$/,'');assert.ok(Object.hasOwn(values,name));return {
  inputValue:async()=>{read.push(name);return values[name];},isChecked:async()=>{read.push(name);return values[name];}};};
 const captured=await captureAppearanceSettings(label,'image','Native input');
 assert.deepEqual(new Set(read),new Set(Object.keys(values)));
 assert.deepEqual(captured,{contrastLimits:[0,3000],gamma:1,rendering:'attenuated_mip',isoThreshold:1500,attenuation:0.4,autoContrast:false});
 values['Contrast maximum']='5000';assert.equal(captured.contrastLimits[1],3000);
 values.Gamma='';await assert.rejects(captureAppearanceSettings(label,'image','Native input'),/pre-Apply/);
 values.Gamma='Infinity';await assert.rejects(captureAppearanceSettings(label,'image','Native input'),/pre-Apply/);
});

test('owned surface form capture includes all hidden unchanged line and normal fields',async()=>{
 const values={'Shading':'smooth','Surface gamma':'2','Surface contrast minimum':'10','Surface contrast maximum':'100','Automatic surface contrast':false};
 for(const [index,prefix]of ['Wireframe','Face normals','Vertex normals'].entries()){
  values[`Show ${prefix.toLowerCase()}`]=index===1;values[`${prefix} width`]=String(index+1);
  for(const[channel,value]of Object.entries({red:0.1,green:0.2,blue:0.3,alpha:1}))values[`${prefix} ${channel}`]=String(value);
  if(index)values[`${prefix} length`]=String(index*10);
 }
 const read=[];const label=full=>{assert.ok(full.endsWith(' Measured mesh'));const name=full.slice(0,-' Measured mesh'.length);assert.ok(Object.hasOwn(values,name));return {
  inputValue:async()=>{read.push(name);return values[name];},isChecked:async()=>{read.push(name);return values[name];}};};
 const captured=await captureAppearanceSettings(label,'surface','Measured mesh');
 assert.deepEqual(new Set(read),new Set(Object.keys(values)));
 assert.deepEqual(captured,{shading:'smooth',gamma:2,contrastLimits:[10,100],autoContrast:false,
  wireframe:{visible:false,width:1,color:[0.1,0.2,0.3,1]},faceNormals:{visible:true,width:2,color:[0.1,0.2,0.3,1],length:10},
  vertexNormals:{visible:false,width:3,color:[0.1,0.2,0.3,1],length:20}});
 const body={request:{op:'change',request:{operation:'surface-presentation',value:{layer:'mesh',settings:structuredClone(captured)}}}};
 const frame={layers:[{id:'mesh',name:'Measured mesh',type:'surface'}]};
 assert.deepEqual(appearanceTransaction(body,frame,'Measured mesh',captured),['surface-presentation','Measured mesh',captured]);
 body.request.request.value.settings.vertexNormals.color[2]=0.9;
 assert.throws(()=>appearanceTransaction(body,frame,'Measured mesh',captured),/pre-Apply/);
});

test('view change capture only accepts the review bound to the owned document',()=>{
 const owned='owned-review-12345';
 const request=(review,method='POST',op='change')=>({method:()=>method,url:()=>`http://127.0.0.1:1234/api/scientific-reviews/${review}/request`,postDataJSON:()=>({request:{op}})});
 assert.equal(isOwnedViewChange(request(owned),owned),true);
 assert.equal(isOwnedViewChange(request('other-review-12345'),owned),false);
 assert.equal(isOwnedViewChange(request(owned,'GET'),owned),false);
 assert.equal(isOwnedViewChange(request(owned,'POST','observe'),owned),false);
 assert.equal(isOwnedViewChange(request(owned),null),false);
});

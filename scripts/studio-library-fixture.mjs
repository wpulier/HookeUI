/** Isolated renderer evidence: production library components, synthetic state, no services. */
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
import {resolve, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {build} from '../apps/studio/node_modules/vite/dist/node/index.js';

const repo = fileURLToPath(new URL('../', import.meta.url));
const out = resolve(process.argv[2] || 'output/playwright/studio-library');
mkdirSync(out, {recursive:true});
// This asset is delivered only by the reviewer's local Playwright route at the production thumb URL.
const thumbnail='<svg xmlns="http://www.w3.org/2000/svg" width="240" height="135" viewBox="0 0 240 135"><rect width="240" height="135" fill="#b3c29e"/><rect width="24" height="24" fill="#e53935"/><rect x="216" width="24" height="24" fill="#1976d2"/><rect y="111" width="24" height="24" fill="#fdd835"/><rect x="216" y="111" width="24" height="24" fill="#7b1fa2"/><text x="120" y="73" text-anchor="middle" font-family="sans-serif" font-size="15" fill="#26321d">SYNTHETIC · 4 corners</text></svg>';
writeFileSync(join(out,'synthetic-thumbnail.svg'),thumbnail);
const src = join(repo, 'apps/studio/src');
const cases = ['footage-down', 'footage-loading', 'footage-empty', 'footage-library', 'footage-no-match',
  'footage-upload-progress', 'footage-upload-error', 'footage-upload-done', 'datasets-loading', 'datasets-empty',
  'datasets-collecting', 'datasets-saved', 'datasets-gaps', 'datasets-rename', 'datasets-rename-failure',
  'datasets-failure', 'datasets-expanded'];
const entry = `
import ${JSON.stringify(join(src, 'style.css'))};
import ${JSON.stringify(join(src, 'workspace-windows.css'))};
import ${JSON.stringify(join(src, 'studio-shell.css'))};
import ${JSON.stringify(join(src, 'library-pages.css'))};
import {StudioShell} from ${JSON.stringify(join(src, 'studio-shell.ts'))};
import {AssetsView} from ${JSON.stringify(join(src, 'assets.ts'))};
import {DatasetsView} from ${JSON.stringify(join(src, 'datasets.ts'))};

async function start() {
 const scenario = new URLSearchParams(location.search).get('case') || 'footage-library';
 if (!${JSON.stringify(cases)}.includes(scenario)) throw new Error('Unknown synthetic fixture case: '+scenario);
 const commands=[], page=scenario.startsWith('footage-')?'assets':'datasets';
 new StudioShell();
 document.body.dataset.view=page;
 for(const id of ['assets','datasets','research','capabilities']) {const root=document.getElementById(id);if(root)root.hidden=id!==page;}
 for(const tab of document.querySelectorAll('[role=tab][aria-controls]')) tab.setAttribute('aria-selected',String(tab.getAttribute('aria-controls')===page));
 const now=Math.floor(Date.now()/1000), iso=value=>new Date(value*1000).toISOString();
 const row={id:'synthetic-dataset',name:'Synthetic leaf session — rendering only',status:'complete',startedAt:iso(now-900),startedAtEpoch:now-900,
  stoppedAt:iso(now-300),stoppedAtEpoch:now-300,endedBy:'user',reconstructed:false,runs:1,gaps:[],runSpans:[[now-900,now-300]],
  parts:[{n:1,videoKept:'synthetic.mp4',sidecarKept:'synthetic.frames.jsonl',hasSidecar:true,joined:false,bytes:28000000}],
  counts:{events:84,frames:18000},rig:{microscope:{value:'Synthetic test microscope',source:'user'},objective:{value:40,source:'user'},fieldNumber:{value:18,source:'default'}},
  pictures:8,files:{'events.jsonl':8000},bytes:29000000,path:'/synthetic/MicroscopeMind/datasets/synthetic-dataset',
  endCheck:{ok:true,problems:[],notes:[]},thumb:true,screens:0};
 if(scenario==='datasets-collecting') Object.assign(row,{status:'collecting',stoppedAt:null,stoppedAtEpoch:null,endCheck:null,runSpans:[[now-900,null]]});
 if(scenario==='datasets-gaps') Object.assign(row,{runs:2,runSpans:[[now-900,now-700],[now-610,now-300]],gaps:[{from:iso(now-700),to:iso(now-610),seconds:90}],
  counts:{events:84,frames:15300,droppedFrames:12},parts:[{...row.parts[0],hasSidecar:false,sidecarKept:null}],
  endCheck:{ok:false,problems:['Synthetic missing camera frame sidecar. Microphone and tab audio were not enabled.'],notes:[]}});
 const listing={root:'/synthetic/MicroscopeMind/datasets',datasets:scenario==='datasets-empty'?[]:[row],collecting:null};
 let listingFailure=scenario==='datasets-failure', uploadResolve, uploadReject, uploadProgress;
 // No fallback to native fetch. Unimplemented routes fail locally and remain inspectable.
 window.fetch=async(url,init)=>{
  commands.push({kind:'fetch',url:String(url),method:init?.method??'GET'});
  if(String(url)==='/api/datasets') {
   if(scenario==='datasets-loading')return new Promise(()=>{});
   return new Response(JSON.stringify(listingFailure?{error:'Synthetic listing failure'}:listing),{status:listingFailure?503:200,headers:{'Content-Type':'application/json'}});
  }
  if(String(url).endsWith('/citations'))return new Response(JSON.stringify({models:[],complete:false,qualification:'Synthetic fixture: no models ran; no citations can be established.'}),{headers:{'Content-Type':'application/json'}});
  return new Response('This action has no synthetic service adapter',{status:503});
 };
 let view, feed;
 if(page==='assets') {
  const pixels=document.createElement('canvas');pixels.width=240;pixels.height=135;
  const ctx=pixels.getContext('2d');ctx.fillStyle='#b3c29e';ctx.fillRect(0,0,240,135);ctx.fillStyle='#34402b';ctx.font='16px sans-serif';ctx.fillText('Synthetic footage',20,70);
  const image=pixels.toDataURL('image/png');
  const item={name:'synthetic-leaf.mp4',title:'Synthetic leaf clip',group:'Real microscope',seconds:222,truth:false,bytes:28000000,date:'2026-10-10',specimen:'synthetic leaf',backedUp:false};
  const items=[item,{...item,name:'synthetic-other.mp4',title:'Synthetic added recording',group:'Added here'},
   {...item,name:'synthetic-public.mp4',title:'Synthetic public slide',group:'Corpus: synthetic specimen'}];
  feed={state:scenario==='footage-down'?null:{source:'camera',footage:null,folder:'/synthetic/MicroscopeMind',libraryVersion:'synthetic-1',libraryCount:scenario==='footage-empty'?0:3,targetDevice:'Synthetic unavailable camera',bridge:{ok:true,state:'synthetic',stateLabel:'Synthetic bridge state',devicePresent:false}},
   library:['footage-down','footage-loading','footage-empty'].includes(scenario)?[]:items,libraryVersion:'synthetic-1',thumbUrl:()=>image,
   upload:(file,metadata,progress)=>{commands.push({kind:'upload',name:file.name,metadata});uploadProgress=progress;return new Promise((resolve,reject)=>{uploadResolve=resolve;uploadReject=reject;});}};
  view=new AssetsView(document.getElementById('assets'),feed,async(name)=>{commands.push({kind:'play',name});feed.state={...feed.state,source:'footage',footage:name};view.render();});
  view.render();
  if(scenario==='footage-no-match'){const search=document.querySelector('.assets-search');search.value='nothing-matches-this-synthetic-query';search.dispatchEvent(new Event('input',{bubbles:true}));}
  if(scenario.startsWith('footage-upload-')){
   document.querySelector('.assets-add-open').click();
   const form=document.querySelector('.assets-form'),files=new DataTransfer();files.items.add(new File(['synthetic, not a video'],'synthetic-upload.mp4',{type:'video/mp4'}));
   form.querySelector('input[type=file]').files=files.files;form.querySelector('input[required][type=text]').value='Synthetic leaf';
   form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));uploadProgress(.43);
   if(scenario==='footage-upload-error')uploadReject(new Error('Synthetic upload failed: connection interrupted. The original local file is unchanged.'));
   if(scenario==='footage-upload-done'){const added={...item,name:'synthetic-upload.mp4',title:'Synthetic uploaded clip',group:'Added here'};feed.library.push(added);feed.libraryVersion='synthetic-2';uploadProgress(1);uploadResolve(added);}
   await Promise.resolve();await Promise.resolve();
  }
 } else {
  view=new DatasetsView(document.getElementById('datasets'),async(id,name)=>{commands.push({kind:'rename',id,name});if(scenario==='datasets-rename-failure')throw new Error('Synthetic disk unavailable');row.name=name;},
   async id=>{commands.push({kind:'reveal',id});throw new Error('Synthetic fixture cannot open Finder');},
   async(field,value)=>{commands.push({kind:'rig',field,value});},async id=>{commands.push({kind:'trash',id});throw new Error('Synthetic fixture never deletes data');});
  view.setRig({type:'rig',facts:{microscope:{value:'Synthetic microscope',source:'user'}},fields:{}});
  if(scenario==='datasets-loading')void view.load();else await view.load();
  if(['datasets-expanded','datasets-gaps'].includes(scenario))document.querySelector('.ds-more').click();
  if(scenario==='datasets-expanded'){row.counts.events++;await view.load();} // real redraw: expansion must survive it
  if(scenario==='datasets-rename'||scenario==='datasets-rename-failure'){
   document.querySelector('[data-key="synthetic-dataset:rename"]').click();
   const input=document.querySelector('.ds-name-input');input.value='Synthetic renamed session';
   if(scenario==='datasets-rename-failure'){input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));for(let i=0;i<8;i++)await Promise.resolve();}
  }
 }
 const label=document.createElement('p');label.id='fixture-label';label.textContent='SYNTHETIC RENDERING ONLY · '+scenario+' · production components · no backend, recording, model or private data';
 Object.assign(label.style,{position:'fixed',bottom:'2px',left:'4px',right:'4px',zIndex:'99999',fontSize:'10px',lineHeight:'14px',background:'#202024',color:'white',padding:'3px',margin:'0',pointerEvents:'none'});document.body.append(label);
 window.fixture={scenario,commands,view,feed,row,listing,reload:()=>view.load(),setListingFailure:value=>{listingFailure=value;},uploadProgress:value=>uploadProgress?.(value),uploadFail:()=>uploadReject?.(new Error('Synthetic upload interrupted'))};
 window.fixtureReady=true;
}
start().catch(error=>{window.fixtureError=String(error);console.error(error);});
`;
const entryPath=join(out,'fixture-entry.ts');writeFileSync(entryPath,entry);
const result=await build({configFile:false,root:repo,logLevel:'warn',build:{write:false,lib:{entry:entryPath,name:'LibraryFixture',formats:['iife']},cssCodeSplit:false,minify:false}});
const outputs=(Array.isArray(result)?result:[result]).flatMap(r=>r.output);
const script=outputs.filter(x=>x.type==='chunk').map(x=>x.code).join('\n');
const css=outputs.filter(x=>x.type==='asset'&&x.fileName.endsWith('.css')).map(x=>x.source).join('\n');
let html=readFileSync(join(repo,'apps/studio/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
html=html.replace('</head>','<meta http-equiv="Content-Security-Policy" content="connect-src \'none\'; img-src \'self\' data: blob:; media-src \'none\'">'+'<style>'+css+'</style></head>')
 .replace('</body>','<script>'+script.replaceAll('</script>','<\\/script>')+'</script></body>');
writeFileSync(join(out,'library-fixture.html'),html);
const inputPaths=new Set([join(repo,'apps/studio/index.html'),fileURLToPath(import.meta.url),
 ...['style.css','workspace-windows.css','studio-shell.css','library-pages.css'].map(name=>join(src,name)),
 ...outputs.filter(x=>x.type==='chunk').flatMap(x=>Object.keys(x.modules)).filter(path=>path.startsWith(src+'/'))]);
const digest=value=>createHash('sha256').update(value).digest('hex');
writeFileSync(join(out,'manifest.json'),JSON.stringify({generatedAt:new Date().toISOString(),evidenceClass:'synthetic-production-renderer',
 cases,fixtureSha256:digest(html),thumbnailSha256:digest(thumbnail),inputs:[...inputPaths].sort().map(path=>({path:path.slice(repo.length),sha256:digest(readFileSync(path))}))},null,2)+'\n');
writeFileSync(join(out,'README.md'),`# Controlled library rendering\n\nProduction AssetsView, DatasetsView, StudioShell, markup and CSS. All data, images, upload bytes and callbacks are synthetic. Fetch never reaches a service; CSP blocks network connections/media. No camera, microphone, recordings, private assets, cloud or models.\n\nGenerate: node scripts/studio-library-fixture.mjs OUTPUT_DIRECTORY\n\nServe the generated HTML with a Playwright route.fulfill({path}) on an intercepted secure localhost origin; no server required. Intercept /api/datasets/synthetic-dataset/thumb and fulfill synthetic-thumbnail.svg with image/svg+xml; this exercises the actual production img src with four visibly colored source corners and no measured disc geometry. Abort all other requests. Use ?case=CASE. Cases:\n\n${cases.map(x=>'- '+x).join('\n')}\n\nWait for window.fixtureReady; window.fixtureError captures initialization failure. window.fixture.commands records synthetic callbacks. window.fixture.reload() uses production dataset loading; setListingFailure(false) permits synthetic recovery. datasets-expanded opens Details and changes one record count through a real load/redraw to verify expansion survives. This establishes within-page redraw persistence only, not reload/server persistence. Uploads use actual form submit, synthetic File/DataTransfer, and controlled progress/completion callbacks.\n\nControls continue working locally. Rename mutates only synthetic memory. Finder and trash explicitly fail without OS effects. Session save is deliberately unavailable in this fixture.\n\nEvidence class: synthetic production-component rendering/control behavior. This does not qualify real uploads, bridge health, capture clocks, recording completeness, disk persistence or service acceptance.\n`);
console.log(join(out,'library-fixture.html'));

/** Service-free fixture: real legacy components, current markup/styles; synthetic pixels/state only. */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from '../apps/studio/node_modules/vite/dist/node/index.js';
const repo = fileURLToPath(new URL('../', import.meta.url));
const out = resolve(process.argv[2] || 'output/playwright/studio-legacy');
mkdirSync(out, {recursive:true});
const src = join(repo, 'apps/studio/src');
// Keep the notice renderer byte-for-byte with main until it has its own module.
// Only its external camera callbacks below are synthetic and local to this fixture.
const mainSource = readFileSync(join(src, 'main.ts'), 'utf8');
const noticeRenderer = mainSource.slice(mainSource.indexOf('function showNotice('), mainSource.indexOf('\nfunction hideNotice('));
const choiceRenderer = mainSource.slice(mainSource.indexOf('function choiceButton('), mainSource.indexOf('\nfunction vote('));
const threadControls = mainSource.slice(mainSource.indexOf('const threadPick ='), mainSource.indexOf('\nsessionPick.addEventListener'));
const chatVisibility = mainSource.slice(mainSource.indexOf('const chatShow ='), mainSource.indexOf('\n// Record is the one switch'));
const voiceRenderer = mainSource.slice(mainSource.indexOf('function talkLabelText('), mainSource.indexOf('\nfunction handleVoice('));
const voiceWorking = mainSource.slice(mainSource.indexOf('let workingTimer ='), mainSource.indexOf('\nfunction ask('));
const askAvailability = mainSource.slice(mainSource.indexOf('const askForm ='), mainSource.indexOf('\nconst verdict ='));
const entry = `
import ${JSON.stringify(join(src, 'style.css'))};
import ${JSON.stringify(join(src, 'workspace-windows.css'))};
import ${JSON.stringify(join(src, 'studio-shell.css'))};
import ${JSON.stringify(join(src, 'library-pages.css'))};
import {StudioShell} from ${JSON.stringify(join(src, 'studio-shell.ts'))};
import {PerfMeter} from ${JSON.stringify(join(src, 'perf.ts'))};
import {SpendingMeter} from ${JSON.stringify(join(src, 'spending.ts'))};
import {View} from ${JSON.stringify(join(src, 'view.ts'))};
import {PanelStack} from ${JSON.stringify(join(src, 'panels.ts'))};
import {MagnifierPanel} from ${JSON.stringify(join(src, 'magnifier.ts'))};
import {ResultViewer} from ${JSON.stringify(join(src, 'viewer.ts'))};
import {Notes,DescriptionCard} from ${JSON.stringify(join(src, 'notes.ts'))};
import {ShowingBar} from ${JSON.stringify(join(src, 'showing.ts'))};
import {ChatLog} from ${JSON.stringify(join(src, 'chatlog.ts'))};
import {StageLayout} from ${JSON.stringify(join(src, 'stage-layout.ts'))};
import {bindLayerControls} from ${JSON.stringify(join(src, 'layer-controls.ts'))};
import {CAMERA_STEPS} from ${JSON.stringify(join(src, 'camera.ts'))};
import {WorkspaceWindows} from ${JSON.stringify(join(src, 'workspace-windows.ts'))};
import {ResultFocus} from ${JSON.stringify(join(src, 'result-focus.ts'))};
import {livePlacementObject} from ${JSON.stringify(join(src, 'window-placement.ts'))};
import {ExecutionLogView} from ${JSON.stringify(join(src, 'executionlog.ts'))};
import {MadeToday} from ${JSON.stringify(join(src, 'madetoday.ts'))};
import {Shelf, researchersCount} from ${JSON.stringify(join(src, 'shelf.ts'))};
import {ResearcherCards} from ${JSON.stringify(join(src, 'researcher-cards.ts'))};
import {CapabilityPage} from ${JSON.stringify(join(src, 'capabilities.ts'))};
const shell = new StudioShell();
const params = new URLSearchParams(location.search), scenario = params.get('case') || 'magnifier-reference';
const pixels = document.createElement('canvas'); pixels.width=960; pixels.height=600;
const paint = pixels.getContext('2d'); paint.fillStyle='#e1e8d6'; paint.fillRect(0,0,960,600);
for(let y=60;y<580;y+=100) for(let x=60;x<930;x+=120) {paint.beginPath();paint.ellipse(x,y,48,34,0.3,0,Math.PI*2);paint.strokeStyle='#7b9866';paint.lineWidth=5;paint.stroke();paint.fillStyle='#b7cda5';paint.fill();}
paint.fillStyle='#304a22';paint.font='bold 26px sans-serif';paint.fillText('SYNTHETIC RENDERING FIXTURE · no microscopy claim',30,35);
const image = pixels.toDataURL('image/png'), broken='data:image/png;base64,broken';
const sessions = new Map(), commands=[];
const view = new View(document.getElementById('view'));
const stack = new PanelStack();
let magState={type:'magnifier',open:false,objectId:null,sources:[],index:0};
let viewer;
const send=command=>{commands.push(command); if(command.type==='magnifier') {
 if(command.action==='close') magState={...magState,open:false};
 if(command.action==='index') magState={...magState,index:command.index};
 if(command.action==='level') magState={...magState,zoom:command.level};
 mag.render(magState);
} else if(command.type==='presenter' && command.action==='show') viewer.apply({...viewer.state,open:true,index:command.index??0});};
const mag=stack.register(new MagnifierPanel(view,stack,send));
viewer=new ResultViewer(send,()=>sessions,{panels:stack,facts:()=>{const canvas=view.canvas.getBoundingClientRect();return {canvas,area:{width:canvas.width,height:canvas.height},disc:view.discOnCanvas(),object:null,labels:[]};}});
const photo={kind:'image',url:image,label:'Synthetic retained reference with a deliberately long source title',media:'reference',line:'Reference photo — synthetic fixture, not a real retrieved image',credit:'example.org · synthetic attribution',displayScale:1};
const findings={title:'Synthetic study of visible structures',summary:'Controlled rendering fixture. These are invented findings for layout and interaction checks only.',methods:['Synthetic source and values; no model or acquisition executed.'],observations:[{title:'Visible structures',text:'Example observation for report typography.'}],limitations:['No scientific or operational qualification.'],refs:[{url:image,page:'https://example.org/synthetic-source',title:'Synthetic reference photo',credit:'example.org · synthetic'}],findingRevision:{version:1,digest:'synthetic-digest',complete:true}};
const work={resource:{day:'2026-10-10',id:'synthetic-research',version:1},kind:'research'};
const session={rid:'synthetic-research',day:'2026-10-10',n:1,name:'Researcher 1',brief:'Synthetic layout checks',label:'Synthetic retained work',status:'done',snapshot:'/research/2026-10-10/synthetic/view.jpg',outputs:[],items:[],result:findings};
let output={id:'image-1',version:2,current:true,change:'Synthetic revision for visual inspection',kind:'image',path:'synthetic.png',url:image,caption:'Synthetic generated illustration — this is not a microscopy result.',generated:true,altered:false};
if(scenario.startsWith('magnifier')) {
 magState={...magState,open:true,sources:[photo]};
 if(scenario==='magnifier-held') magState={...magState,zoom:2,sources:[{...photo,kind:'held',media:'capture',label:'Synthetic retained capture'}]};
 if(scenario==='magnifier-beside') magState={...magState,sources:[{...photo,kind:'held',media:'capture',label:'Synthetic retained capture'},photo],beside:photo};
 if(scenario==='magnifier-broken') magState={...magState,sources:[{...photo,url:broken}]};
 mag.render(magState);
} else if(!scenario.startsWith('tour-')) {
 if(scenario==='viewer-reference') session.items=[{kind:'reference',ref:0,label:'Reference',media:'reference',line:'Synthetic reference fixture',work}];
 else if(scenario==='viewer-report') session.items=[{kind:'summary',label:'Findings',work}];
 else if(scenario==='viewer-empty') session.items=[];
 else {
  if(scenario==='viewer-image-error') output={...output,url:broken};
  if(scenario==='viewer-video-error') output={...output,kind:'video',url:'data:video/mp4;base64,broken'};
  if(scenario.startsWith('viewer-presentation')) output={...output,kind:'presentation',findingRevision:findings.findingRevision};
  session.outputs=[output]; session.items=[{id:output.id,kind:output.kind,label:'Output',media:'generated',line:'Synthetic output fixture',work}];
 }
 sessions.set(session.rid,session);
 window.fetch=async()=>scenario==='viewer-presentation-loading' ? new Promise(()=>{}) : new Response('Synthetic unavailable response',{status:503});
 viewer.apply({type:'presenter',open:true,rid:session.rid,index:0});
}
// Additional frozen-tour states use real production components with synthetic inputs.
let tour;
if(scenario==='tour-toolbar-healthy') {
 const perf=new PerfMeter(async()=>[]),spending=new SpendingMeter(async()=>({day:'2026-10-10',cap:null,estimatedUsd:.42,trackingSince:1791600000,unpriced:0,recordingErrors:0,entries:[]}));
 const meters=Object.assign(document.createElement('div'),{className:'status-meters'});meters.append(perf.root,spending.root);shell.meterSlot.append(meters);
 perf.update({live:true,fps:30,screenMs:20,analyseMs:10,gpu:{ready:true,device:'synthetic',error:''},machine:{clickP95Ms:100,load1:1,cores:8,memFreePct:70,swapTotalMb:0,swapUsedMb:0,diskFreeGb:150},view:{delayMs:20,slide:'still',outlines:2}});
 shell.describe('made','6 kept · 4 on screen · 1 being made');shell.count('made',6,true);
 shell.describe('researchers','1 working · Research today: $0.42');shell.count('researchers',2,true);
 shell.describe('activity','2 recorded requests');shell.count('activity',2);
 tour={perf,spending,shell};
}
if(scenario==='tour-chat-controls') {
 document.body.dataset.view='live';
 const element=<T extends HTMLElement>(id:string)=>document.getElementById(id) as T;
 const talkButton=element<HTMLButtonElement>('talk'),talkLabel=talkButton.querySelector<HTMLSpanElement>('.talk-label')!;
 ${askAvailability}
 const connection={send:command=>{commands.push(command);return true;}};
 ${chatVisibility}
 const chat=new ChatLog(document.getElementById('log'),document.getElementById('starters'),{activity:document.getElementById('activity-log'),research:()=>undefined,show:()=>{},describe:()=>{},hint:()=>{},voiceLive:()=>false,unread:()=>{if(document.body.dataset.chat==='hidden')chatUnread.hidden=false;}});
 ${threadControls}
 ${voiceWorking}
 ${voiceRenderer}
 const initialDay={type:'day',id:'2026-10-10',active:'synthetic-thread-a',threads:[{id:'synthetic-thread-a',title:'Synthetic leaf conversation',subjects:['leaf'],started:1791600000},{id:'synthetic-thread-b',title:'Synthetic second conversation',subjects:[],started:1791600300}]};
 renderDay(initialDay);chat.setActiveThread(initialDay.active);
 chat.apply({type:'chat',role:'assistant',thread:initialDay.active,turnId:1,text:'Synthetic response for local conversation controls. No model ran.'});
 renderVoiceState('off');
 tour={shell,chat,renderVoiceState,renderDay,setChatHidden,setReady:value=>{workspaceReady=value;updateAskAvailability();askStatus.hidden=value;},setPending:value=>{requestPending=value;updateAskAvailability();},
  echoThread:id=>{renderDay({...initialDay,active:id});chat.apply({type:'thread',thread:id,day:initialDay.id,lines:[]});},
  arrival:()=>{shell.count('researchers',2,true);shell.describe('researchers','Synthetic background work');chat.apply({type:'chat',role:'assistant',thread:day.active,turnId:2,text:'Synthetic late answer for unread-state inspection.'});}};
}
if(scenario==='tour-no-camera') {
 const notice=document.getElementById('notice'); let noticeKey='';
 const feed={bridgeUrl:'http://controlled.fixture',reconnect:async()=>{commands.push({fixture:'reconnect'});return 'Synthetic reconnect: no camera connected';}};
 const showHint=(text)=>commands.push({fixture:'hint',text});
 const setCameraPanel=(open)=>commands.push({fixture:'camera-panel',open});
 ${noticeRenderer}
 showNotice('camera','No camera detected',CAMERA_STEPS,true);
}
if(scenario==='tour-details') {
 const notes=new Notes();
 const obj={id:'synthetic-outline',kind:'highlight',polygons:[],anchorFrame:1,anchorEpoch:1,anchorOffset:[0,0],source:'synthetic-fixture',label:'Synthetic guard cell',labelKey:'synthetic-cell',status:'likely',confidence:.72,note:'',seen:'Synthetic observation for layout only.',group:null,trial:null,parent:null,arm:null,track:'tracking',revisedBy:null,pinned:null,createdAt:0};
 if(params.get('confidence')==='guess') obj.confidence=null;
 if(params.get('confidence')==='words') {obj.source='ai:sam3.1-concept';obj.confidence=.93;}
 if(params.get('confidence')==='confirmed') obj.status='confirmed';
 notes.apply({full:{'synthetic-cell':{key:'synthetic-cell',label:'Synthetic guard cell',status:'ready',description:{title:'Synthetic cell description',summary:'This is synthetic descriptive text used to inspect the production details card. It makes no biological claim.',details:['A first synthetic detail.','A second synthetic detail that wraps across a narrow phone screen.'],look_for:'Synthetic example only',confidence:.72,sources:['https://example.org/synthetic']},refs:[]}}});
 const card=stack.register(new DescriptionCard(view,notes,send));card.setObjects([obj]);card.openFor(obj.id);tour=card;
}
if(scenario==='tour-mode-strips') {
 document.getElementById('blind-bar').hidden=false;
 document.getElementById('stage').classList.add('blind');
 const showing=stack.register(new ShowingBar(document.getElementById('showing-bar'),document.getElementById('showing-text'),document.getElementById('showing-stop'),send));
 showing.set({type:'showing',id:'synthetic-showing',rid:'synthetic-research',about:'synthetic retained finding with a long descriptive title',active:true});tour=showing;
 ${choiceRenderer}
 document.getElementById('verdict').hidden=false;
 document.getElementById('verdict-choices').replaceChildren(choiceButton('A','A',[]),choiceButton('B','B',[10,5]),choiceButton('C','C',[1.5,5]),choiceButton('tie','Tie'),choiceButton('none','None right'));
}
if(scenario==='tour-layers') {
 const stub={externalLegends:false,population:{count:128},layer:{population:'synthetic-population',verified:['c1','c2'],lastChange:{by:'assistant',at:Date.now()/1000},style:{opacity:.85,colour:'column',column:'area',fill:false,legend:{label:'Synthetic cell area',min:12,max:800,unit:'px²'}}},shownTracks:{id:'synthetic-tracks',title:'Synthetic motion sample',count:16,interval:'00:00–00:05',seconds:5,unit:'px/s',scale:null,how:'measured'},tracksLayer:{tracks:'synthetic-tracks',verified:false,why:'Synthetic captured-view sample',style:{opacity:.7,colour:'column',legend:{label:'Synthetic speed',min:0,max:22,unit:'px/s'}}}};
 const layers=bindLayerControls(stub,send);layers.syncCells();layers.syncTracks();const layout=new StageLayout(document.getElementById('stage'),view);layout.schedule();tour={stub,layers,view,layout,stack,WorkspaceWindows};
 createImageBitmap(pixels).then(bitmap=>{const now=Date.now()/1000;view.live=true;view.setFrame({bitmap,header:{type:'frame',id:1,run:'synthetic',boot:'synthetic',seq:1,w:960,h:600,dx:0,dy:0,epoch:1,tracked:true,steady:true,speed:0,t:now,sent:now,fov:[480,300,280]}});layout.schedule();});
}
if(scenario.startsWith('tour-working-')||scenario==='tour-result-card'||scenario==='tour-activity') {
 const chat=new ChatLog(document.getElementById('log'),document.getElementById('starters'),{activity:document.getElementById('activity-log'),research:()=>undefined,show:rid=>commands.push({fixture:'show',rid}),describe:()=>{},hint:()=>{},voiceLive:()=>false,unread:()=>{},openResult:ref=>commands.push({fixture:'open-result',ref})});
 chat.setActiveThread('synthetic-thread');
 chat.apply({type:'chat',role:'user',thread:'synthetic-thread',turnId:1,text:'Show a synthetic reference image for this rendering check.'});
 const complete=scenario.endsWith('complete')||scenario==='tour-result-card', failed=scenario.endsWith('failed');
 const action={tool:'reference_photo',ok:complete?true:failed?false:null,summary:complete?'Synthetic photo rendered':failed?'Synthetic source failed':'Synthetic photo retrieval pending',ms:complete?500:0};
 if(scenario==='tour-result-card')action.resource={day:'2026-10-10',id:'synthetic-reference',version:3,picture:2,title:'Synthetic saved reference',kind:'Reference photo'};
 chat.apply({type:'chat',role:'assistant',thread:'synthetic-thread',turnId:1,text:complete?'Here is the synthetic retained reference.':failed?'This synthetic source failed; the retained image remains available.':'I am finding a synthetic reference for this layout check.',actions:[action]});
 if(scenario.endsWith('unknown'))chat.connected(false);
 if(scenario==='tour-activity') {
  chat.apply({type:'chat',role:'assistant',thread:'synthetic-thread',turnId:2,at:1791600000,text:'Synthetic ordered activity',actions:[{tool:'reference_photo',ok:false,summary:'Synthetic download refused by source',ms:380,at:1791600002.125,requestId:'synthetic-request',actionId:'synthetic-action'},{tool:'zoom',ok:true,summary:'Synthetic zoom rendered',ms:50,at:1791600001.5,requestId:'synthetic-request',actionId:'synthetic-zoom'}]});
  document.getElementById('tray-tab-activity').click();if(document.body.dataset.tray!=='expanded')document.querySelector('[aria-label="Expand tray"]').click();
 }
 tour=chat;
}
if(scenario==='tour-researchers') {
 const now=Date.now()/1000;
 const states=['running','running','partial','done'].map((status,index)=>({...session,rid:'synthetic-research-'+index,n:index+1,name:'Researcher '+(index+1),purpose:'research',created:now-index,started:now-8,seconds:60,spent:0,cap:0,findings:[],corrections:[],history:[],status,step:index===0?'Reviewing retained frames':'',snapshot:image,result:index<2?null:findings,requests:index===1?[{id:'synthetic-question',run:2,what:'a steadier view',why:'Synthetic evidence request only',seconds:15,at:now,status:'asked'}]:[]}));
 const shelf=new Shelf(rid=>commands.push({fixture:'show',rid}),rid=>commands.push({fixture:'dismiss',rid}));shelf.update(states,null);
 const cards=new ResearcherCards(rid=>commands.push({fixture:'show',rid}),send,s=>shelf.isNew(s));cards.update(states,null);tour={shelf,cards};
 document.getElementById('tray-tab-researchers').click();document.querySelector('[aria-label="Expand tray"]').click();
}
if(scenario==='tour-shelf') {
 // The shelf over a live view: chips in the stage's bottom corner, beside the eyepiece, above the dock's tabs.
 const now=Date.now()/1000, day='2026-10-11';
 const base={...session,day,sitting:day,purpose:'research',findings:[],corrections:[],history:[],spent:0,cap:.25,seconds:75,outputs:[],items:[],snapshot:image,requests:[]};
 const states=[
  {...base,rid:'s1',n:1,name:'Researcher 1',status:'done',created:now-400,started:now-390,result:{...findings,title:'How the stomata open'},outputs:[{...output,kind:'image',url:image}]},
  {...base,rid:'s2',n:2,name:'Researcher 2',status:'running',created:now-60,started:now-28,step:'reading a source · 2 of 5 read',brief:'Guard cell turgor and light',result:null},
  {...base,rid:'s3',n:3,name:'Researcher 3',status:'queued',created:now-5,started:null,brief:'Make a lesson on leaf sections',result:null},
 ];
 if(params.get('asking')==='yes') states.push({...base,rid:'s4',n:4,name:'Researcher 4',status:'running',created:now-2,started:now-40,brief:'Count the moving cells',result:null,requests:[{id:'q1',run:0,what:'15 s of this field',why:'Synthetic request only',seconds:15,at:now,status:'asked'}]});
 const count=Number(params.get('count')||states.length);
 const shown=states.slice(0,count);
 const shelf=new Shelf(rid=>commands.push({fixture:'show',rid}),rid=>commands.push({fixture:'dismiss',rid}));shelf.update(shown,{type:'presenter',open:false,rid:null,index:0,shelfRemoved:[]},day);
 const cards=new ResearcherCards(rid=>commands.push({fixture:'show',rid}),send,s=>shelf.isNew(s));cards.update(shown,null,day);
 const tally=researchersCount(shown,day,s=>shelf.isNew(s));shell.count('researchers',tally.count,tally.state);shell.describe('researchers',tally.working+' working · Research today: $0.42');
 shelf.openList=()=>shell.openPage('researchers');
 if(params.get('layers')==='yes'){const stub={externalLegends:false,population:{count:128},layer:{population:'synthetic-population',verified:['c1','c2'],lastChange:{by:'assistant',at:now},style:{opacity:.85,colour:'column',column:'area',fill:false,legend:{label:'Synthetic cell area',min:12,max:800,unit:'px²'}}}};bindLayerControls(stub,send).syncCells();}
 const layout=new StageLayout(document.getElementById('stage'),view);layout.onShelfFit=n=>shelf.fit(n);layout.schedule();tour={shelf,cards,view,layout};
 if(params.get('tray')) {document.getElementById('tray-tab-researchers').click();if(params.get('tray')==='expanded')document.querySelector('[aria-label="Expand tray"]').click();}
 let seq=0;const push=async()=>{const t=Date.now()/1000;view.live=true;view.setFrame({bitmap:await createImageBitmap(pixels),header:{type:'frame',id:++seq,run:'synthetic',boot:'synthetic',seq,w:960,h:600,dx:0,dy:0,epoch:1,tracked:true,steady:true,speed:0,t,sent:t,fov:[480,300,280]}});layout.schedule();};
 void push();setInterval(()=>{void push();},250);
}
if(scenario==='tour-report') {
 const root=document.getElementById('capabilities');root.hidden=false;document.body.dataset.view='capabilities';
 const capability={id:'synthetic-capability',actor:'you',family:'outlining',title:'Synthetic capability',roles:params.get('facts')==='unknown'?[]:['synthetic-role'],evidence:params.get('facts')==='unknown'?[]:['sim:synthetic-layout'],what:'Synthetic local report test',how:{gesture:'click'},available:true,why:'',verdict:null};
 const wire={type:'capabilities',families:[{id:'outlining',title:'Outlining'}],actors:{you:'You',agent:'Agent',auto:'Automatic',researcher:'Researcher'},capabilities:[capability],roles:[{id:'synthetic-role',title:'Synthetic seeing',kind:'model',purpose:'Synthetic model details',current:'synthetic-model',options:[{id:'synthetic-model',label:'Synthetic model',available:true,runs:'mac',tier:'open',licence:'Synthetic declared licence',note:'No measured result; fixture only',sources:[{title:'Synthetic implementation',url:'https://example.org/synthetic-source',licence:'Synthetic declared licence'}]}],powers:['synthetic-capability'],agentCanChange:[]}],preferences:[],pending:0,build:'synthetic-build',agentText:''};
 const capabilities=new CapabilityPage(root,(id,state,note)=>new Promise((resolve,reject)=>{commands.push({fixture:'validate',id,state,note});window.fixtureResolveVerdict=resolve;window.fixtureRejectVerdict=reject;}),()=>{});
 capabilities.update(wire);tour=capabilities;
}
if(scenario==='tour-made-kinds') {
 // Made today with one card per product kind: thumbnails come from /api/resources/<id>/thumbnail (served beside the
 // fixture when made; a missing one shows its kind's icon).
 const day='2026-10-11', at=1791600000;
 const kinds=[['e1','population','Cell outlines · 03:02'],['m1','tracks','Motion · 03:05'],['r2','research',"Researcher 2's lesson"],
  ['snapshot-030812-f9','snapshot','Snapshot · 03:08'],['d3','result','Clearer picture · 03:09'],['b1','bookmark','Bookmark · 03:10 · the stoma'],
  ['k1','clip','Held footage · 15 s · 03:11'],['i4','individual','Followed · 03:12 · cell 12'],['ds-20261011-031500','dataset','Dataset · 03:15'],
  ['MicroscopeMind-20261011-031600','recording','Screen recording · 03:16']];
 const entries=kinds.map(([id,kind,title],i)=>({id,kind,title,day,version:0,summary:'',state:'saved',actions:['open'],at:at+i,product:true,madeBy:kind==='result'?{tool:'enhance'}:{}}));
 window.fetch=async(url)=>new Response(JSON.stringify(String(url).endsWith('/summary')?{kinds:kinds.map(([,kind,name])=>({kind,name:name.split(' · ')[0],count:1})),couldMake:[],running:[]}:{entries}),{status:200,headers:{'Content-Type':'application/json'}});
 const made=new MadeToday(send,(source,title)=>commands.push({fixture:'open-resource',source,title}));tour=made;
 document.getElementById('made-today-button').click();if(params.get('tray')==='expanded')document.querySelector('[aria-label="Expand tray"]').click();void made.open();
}
if(scenario==='tour-made') {
 const base={day:'2026-10-10',version:2,kind:'capture',summary:'Synthetic retained evidence',state:'ready',actions:['open'],at:1791600000};
 const entries=[{...base,id:'requested',title:'Requested synthetic capture',media:'capture',source:{frame:7},madeBy:{by:'agent',requestedBy:'user'},readBy:[{researcher:'r-synthetic',name:'Researcher 7',at:1791600100,version:2,how:'successful resource read'}]},
 {...base,id:'created',title:'User-made synthetic capture',madeBy:{by:'user'}},
 {...base,id:'auto',title:'Independent synthetic work',madeBy:{by:'agent'},media:'generated'},
 {...base,id:'unknown',title:'Older work with unknown attribution',madeBy:{},media:'reference'}];
 window.fetch=async(url)=>new Response(JSON.stringify(String(url).endsWith('/summary')?{kinds:[{kind:'capture',name:'Captures',count:4}],couldMake:[],running:[]}:{entries}),{status:200,headers:{'Content-Type':'application/json'}});
 const made=new MadeToday(send,(source,title)=>commands.push({fixture:'open-resource',source,title}));
 made.views=()=>[{id:'hidden-extra',title:'Unindexed synthetic window',visible:false,resource:null}];
 made.restore=id=>commands.push({fixture:'restore',id});tour=made;
 document.getElementById('made-today-button').click();if(document.body.dataset.tray!=='expanded')document.querySelector('[aria-label="Expand tray"]').click();void made.open();
}
if(scenario==='tour-execution') {
 const root={id:'synthetic-receipt',kind:'tool',at:1791600000.125,day:'2026-10-10',thread:'synthetic-thread',generation:1,turnId:null,objectIds:[],trialId:null,outcome:'failed',revision:1,incomplete:false};
 const detail={status:'available',root,durable:true,steps:[{id:'synthetic-action',parent:root.id,operation:'reference_photo',outcome:'failed',ms:380}],context:{words:'Synthetic photo request',owner:{workspaceId:'synthetic-browser'},build:{revision:'synthetic-build',dirty:false,sourceDigest:'synthetic-source'},settings:{vision:'synthetic-model'}}};
 window.fetch=async(url,init)=>{commands.push({fixture:'fetch',url:String(url),method:init?.method??'GET',body:init?.body??null});return new Response(JSON.stringify(String(url).endsWith('/mark')?{ok:true}:detail),{status:200,headers:{'Content-Type':'application/json'}});};
 const log=new ExecutionLogView(document.getElementById('activity-log'),undefined,async(fields)=>{commands.push({fixture:'retry',...fields});return 'receiptId' in fields?{operations:[{actionId:'synthetic-action',tool:'reference_photo',available:true,reason:'',status:'failed'}]}:{ok:true};});
 log.day=root.day;log.generation=1;log.store.add(root);log.sync(root.thread,new Map());tour=log;
 document.getElementById('tray-tab-activity').click();if(document.body.dataset.tray!=='expanded')document.querySelector('[aria-label="Expand tray"]').click();
}
if(scenario==='tour-windows') {
 const context={workspaceId:'synthetic-workspace',clientId:'synthetic-client',pageEpoch:'synthetic-page',connectionEpoch:'synthetic-connection',appRunId:'synthetic-run'};
 const focus=new ResultFocus();window.addEventListener('resize',()=>focus.sync());
 focus.register('magnifier',document.getElementById('magnifier'),()=> 'Synthetic legacy magnifier',true);
 focus.register('viewer',document.getElementById('viewer'),()=> 'Synthetic retained result',true);
 const states=['native-a','native-b'].map((id,index)=>({id,workspaceId:context.workspaceId,source:{key:id,kind:'saved-image',media:index?'reference':'capture',availability:'retained',identity:{resource:{day:'2026-10-10',id:'synthetic-'+index,version:1,picture:0}},provenance:{}},sourceGeneration:1,lifetimeGeneration:'synthetic-life',revision:1,open:true,title:index?'Synthetic native reference':'Synthetic retained capture',geometryIntent:{position:[30+index*410,150],size:[380,280]},view:{zoom:1,pan:[.5,.5],group:null},propertyVersions:{geometry:1,source:1,open:1,zoom:1,pan:1,front:1,group:1},propertyOwners:{},delivery:{sourceToken:id+'-source',url:image}}));
 let occurrence=0;const admitted=(op,items)=>({context,requestId:'synthetic-request',commandId:'synthetic-command-'+(++occurrence),actionOccurrenceId:'synthetic-occurrence-'+occurrence,items,op});
 const windows=new WorkspaceWindows({host:document.getElementById('stage'),panels:stack,context,facts:()=>{const canvas=view.canvas.getBoundingClientRect();return{canvas,area:{width:canvas.width,height:canvas.height},disc:null,object:null,labels:[]}},onPresentation:(id,root,title,foreground)=>{focus.register(id,root,title);if(foreground)focus.activate(id);},send:command=>{commands.push(command);if(command.action==='group'){const groupIds=[command.instance,...(command.companions??[]).map(x=>x.instance)];const grouped=states.filter(x=>groupIds.includes(x.id));for(const x of grouped){x.revision++;x.view.group='synthetic-group';x.propertyVersions.group++;}windows.apply(admitted('group',grouped));}}});
 if(params.get('live')==='yes') {states[0].source={key:'synthetic-live',kind:'live',media:'live',availability:'live',identity:{run:'synthetic-run',object:'synthetic-object',epoch:1},provenance:{}};states[0].title='Synthetic live placement';}
 windows.apply(admitted('open',states));
 let placementCurrent=true;
 if(params.get('live')==='yes') { void (async()=>{
  const liveImage=new Image();liveImage.src=image;await liveImage.decode();
  const sample={run:'synthetic-run',boot:'synthetic-boot',seq:1,frame:1,epoch:1};
  windows.drawLiveFrame('native-a',{image:liveImage,sample,sourceToken:states[0].delivery.sourceToken,sourceGeneration:1,lifetimeGeneration:'synthetic-life'});
  const pushFrame=async()=>{const now=Date.now()/1000;view.live=true;view.setFrame({bitmap:await createImageBitmap(pixels),header:{type:'frame',id:1,run:sample.run,boot:sample.boot,seq:1,w:960,h:600,dx:0,dy:0,epoch:1,tracked:true,steady:true,speed:0,t:now,sent:now,fov:[480,300,280]}});};
  await pushFrame();setInterval(()=>{void pushFrame();windows.drawLiveFrame('native-a',{image:liveImage,sample,sourceToken:states[0].delivery.sourceToken,sourceGeneration:1,lifetimeGeneration:'synthetic-life'});},200);
  // Only the observation result is synthetic; the actual decoration renderer and freshness filter run.
  view.windowLeaders=()=>windows.livePlacements((source,observed)=>{const object=livePlacementObject(source,observed,view.header,view.isFresh());const canvas=view.canvas.getBoundingClientRect();return object&&placementCurrent?[canvas.x+canvas.width*.55,canvas.y+canvas.height*.85]:null;});
 })(); }
 magState={...magState,open:true,sources:[photo]};mag.render(magState);
 session.outputs=[output];session.items=[{id:output.id,kind:output.kind,label:'Output',media:'generated',line:'Synthetic output fixture',work}];sessions.set(session.rid,session);viewer.apply({type:'presenter',open:true,rid:session.rid,index:0});focus.activate('native-a');
 if(params.get('live')==='yes'){magState={...magState,open:false};mag.render(magState);viewer.apply({type:'presenter',open:false,rid:session.rid,index:0});}
 tour={windows,focus,states,activate:id=>{const state=states.find(x=>x.id===id);if(state){state.revision++;state.propertyVersions.front++;windows.apply(admitted('front',[state]));}focus.activate(id);},setPlacement:current=>{placementCurrent=current}};
}
const label=document.createElement('p'); label.id='fixture-label'; label.textContent='SYNTHETIC RENDERING ONLY · '+scenario+' · production components/styles · no backend, camera, audio or model';
Object.assign(label.style,{position:'fixed',bottom:'2px',left:'4px',right:'4px',zIndex:'99999',fontSize:'10px',lineHeight:'14px',background:'#202024',color:'white',padding:'3px',margin:'0',pointerEvents:'none'});document.body.append(label);
if(scenario==='tour-chat-controls'){label.style.top='2px';label.style.bottom='auto';}
window.fixture={scenario,commands,mag,viewer,source:image,tour};
stack.start(document.getElementById('stage'),room=>{view.reserved=room;});
window.fixtureReady=true;
`;
const entryPath = join(out, 'fixture-entry.ts');
writeFileSync(entryPath, entry);
const result = await build({configFile:false,root:repo,logLevel:'warn',build:{write:false,lib:{entry:entryPath,name:'ControlledFixture',formats:['iife']},cssCodeSplit:false,minify:false}});
const assets = (Array.isArray(result)?result:[result]).flatMap(r=>r.output);
const script=assets.filter(a=>a.type==='chunk').map(a=>a.code).join('\n');
const css=assets.filter(a=>a.type==='asset' && a.fileName.endsWith('.css')).map(a=>a.source).join('\n');
let html=readFileSync(join(repo,'apps/studio/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
html=html.replace('</head>','<style>'+css+'</style></head>').replace('</body>','<script>'+script.replaceAll('</script>','<\\/script>')+'</script></body>');
writeFileSync(join(out,'legacy-fixture.html'),html);
writeFileSync(join(out,'README.md'),'# Controlled legacy component fixture\n\nSynthetic rendering only. No backend, model, camera, microphone, or recording calls. Current production MagnifierPanel, ResultViewer, StudioShell, View, markup and CSS are bundled directly. UI commands are locally echoed only.\n\nOpen legacy-fixture.html?case=CASE. Cases: magnifier-reference, magnifier-held, magnifier-beside, magnifier-broken, viewer-image, viewer-reference, viewer-report, viewer-empty, viewer-image-error, viewer-video-error, viewer-presentation-loading, viewer-presentation-error, tour-no-camera, tour-layers, tour-mode-strips, tour-details, tour-working-thinking, tour-working-complete, tour-working-unknown, tour-working-failed, tour-result-card, tour-researchers, tour-shelf (asking=yes, layers=yes, count=N, tray=compact|expanded), tour-report, tour-made, tour-activity, tour-execution, tour-windows. The tour-details route also accepts confidence=guess, words, or confirmed.\n\nThe notice and vote-button renderer functions are copied byte-for-byte from production main.ts at fixture build time; their external callbacks are isolated. Layer controls, details, chat, Shelf and capability reports instantiate production components. Load the generated HTML through an intercepted localhost URL for secure-context UUID-dependent controls; no server is needed.\n\nReport acknowledgements are manually resolved or rejected through fixture-only hooks. Generated downloads contain synthetic data only.\n\nThis establishes layout and local interaction evidence only. It does not establish correct-browser service delivery, retrieval relevance, retained-source persistence, live camera/voice behavior, or runtime build identity.\n');
console.log(join(out,'legacy-fixture.html'));

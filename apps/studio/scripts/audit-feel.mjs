#!/usr/bin/env node
// The feel budget, measured (docs/design/2026-10-23-part1-uiux-pass/audit.md): click → painted for the interactions
// Will named (Tabs, Models, Library, Data, a researcher Tab), the Tab row's layout shift, repeated and summarised as
// p50/p95. Drives Chrome through Playwright against an isolated instance. Numbers, not adjectives. The layout-shift
// observer watches the Tab row and the Stage bar under it (workspace-feel step 2), which must never move, and the Stage
// canvas is measured as a run's pill comes and goes on the Working strip (step 3): it must keep its exact size.
//
//   node apps/studio/scripts/audit-feel.mjs --base http://127.0.0.1:15280 [--runs 12] [--workspace <id>]
//
// --workspace pins the browser workspace id (sessionStorage 'micromind-workspace'), so a run against an instance that
// keeps a few browser workspaces reuses one instead of making a new one.
import {existsSync, readdirSync} from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const args=process.argv.slice(2);
const opt=(name,fallback)=>{const i=args.indexOf(`--${name}`);return i>=0&&args[i+1]?args[i+1]:fallback;};
const base=opt('base','http://127.0.0.1:15280'),runs=Number(opt('runs','12')),workspace=opt('workspace','');
const core=process.env.PLAYWRIGHT_CORE||readdirSync(path.join(os.homedir(),'.npm/_npx')).map(d=>path.join(os.homedir(),'.npm/_npx',d,'node_modules/playwright-core')).find(existsSync);
if(!core){console.error('playwright-core not found; set PLAYWRIGHT_CORE');process.exit(2);}
const {chromium}=await import(path.join(core,'index.mjs')).catch(()=>import(core));
const chrome=process.env.CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const browser=await chromium.launch({executablePath:chrome,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:900},colorScheme:'dark'});
if(workspace)await page.addInitScript(id=>{try{sessionStorage.setItem('micromind-workspace',id);}catch{}},workspace);
await page.goto(base+'/#',{waitUntil:'domcontentloaded',timeout:30000});
await page.waitForSelector('.workspace-view-tabs [role=tab]',{timeout:20000});await sleep(2000);

// In-page helpers: press something, then wait until a predicate holds, reporting the time to the next painted frame
// after it. The layout-shift observer watches the Tab row and the Stage bar. Every press here is scripted, so the
// browser's own hadRecentInput never excludes the shifts a press causes (the Assistant stepping aside on a researcher's
// Tab widens both rows, as it should); a shift that starts within 500 ms of a scripted press is left out the same way,
// so what is counted is a row moving on its own: on opening (before the first press) and afterwards.
await page.evaluate(()=>{
 window.__feel={
  lastPress:-Infinity,
  press(fn){this.lastPress=performance.now();return fn();},
  async measure(click,ready){const t0=performance.now();this.lastPress=t0;click();let t1=null;while(performance.now()-t0<4000){await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));if(ready()){t1=performance.now();break;}}return t1===null?null:Math.round(t1-t0);},
  rail(label){return [...document.querySelectorAll('button')].find(b=>{const r=b.getBoundingClientRect();return b.getAttribute('aria-label')===label&&r.width>0&&r.left<70;});},
  toolsList(){return [...document.querySelectorAll('.trow')].some(r=>r.getBoundingClientRect().width>0);},
  shifts:{opening:0,after:0},moved:{},
 };
 const rows=[document.querySelector('.workspace-view-strip'),document.querySelector('.workspace-stage-bar')].filter(Boolean);
 const name=n=>n.nodeType===1?`${n.tagName.toLowerCase()}${n.classList.length?'.'+[...n.classList].join('.'):''}`:name(n.parentElement);
 new PerformanceObserver(list=>{const f=window.__feel;for(const entry of list.getEntries()){if(entry.hadRecentInput||entry.startTime-f.lastPress<500)continue;
  const ours=(entry.sources??[]).filter(source=>source.node&&rows.some(row=>row.contains(source.node)));if(!ours.length)continue;
  f.shifts[f.lastPress===-Infinity?'opening':'after']+=entry.value;for(const source of ours)f.moved[name(source.node)]=(f.moved[name(source.node)]??0)+1;}}).observe({type:'layout-shift',buffered:true});
});

const sample=async(name,fn)=>{const values=[];for(let i=0;i<runs;i++){const v=await page.evaluate(fn);if(typeof v==='number')values.push(v);await sleep(250);}values.sort((a,b)=>a-b);const q=p=>values.length?values[Math.min(values.length-1,Math.floor(p*values.length))]:null;return {name,n:values.length,p50:q(0.5),p95:q(0.95)};};
const results=[];
// Models sheet open and close.
results.push(await sample('Models sheet open',async()=>{const f=window.__feel;const t=await f.measure(()=>f.rail('Models').click(),()=>{const c=document.querySelector('.study-overlay[open] .model-card');return !!c&&c.getBoundingClientRect().height>0;});f.press(()=>document.querySelector('.study-overlay')?.dispatchEvent(new Event('cancel',{cancelable:true})));await new Promise(r=>setTimeout(r,300));return t;}));
results.push(await sample('Library sheet open',async()=>{const f=window.__feel;const t=await f.measure(()=>f.rail('Library').click(),()=>{const c=document.querySelector('.study-overlay[open] #assets');return !!c&&c.getBoundingClientRect().height>0;});f.press(()=>document.querySelector('.study-overlay')?.dispatchEvent(new Event('cancel',{cancelable:true})));await new Promise(r=>setTimeout(r,300));return t;}));
results.push(await sample('Data drawer open',async()=>{const f=window.__feel;const w=document.querySelector('.study-data-window');if(w&&!w.hidden){f.press(()=>f.rail('Data').click());await new Promise(r=>setTimeout(r,300));}const t=await f.measure(()=>f.rail('Data').click(),()=>{const w2=document.querySelector('.study-data-window');return !!w2&&!w2.hidden&&w2.getBoundingClientRect().width>100;});f.press(()=>f.rail('Data').click());await new Promise(r=>setTimeout(r,200));return t;}));
// The Tools drawer (workspace-feel step 4) and a task's run page in it (step 5). Tools closes when its list is the place
// showing; a run page closes by its own × and hands the drawer back to the list.
results.push(await sample('Tools drawer open',async()=>{const f=window.__feel;if(f.toolsList()){f.press(()=>f.rail('Tools').click());await new Promise(r=>setTimeout(r,300));}const t=await f.measure(()=>f.rail('Tools').click(),()=>f.toolsList());f.press(()=>f.rail('Tools').click());await new Promise(r=>setTimeout(r,300));return t;}));
results.push(await sample('Run page open',async()=>{const f=window.__feel;if(!f.toolsList()){f.press(()=>f.rail('Tools').click());await new Promise(r=>setTimeout(r,400));}
 const hit=document.querySelector('.trow[data-action=cells] .trow-hit')??document.querySelector('.trow[data-available=true] .trow-hit');if(!hit)return null;
 const open=()=>[...document.querySelectorAll('.capability-controls .run-body')].find(b=>b.getBoundingClientRect().width>0);
 const t=await f.measure(()=>hit.click(),()=>!!open());const shell=open()?.closest('.capability-controls');f.press(()=>shell?.querySelector('.capability-controls-close')?.click());await new Promise(r=>setTimeout(r,300));
 if(f.toolsList()){f.press(()=>f.rail('Tools').click());await new Promise(r=>setTimeout(r,300));}return t;}));
// Tab switch: needs two work Tabs; make one if there is only Main.
await page.evaluate(async()=>{if(document.querySelectorAll('.workspace-view-tabs [data-workspace-view-tab]').length<2){const f=window.__feel;f.press(()=>document.querySelector('.workspace-new-tab').dispatchEvent(new MouseEvent('click',{bubbles:true})));await new Promise(r=>setTimeout(r,200));f.press(()=>[...document.querySelectorAll('.workspace-add-menu [role=menuitem]')].find(b=>b.textContent.startsWith('New Tab'))?.click());await new Promise(r=>setTimeout(r,1500));}});
// A switch redraws the row, so the Tab is found again by its id each frame.
results.push(await sample('Tab switch',async()=>{const f=window.__feel;const tabs=[...document.querySelectorAll('.workspace-view-tabs [data-workspace-view-tab]')];const target=tabs.find(t=>t.getAttribute('aria-selected')!=='true');if(!target)return null;const id=target.dataset.workspaceViewTab;return f.measure(()=>target.click(),()=>document.querySelector(`[data-workspace-view-tab="${id}"]`)?.getAttribute('aria-selected')==='true');}));
results.push(await sample('All Tabs open',async()=>{const f=window.__feel;const t=await f.measure(()=>document.querySelector('.workspace-all-tabs').click(),()=>{const m=document.querySelector('.workspace-all-menu');return !!m&&m.getBoundingClientRect().height>0;});f.press(()=>document.querySelector('.workspace-all-menu')?.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));await new Promise(r=>setTimeout(r,200));return t;}));
results.push(await sample('New researcher Tab open',async()=>{const f=window.__feel;const t=await f.measure(()=>{document.querySelector('.workspace-new-tab').dispatchEvent(new MouseEvent('click',{bubbles:true}));setTimeout(()=>[...document.querySelectorAll('.workspace-add-menu [role=menuitem]')].find(b=>b.textContent.startsWith('New researcher'))?.click(),0);},()=>{const p=document.querySelector('.researcher-draft');return !!p&&!p.hidden&&p.getBoundingClientRect().height>0&&!document.querySelector('.study-research-stage').hidden;});f.press(()=>document.querySelector('[data-workspace-view-tab=main]')?.click());await new Promise(r=>setTimeout(r,400));return t;}));
// The Working strip: a run's pill comes (after a second), turns done and is hidden; the Stage canvas never changes. The run
// is the page's own start/finish events with a test id (nothing is sent to the server).
const stage=await page.evaluate(async()=>{
 const size=()=>{const c=document.querySelector('#stage canvas');const r=c.getBoundingClientRect();return [r.x,r.y,r.width,r.height,c.width,c.height].map(Math.round).join(',');};
 const run='audit-feel-strip',send=(detail)=>window.dispatchEvent(new CustomEvent('workspace-study-action',{detail:{run,...detail}}));
 const sizes=[size()];
 send({phase:'start',capability:'cells',args:{},use:{cells:'basic-bright-objects'},input:{session:document.body.dataset.workspaceProject,title:'Audit',onStage:null,source:null,frame:null,ready:true,held:false}});
 await new Promise(r=>setTimeout(r,1300));const shown=!!document.querySelector(`.strip-pill[data-id="run:${run}"]`);sizes.push(size());
 send({phase:'finish',reply:{ok:true,summary:'audit'}});await new Promise(r=>setTimeout(r,300));sizes.push(size());
 document.querySelector(`.strip-pill[data-id="run:${run}"] .strip-pill-x`)?.click();await new Promise(r=>setTimeout(r,300));sizes.push(size());
 return {shown,sizes,same:sizes.every(s=>s===sizes[0])};
});
const shifts=await page.evaluate(()=>({shifts:window.__feel.shifts,moved:window.__feel.moved}));
await browser.close();
console.log(`interaction\tn\tp50 ms\tp95 ms`);
for(const r of results)console.log(`${r.name}\t${r.n}\t${r.p50}\t${r.p95}`);
console.log(`Tab row and Stage bar layout shift on opening, not caused by a press (cumulative)\t${shifts.shifts.opening.toFixed(4)}`);
console.log(`Tab row and Stage bar layout shift afterwards, not caused by a press (cumulative)\t${shifts.shifts.after.toFixed(4)}`);
if(Object.keys(shifts.moved).length)console.log(`What moved (shifts)\t${Object.entries(shifts.moved).map(([n,c])=>`${n} ×${c}`).join(', ')}`);
console.log(`Stage canvas as a strip pill comes and goes\t${stage.same&&stage.shown?'unchanged':'CHANGED or no pill'}\t${stage.sizes.join(' | ')}`);

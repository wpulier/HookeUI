#!/usr/bin/env node
// Screens of the studio for the usability audit (docs/design/2026-10-23-part1-uiux-pass/audit.md), one picture per
// scene and size, so each can sit beside its target. Drives a real Chrome through Playwright against an isolated
// instance (never the live app); research states come from the in-page stub, which is design evidence only.
//
//   node apps/studio/scripts/audit-shots.mjs --base http://127.0.0.1:15280 --out output/playwright/audit [--scene name]...
//     [--workspace <id>] [--size 1440x900]...
//
// --workspace pins the browser workspace id (sessionStorage 'micromind-workspace'), so a run against an instance that
// keeps a few browser workspaces reuses one instead of making one per size; --size picks sizes (default both).
//
// Needs PLAYWRIGHT_CORE (a playwright-core package directory) or the npx cache, and Google Chrome.
import {mkdir} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const args=process.argv.slice(2);
const opt=(name,fallback)=>{const i=args.indexOf(`--${name}`);return i>=0&&args[i+1]?args[i+1]:fallback;};
const base=opt('base','http://127.0.0.1:15280');
const out=path.resolve(opt('out','output/playwright/audit'));
const stub=opt('stub','http://127.0.0.1:18999/sim2.js');
const only=args.flatMap((a,i)=>a==='--scene'&&args[i+1]?[args[i+1]]:[]);
const workspace=opt('workspace','');
const chosenSizes=args.flatMap((a,i)=>a==='--size'&&args[i+1]?[args[i+1].split('x').map(Number)]:[]);
const sizes=chosenSizes.length?chosenSizes:[[1440,900],[1100,800]];

const core=process.env.PLAYWRIGHT_CORE||[...(await import('node:fs')).readdirSync(path.join(os.homedir(),'.npm/_npx')).map(d=>path.join(os.homedir(),'.npm/_npx',d,'node_modules/playwright-core'))].find(existsSync);
if(!core){console.error('playwright-core not found; set PLAYWRIGHT_CORE');process.exit(2);}
const {chromium}=await import(path.join(core,'index.mjs')).catch(()=>import(core));
const chrome=process.env.CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const rail=async(page,label)=>{await page.evaluate(label=>{const b=[...document.querySelectorAll('button')].find(b=>{const r=b.getBoundingClientRect();return b.getAttribute('aria-label')===label&&r.width>0&&r.left<70;});if(!b)throw new Error(`no rail button ${label}`);b.click();},label);await sleep(500);};
// The Tools list in the drawer: Tools closes when its list is already the place showing, so press it only when it is not.
const tools=async page=>{if(!(await page.evaluate(()=>[...document.querySelectorAll('.trow')].some(r=>r.getBoundingClientRect().width>0))))await rail(page,'Tools');};
const plus=async(page,item)=>{await page.locator('.workspace-new-tab').click();await sleep(200);await page.locator('.workspace-add-menu [role=menuitem]',{hasText:item}).first().click();await sleep(900);};
// The live view streams for ever, so 'load' never fires: wait for the document and the Tab row, not the network.
const home=async page=>{await page.goto(`${base}/?t=${Date.now()}#`,{waitUntil:'domcontentloaded',timeout:30000});await page.waitForSelector('.workspace-view-tabs [role=tab]',{timeout:20000});await sleep(1500);};
const within=(ms,work)=>Promise.race([work,new Promise((_,reject)=>setTimeout(()=>reject(new Error(`took longer than ${ms} ms`)),ms))]);
const stripRun=page=>page.evaluate(()=>window.dispatchEvent(new CustomEvent('workspace-study-action',{detail:{run:'audit-shots-strip',phase:'start',capability:'cells',args:{},use:{cells:'basic-bright-objects'},input:{session:document.body.dataset.workspaceProject,title:'Audit',onStage:null,source:null,frame:null,ready:true,held:false}}})));
const withStub=async page=>{await page.evaluate(async url=>{await import(url+'?'+Date.now());},stub);await sleep(300);};
// A fixture page beside the studio (design evidence only: its states are scripted, not the server's).
const fixture=async(page,name)=>{await page.goto(`${base}/${name}`,{waitUntil:'domcontentloaded',timeout:30000});await sleep(1500);};

/** Scene name → how to reach it. Each starts from the workspace with a Project open. */
const scenes={
 'rail-home':async()=>{},
 'plus-menu':async page=>{await page.locator('.workspace-new-tab').click();await sleep(250);},
 // The Tab row's ⌄ (All Tabs) and the Stage bar under the row (workspace-feel step 2).
 'all-tabs':async page=>{await page.locator('.workspace-all-tabs').click();await sleep(250);},
 'new-tab':async page=>{await plus(page,'New Tab');await sleep(600);},
 'close-tab':async page=>{await plus(page,'New Tab');await sleep(600);await page.locator('.workspace-view-tab:has([aria-selected=true]) .workspace-tab-x').click();await sleep(150);},
 'new-researcher':async page=>{await plus(page,'New researcher');},
 'new-researcher-failed':async page=>{await plus(page,'New researcher');await page.fill('.researcher-draft textarea','count the bright cells in the latest picture');await page.locator('.researcher-draft .study-primary').click();await sleep(5000);},
 'new-study':async page=>{await withStub(page);await plus(page,'New study');await sleep(1200);},
 // A Study made through the Tab row's + (New study…) with the stub's routes; its experiments are the stub's.
 'study-tab':async page=>{await withStub(page);await plus(page,'New study');await page.evaluate(async()=>{
  const d=document.querySelector('dialog.study-new-study'),set=(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));};
  const [question,judged]=d.querySelectorAll('textarea');set(question,'Which segmentation model transfers best to three withheld cell lines, measured on the same masks?');
  set(judged,'Mean IoU against the hand-corrected masks of the three withheld lines; a model must beat Cellpose by 0.03 on two of the three.');
  set(d.querySelector('input'),'Withheld lines');
  for(const c of d.querySelectorAll('input[type=checkbox]:not(:disabled)')){c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));}
  window.__sim.study=null;[...d.querySelectorAll('button')].find(b=>b.textContent==='Create study').click();await new Promise(r=>setTimeout(r,1500));
  window.__sim.experiments?.();});await sleep(2500);},
 'models':async page=>{await rail(page,'Models');await sleep(800);},
 // A Models card opens its Details beside the grid from anywhere on it (workspace-feel step 8).
 'models-details':async page=>{await rail(page,'Models');await sleep(800);await page.locator('.model-card .model-card-hit').first().click();await sleep(800);},
 'library':async page=>{await rail(page,'Library');await sleep(800);},
 'data':async page=>{await rail(page,'Data');},
 'activity':async page=>{await rail(page,'Activity');},
 'inputs':async page=>{await rail(page,'Inputs');},
 'tools':async page=>{await tools(page);},
 // A task's run page in the Tools drawer, with ← Tools (workspace-feel step 5): Outline every cell, else the first task
 // that can run.
 'run-page':async page=>{await tools(page);const cells=page.locator('.trow[data-action=cells] .trow-hit');await (await cells.count()?cells:page.locator('.trow[data-available=true] .trow-hit')).first().click();await sleep(900);},
 'researchers':async page=>{await rail(page,'Researchers');await sleep(800);},
 // The Stage bar's Assistant toggles: press it only when the Assistant is closed.
 'assistant':async page=>{await page.locator('.workspace-stage-bar .study-agent-toggle:not([aria-pressed=true])').click({timeout:1000}).catch(()=>{});await sleep(500);},
 // The Working strip (workspace-feel step 3): a run's pill while it runs and once done, through the page's own run
 // events with a test id (nothing reaches the server); researchers' pills come from the stub or a real run.
 'strip-run':async page=>{await stripRun(page);await sleep(1400);},
 'strip-run-done':async page=>{await stripRun(page);await sleep(1400);await page.evaluate(()=>window.dispatchEvent(new CustomEvent('workspace-study-action',{detail:{run:'audit-shots-strip',phase:'finish',reply:{ok:true,summary:'audit'}}})));await sleep(400);},
 // The researcher Tab (workspace-feel step 7) and Models' card states (step 8) from their fixtures.
 'researcher-tab-fixture':async page=>{await fixture(page,'workspace-researcher.fixture.html?state=writing');},
 'researcher-report-fixture':async page=>{await fixture(page,'workspace-researcher.fixture.html?state=report');},
 'models-fixture':async page=>{await fixture(page,'models.fixture.html');},
 'help':async page=>{await rail(page,'Help');await sleep(600);},
 'settings':async page=>{await rail(page,'Settings');await sleep(600);},
 'launcher':async page=>{for(let i=0;i<8;i++){const closed=await page.evaluate(()=>{const narrow=document.querySelector('.study-project-close');const wide=document.querySelector('.study-tab-close');const b=narrow&&getComputedStyle(narrow).display!=='none'?narrow:wide;if(!b||!document.querySelectorAll('.study-session-tab, .study-project-switcher option[value]').length)return false;b.click();return true;});if(!closed)break;await sleep(700);if(await page.evaluate(()=>!document.querySelector('.study-session-launcher')?.hidden))break;}await sleep(800);},
};

await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:chrome,headless:true});
const results=[];
for(const [w,h] of sizes){
 const context=await browser.newContext({viewport:{width:w,height:h},deviceScaleFactor:1,colorScheme:'dark'});
 if(workspace)await context.addInitScript(id=>{try{sessionStorage.setItem('micromind-workspace',id);}catch{}},workspace);
 const page=await context.newPage();
 for(const [name,run] of Object.entries(scenes)){
  if(only.length&&!only.includes(name))continue;
  try{await within(60000,(async()=>{await home(page);await run(page);const file=path.join(out,`${name}@${w}.png`);await page.screenshot({path:file});})());results.push([name,w,'ok']);}
  catch(error){results.push([name,w,`failed: ${error.message.split('\n')[0]}`]);}
  console.error(`${name}@${w}\t${results[results.length-1][2]}`);
 }
 await context.close();
}
await browser.close();
for(const [name,w,state] of results)console.log(`${name}@${w}\t${state}`);

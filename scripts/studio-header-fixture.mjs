/** Build a labelled, service-free rendering fixture from production markup, CSS and StudioShell.
 * This checks layout combinations only; it cannot certify recording, sharing or model behavior.
 * node scripts/studio-header-fixture.mjs /absolute/private/evidence/directory
 */
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from '../apps/studio/node_modules/typescript/lib/typescript.js';
const repo = fileURLToPath(new URL('../', import.meta.url));
const out = resolve(process.argv[2] || 'output/playwright/studio-header');
mkdirSync(out, { recursive: true });
const dist = join(repo, 'apps/studio/dist');
let html = readFileSync(join(dist, 'index.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '')
  .replace(/<link\b[^>]*href="\/assets\/[^>]+>/g, '');
const css = readdirSync(join(dist, 'assets')).filter(f => f.endsWith('.css')).map(f => readFileSync(join(dist, 'assets', f), 'utf8')).join('\n');
const compile = f => ts.transpileModule(readFileSync(join(repo, 'apps/studio/src', f), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText.replace(/^import .*?;$/gm, '').replace(/^export /gm, '');
const script = `${compile('layout-rules.ts')}\n${compile('studio-shell.ts')}\nnew StudioShell();
const q = new URLSearchParams(location.search), share = q.get('share') || 'starting', record = q.get('record') || 'recording', data = q.get('data') || 'off';
const el = id => document.getElementById(id);
el('workspace-share').parentElement.dataset.state = share;
el('workspace-share').disabled = true; el('workspace-share-stop').hidden = false;
el('workspace-share-status').textContent = share === 'starting' ? 'Tab sharing starting' : 'Tab sharing on';
el('record').dataset.state = record; el('record').setAttribute('aria-pressed', 'true');
el('record').querySelector('.record-label').textContent = record === 'saving' ? 'Saving…' : 'Stop 180:00';
el('dataset').dataset.state = data; el('dataset').querySelector('.dataset-label').textContent = ({off:'Data',checking:'Checking',ok:'Collecting',problem:'Gaps',saving:'Saving',saved:'Saved'})[data];
el('source').dataset.state = 'footage'; el('source').textContent = 'Leaf epidermis under brightfield illumination · a deliberately long retained recording name · 3 min 43 sec';
el('models-toggle').textContent = 'Gemini 3.8 Flash';
// The session chip (session-menu.ts) at its widest: a long name with since, over what it holds, and the suggestion dot.
const chip = el('session'); chip.hidden = false;
const chipName = q.get('session') || 'Leaf epidermis under brightfield, second slide', chipSub = q.get('sessionSub') || '12 made · 3 researchers';
const words = document.createElement('span'); words.className = 'session-chip-words';
const line = document.createElement('span'); line.className = 'session-chip-line';
const nameSpan = document.createElement('span'); nameSpan.className = 'session-chip-name'; nameSpan.textContent = chipName;
const since = document.createElement('span'); since.className = 'session-chip-since'; since.textContent = ' · since yesterday';
const sub = document.createElement('span'); sub.className = 'session-chip-sub'; sub.textContent = chipSub;
const dot = document.createElement('span'); dot.className = 'session-chip-dot'; dot.hidden = q.get('suggest') !== '1';
line.append(nameSpan, since); words.append(line, sub); chip.replaceChildren(words, dot);
chip.setAttribute('aria-label', 'Session: ' + chipName + ' · since yesterday · ' + chipSub); chip.title = chip.getAttribute('aria-label');
const label = document.createElement('p'); label.textContent = 'CONTROLLED RENDERING ONLY · Production shell and styles · No camera, microphone, sharing, recording, model or service calls';
Object.assign(label.style, {position:'fixed',bottom:'4px',left:'8px',zIndex:'9999',fontSize:'11px',background:'#202024',color:'#ffffff',padding:'4px'}); document.body.append(label);
window.fixtureReady = true;
`;
html = html.replace('</head>', `<style>${css}</style></head>`).replace('</body>', `<script>${script}</script></body>`);
writeFileSync(join(out, 'header-fixture.html'), html);
console.log(join(out, 'header-fixture.html'));

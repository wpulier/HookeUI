// Drives one plan through a second stack's studio in a real Chromium (docs/bench/voice-journeys.md), for run.py.
// argv: the playwright module (index.mjs), the Chromium executable, a fresh profile folder, the studio URL, the plan
// (JSON path), where to write the player's state (JSON path), the options (JSON: mode, setup, bridge, acoustic,
// restart: the command that quits and reopens Hooke).
// harness.js is loaded before the studio's own code, so it sees every socket. The talk key is Playwright's trusted
// Control+Option (the studio's hold, live-partner-contract.md Amendment 10), lent to the page as window.__benchKey. At a
// restart step the player pauses; this runs the restart command, reloads the page and resumes after it.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const [,, module, executable, profile, url, planPath, outPath, optionsJson] = process.argv;
const options = JSON.parse(optionsJson || '{}');
const plan = JSON.parse(readFileSync(planPath, 'utf8'));
const harness = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'harness.js'), 'utf8');
const { chromium } = await import(module);

const context = await chromium.launchPersistentContext(profile, {
  executablePath: executable,
  headless: !options.acoustic, // acoustic: the speakers and microphone are real, so the window is too
  viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1,
  permissions: ['microphone'],
  args: ['--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream'],
});
await context.addInitScript(`window.__benchOptions = ${JSON.stringify({ acoustic: !!options.acoustic })};`);
await context.addInitScript(harness);
const page = context.pages()[0] ?? await context.newPage();
const problems = [];
page.on('pageerror', (error) => problems.push(`pageerror ${error.message}`.slice(0, 400)));
page.on('console', (message) => { if (message.type() === 'error') problems.push(`console ${message.text()}`.slice(0, 400)); });
// A takeover while the partner acts (the Drive line): the person's own mouse or Escape, trusted.
await page.exposeFunction('__benchTakeover', async (how) => {
  if (how === 'escape') await page.keyboard.press('Escape');
  else { await page.mouse.move(700, 450); await page.mouse.move(760, 490, { steps: 6 }); }
  return Date.now();
});
await page.exposeFunction('__benchKey', async (down) => {
  if (down) { await page.keyboard.down('Control'); await page.keyboard.down('Alt'); }
  else { await page.keyboard.up('Alt'); await page.keyboard.up('Control'); }
  return Date.now();
});

async function open() {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.locator('#talk').waitFor({ state: 'visible', timeout: 120000 });
  await page.waitForTimeout(options.settleMs ?? 5000); // the studio connects and draws its first frames
}

await open();
let from = 0, carry = null, state = null;
for (;;) {
  state = await page.evaluate(([p, o]) => window.__bench.run(p, o), [plan, { bridge: options.bridge, mode: options.mode,
    setup: !!options.setup, from, carry }]);
  if (!state.paused?.restart) break;
  const t0 = Date.now();
  const [command, ...args] = options.restart;
  const told = execFileSync(command, args, { encoding: 'utf8', cwd: options.cwd });
  state.events.push({ t: Date.now(), e: 'restart', ms: Date.now() - t0, told: told.trim().slice(-300) });
  carry = state; from = state.paused.next;
  await open();
}
state.problems = problems.splice(0);
state.voice = options.voice ?? null;
writeFileSync(outPath, JSON.stringify(state));
await context.close();
process.exit(0);
